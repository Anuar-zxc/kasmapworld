"""OpenStreetMap adapter: Geofabrik country extracts (.osm.pbf) parsed with pyosmium.

Never calls the public OSM API.  Extracts are shared between countries that live in one
Geofabrik file (e.g. 'gcc-states'), and records are cut to the country polygon.
Version = the extract's Last-Modified date (YYYY-MM-DD).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from kasmap.config import Settings
from kasmap.ingestion.base import CountryRef, GeoDataSource, JobContext, SourceMeta
from kasmap.processing.categories import map_osm, osm_primary_tag
from kasmap.processing.records import PlaceRecord
from kasmap.processing.validation import UNNAMED_OK_PREFIXES

log = logging.getLogger(__name__)

OSM_META = SourceMeta(
    id="osm",
    name="OpenStreetMap (Geofabrik extracts)",
    license="ODbL-1.0",
    attribution="© OpenStreetMap contributors",
    commercial_ok=True,
    storage_ok=True,
    share_alike=True,
    update_freq="daily (we ingest monthly)",
    priority=10,  # local names/tags tend to be better curated; Overture wins on coverage
    license_url="https://www.openstreetmap.org/copyright",
)

# ISO2 → Geofabrik extract path.  Extend as countries are onboarded.
GEOFABRIK_PATHS: dict[str, str] = {
    "KZ": "asia/kazakhstan",
    "UZ": "asia/uzbekistan",
    "KG": "asia/kyrgyzstan",
    "TJ": "asia/tajikistan",
    "TM": "asia/turkmenistan",
    "RU": "russia",
    "DE": "europe/germany",
    "US": "north-america/us",
    "AE": "asia/gcc-states",
    "JP": "asia/japan",
    "BR": "south-america/brazil",
}

KEEP_TAG_KEYS = ("amenity", "shop", "leisure", "tourism", "office", "healthcare", "craft")


def extract_url(settings: Settings, geofabrik_path: str) -> str:
    return f"{settings.geofabrik_root}/{geofabrik_path}-latest.osm.pbf"


def version_from_last_modified(header: str | None) -> str:
    if not header:
        raise RuntimeError("Geofabrik response has no Last-Modified header")
    return parsedate_to_datetime(header).date().isoformat()


def is_poi(tags: dict[str, str]) -> bool:
    """Named objects with a mapped category or a POI-like primary tag.

    Unnamed objects are kept only where that is normal (bus stops, ATMs); an unnamed cafe
    cannot be matched or shown, so it is skipped here rather than counted as bad data.
    """
    category = map_osm(tags)
    named = bool(tags.get("name") or tags.get("name:ru") or tags.get("name:kk")
                 or tags.get("name:en"))
    if category and (named or category.startswith(UNNAMED_OK_PREFIXES)):
        return True
    return named and any(k in tags for k in KEEP_TAG_KEYS)


def tags_to_record(
    osm_id: str, tags: dict[str, str], lat: float, lon: float, iso2: str
) -> PlaceRecord:
    name = tags.get("name") or tags.get("name:ru") or tags.get("name:kk") or tags.get("name:en") or ""
    address = {
        k.removeprefix("addr:"): v for k, v in tags.items() if k.startswith("addr:")
    } or None
    status = "unknown"
    if tags.get("disused:shop") or tags.get("disused:amenity") or tags.get("abandoned") == "yes":
        status = "closed"
    return PlaceRecord(
        source_id=OSM_META.id,
        source_record_id=osm_id,
        name=name,
        lat=lat,
        lon=lon,
        country_iso2=iso2,
        category_id=map_osm(tags),
        source_category=osm_primary_tag(tags),
        brand=tags.get("brand"),
        address=address,
        phone=tags.get("phone") or tags.get("contact:phone"),
        website=tags.get("website") or tags.get("contact:website"),
        opening_hours=tags.get("opening_hours"),
        operating_status=status,
        source_confidence=None,
        raw={"tags": tags},
    )


class OSMSource(GeoDataSource):
    meta = OSM_META

    def __init__(self) -> None:
        self._version_cache: dict[str, str] = {}

    def resolve_version(self, settings: Settings, country: CountryRef) -> str:
        geofabrik_path = country.geofabrik_path or GEOFABRIK_PATHS.get(country.iso2)
        if not geofabrik_path:
            raise RuntimeError(f"No Geofabrik extract configured for {country.iso2}")
        if geofabrik_path not in self._version_cache:
            import httpx  # noqa: PLC0415

            resp = httpx.head(extract_url(settings, geofabrik_path), follow_redirects=True,
                              timeout=30)
            resp.raise_for_status()
            self._version_cache[geofabrik_path] = version_from_last_modified(
                resp.headers.get("last-modified")
            )
        return self._version_cache[geofabrik_path]

    def _geofabrik_path(self, ctx: JobContext) -> str:
        path = ctx.country.geofabrik_path or GEOFABRIK_PATHS.get(ctx.iso2)
        if not path:
            raise RuntimeError(f"No Geofabrik extract configured for {ctx.iso2}")
        return path

    def _pbf(self, ctx: JobContext) -> Path:
        # Shared per extract, not per country (one gcc-states file serves AE, SA, QA, ...).
        gf = self._geofabrik_path(ctx)
        d = ctx.lake.raw(self.source_id, ctx.version, "_extracts")
        return d / (gf.replace("/", "__") + ".osm.pbf")

    def download(self, ctx: JobContext) -> dict[str, Any]:
        import httpx  # noqa: PLC0415

        pbf = self._pbf(ctx)
        url = extract_url(ctx.settings, self._geofabrik_path(ctx))
        if not pbf.exists():
            tmp = pbf.with_name(pbf.name + ".tmp")
            md5 = hashlib.md5()  # noqa: S324 (Geofabrik publishes md5 checksums)
            with httpx.stream("GET", url, follow_redirects=True, timeout=None) as resp:
                resp.raise_for_status()
                served = version_from_last_modified(resp.headers.get("last-modified"))
                if served != ctx.version:
                    raise RuntimeError(
                        f"Extract changed while queued ({served} != {ctx.version}); re-enqueue"
                    )
                with tmp.open("wb") as f:
                    for chunk in resp.iter_bytes(1 << 20):
                        f.write(chunk)
                        md5.update(chunk)
            expected = httpx.get(url + ".md5", follow_redirects=True, timeout=30).text.split()[0]
            if expected != md5.hexdigest():
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"MD5 mismatch for {url}")
            tmp.replace(pbf)
        return {"file": str(pbf), "url": url, "bytes": pbf.stat().st_size}

    def normalize(self, ctx: JobContext) -> Iterator[PlaceRecord]:
        try:
            import osmium  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("OSM ingestion needs pyosmium: pip install 'kasmap[osm]'") from exc

        inside = ctx.country.contains_predicate()
        bbox = ctx.country.bbox
        records: list[PlaceRecord] = []
        iso2 = ctx.iso2

        def keep(lat: float, lon: float) -> bool:
            if bbox is not None and not bbox.contains(lon, lat):
                return False
            return inside is None or inside(lon, lat)

        class Handler(osmium.SimpleHandler):
            def node(self, n):  # noqa: N805
                if not n.tags or not n.location.valid():
                    return
                tags = {t.k: t.v for t in n.tags}
                if is_poi(tags) and keep(n.location.lat, n.location.lon):
                    records.append(tags_to_record(f"n{n.id}", tags, n.location.lat,
                                                  n.location.lon, iso2))

            def way(self, w):  # noqa: N805
                if not w.tags:
                    return
                tags = {t.k: t.v for t in w.tags}
                if not is_poi(tags):
                    return
                pts = [(nd.lat, nd.lon) for nd in w.nodes if nd.location.valid()]
                if not pts:
                    return
                if len(pts) > 1 and pts[0] == pts[-1]:
                    pts = pts[:-1]
                lat = sum(p[0] for p in pts) / len(pts)
                lon = sum(p[1] for p in pts) / len(pts)
                if keep(lat, lon):
                    records.append(tags_to_record(f"w{w.id}", tags, lat, lon, iso2))

        Handler().apply_file(str(self._pbf(ctx)), locations=True, idx="flex_mem")
        log.info("osm %s %s: %d POIs", ctx.version, iso2, len(records))
        yield from records
