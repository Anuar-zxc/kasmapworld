"""Opportunity Score v0 — explainable, percentile-based (Blueprint §9, §11).

Unit: H3 res-9 cell (~0.1 km²).  For a business category the score combines
  activity    — places of any kind around the cell (proxy for footfall / commercial street)
  anchors     — universities, schools, malls, business centres, metro/rail, hospitals nearby
  competition — same-category places nearby (an inverted U: none = unproven, many = crowded)
  gap         — how few competitors there are relative to the activity around
Every component is a percentile within the city, and so is the final score:
score 87 = "better than 87 % of the city's cells for this category by these factors".
It is not a probability of success; calibration on real outcomes comes later.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from statistics import median

RingFn = Callable[[str, int], Iterable[str]]  # (cell, k) -> cells at exactly distance k

RING_WEIGHTS = (1.0, 0.6, 0.3)          # k = 0, 1, 2
WEIGHTS = {"activity": 0.35, "anchors": 0.20, "competition": 0.20, "gap": 0.25}
ANCHOR_PREFIXES = (
    "education.university", "education.school", "retail.mall", "office.business_center",
    "transport.metro", "transport.rail", "health.hospital",
)


def is_anchor(category: str | None) -> bool:
    return bool(category) and category.startswith(ANCHOR_PREFIXES)


def competitor_weight(target: str, category: str | None) -> float:
    """Same category (or a sub-category) = 1; the generic parent (cafe for a coffee shop) = 0.5."""
    if not category:
        return 0.0
    if category == target or category.startswith(target + "."):
        return 1.0
    parent = target.rsplit(".", 1)[0] if "." in target else None
    if parent and category == parent and parent.count(".") >= 1:
        return 0.5
    return 0.0


def competition_balance(competitors: float) -> float:
    if competitors < 0.5:
        return 0.55          # no proof of demand for this category yet
    if competitors <= 3:
        return 1.0           # proven demand, room left
    if competitors <= 6:
        return 0.65
    return max(0.15, 0.65 - 0.07 * (competitors - 6))


class Percentile:
    def __init__(self, values: Iterable[float]) -> None:
        self.sorted = sorted(values)

    def __call__(self, v: float) -> float:
        n = len(self.sorted)
        if n == 0:
            return 0.0
        lo, hi = bisect_left(self.sorted, v), bisect_right(self.sorted, v)
        return (lo + hi) / 2 / n


@dataclass
class CellScore:
    h3: str
    score: int
    activity: float
    anchors: float
    competitors: float
    components: dict[str, float]
    why: list[dict[str, object]] = field(default_factory=list)


@dataclass
class CityScores:
    category: str
    cells: list[CellScore]
    medians: dict[str, float]

    def top(self, n: int = 5, min_separation: int = 2,
            ring: RingFn | None = None) -> list[CellScore]:
        """Best cells, skipping neighbours of already chosen ones so results spread out."""
        chosen: list[CellScore] = []
        blocked: set[str] = set()
        for c in sorted(self.cells, key=lambda c: (-c.score, -c.activity)):
            if c.h3 in blocked:
                continue
            chosen.append(c)
            if ring is not None:
                for k in range(min_separation + 1):
                    blocked.update(ring(c.h3, k))
            if len(chosen) == n:
                break
        return chosen


def _weighted(counts: Mapping[str, float], cell: str, ring: RingFn, max_k: int) -> float:
    total = 0.0
    for k in range(max_k + 1):
        w = RING_WEIGHTS[k]
        for n in ring(cell, k):
            total += w * counts.get(n, 0.0)
    return total


def score_city(places: Iterable[tuple[str, str | None]], target: str, ring: RingFn) -> CityScores:
    """places: (h3_r9, category_id) for every place in the city."""
    total: Counter[str] = Counter()
    anchors: Counter[str] = Counter()
    competitors: Counter[str] = Counter()
    for cell, category in places:
        total[cell] += 1
        if is_anchor(category):
            anchors[cell] += 1
        w = competitor_weight(target, category)
        if w:
            competitors[cell] += w

    candidates: set[str] = set()
    for cell in total:
        for k in (0, 1):
            candidates.update(ring(cell, k))

    raw = []
    for cell in candidates:
        act = _weighted(total, cell, ring, 2)
        anc = _weighted(anchors, cell, ring, 2)
        comp = _weighted(competitors, cell, ring, 1)
        raw.append((cell, act, anc, comp, comp / (act + 1.0)))

    if not raw:
        return CityScores(target, [], {})
    p_act = Percentile(r[1] for r in raw)
    p_anc = Percentile(r[2] for r in raw)
    p_sat = Percentile(r[4] for r in raw)
    blended = []
    for cell, act, anc, comp, sat in raw:
        comps = {
            "activity": p_act(act),
            "anchors": p_anc(anc),
            "competition": competition_balance(comp),
            "gap": 1.0 - p_sat(sat),
        }
        blended.append((cell, act, anc, comp, comps, sum(WEIGHTS[k] * v for k, v in comps.items())))
    p_final = Percentile(b[5] for b in blended)
    medians = {
        "activity": median(r[1] for r in raw),
        "anchors": median(r[2] for r in raw),
        "competitors": median(r[3] for r in raw),
    }
    cells = []
    for cell, act, anc, comp, comps, value in blended:
        cs = CellScore(
            h3=cell, score=round(100 * p_final(value)), activity=round(act, 1),
            anchors=round(anc, 1), competitors=round(comp, 1),
            components={k: round(v, 3) for k, v in comps.items()},
        )
        cs.why = explain(cs, medians)
        cells.append(cs)
    return CityScores(target, cells, medians)


def explain(c: CellScore, medians: Mapping[str, float]) -> list[dict[str, object]]:
    """Plain-language reasons with the numbers behind them (positive first)."""
    out: list[dict[str, object]] = []
    act_m = medians.get("activity", 0) or 0
    if c.components["activity"] >= 0.75:
        out.append({"sign": "+", "text": f"Оживлённое место: индекс активности {c.activity:g} "
                                         f"при медиане по городу {act_m:g}"})
    elif c.components["activity"] <= 0.3:
        out.append({"sign": "-", "text": f"Мало заведений вокруг: индекс активности {c.activity:g} "
                                         f"при медиане {act_m:g}"})
    if c.components["anchors"] >= 0.75 and c.anchors > 0:
        out.append({"sign": "+", "text": f"Рядом точки притяжения (вузы, школы, ТРЦ, бизнес-центры, "
                                         f"метро): индекс {c.anchors:g}"})
    if c.competitors < 0.5:
        out.append({"sign": "~", "text": "Конкурентов рядом нет — ниша свободна, но спрос на эту "
                                         "категорию здесь не подтверждён"})
    elif c.competitors <= 3:
        out.append({"sign": "+", "text": f"Умеренная конкуренция: {c.competitors:g} "
                                         f"(взвешенно) в радиусе ~350 м — спрос подтверждён"})
    else:
        out.append({"sign": "-", "text": f"Плотная конкуренция: {c.competitors:g} "
                                         f"(взвешенно) в радиусе ~350 м"})
    if c.components["gap"] >= 0.7 and c.competitors >= 0.5:
        out.append({"sign": "+", "text": "Конкурентов меньше, чем обычно при такой активности"})
    order = {"+": 0, "~": 1, "-": 2}
    return sorted(out, key=lambda r: order[str(r["sign"])])
