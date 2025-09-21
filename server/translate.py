"""Translation helpers using the Cerebras SDK when available."""

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Protocol, Sequence, Tuple, cast

from .types import WordGroup

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


def _group_sort_key(group: WordGroup) -> tuple[float, float]:
    x0, y0, _, _ = group.get("bbox", (0.0, 0.0, 0.0, 0.0))
    orientation = group.get("orientation", "horizontal")
    if orientation == "vertical":
        return (x0, y0)
    return (y0, x0)


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


def translate_groups_kr_to_en(groups: Iterable[WordGroup]) -> Dict[str, str]:
    groups_list: List[WordGroup] = list(groups)
    groups_list.sort(key=_group_sort_key)
    client = _client()
    if client is None:
        return {g["id"]: g.get("kr_text", "") for g in groups_list}

    all_translations: Dict[str, str] = {}
    # Process groups in larger JSON batches to minimize API calls
    for batch_index, (batch_groups, payload) in enumerate(_batched_groups(groups_list)):
        payload_json = json.dumps({"items": payload}, ensure_ascii=False)
        system_prompt = (
            "You are a professional manhwa translator. Translate Korean to natural, "
            "concise English while preserving honorifics when present. Remember translate Korean to English. Return JSON only."
        )
        user_prompt = (
            "Translate each Korean entry in the following JSON payload to English. "
            "Respond with JSON matching the schema and include every id.\n" + payload_json
        )

        response = None
        delay = float(INITIAL_RETRY_DELAY_SECONDS)
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
                break  # Success! Exit the retry loop.
            except Exception as e:
                status_code = _status_code_from_error(e)
                logger.warning(
                    "Cerebras API call failed (attempt %s/%s, status %s): %s. Retrying in %.1fs...",
                    attempt + 1,
                    MAX_RETRIES,
                    status_code if status_code is not None else "unknown",
                    e,
                    delay,
                )
                time.sleep(delay)
                retry_hint = _retry_after_seconds(e)
                if retry_hint is not None:
                    delay = max(retry_hint, delay * 1.5)
                else:
                    delay *= 1.5  # Gentler backoff

        if response:
            choices: Sequence[Any] = getattr(response, "choices", [])
            if choices:
                message: Any = getattr(choices[0], "message", {})
                content = str(getattr(message, "content", ""))
                try:
                    data = json.loads(content)
                    translations = {
                        item.get("id", ""): item.get("en", "")
                        for item in data.get("items", [])
                        if item.get("id")
                    }
                    for group in batch_groups:
                        text = translations.get(group["id"], group.get("kr_text", ""))
                        all_translations[group["id"]] = text or group.get("kr_text", "")
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from API response: {content}")
                    for group in batch_groups:
                        all_translations[group["id"]] = group.get("kr_text", "")
                continue
        # Ensure every group in the batch receives some text, even after failures.
        if response is None or not choices:
            logger.error(
                "API call failed for batch %s (size %s) after %s retries.",
                batch_index,
                len(batch_groups),
                MAX_RETRIES,
            )
            for group in batch_groups:
                all_translations[group["id"]] = group.get("kr_text", "")

    return all_translations


__all__ = ["translate_groups_kr_to_en"]
