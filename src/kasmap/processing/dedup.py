"""Entity resolution: many source records → one canonical place (Blueprint §8).

Pipeline: blocking (metric grid) → pairwise features → rule decision → union-find
clustering with a one-record-per-source guard → canonical record.

v1 is rule-based and explainable: every merge stores the rule that fired
(`match_method`) and a score.  Pairs in the grey zone are returned for manual review
and become training data for the v2 learned matcher.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from kasmap.processing.categories import compatible
from kasmap.processing.geo import MetricGrid, haversine_m
from kasmap.processing.records import PlaceRecord

# ─────────────────────────── string similarity ───────────────────────────


def jaro_winkler(a: str, b: str, prefix_scale: float = 0.1) -> float:
    if a == b:
        return 1.0 if a else 0.0
    if not a or not b:
        return 0.0
    la, lb = len(a), len(b)
    window = max(0, max(la, lb) // 2 - 1)
    a_flags = [False] * la
    b_flags = [False] * lb
    matches = 0
    for i, ch in enumerate(a):
        lo, hi = max(0, i - window), min(lb, i + window + 1)
        for j in range(lo, hi):
            if not b_flags[j] and b[j] == ch:
                a_flags[i] = b_flags[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    transpositions = 0
    j = 0
    for i in range(la):
        if a_flags[i]:
            while not b_flags[j]:
                j += 1
            if a[i] != b[j]:
                transpositions += 1
            j += 1
    m = float(matches)
    jaro = (m / la + m / lb + (m - transpositions / 2) / m) / 3
    prefix = 0
    for ca, cb in zip(a[:4], b[:4], strict=False):
        if ca != cb:
            break
        prefix += 1
    return jaro + prefix * prefix_scale * (1 - jaro)


def token_set_similarity(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    if not inter:
        return 0.0
    return len(inter) / min(len(ta), len(tb))


def name_similarity(a: PlaceRecord, b: PlaceRecord) -> float:
    ca, cb = a.core_name, b.core_name
    if not ca or not cb:
        return 0.0
    return max(jaro_winkler(ca, cb), token_set_similarity(ca, cb))


# ──────────────────────────── decision rules ────────────────────────────


@dataclass(frozen=True)
class MatchConfig:
    block_cell_m: float = 150.0
    phone_max_m: float = 150.0
    domain_max_m: float = 100.0
    name_close_max_m: float = 75.0
    name_close_sim: float = 0.92
    name_near_max_m: float = 40.0
    name_near_sim: float = 0.85
    same_source_max_m: float = 15.0
    same_source_sim: float = 0.97
    review_sim: float = 0.75
    review_max_m: float = 60.0


@dataclass(frozen=True)
class PairDecision:
    match: bool
    score: float
    method: str
    distance_m: float
    name_sim: float


def decide(a: PlaceRecord, b: PlaceRecord, cfg: MatchConfig) -> PairDecision | None:
    """Return a decision for a candidate pair, or None if the pair is clearly distinct."""
    if not compatible(a.category_id, b.category_id):
        return None
    d = haversine_m(a.lat, a.lon, b.lat, b.lon)
    sim = name_similarity(a, b)
    if a.source_id == b.source_id:
        # Sources occasionally duplicate themselves; only merge near-identical records.
        if d <= cfg.same_source_max_m and sim >= cfg.same_source_sim:
            return PairDecision(True, 0.85, "rule:same_source_duplicate", d, sim)
        return None
    if a.phone_e164 and a.phone_e164 == b.phone_e164 and d <= cfg.phone_max_m:
        return PairDecision(True, 0.95, "rule:phone", d, sim)
    if a.domain and a.domain == b.domain and d <= cfg.domain_max_m and sim >= 0.6:
        return PairDecision(True, 0.9, "rule:domain", d, sim)
    if sim >= cfg.name_close_sim and d <= cfg.name_close_max_m:
        return PairDecision(True, 0.9, "rule:name_close", d, sim)
    if sim >= cfg.name_near_sim and d <= cfg.name_near_max_m:
        return PairDecision(True, 0.8, "rule:name_near", d, sim)
    if sim >= cfg.review_sim and d <= cfg.review_max_m:
        return PairDecision(False, round(sim, 3), "review:grey_zone", d, sim)
    return None


# ───────────────────────────── clustering ────────────────────────────────


class _UnionFind:
    def __init__(self, records: Sequence[PlaceRecord]) -> None:
        self.parent = list(range(len(records)))
        self.sources: list[set[str]] = [{r.source_id} for r in records]

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int, same_source_pair: bool) -> bool:
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return True
        # Guard: an entity holds at most one record per source, except for explicit
        # same-source duplicates.  Stops bridges like OSM-A ~ Overture ~ OSM-B.
        if (self.sources[ri] & self.sources[rj]) and not same_source_pair:
            return False
        self.parent[rj] = ri
        self.sources[ri] |= self.sources[rj]
        return True


@dataclass
class Member:
    record: PlaceRecord
    score: float
    method: str


@dataclass
class Entity:
    members: list[Member]
    canonical: PlaceRecord
    confidence: float
    source_count: int


@dataclass
class ResolutionResult:
    entities: list[Entity]
    review_pairs: list[tuple[PlaceRecord, PlaceRecord, PairDecision]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _depth(category_id: str | None) -> int:
    return 0 if not category_id else category_id.count(".") + 1


def _completeness(r: PlaceRecord) -> int:
    return sum(bool(x) for x in (r.name, r.phone_e164, r.website, r.address, r.category_id,
                                 r.opening_hours, r.brand))


def build_canonical(
    members: list[PlaceRecord], source_priority: Mapping[str, int]
) -> tuple[PlaceRecord, float]:
    """Merge member records field by field; return the canonical record and confidence."""

    def prio(r: PlaceRecord) -> int:
        return source_priority.get(r.source_id, 100)

    by_priority = sorted(members, key=lambda r: (prio(r), -_completeness(r)))
    best = by_priority[0]
    canonical = PlaceRecord(**{**best.to_dict()})
    canonical.raw = {}

    def first(attr: str):
        for r in by_priority:
            value = getattr(r, attr)
            if value:
                return value
        return None

    canonical.name = best.name or first("name") or ""
    for attr in ("phone", "phone_e164", "website", "domain", "address", "opening_hours", "brand"):
        setattr(canonical, attr, first(attr))
    deepest = max(members, key=lambda r: (_depth(r.category_id), -prio(r)))
    canonical.category_id = deepest.category_id
    canonical.source_category = first("source_category")
    statuses = {r.operating_status for r in members}
    canonical.operating_status = (
        "closed" if statuses == {"closed"} else best.operating_status
    )
    miss = 1.0
    for r in members:
        c = r.source_confidence if r.source_confidence is not None else 0.5
        miss *= 1 - min(max(c, 0.0), 0.99)
    confidence = round(min(0.99, 1 - miss), 3)
    return canonical, confidence


def resolve(
    records: Sequence[PlaceRecord],
    source_priority: Mapping[str, int] | None = None,
    cfg: MatchConfig | None = None,
) -> ResolutionResult:
    cfg = cfg or MatchConfig()
    source_priority = source_priority or {}
    if not records:
        return ResolutionResult(entities=[], stats={"records": 0, "entities": 0})

    ref_lat = sum(r.lat for r in records) / len(records)
    grid = MetricGrid(cfg.block_cell_m, ref_lat)
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, r in enumerate(records):
        buckets[grid.key(r.lat, r.lon)].append(idx)

    decisions: list[tuple[int, int, PairDecision]] = []
    review: list[tuple[PlaceRecord, PlaceRecord, PairDecision]] = []
    compared = 0
    for key, idxs in buckets.items():
        for nkey in grid.neighbours(key):
            if nkey < key:  # each bucket pair visited once
                continue
            others = buckets.get(nkey)
            if not others:
                continue
            for i in idxs:
                for j in others:
                    if nkey == key and j <= i:
                        continue
                    compared += 1
                    dec = decide(records[i], records[j], cfg)
                    if dec is None:
                        continue
                    if dec.match:
                        decisions.append((i, j, dec))
                    else:
                        review.append((records[i], records[j], dec))

    # Strongest evidence first, so guards keep the best pairing.
    decisions.sort(key=lambda t: (-t[2].score, t[2].distance_m))
    uf = _UnionFind(records)
    member_info: dict[int, tuple[float, str]] = {}
    blocked = 0
    for i, j, dec in decisions:
        same = records[i].source_id == records[j].source_id
        if uf.union(i, j, same):
            for k in (i, j):
                prev = member_info.get(k)
                if prev is None or dec.score > prev[0]:
                    member_info[k] = (dec.score, dec.method)
        else:
            blocked += 1

    clusters: dict[int, list[int]] = defaultdict(list)
    for idx in range(len(records)):
        clusters[uf.find(idx)].append(idx)

    entities: list[Entity] = []
    for idxs in clusters.values():
        members = [records[k] for k in idxs]
        canonical, confidence = build_canonical(members, source_priority)
        entity_members = [
            Member(records[k], *member_info.get(k, (1.0, "seed"))) for k in idxs
        ]
        entities.append(
            Entity(entity_members, canonical, confidence, len({m.source_id for m in members}))
        )

    stats = {
        "records": len(records),
        "entities": len(entities),
        "pairs_compared": compared,
        "merges": len(decisions) - blocked,
        "merges_blocked_by_source_guard": blocked,
        "review_pairs": len(review),
    }
    return ResolutionResult(entities=entities, review_pairs=review, stats=stats)
