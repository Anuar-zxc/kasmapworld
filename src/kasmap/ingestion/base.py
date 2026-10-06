"""Source abstraction.  Every dataset plugs in through one adapter (World Coverage Engine §3).

    download → normalize → validate → transform → load

Checkpoints: `download` (raw files on disk) and `process` (normalize+validate+transform →
processed file + quality report).  A restarted job resumes after the last checkpoint.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kasmap.config import Settings
from kasmap.ingestion import storage
from kasmap.processing.geo import BBox
from kasmap.processing.pipeline import enrich_all
from kasmap.processing.records import PlaceRecord
from kasmap.processing.validation import QualityReport, Validator, validate_records

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceMeta:
    id: str
    name: str
    license: str
    attribution: str
    commercial_ok: bool
    storage_ok: bool
    share_alike: bool
    update_freq: str
    priority: int
    license_url: str
    notes: str = ""


@dataclass
class CountryRef:
    iso2: str
    name: str
    bbox: BBox | None = None
    geom_wkb: bytes | None = None
    geofabrik_path: str | None = None
    db_id: int | None = None

    def contains_predicate(self):
        """Exact point-in-country test (shapely), or None if geometry is unknown."""
        if not self.geom_wkb:
            return None
        from shapely import wkb  # noqa: PLC0415
        from shapely.geometry import Point  # noqa: PLC0415
        from shapely.prepared import prep  # noqa: PLC0415

        prepared = prep(wkb.loads(self.geom_wkb))
        return lambda lon, lat: prepared.covers(Point(lon, lat))


@dataclass
class JobContext:
    country: CountryRef
    version: str
    settings: Settings
    lake: storage.DataLake
    previous_count: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def iso2(self) -> str:
        return self.country.iso2


@dataclass
class ProcessResult:
    path: Path
    report: QualityReport
    count: int


class GeoDataSource(ABC):
    """Base class for all sources.  Subclasses implement `download` and `normalize`."""

    meta: SourceMeta
    produces_places: bool = True

    @property
    def source_id(self) -> str:
        return self.meta.id

    # ── version ──
    @abstractmethod
    def resolve_version(self, settings: Settings, country: CountryRef) -> str:
        """The release/snapshot id this run will ingest (pinned, never 'latest')."""

    # ── steps ──
    @abstractmethod
    def download(self, ctx: JobContext) -> dict[str, Any]:
        """Fetch raw data for ctx.country into ctx.lake.raw(...). Returns manifest info."""

    @abstractmethod
    def normalize(self, ctx: JobContext) -> Iterator[PlaceRecord]:
        """Read raw files and yield PlaceRecords in KasMap's schema."""

    def validate(
        self, ctx: JobContext, records: Iterable[PlaceRecord]
    ) -> tuple[list[PlaceRecord], QualityReport]:
        validator = Validator(
            country_bbox=ctx.country.bbox, in_country=ctx.country.contains_predicate()
        )
        return validate_records(records, validator, previous_count=ctx.previous_count)

    def transform(self, ctx: JobContext, records: Iterable[PlaceRecord]) -> Iterator[PlaceRecord]:
        return enrich_all(records, with_h3=True)

    def process(self, ctx: JobContext) -> ProcessResult:
        """normalize → transform → validate → write processed file (checkpoint 'process')."""
        out_dir = ctx.lake.processed(self.source_id, ctx.version, ctx.iso2)
        transformed = self.transform(ctx, self.normalize(ctx))
        kept, report = self.validate(ctx, transformed)
        path = storage.records_path(out_dir, "places")
        count = storage.write_records(path, kept)
        storage.mark_done(out_dir, "process", {"path": str(path), "report": report.to_dict()})
        log.info("processed %s %s %s: %s", self.source_id, ctx.version, ctx.iso2, report.to_dict())
        return ProcessResult(path=path, report=report, count=count)
