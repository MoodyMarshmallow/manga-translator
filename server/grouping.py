"""Group OCR words into speech-bubble style regions."""

from __future__ import annotations

from collections import deque
from statistics import median
from typing import Any, Iterable, List, Literal, Sequence, TYPE_CHECKING, cast

import math

from .types import BBox, OCRWord, Point, WordGroup

if TYPE_CHECKING:
    from typing import Protocol

    class _KDTreeProtocol(Protocol):
        def __init__(self, data: Sequence[Point]) -> None: ...

        def query_ball_point(self, x: Point, r: float) -> List[int]: ...

    KDTreeType = type[_KDTreeProtocol]
else:  # pragma: no cover - hint for static analyzers
    KDTreeType = Any

try:
    from scipy.spatial import KDTree as _SciPyKDTree  # type: ignore[import]
except ImportError:  # pragma: no cover - optional dependency
    _SciPyKDTree = None

KDTree: KDTreeType | None = cast(KDTreeType | None, _SciPyKDTree)


def _polygon_to_box(poly: Sequence[Sequence[float]]) -> BBox:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def _box_center(box: BBox) -> Point:
    x0, y0, x1, y1 = box
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _box_height(box: BBox) -> float:
    return max(1.0, box[3] - box[1])


def _variance(values: Iterable[float]) -> float:
    values = list(values)
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _axis_gap(a0: float, a1: float, b0: float, b1: float) -> float:
    if a1 < b0:
        return b0 - a1
    if b1 < a0:
        return a0 - b1
    return 0.0


def _boxes_close(box_a: BBox, box_b: BBox, threshold: float) -> bool:
    x0_a, y0_a, x1_a, y1_a = box_a
    x0_b, y0_b, x1_b, y1_b = box_b
    gap_x = _axis_gap(x0_a, x1_a, x0_b, x1_b)
    gap_y = _axis_gap(y0_a, y1_a, y0_b, y1_b)
    if gap_x == 0.0 and gap_y == 0.0:
        return True
    return math.hypot(gap_x, gap_y) <= threshold


def _merge_adjacent_groups(groups: List[WordGroup], radius: float) -> List[WordGroup]:
    if len(groups) <= 1:
        return groups

    proximity = max(12.0, radius * 0.6)
    merged: List[WordGroup] = []
    for group in sorted(groups, key=lambda g: (g["bbox"][1], g["bbox"][0])):
        target = None
        for existing in merged:
            if existing["orientation"] != group["orientation"]:
                continue
            if _boxes_close(existing["bbox"], group["bbox"], proximity):
                target = existing
                break
        if target is None:
            merged.append({
                "id": group["id"],
                "bbox": tuple(group["bbox"]), # type: ignore
                "word_idx": list(group["word_idx"]),
                "orientation": group["orientation"],
            })
            continue
        target["word_idx"].extend(group["word_idx"])
        x0 = min(target["bbox"][0], group["bbox"][0])
        y0 = min(target["bbox"][1], group["bbox"][1])
        x1 = max(target["bbox"][2], group["bbox"][2])
        y1 = max(target["bbox"][3], group["bbox"][3])
        target["bbox"] = (x0, y0, x1, y1)

    for idx, group in enumerate(merged):
        group["word_idx"] = sorted(set(group["word_idx"]))
        group["id"] = f"g_{idx}"

    return merged


def _neighbors_with_kdtree(points: List[Point], radius: float) -> List[List[int]]:
    if KDTree is None:
        return _neighbors_naive(points, radius)
    tree = KDTree(points)
    adjacency: List[List[int]] = [[] for _ in points]
    for idx, point in enumerate(points):
        neighbors = tree.query_ball_point(point, r=radius)
        adjacency[idx] = [n for n in neighbors if n != idx]
    return adjacency


def _neighbors_naive(points: List[Point], radius: float) -> List[List[int]]:
    radius_sq = radius * radius
    adjacency: List[List[int]] = [[] for _ in points]
    for i, (x1, y1) in enumerate(points):
        for j in range(i + 1, len(points)):
            x2, y2 = points[j]
            if (x1 - x2) ** 2 + (y1 - y2) ** 2 <= radius_sq:
                adjacency[i].append(j)
                adjacency[j].append(i)
    return adjacency


def _connected_components(adjacency: List[List[int]]) -> List[List[int]]:
    seen: set[int] = set()
    components: List[List[int]] = []
    for start in range(len(adjacency)):
        if start in seen:
            continue
        queue: deque[int] = deque([start])
        comp: List[int] = []
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            comp.append(node)
            queue.extend(adjacency[node])
        components.append(comp)
    return components


def group_words(words: Sequence[OCRWord]) -> List[WordGroup]:
    """Group OCR word entries into clusters."""
    if not words:
        return []
    boxes: List[BBox] = [_polygon_to_box(word["poly"]) for word in words]
    centers: List[Point] = [_box_center(box) for box in boxes]
    heights = [_box_height(box) for box in boxes]
    height_med = median(heights) if heights else 20.0
    radius = float(height_med * 1.4)
    if KDTree is not None and len(centers) >= 2:
        adjacency = _neighbors_with_kdtree(centers, radius)
    else:
        adjacency = _neighbors_naive(centers, radius)
    components = _connected_components(adjacency)

    groups: List[WordGroup] = []
    for idx, comp in enumerate(components):
        x0 = min(boxes[i][0] for i in comp)
        y0 = min(boxes[i][1] for i in comp)
        x1 = max(boxes[i][2] for i in comp)
        y1 = max(boxes[i][3] for i in comp)
        xs = [centers[i][0] for i in comp]
        ys = [centers[i][1] for i in comp]
        var_x = _variance(xs)
        var_y = _variance(ys)
        orientation: Literal["vertical", "horizontal"] = "vertical" if var_y > var_x * 1.3 else "horizontal"
        groups.append(
            {
                "id": f"g_{idx}",
                "bbox": (x0, y0, x1, y1),
                "word_idx": list(comp),
                "orientation": orientation,
            }
        )
    return _merge_adjacent_groups(groups, radius)


__all__ = ["group_words"]
