"""Group OCR words into speech-bubble style regions."""

from __future__ import annotations

from collections import deque
from statistics import median
from typing import Iterable, List, Sequence, Tuple

try:
    from scipy.spatial import KDTree  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    KDTree = None  # type: ignore

Point = Tuple[float, float]
Box = Tuple[float, float, float, float]


def _polygon_to_box(poly: Sequence[Sequence[float]]) -> Box:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def _box_center(box: Box) -> Point:
    x0, y0, x1, y1 = box
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _box_height(box: Box) -> float:
    return max(1.0, box[3] - box[1])


def _variance(values: Iterable[float]) -> float:
    values = list(values)
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _neighbors_with_kdtree(points: List[Point], radius: float) -> List[List[int]]:
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
    seen = set()
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


def group_words(words: Sequence[dict]) -> List[dict]:
    """Group OCR word entries into clusters."""
    if not words:
        return []
    boxes: List[Box] = [_polygon_to_box(word["poly"]) for word in words]
    centers: List[Point] = [_box_center(box) for box in boxes]
    heights = [_box_height(box) for box in boxes]
    height_med = median(heights) if heights else 20.0
    radius = float(height_med * 1.4)
    if KDTree is not None and len(centers) >= 2:
        adjacency = _neighbors_with_kdtree(centers, radius)
    else:
        adjacency = _neighbors_naive(centers, radius)
    components = _connected_components(adjacency)

    groups: List[dict] = []
    for idx, comp in enumerate(components):
        x0 = min(boxes[i][0] for i in comp)
        y0 = min(boxes[i][1] for i in comp)
        x1 = max(boxes[i][2] for i in comp)
        y1 = max(boxes[i][3] for i in comp)
        xs = [centers[i][0] for i in comp]
        ys = [centers[i][1] for i in comp]
        var_x = _variance(xs)
        var_y = _variance(ys)
        orientation = "vertical" if var_y > var_x * 1.3 else "horizontal"
        groups.append(
            {
                "id": f"g_{idx}",
                "bbox": (x0, y0, x1, y1),
                "word_idx": comp,
                "orientation": orientation,
            }
        )
    return groups


__all__ = ["group_words"]
