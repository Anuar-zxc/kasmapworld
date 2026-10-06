"""Overture Maps adapters (bulk GeoParquet via DuckDB, never per-object API calls).

Reads only the country's bounding box thanks to the `bbox` struct column (predicate
pushdown on Parquet row-group statistics), then filters precisely by the country polygon.

Schema notes (verified against release notes 2026-08-19.0, schema v1.18.0):
* places: `categories` is deprecated (removal announced for September 2026) in favour of
  `basic_category` + `taxonomy`.  Column lists are therefore discovered with DESCRIBE and
  the query adapts, so one adapter works across releases.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kasmap.config import Settings
from kasmap.ingestion import storage
from kasmap.ingestion.base import CountryRef, GeoDataSource, JobContext, SourceMeta
from kasmap.processing.categories import map_overture
from kasmap.processing.geo import BBox
from kasmap.processing.records import PlaceRecord

log = logging.getLogger(__name__)

PLACES_META = SourceMeta(
    id="overture_places",
    name="Overture Maps — Places",
    license="CDLA-Permissive-2.0",
    attribution="Overture Maps Foundation; contributors incl. Meta, Microsoft, Foursquare "
                "(Apache-2.0), AllThePlaces (CC0-1.0)",
    commercial_ok=True,
    storage_ok=True,
    share_alike=False,
    update_freq="monthly",
    priority=20,
    license_url="https://docs.overturemaps.org/attribution/",
)

DIVISIONS_META = SourceMeta(
    id="overture_divisions",
    name="Overture Maps — Divisions",
    license="ODbL-1.0",
    attribution="© OpenStreetMap contributors, Overture Maps Foundation; "
                "geoBoundaries / Esri Community Maps (CC-BY-4.0)",
    commercial_ok=True,
    storage_ok=True,
    share_alike=True,
    update_freq="monthly",
    priority=20,
    license_url="https://docs.overturemaps.org/attribution/",
)

OPERATING_STATUS = {
    "open": "open",
    "permanently_closed": "closed",
    "closed": "closed",
    "temporarily_closed": "temporarily_closed",
}

# Division subtypes → KasMap admin levels.  'county' and 'localadmin' are skipped for now.
ADMIN_SUBTYPES = {"region": "region", "locality": "city", "macrohood": "district"}


# ───────────────────────────── SQL building (pure) ─────────────────────────────


def theme_path(settings: Settings, release: str, theme: str, type_: str) -> str:
    return f"{settings.overture_s3_root}/{release}/theme={theme}/type={type_}/*"


def bbox_predicate(b: BBox) -> str:
    """Intersects-test on Overture's per-row bbox struct (enables row-group skipping)."""
    return (
        f"bbox.xmin <= {b.xmax!r} AND bbox.xmax >= {b.xmin!r} "
        f"AND bbox.ymin <= {b.ymax!r} AND bbox.ymax >= {b.ymin!r}"
    )


def land_predicate(columns: set[str]) -> str:
    if "is_land" in columns:
        return "is_land"
    if "class" in columns:
        return "class = 'land'"
    return "TRUE"


def place_select(columns: set[str]) -> list[str]:
    cols = [
        "id",
        "names.primary AS name",
        "ST_X(geometry) AS lon",
        "ST_Y(geometry) AS lat",
    ]
    optional = {
        "basic_category": "basic_category",
        "categories": "categories.primary AS category_primary",
        "taxonomy": "to_json(taxonomy) AS taxonomy_json",
        "confidence": "confidence",
        "websites": "websites[1] AS website",
        "phones": "phones[1] AS phone",
        "brand": "brand.names.primary AS brand",
        "addresses": "to_json(addresses[1]) AS address_json",
        "operating_status": "operating_status",
    }
    cols += [expr for name, expr in optional.items() if name in columns]
    return cols


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# ───────────────────────────── DuckDB plumbing ─────────────────────────────


def connect(settings: Settings):
    import duckdb  # noqa: PLC0415

    con = duckdb.connect()
    for ext in ("spatial", "httpfs"):
        con.execute(f"INSTALL {ext}; LOAD {ext};")
    con.execute(f"SET s3_region={sql_literal(settings.overture_s3_region)}")
    return con


def describe_columns(con, path: str) -> set[str]:
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet({sql_literal(path)}, hive_partitioning=1)"
    ).fetchall()
    return {r[0] for r in rows}


def fetch_country(con, settings: Settings, release: str, iso2: str) -> dict[str, Any] | None:
    """Country polygon (land), names and bbox from Overture divisions."""
    path = theme_path(settings, release, "divisions", "division_area")
    columns = describe_columns(con, path)
    row = con.execute(
        f"""
        SELECT any_value(names.primary)                         AS name,
               any_value(to_json(names))                        AS names_json,
               ST_AsWKB(ST_Union_Agg(geometry))                 AS wkb,
               min(bbox.xmin), min(bbox.ymin), max(bbox.xmax), max(bbox.ymax)
        FROM read_parquet({sql_literal(path)}, hive_partitioning=1)
        WHERE subtype = 'country' AND country = {sql_literal(iso2.upper())}
          AND {land_predicate(columns)}
        """
    ).fetchone()
    if row is None or row[2] is None:
        return None
    name, names_json, wkb, xmin, ymin, xmax, ymax = row
    return {
        "iso2": iso2.upper(),
        "name": name,
        "names": json.loads(names_json) if names_json else {},
        "wkb": bytes(wkb),
        "bbox": BBox(xmin, ymin, xmax, ymax),
    }


def list_countries(con, settings: Settings, release: str) -> list[dict[str, Any]]:
    """All countries in the release (for `kasmap registry sync`)."""
    path = theme_path(settings, release, "divisions", "division_area")
    columns = describe_columns(con, path)
    rows = con.execute(
        f"""
        SELECT country, any_value(names.primary), any_value(to_json(names)),
               ST_AsWKB(ST_Union_Agg(geometry)),
               min(bbox.xmin), min(bbox.ymin), max(bbox.xmax), max(bbox.ymax)
        FROM read_parquet({sql_literal(path)}, hive_partitioning=1)
        WHERE subtype = 'country' AND {land_predicate(columns)} AND country IS NOT NULL
        GROUP BY country
        ORDER BY country
        """
    ).fetchall()
    return [
        {
            "iso2": r[0],
            "name": r[1],
            "names": json.loads(r[2]) if r[2] else {},
            "wkb": bytes(r[3]),
            "bbox": BBox(r[4], r[5], r[6], r[7]),
        }
        for r in rows
    ]


# ───────────────────────────── adapters ─────────────────────────────


class OverturePlacesSource(GeoDataSource):
    meta = PLACES_META

    def resolve_version(self, settings: Settings, country: CountryRef) -> str:
        return settings.overture_release

    def _raw_file(self, ctx: JobContext) -> Path:
        return ctx.lake.raw(self.source_id, ctx.version, ctx.iso2) / "places.parquet"

    def download(self, ctx: JobContext) -> dict[str, Any]:
        if ctx.country.geom_wkb is None or ctx.country.bbox is None:
            raise RuntimeError(
                f"Country {ctx.iso2} has no geometry; run `kasmap registry sync` first"
            )
        out = self._raw_file(ctx)
        con = connect(ctx.settings)
        path = theme_path(ctx.settings, ctx.version, "places", "place")
        columns = describe_columns(con, path)
        select = ",\n               ".join(place_select(columns))
        con.execute(
            f"CREATE TEMP TABLE country AS "
            f"SELECT ST_GeomFromHEXWKB({sql_literal(ctx.country.geom_wkb.hex())}) AS g"
        )
        tmp = out.with_name(out.name + ".tmp")
        con.execute(
            f"""
            COPY (
              SELECT {select}
              FROM read_parquet({sql_literal(path)}, hive_partitioning=1) p, country
              WHERE {bbox_predicate(ctx.country.bbox)}
                AND ST_Intersects(p.geometry, country.g)
            ) TO {sql_literal(str(tmp))} (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        tmp.replace(out)
        count = con.execute(f"SELECT count(*) FROM read_parquet({sql_literal(str(out))})").fetchone()[0]
        info = {"file": str(out), "rows": count, "sha256": storage.sha256_file(out),
                "columns": sorted(columns), "release": ctx.version}
        log.info("overture places %s %s: %d rows", ctx.version, ctx.iso2, count)
        return info

    def normalize(self, ctx: JobContext) -> Iterator[PlaceRecord]:
        import pyarrow.parquet as pq  # noqa: PLC0415

        for batch in pq.ParquetFile(self._raw_file(ctx)).iter_batches(batch_size=50_000):
            for row in batch.to_pylist():
                yield row_to_record(row, ctx.iso2)


def row_to_record(row: dict[str, Any], iso2: str) -> PlaceRecord:
    basic = row.get("basic_category")
    primary = row.get("category_primary")
    address = None
    if row.get("address_json"):
        try:
            address = json.loads(row["address_json"])
        except (TypeError, ValueError):
            address = None
    raw = {k: row.get(k) for k in ("basic_category", "category_primary", "taxonomy_json")
           if row.get(k) is not None}
    return PlaceRecord(
        source_id=PLACES_META.id,
        source_record_id=row["id"],
        name=row.get("name") or "",
        lat=row.get("lat"),
        lon=row.get("lon"),
        country_iso2=iso2,
        category_id=map_overture(basic, primary),
        source_category=basic or primary,
        brand=row.get("brand"),
        address=address,
        phone=row.get("phone"),
        website=row.get("website"),
        operating_status=OPERATING_STATUS.get((row.get("operating_status") or "").lower(),
                                              "unknown"),
        source_confidence=row.get("confidence"),
        raw=raw,
    )


class OvertureDivisionsSource(GeoDataSource):
    """Admin areas (regions, cities, districts) inside a country → geo.admin_area."""

    meta = DIVISIONS_META
    produces_places = False

    def resolve_version(self, settings: Settings, country: CountryRef) -> str:
        return settings.overture_release

    def raw_file(self, ctx: JobContext) -> Path:
        return ctx.lake.raw(self.source_id, ctx.version, ctx.iso2) / "admin_areas.parquet"

    def download(self, ctx: JobContext) -> dict[str, Any]:
        out = self.raw_file(ctx)
        con = connect(ctx.settings)
        path = theme_path(ctx.settings, ctx.version, "divisions", "division_area")
        columns = describe_columns(con, path)
        subtypes = ", ".join(sql_literal(s) for s in ADMIN_SUBTYPES)
        tmp = out.with_name(out.name + ".tmp")
        con.execute(
            f"""
            COPY (
              SELECT id, division_id, subtype, names.primary AS name, to_json(names) AS names_json,
                     ST_AsWKB(geometry) AS wkb  -- ST_Multi applied in PostGIS on load
              FROM read_parquet({sql_literal(path)}, hive_partitioning=1)
              WHERE country = {sql_literal(ctx.iso2)} AND subtype IN ({subtypes})
                AND {land_predicate(columns)}
            ) TO {sql_literal(str(tmp))} (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        tmp.replace(out)
        count = con.execute(f"SELECT count(*) FROM read_parquet({sql_literal(str(out))})").fetchone()[0]
        return {"file": str(out), "rows": count, "release": ctx.version}

    def normalize(self, ctx: JobContext) -> Iterator[PlaceRecord]:
        return iter(())  # no places; admin areas are loaded by the loader directly

    def iter_admin_areas(self, ctx: JobContext) -> Iterator[dict[str, Any]]:
        import pyarrow.parquet as pq  # noqa: PLC0415

        for batch in pq.ParquetFile(self.raw_file(ctx)).iter_batches(batch_size=5_000):
            for row in batch.to_pylist():
                yield {
                    "gers_id": row["id"],
                    "level": ADMIN_SUBTYPES[row["subtype"]],
                    "name": row.get("name") or "",
                    "names": json.loads(row["names_json"]) if row.get("names_json") else {},
                    "wkb": bytes(row["wkb"]),
                }
