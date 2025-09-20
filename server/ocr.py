"""OCR helpers for the manga translator backend."""

from __future__ import annotations

import io
import logging
from typing import List, Tuple

from PIL import Image

try:
    from google.cloud import vision  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    vision = None  # type: ignore

logger = logging.getLogger(__name__)

WordPoly = List[Tuple[int, int]]


def _fallback_words(image_bytes: bytes) -> List[dict]:
    """Return a single placeholder word covering the entire image."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        width, height = im.size
    poly: WordPoly = [(0, 0), (width, 0), (width, height), (0, height)]
    return [{"text": "[ocr unavailable]", "poly": poly}]


def document_ocr(image_bytes: bytes, language_hint: str | None = "ja") -> Tuple[List[dict], object | None]:
    """Run Google Cloud Vision OCR if available, otherwise fall back.

    Returns a tuple of (words, raw_response). Each word is a mapping with keys
    ``text`` and ``poly`` (list of (x, y) tuples describing the bounding
    quadrilateral in image pixel coordinates).
    """

    if vision is None:
        logger.warning("google-cloud-vision not installed; returning fallback OCR result")
        return _fallback_words(image_bytes), None

    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)
    image_context = None
    if language_hint:
        image_context = vision.ImageContext(language_hints=[language_hint])

    response = client.document_text_detection(image=image, image_context=image_context)
    words: List[dict] = []
    for page in response.full_text_annotation.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    text = "".join(symbol.text for symbol in word.symbols)
                    verts = [(vertex.x, vertex.y) for vertex in word.bounding_box.vertices]
                    words.append({"text": text, "poly": verts})
    if not words:
        logger.info("Vision OCR returned no words; using fallback bubble")
        return _fallback_words(image_bytes), response
    return words, response


__all__ = ["document_ocr"]
