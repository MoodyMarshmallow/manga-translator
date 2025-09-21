"""Translation helpers with pluggable translation backends."""

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Protocol, Sequence, Tuple, TypedDict, cast

import requests

from .types import WordGroup
from .context_store import ContextEntry

# Constants for robust API calls
MAX_ITEMS_PER_REQUEST = 40  # Larger batches reduce total API calls
MAX_JSON_CHARS_PER_REQUEST = 12_000  # Guardrail to stay within context limits
MAX_RETRIES = 3
INITIAL_RETRY_DELAY_SECONDS = 3
MIN_SECONDS_BETWEEN_CALLS = 1.2  # Throttle to stay below Cerebras 1 RPS limit


class _ChatCompletionsProtocol(Protocol):
    def create(
        self,
        *,
        model: str,
        messages: Sequence[Dict[str, str]],
        response_format: Dict[str, Any],
        temperature: float,
    ) -> Any:
        """Return a chat completion payload."""


class _ChatProtocol(Protocol):
    completions: _ChatCompletionsProtocol


class CerebrasClient(Protocol):
    chat: _ChatProtocol


_cerebras_class: Any | None = None
_cerebras_attempted = False

logger = logging.getLogger(__name__)

_rate_limit_lock = threading.Lock()
_last_api_call: float = 0.0

TRANSLATION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "bubble_translations",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string"},
                            "en": {"type": "string"},
                        },
                        "required": ["id", "en"],
                    },
                }
            },
            "required": ["items"],
        },
    },
}


def _load_cerebras_class() -> Any | None:
    global _cerebras_class, _cerebras_attempted
    if _cerebras_attempted:
        return _cerebras_class
    _cerebras_attempted = True
    try:
        module = importlib.import_module("cerebras.cloud.sdk")  # pyright: ignore[reportMissingImports]
    except ImportError:
        _cerebras_class = None
    else:
        _cerebras_class = cast(Any, getattr(module, "Cerebras", None))
    return _cerebras_class


def _client() -> CerebrasClient | None:
    api_key = os.environ.get("CEREBRAS_API_KEY")
    cerebras_class = _load_cerebras_class()
    if cerebras_class is None or not api_key:
        if cerebras_class is None:
            logger.warning("cerebras-cloud-sdk not installed; returning fallback translations")
        else:
            logger.warning("CEREBRAS_API_KEY not set; returning fallback translations")
        return None
    return cast(CerebrasClient, cerebras_class(api_key=api_key))


def _respect_rate_limit() -> None:
    """Sleep just enough to respect Cerebras' per-second quota across threads."""

    if MIN_SECONDS_BETWEEN_CALLS <= 0:
        return

    global _last_api_call
    with _rate_limit_lock:
        now = time.monotonic()
        wait = _last_api_call + MIN_SECONDS_BETWEEN_CALLS - now
        if wait > 0:
            logger.debug("Throttling Cerebras call for %.2fs to respect rate limits", wait)
            time.sleep(wait)
            now = time.monotonic()
        _last_api_call = now


def _retry_after_seconds(error: Exception) -> float | None:
    """Extract a Retry-After hint if the SDK surfaced one."""

    header_value: str | None = None
    response = getattr(error, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        if headers and hasattr(headers, "get"):
            header_value = headers.get("Retry-After")
    if header_value is None:
        headers = getattr(error, "headers", None)
        if headers and hasattr(headers, "get"):
            header_value = headers.get("Retry-After")
    if not header_value:
        return None
    try:
        return float(header_value)
    except (TypeError, ValueError):
        return None


def _status_code_from_error(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    if response is not None and hasattr(response, "status_code"):
        return cast(int, getattr(response, "status_code", None))
    status = getattr(error, "status_code", None)
    return cast(int | None, status)


class _AnnotatedGroup(TypedDict):
    group: WordGroup
    x_center: float
    y_center: float
    width: float


class _Column(TypedDict):
    x_center: float
    tolerance: float
    items: List[_AnnotatedGroup]


def _batched_groups(
    groups: Sequence[WordGroup],
) -> Iterable[Tuple[List[WordGroup], List[Dict[str, str]]]]:
    """Yield groups and serialized payloads sized for larger JSON batches."""

    prefix_size = len("{\"items\":[")
    suffix_size = len("]}")
    current_groups: List[WordGroup] = []
    current_payload: List[Dict[str, str]] = []
    current_size = prefix_size + suffix_size

    for group in groups:
        payload_entry = {"id": group["id"], "kr": group.get("kr_text", "")}
        entry_json = json.dumps(payload_entry, ensure_ascii=False)
        entry_size = len(entry_json)
        separator_size = 1 if current_payload else 0

        if current_payload and (
            len(current_payload) >= MAX_ITEMS_PER_REQUEST
            or current_size + separator_size + entry_size > MAX_JSON_CHARS_PER_REQUEST
        ):
            yield current_groups, current_payload
            current_groups = []
            current_payload = []
            current_size = prefix_size + suffix_size
            separator_size = 0

        current_groups.append(group)
        current_payload.append(payload_entry)
        current_size += separator_size + entry_size

    if current_payload:
        yield current_groups, current_payload


def _order_groups_left_to_right(groups: Sequence[WordGroup]) -> List[WordGroup]:
    """Return groups ordered by columns (left→right) and rows (top→bottom)."""

    annotated: List[_AnnotatedGroup] = []
    for group in groups:
        bbox = group.get("bbox", (0.0, 0.0, 0.0, 0.0))
        try:
            x0, y0, x1, y1 = (float(coord) for coord in bbox)
        except (TypeError, ValueError):
            x0 = y0 = 0.0
            x1 = x0 + 1.0
            y1 = y0 + 1.0
        width = max(x1 - x0, 1.0)
        height = max(y1 - y0, 1.0)
        annotated.append(
            {
                "group": group,
                "x_center": x0 + width / 2.0,
                "y_center": y0 + height / 2.0,
                "width": width,
            }
        )

    if not annotated:
        return list(groups)

    columns: List[_Column] = []
    for item in sorted(annotated, key=lambda entry: entry["x_center"]):
        tolerance = max(item["width"] * 1.5, 64.0)
        target_column: _Column | None = None
        for column in columns:
            if abs(item["x_center"] - column["x_center"]) <= column["tolerance"]:
                target_column = column
                break
        if target_column is None:
            target_column = {
                "x_center": item["x_center"],
                "tolerance": tolerance,
                "items": [],
            }
            columns.append(target_column)
        target_column["items"].append(item)
        count = len(target_column["items"])
        target_column["x_center"] += (item["x_center"] - target_column["x_center"]) / count
        target_column["tolerance"] = max(target_column["tolerance"], tolerance)

    ordered: List[WordGroup] = []
    for column in sorted(columns, key=lambda col: col["x_center"]):
        column["items"].sort(key=lambda entry: entry["y_center"])
        ordered.extend(entry["group"] for entry in column["items"])
    return ordered


TRANSLATOR_PROVIDER_ENV = "TRANSLATOR_PROVIDER"
DEFAULT_TRANSLATOR_PROVIDER = "cerebras"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT_TEXT = (
    "You are a professional manhwa translator. Translate Korean to natural, concise English while preserving honorifics when present. Prioritise translating the meaning of the sentence in the narrative ratheer than preserving word-for-word accuracy. Remember translate Korean to English. Return JSON only."
)

USER_PROMPT_PREFIX = (
    "Translate each Korean entry in the following JSON payload to English. Respond with JSON "
    "matching the schema and include every id.\n"
)


class TranslationError(Exception):
    """Raised when a translation provider cannot satisfy a batch request."""


class Translator(Protocol):
    def is_available(self) -> bool:
        ...

    def translate_batch(
        self,
        batch_groups: Sequence[WordGroup],
        payload: List[Dict[str, str]],
        *,
        context_json: str | None,
    ) -> Dict[str, str]:
        ...


class FallbackTranslator:
    """Returns Korean text unchanged when no provider is configured."""

    def is_available(self) -> bool:
        return True

    def translate_batch(
        self,
        batch_groups: Sequence[WordGroup],
        payload: List[Dict[str, str]],
        *,
        context_json: str | None,
    ) -> Dict[str, str]:
        return {entry["id"]: entry.get("kr", "") for entry in payload if entry.get("id")}


class CerebrasTranslator:
    def __init__(self) -> None:
        self._client = _client()

    def is_available(self) -> bool:
        return self._client is not None

    def translate_batch(
        self,
        batch_groups: Sequence[WordGroup],
        payload: List[Dict[str, str]],
        *,
        context_json: str | None,
    ) -> Dict[str, str]:
        client = self._client
        if client is None:
            raise TranslationError("Cerebras client unavailable")

        payload_json = json.dumps({"items": payload}, ensure_ascii=False)
        system_prompt = SYSTEM_PROMPT_TEXT
        if context_json:
            system_prompt += " Maintain consistency with the supplied prior dialogue context."

        user_sections: List[str] = []
        if context_json:
            user_sections.append("Earlier conversation context:\n" + context_json)
        user_sections.append(USER_PROMPT_PREFIX + payload_json)
        user_prompt = "\n\n".join(user_sections)

        delay = float(INITIAL_RETRY_DELAY_SECONDS)
        last_exception: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                _respect_rate_limit()
                response = client.chat.completions.create(
                    model="llama-3.3-70b",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format=TRANSLATION_SCHEMA,
                    temperature=0.2,
                )
                choices: Sequence[Any] = getattr(response, "choices", [])
                if not choices:
                    raise TranslationError("Empty response from Cerebras API")
                message: Any = getattr(choices[0], "message", {})
                content = str(getattr(message, "content", ""))
                data = json.loads(content)
                return {
                    item.get("id", ""): item.get("en", "")
                    for item in data.get("items", [])
                    if item.get("id")
                }
            except json.JSONDecodeError as exc:  # noqa: PERF203
                logger.error("Failed to decode JSON from Cerebras response: %s", exc)
                raise TranslationError("Malformed JSON from Cerebras API") from exc
            except Exception as exc:  # noqa: PERF203
                last_exception = exc
                status_code = _status_code_from_error(exc)
                logger.warning(
                    "Cerebras API call failed (attempt %s/%s, status %s): %s. Retrying in %.1fs...",
                    attempt + 1,
                    MAX_RETRIES,
                    status_code if status_code is not None else "unknown",
                    exc,
                    delay,
                )
                time.sleep(delay)
                retry_hint = _retry_after_seconds(exc)
                if retry_hint is not None:
                    delay = max(retry_hint, delay * 1.5)
                else:
                    delay *= 1.5

        raise TranslationError("Cerebras API call failed after retries") from last_exception


class GeminiTranslator:
    def __init__(self) -> None:
        self._api_key = os.environ.get("GEMINI_API_KEY")
        self._model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self._session = requests.Session()

    def is_available(self) -> bool:
        return bool(self._api_key)

    def translate_batch(
        self,
        batch_groups: Sequence[WordGroup],
        payload: List[Dict[str, str]],
        *,
        context_json: str | None,
    ) -> Dict[str, str]:
        if not self._api_key:
            raise TranslationError("GEMINI_API_KEY not configured")

        payload_json = json.dumps({"items": payload}, ensure_ascii=False)
        instruction = SYSTEM_PROMPT_TEXT
        user_sections: List[str] = []
        if context_json:
            user_sections.append("Earlier conversation context:\n" + context_json)
        user_sections.append(USER_PROMPT_PREFIX + payload_json)
        prompt_text = instruction + "\n\n" + "\n\n".join(user_sections)

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent"
        )

        delay = float(INITIAL_RETRY_DELAY_SECONDS)
        last_exception: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._session.post(
                    url,
                    params={"key": self._api_key},
                    json={
                        "contents": [
                            {
                                "role": "user",
                                "parts": [{"text": prompt_text}],
                            }
                        ],
                        "generationConfig": {
                            "temperature": 0.2,
                        },
                    },
                    timeout=30,
                )
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = max(float(retry_after), delay * 1.5)
                        except ValueError:
                            delay *= 1.5
                    else:
                        delay *= 1.5
                    raise TranslationError("Gemini rate limited")
                response.raise_for_status()
                body = response.json()
                candidates = body.get("candidates", [])
                if not candidates:
                    raise TranslationError("Gemini returned no candidates")
                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    raise TranslationError("Gemini candidate missing parts")
                content_text = parts[0].get("text", "")
                data = json.loads(content_text)
                return {
                    item.get("id", ""): item.get("en", "")
                    for item in data.get("items", [])
                    if item.get("id")
                }
            except (requests.RequestException, json.JSONDecodeError, TranslationError) as exc:
                last_exception = exc
                logger.warning(
                    "Gemini API call failed (attempt %s/%s): %s. Retrying in %.1fs...",
                    attempt + 1,
                    MAX_RETRIES,
                    exc,
                    delay,
                )
                time.sleep(delay)
                delay *= 1.5

        raise TranslationError("Gemini API call failed after retries") from last_exception


_translator_instance: Translator | None = None


def _build_context_json(
    conversation_context: Sequence[ContextEntry] | None,
) -> str | None:
    if not conversation_context:
        return None
    items: List[Dict[str, str]] = []
    for entry in conversation_context:
        context_entry: Dict[str, str] = {}
        kr_text = entry.get("kr", "")
        en_text = entry.get("en", "")
        if kr_text:
            context_entry["kr"] = kr_text
        if en_text:
            context_entry["en"] = en_text
        if context_entry:
            items.append(context_entry)
    if not items:
        return None
    return json.dumps({"items": items}, ensure_ascii=False)


def _get_translator() -> Translator:
    global _translator_instance
    if _translator_instance is not None:
        return _translator_instance

    provider = os.environ.get(TRANSLATOR_PROVIDER_ENV, DEFAULT_TRANSLATOR_PROVIDER).lower().strip()
    translator: Translator
    if provider == "gemini":
        translator = GeminiTranslator()
    elif provider == "cerebras":
        translator = CerebrasTranslator()
    else:
        logger.warning("Unknown translator provider '%s'; defaulting to Cerebras", provider)
        translator = CerebrasTranslator()

    if not translator.is_available():
        logger.warning(
            "Translator provider '%s' is unavailable; falling back to echo translations",
            provider,
        )
        translator = FallbackTranslator()

    _translator_instance = translator
    return translator


def translate_groups_kr_to_en(
    groups: Iterable[WordGroup],
    *,
    conversation_context: Sequence[ContextEntry] | None = None,
) -> Dict[str, str]:
    groups_list = _order_groups_left_to_right(list(groups))

    translator = _get_translator()
    context_json = _build_context_json(conversation_context)

    all_translations: Dict[str, str] = {}
    for batch_index, (batch_groups, payload) in enumerate(_batched_groups(groups_list)):
        try:
            translations = translator.translate_batch(
                batch_groups,
                payload,
                context_json=context_json,
            )
        except TranslationError as exc:
            logger.error(
                "Translation provider failed for batch %s (size %s): %s",
                batch_index,
                len(batch_groups),
                exc,
            )
            translations = {}

        for group in batch_groups:
            fallback_text = group.get("kr_text", "")
            text = translations.get(group["id"], fallback_text)
            all_translations[group["id"]] = text or fallback_text

    return all_translations


__all__ = ["translate_groups_kr_to_en"]
