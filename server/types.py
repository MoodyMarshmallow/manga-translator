"""Typed structures shared by the backend modules."""

from __future__ import annotations

from typing import List, Literal, Sequence, Tuple, TypedDict

Point = Tuple[float, float]
BBox = Tuple[float, float, float, float]
Polygon = Sequence[Point]


class OCRWord(TypedDict):
    """Single OCR word with associated polygon."""

    text: str
    poly: Polygon


class WordGroupRequired(TypedDict):
    """Fields that every grouped word entry must expose."""

    id: str
    bbox: BBox
    word_idx: List[int]
    orientation: Literal["horizontal", "vertical"]


class WordGroup(WordGroupRequired, total=False):
    """Grouped words with optional translation metadata."""

    jp_text: str
    en_text: str


__all__ = ["Point", "BBox", "Polygon", "OCRWord", "WordGroup"]
