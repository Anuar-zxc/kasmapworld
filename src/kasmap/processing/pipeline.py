"""Record enrichment: the 'normalize' step shared by all sources."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from kasmap.processing import normalization as norm
from kasmap.processing.records import PlaceRecord


def enrich(record: PlaceRecord, with_h3: bool = True) -> PlaceRecord:
    record.name = (record.name or "").strip()
    record.name_norm = norm.name_norm(record.name)
    record.core_name = norm.core_name(record.name)
    record.phone_e164 = norm.normalize_phone(record.phone, record.country_iso2)
    record.domain = norm.normalize_domain(record.website)
    if (
        with_h3
        and record.lat is not None
        and record.lon is not None
        and -90.0 <= record.lat <= 90.0
        and -180.0 <= record.lon <= 180.0
    ):
        from kasmap.processing import h3index  # noqa: PLC0415

        record.h3_r9 = h3index.cell(record.lat, record.lon)
    return record


def enrich_all(records: Iterable[PlaceRecord], with_h3: bool = True) -> Iterator[PlaceRecord]:
    for r in records:
        yield enrich(r, with_h3=with_h3)
