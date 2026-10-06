"""Registry of source adapters.  Add a source = add an adapter + one line here."""

from __future__ import annotations

from kasmap.ingestion.base import GeoDataSource, SourceMeta
from kasmap.ingestion.sources.osm import OSM_META, OSMSource
from kasmap.ingestion.sources.overture import (
    DIVISIONS_META,
    PLACES_META,
    OvertureDivisionsSource,
    OverturePlacesSource,
)

SOURCE_METAS: dict[str, SourceMeta] = {m.id: m for m in (PLACES_META, DIVISIONS_META, OSM_META)}

_FACTORIES = {
    PLACES_META.id: OverturePlacesSource,
    DIVISIONS_META.id: OvertureDivisionsSource,
    OSM_META.id: OSMSource,
}

# What `kasmap ingest country XX` runs by default.
DEFAULT_SOURCES: tuple[str, ...] = (DIVISIONS_META.id, PLACES_META.id, OSM_META.id)

# Aliases accepted on the command line.
ALIASES = {"overture": PLACES_META.id, "divisions": DIVISIONS_META.id, "osm": OSM_META.id}


def get_source(source_id: str) -> GeoDataSource:
    source_id = ALIASES.get(source_id, source_id)
    try:
        return _FACTORIES[source_id]()
    except KeyError as exc:
        raise ValueError(f"Unknown source '{source_id}'. Known: {sorted(_FACTORIES)}") from exc


def source_priority() -> dict[str, int]:
    return {m.id: m.priority for m in SOURCE_METAS.values()}
