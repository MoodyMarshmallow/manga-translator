"""OCR helpers for the manga translator backend."""

from __future__ import annotations

import io
import logging
from typing import Any, Iterable, List, Tuple, cast

from PIL import Image

from .types import OCRWord

try:  # pragma: no cover - optional dependency
    from google.cloud import vision as _vision  # type: ignore[import]
except ImportError:  # pragma: no cover - optional dependency
    _vision = None

vision = cast(Any | None, _vision)

logger = logging.getLogger(__name__)

WordPoly = List[Tuple[int, int]]


def _fallback_words(image_bytes: bytes) -> List[OCRWord]:
    """Return a single placeholder word covering the entire image."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        width, height = im.size
    poly: WordPoly = [(0, 0), (width, 0), (width, height), (0, height)]
    return [{"text": "[ocr unavailable]", "poly": poly}]


def document_ocr(image_bytes: bytes, language_hint: str | None = "ja") -> Tuple[List[OCRWord], Any | None]:
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
    image_context: Any | None = None
    if language_hint:
        image_context = vision.ImageContext(language_hints=[language_hint])

    response: Any = client.document_text_detection(image=image, image_context=image_context)
    words: List[OCRWord] = []
    annotations: Any = getattr(response, "full_text_annotation", None)
    pages: Iterable[Any] = getattr(annotations, "pages", [])
    for page in pages:
        blocks: Iterable[Any] = getattr(page, "blocks", [])
        for block in blocks:
            paragraphs: Iterable[Any] = getattr(block, "paragraphs", [])
            for paragraph in paragraphs:
                vision_words: Iterable[Any] = getattr(paragraph, "words", [])
                for word in vision_words:
                    symbols: Iterable[Any] = getattr(word, "symbols", [])
                    text = "".join(str(getattr(symbol, "text", "")) for symbol in symbols)
                    bounding_box: Any = getattr(word, "bounding_box", None)
                    vertices: Iterable[Any] = getattr(bounding_box, "vertices", [])
                    verts: WordPoly = [
                        (int(getattr(vertex, "x", 0)), int(getattr(vertex, "y", 0)))
                        for vertex in vertices
                    ]
                    words.append({"text": text, "poly": verts})
    if not words:
        logger.info("Vision OCR returned no words; using fallback bubble")
        return _fallback_words(image_bytes), response
    return words, response


__all__ = ["document_ocr"]
