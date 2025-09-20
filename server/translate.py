"""Translation helpers using the Cerebras SDK when available."""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, Iterable

try:
    from cerebras.cloud.sdk import Cerebras  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    Cerebras = None  # type: ignore

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


def _client() -> "Cerebras" | None:
    api_key = "csk-r8ym8cw3jcrtfmdfjh3cmchyfthcwr8nt4mkymyfrthkcr42"
    if Cerebras is None or not api_key:
        if Cerebras is None:
            logger.warning("cerebras-cloud-sdk not installed; returning fallback translations")
        else:
            logger.warning("CEREBRAS_API_KEY not set; returning fallback translations")
        return None
    return Cerebras(api_key=api_key)


def translate_groups_kr_to_en(groups: Iterable[dict]) -> Dict[str, str]:
    groups = list(groups)
    client = _client()
    if client is None:
        return {g["id"]: g.get("kr_text", "") for g in groups}

    payload = [{"id": g["id"], "kr": g.get("kr_text", "")} for g in groups]
    system_prompt = (
        "You are a professional manhwa translator. Translate Korean to natural, "
        "concise English while preserving honorifics when present. Return JSON only."
    )
    user_prompt = "Translate the following Korean text entries to English: \n" + json.dumps(payload, ensure_ascii=False)
    response = client.chat.completions.create(
        model="llama-3.3-70b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=TRANSLATION_SCHEMA,
        temperature=0.2,
    )
    content = response.choices[0].message.content
    data = json.loads(content)
    return {item["id"]: item.get("en", "") for item in data.get("items", [])}


__all__ = ["translate_groups_kr_to_en"]
