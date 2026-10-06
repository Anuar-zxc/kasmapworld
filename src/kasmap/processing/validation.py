"""Record- and dataset-level data quality checks.

Errors drop a record; warnings keep it but lower its confidence.  Every run produces a
`QualityReport` that is stored in `meta.dataset_version.quality_report`, and a dataset
whose score is below the threshold never becomes `active`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum

from kasmap.processing.categories import CATEGORY_IDS
from kasmap.processing.geo import BBox
from kasmap.processing.records import PlaceRecord


class Severity(str, Enum):
    ERROR = "error"      # record dropped
    WARNING = "warning"  # record kept, confidence lowered, counts against the quality score
    INFO = "info"        # recorded only (e.g. a category we have not mapped yet)


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity


MISSING_COORDS = Issue("missing_coordinates", Severity.ERROR)
INVALID_COORDS = Issue("impossible_coordinates", Severity.ERROR)
NULL_ISLAND = Issue("null_island", Severity.ERROR)
OUTSIDE_COUNTRY_BBOX = Issue("outside_country_bbox", Severity.ERROR)
OUTSIDE_COUNTRY = Issue("outside_country_boundary", Severity.WARNING)
IN_WATER = Issue("in_water", Severity.WARNING)
EMPTY_NAME = Issue("empty_name", Severity.ERROR)
UNKNOWN_CATEGORY = Issue("unknown_category", Severity.INFO)
INVALID_CATEGORY_ID = Issue("invalid_category_id", Severity.ERROR)
LOW_SOURCE_CONFIDENCE = Issue("low_source_confidence", Severity.WARNING)
CLOSED = Issue("operating_status_closed", Severity.WARNING)

# Categories whose objects are legitimately unnamed (a bus stop, an ATM).
UNNAMED_OK_PREFIXES = ("transport.", "finance.atm")

PointPredicate = Callable[[float, float], bool]  # (lon, lat) -> bool


@dataclass
class Validator:
    country_bbox: BBox | None = None
    in_country: PointPredicate | None = None   # precise polygon test (shapely), optional
    in_water: PointPredicate | None = None
    min_source_confidence: float = 0.3

    def check(self, r: PlaceRecord) -> list[Issue]:
        issues: list[Issue] = []
        if r.lat is None or r.lon is None:
            return [MISSING_COORDS]
        if not (-90.0 <= r.lat <= 90.0 and -180.0 <= r.lon <= 180.0):
            return [INVALID_COORDS]
        if abs(r.lat) < 1e-6 and abs(r.lon) < 1e-6:
            return [NULL_ISLAND]
        if self.country_bbox is not None and not self.country_bbox.buffered(0.05).contains(
            r.lon, r.lat
        ):
            issues.append(OUTSIDE_COUNTRY_BBOX)
        elif self.in_country is not None and not self.in_country(r.lon, r.lat):
            issues.append(OUTSIDE_COUNTRY)
        if self.in_water is not None and self.in_water(r.lon, r.lat):
            issues.append(IN_WATER)
        if not (r.name or "").strip():
            if not (r.category_id or "").startswith(UNNAMED_OK_PREFIXES):
                issues.append(EMPTY_NAME)
        if r.category_id is None:
            issues.append(UNKNOWN_CATEGORY)
        elif r.category_id not in CATEGORY_IDS:
            issues.append(INVALID_CATEGORY_ID)
        if r.source_confidence is not None and r.source_confidence < self.min_source_confidence:
            issues.append(LOW_SOURCE_CONFIDENCE)
        if r.operating_status == "closed":
            issues.append(CLOSED)
        return issues


@dataclass
class QualityReport:
    total: int = 0
    valid: int = 0
    dropped: int = 0
    warnings: int = 0
    issue_counts: Counter[str] = field(default_factory=Counter)
    previous_count: int | None = None
    max_delta: float = 0.15

    @property
    def score(self) -> float:
        """Share of records without errors; records with warnings count half."""
        if self.total == 0:
            return 0.0
        clean = self.valid - self.warnings
        return round((clean + 0.5 * self.warnings) / self.total, 3)

    @property
    def delta(self) -> float | None:
        if not self.previous_count:
            return None
        return (self.valid - self.previous_count) / self.previous_count

    @property
    def delta_exceeded(self) -> bool:
        d = self.delta
        return d is not None and abs(d) > self.max_delta

    def passes(self, min_score: float = 0.8) -> bool:
        return self.total > 0 and self.score >= min_score and not self.delta_exceeded

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "valid": self.valid,
            "dropped": self.dropped,
            "with_warnings": self.warnings,
            "score": self.score,
            "issues": dict(self.issue_counts),
            "previous_count": self.previous_count,
            "delta": self.delta,
            "delta_exceeded": self.delta_exceeded,
        }


def validate_records(
    records: Iterable[PlaceRecord],
    validator: Validator,
    previous_count: int | None = None,
) -> tuple[list[PlaceRecord], QualityReport]:
    """Run checks; return the kept records and the report.

    Warnings lower `source_confidence` so downstream features weight the record less.
    """
    report = QualityReport(previous_count=previous_count)
    kept: list[PlaceRecord] = []
    for r in records:
        report.total += 1
        issues = validator.check(r)
        for issue in issues:
            report.issue_counts[issue.code] += 1
        if any(i.severity is Severity.ERROR for i in issues):
            report.dropped += 1
            continue
        if any(i.severity is Severity.WARNING for i in issues):
            report.warnings += 1
            base = r.source_confidence if r.source_confidence is not None else 0.5
            r.source_confidence = round(base * 0.8, 3)
        report.valid += 1
        kept.append(r)
    return kept, report
