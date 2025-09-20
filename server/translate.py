"""Translation helpers using the Cerebras SDK when available."""

from __future__ import annotations

import importlib
import json
import logging
import os
from typing import Any, Dict, Iterable, List, Protocol, Sequence, cast

from .types import WordGroup


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


def translate_groups_jp_to_en(groups: Iterable[WordGroup]) -> Dict[str, str]:
    groups_list: List[WordGroup] = list(groups)
    client = _client()
    if client is None:
        return {g["id"]: g.get("jp_text", "") for g in groups_list}

    payload: List[Dict[str, str]] = [
        {"id": g["id"], "jp": g.get("jp_text", "")}
        for g in groups_list
    ]
    system_prompt = (
        "You are a professional manga translator. Translate Japanese to natural, "
        "concise English while preserving honorifics when present. Return JSON only."
    )
    user_prompt = "Translate the following Japanese text entries to English: \n" + json.dumps(
        payload, ensure_ascii=False
    )
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
        return {g["id"]: "" for g in groups_list}
    message: Any = getattr(choices[0], "message", {})
    content = str(getattr(message, "content", ""))
    data = json.loads(content)
    return {item["id"]: item.get("en", "") for item in data.get("items", [])}


__all__ = ["translate_groups_jp_to_en"]
