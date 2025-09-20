"""Translation helpers using the Cerebras SDK when available."""

from __future__ import annotations

import importlib
import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Protocol, Sequence, cast

from .types import WordGroup

# Constants for robust API calls
BATCH_SIZE = 15
MAX_RETRIES = 3
INITIAL_RETRY_DELAY_SECONDS = 2


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


def translate_groups_kr_to_en(groups: Iterable[WordGroup]) -> Dict[str, str]:
    groups_list: List[WordGroup] = list(groups)
    client = _client()
    if client is None:
        return {g["id"]: g.get("kr_text", "") for g in groups_list}

    all_translations: Dict[str, str] = {}
    # Process groups in smaller, more reliable batches
    for i in range(0, len(groups_list), BATCH_SIZE):
        batch = groups_list[i : i + BATCH_SIZE]
        payload: List[Dict[str, str]] = [{"id": g["id"], "kr": g.get("kr_text", "")} for g in batch]
        system_prompt = (
            "You are a professional manhwa translator. Translate Korean to natural, "
            "concise English while preserving honorifics when present. Return JSON only."
        )
        user_prompt = "Translate the following Korean text entries to English: \n" + json.dumps(
            payload, ensure_ascii=False
        )

        response = None
        delay = float(INITIAL_RETRY_DELAY_SECONDS)
        for attempt in range(MAX_RETRIES):
            try:
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
                logger.warning(
                    f"API call failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}. Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
                delay *= 2  # Exponential backoff

        if response:
            choices: Sequence[Any] = getattr(response, "choices", [])
            if choices:
                message: Any = getattr(choices[0], "message", {})
                content = str(getattr(message, "content", ""))
                try:
                    data = json.loads(content)
                    for item in data.get("items", []):
                        all_translations[item["id"]] = item.get("en", "")
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from API response: {content}")
        else:
            logger.error(f"API call failed for batch starting at index {i} after {MAX_RETRIES} retries.")
            # Mark items in the failed batch as untranslated so the app doesn't crash
            for group in batch:
                all_translations[group["id"]] = f"[Translation failed for: {group.get('kr_text', '')}]"

    return all_translations


__all__ = ["translate_groups_kr_to_en"]