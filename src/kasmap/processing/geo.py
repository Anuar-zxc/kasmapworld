"""Small geometry helpers that need no compiled dependencies."""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class BBox:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    def contains(self, lon: float, lat: float) -> bool:
        return self.xmin <= lon <= self.xmax and self.ymin <= lat <= self.ymax

    def buffered(self, degrees: float) -> BBox:
        return BBox(self.xmin - degrees, self.ymin - degrees, self.xmax + degrees, self.ymax + degrees)

    def split(self, step_deg: float) -> Iterator[BBox]:
        """Tile the box (used to split large countries into sub-jobs / query chunks)."""
        y = self.ymin
        while y < self.ymax:
            x = self.xmin
            y2 = min(y + step_deg, self.ymax)
            while x < self.xmax:
                x2 = min(x + step_deg, self.xmax)
                yield BBox(x, y, x2, y2)
                x = x2
            y = y2


class MetricGrid:
    """Square grid of roughly `cell_m` metres, used for dedup blocking.

    Independent from H3 on purpose: blocking needs 'everything within ~N metres',
    which a square grid + 8 neighbours answers exactly and cheaply.
    """

    def __init__(self, cell_m: float = 150.0, ref_lat: float = 45.0) -> None:
        self.dlat = cell_m / 111_320.0
        self.dlon = cell_m / (111_320.0 * max(0.1, math.cos(math.radians(ref_lat))))

    def key(self, lat: float, lon: float) -> tuple[int, int]:
        return (math.floor(lat / self.dlat), math.floor(lon / self.dlon))

    @staticmethod
    def neighbours(key: tuple[int, int]) -> Iterator[tuple[int, int]]:
        i, j = key
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                yield (i + di, j + dj)
