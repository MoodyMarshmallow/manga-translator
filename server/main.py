"""FastAPI server orchestrating OCR, grouping, and translation."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from PIL import Image

from .grouping import group_words
from .ocr import document_ocr
from .translate import translate_groups_kr_to_en
from .translate import translate_groups_jp_to_en
from .types import OCRWord, WordGroup

logger = logging.getLogger(__name__)

app: FastAPI = FastAPI(title="Manga Translator API", version="0.1.0")


class Size(BaseModel):
    w: int = Field(..., ge=1)
    h: int = Field(..., ge=1)


class AnalyzeRequest(BaseModel):
    image_url: Optional[str] = None
    image_b64: Optional[str] = None
    intrinsic_size: Optional[Size] = None
    language_hint: Optional[str] = Field(default="ja", description="Language hint for OCR")

    def load_bytes(self) -> bytes:
        if self.image_b64:
            try:
                _, data = self.image_b64.split(",", 1)
            except ValueError:
                data = self.image_b64
            return base64.b64decode(data)
        if self.image_url:
            logger.info("Fetching image from %s", self.image_url)
            response = requests.get(self.image_url, timeout=20)
            if not response.ok:
                raise HTTPException(status_code=502, detail="Failed to fetch image URL")
            return response.content
        raise HTTPException(status_code=400, detail="Provide image_url or image_b64")


@app.post("/analyze")
def analyze(req: AnalyzeRequest) -> Dict[str, Any]:
    image_bytes = req.load_bytes()
    words, _ = document_ocr(image_bytes, language_hint=req.language_hint)
    groups: List[WordGroup] = group_words(words)

    for group in groups:
        indices: List[int] = group["word_idx"]
        words_in_group: List[OCRWord] = [words[i] for i in indices]
        if group["orientation"] == "vertical":
            words_in_group.sort(
                key=lambda w: (
                    sum(p[0] for p in w["poly"]) / 4.0,
                    sum(p[1] for p in w["poly"]) / 4.0,
                ),
                reverse=True,
            )
        else:
            words_in_group.sort(
                key=lambda w: (
                    min(p[1] for p in w["poly"]),
                    min(p[0] for p in w["poly"]),
                )
            )
        group["kr_text"] = " ".join(word["text"] for word in words_in_group)

    translation_map = translate_groups_kr_to_en(groups)
    for group in groups:
        group["en_text"] = translation_map.get(group["id"], "")

    with Image.open(io.BytesIO(image_bytes)) as im:
        width, height = im.size

    response_groups: List[Dict[str, Any]] = []
    for group in groups:
        x0, y0, x1, y1 = group["bbox"]
        response_groups.append(
            {
                "id": group["id"],
                "bbox": {"x0": int(x0), "y0": int(y0), "x1": int(x1), "y1": int(y1)},
                "orientation": group["orientation"],
                "kr_text": group.get("kr_text", ""),
                "en_text": group.get("en_text", ""),
            }
        )

    return {
        "ocr_image_size": {"w": width, "h": height},
        "groups": response_groups,
    }


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}
