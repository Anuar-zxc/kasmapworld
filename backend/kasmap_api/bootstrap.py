"""City bootstrap: schema → reference data → Overture places for one city → PostGIS.

Runs inside a Vercel Function (≤ 300 s on Hobby), so it is deliberately small:
one source (Overture Places), one city extent, DuckDB over Overture's public S3 with
bbox pushdown, a single COPY into Neon.  Country-scale ingestion with OSM, dedup and
admin boundaries stays in the `kasmap` pipeline (GitHub Actions).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from kasmap_api import categories as cats
from kasmap_api import normalization as norm
from kasmap_api.cities import COUNTRIES, City

log = logging.getLogger(__name__)

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "sql" / "0001_initial.sql"
OVERTURE_ROOT = "s3://overturemaps-us-west-2/release"
SOURCE_ID = "overture_places"
PIPELINE_VERSION = "api-bootstrap-0.1"
ID_NAMESPACE = uuid.UUID("5b0c1a9e-6c2f-4d8e-9a57-0d3c1f7a2b61")
MIN_CONFIDENCE = 0.3
ADVISORY_LOCK = 724_001  # one bootstrap at a time


def place_uuid(gers_id: str) -> str:
    """Stable place id from the Overture GERS id (same place → same id every release)."""
    return str(uuid.uuid5(ID_NAMESPACE, f"overture:{gers_id}"))


# ───────────────────────────── schema & seed ─────────────────────────────


def ensure_schema(conn) -> bool:
    exists = conn.execute("SELECT to_regclass('poi.place') IS NOT NULL AS ok").fetchone()["ok"]
    if exists:
        return False
    conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.commit()
    return True


def seed(conn, city: City) -> dict[str, int]:
    conn.execute(
        """
        INSERT INTO meta.source (id, name, license, attribution, commercial_ok, storage_ok,
                                 share_alike, update_freq, priority, license_url)
        VALUES ('overture_places', 'Overture Maps — Places', 'CDLA-Permissive-2.0',
                'Overture Maps Foundation; contributors incl. Meta, Microsoft, Foursquare',
                true, true, false, 'monthly', 20, 'https://docs.overturemaps.org/attribution/')
        ON CONFLICT (id) DO NOTHING
        """
    )
    for c in cats.TAXONOMY:
        conn.execute(
            """
            INSERT INTO poi.category (id, parent_id, name) VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (id) DO UPDATE SET parent_id = EXCLUDED.parent_id, name = EXCLUDED.name
            """,
            (c.id, c.parent_id, json.dumps({"ru": c.ru, "kk": c.kk, "en": c.en}, ensure_ascii=False)),
        )
    name, (x0, y0, x1, y1) = COUNTRIES[city.country_iso2]
    country_id = conn.execute(
        """
        INSERT INTO geo.country (iso2, name, bbox)
        VALUES (%s, %s, ST_MakeEnvelope(%s, %s, %s, %s, 4326))
        ON CONFLICT (iso2) DO UPDATE SET name = geo.country.name
        RETURNING id
        """,
        (city.country_iso2, name, x0, y0, x1, y1),
    ).fetchone()["id"]
    city_area_id = conn.execute(
        """
        INSERT INTO geo.admin_area (level, country_iso2, name, names, geom, gers_id)
        VALUES ('city', %s, %s, %s::jsonb,
                ST_Multi(ST_MakeEnvelope(%s, %s, %s, %s, 4326)), %s)
        ON CONFLICT (gers_id) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        (city.country_iso2, city.name_ru,
         json.dumps({"ru": city.name_ru, "en": city.name_en, "extent": "approximate"},
                    ensure_ascii=False),
         city.xmin, city.ymin, city.xmax, city.ymax, f"kasmap:city:{city.slug}"),
    ).fetchone()["id"]
    conn.commit()
    return {"country_id": country_id, "city_area_id": city_area_id}


# ───────────────────────────── Overture extraction ─────────────────────────────


def _duckdb():
    import duckdb  # noqa: PLC0415

    con = duckdb.connect()
    con.execute("SET home_directory='/tmp'")
    con.execute("SET extension_directory='/tmp/duckdb_extensions'")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2'")
    return con


def latest_release(con) -> str:
    rows = con.execute(
        f"SELECT file FROM glob('{OVERTURE_ROOT}/*/theme=places/type=place/*.parquet')"
    ).fetchall()
    releases = sorted({r[0].split("/release/")[1].split("/")[0] for r in rows})
    if not releases:
        raise RuntimeError("No Overture releases found")
    return releases[-1]


def select_list(columns: set[str]) -> str:
    cols = ["id", "names.primary AS name", "bbox.xmin AS lon", "bbox.ymin AS lat"]
    optional = {
        "basic_category": "basic_category",
        "categories": "categories.primary AS category_primary",
        "taxonomy": "taxonomy.primary AS tax_primary, taxonomy.hierarchy AS tax_hierarchy",
        "confidence": "confidence",
        "websites": "websites[1] AS website",
        "phones": "phones[1] AS phone",
        "brand": "brand.names.primary AS brand",
        "addresses": "addresses[1].freeform AS address",
        "operating_status": "operating_status",
    }
    cols += [expr for key, expr in optional.items() if key in columns]
    return ", ".join(cols)


def extract_city(con, release: str, city: City) -> list[dict[str, Any]]:
    path = f"{OVERTURE_ROOT}/{release}/theme=places/type=place/*"
    columns = {r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{path}', hive_partitioning=1)").fetchall()}
    sql = f"""
        SELECT {select_list(columns)}
        FROM read_parquet('{path}', hive_partitioning=1)
        WHERE bbox.xmin BETWEEN {city.xmin} AND {city.xmax}
          AND bbox.ymin BETWEEN {city.ymin} AND {city.ymax}
    """
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def to_place(row: dict[str, Any], iso2: str) -> dict[str, Any] | None:
    import h3  # noqa: PLC0415

    name = (row.get("name") or "").strip()
    status = (row.get("operating_status") or "").lower()
    confidence = row.get("confidence")
    if not name or status in ("permanently_closed", "closed"):
        return None
    if confidence is not None and confidence < MIN_CONFIDENCE:
        return None
    lat, lon = row["lat"], row["lon"]
    primary = row.get("tax_primary") or row.get("category_primary")
    category = cats.map_overture(row.get("basic_category"), primary,
                                 list(row.get("tax_hierarchy") or []))
    phone = norm.normalize_phone(row.get("phone"), iso2)
    return {
        "id": place_uuid(row["id"]),
        "gers_id": row["id"],
        "name": name,
        "name_norm": norm.name_norm(name),
        "brand": row.get("brand"),
        "category_id": category,
        "source_category": primary or row.get("basic_category"),
        "lon": lon,
        "lat": lat,
        "h3": h3.latlng_to_cell(lat, lon, 9),
        "address": json.dumps({"freeform": row["address"]}, ensure_ascii=False)
        if row.get("address") else None,
        "website": row.get("website"),
        "phone": phone or row.get("phone"),
        "operating_status": "temporarily_closed" if status == "temporarily_closed" else (
            "open" if status == "open" else "unknown"),
        "confidence": round(float(confidence if confidence is not None else 0.5), 3),
    }


# ───────────────────────────── load ─────────────────────────────


def load_places(conn, places: list[dict[str, Any]], *, release: str, iso2: str,
                country_id: int, city_area_id: int) -> dict[str, int]:
    dv = conn.execute(
        """
        INSERT INTO meta.dataset_version (source_id, source_version, country_id, pipeline_version,
                                          raw_uri, row_count, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'staged')
        ON CONFLICT (source_id, source_version, country_id, pipeline_version)
        DO UPDATE SET row_count = EXCLUDED.row_count, ingested_at = now()
        RETURNING id
        """,
        (SOURCE_ID, release, country_id, PIPELINE_VERSION,
         f"{OVERTURE_ROOT}/{release}/theme=places/type=place", len(places)),
    ).fetchone()["id"]
    conn.execute(
        """
        CREATE TEMP TABLE tmp_place (
            id uuid, gers_id text, name text, name_norm text, brand text, category_id text,
            source_category text, lon double precision, lat double precision, h3 text,
            address jsonb, website text, phone text, operating_status text,
            confidence numeric(4,3)
        ) ON COMMIT DROP
        """
    )
    cols = ("id", "gers_id", "name", "name_norm", "brand", "category_id", "source_category",
            "lon", "lat", "h3", "address", "website", "phone", "operating_status", "confidence")
    with conn.cursor().copy(f"COPY tmp_place ({', '.join(cols)}) FROM STDIN") as copy:
        for p in places:
            copy.write_row(tuple(p[c] for c in cols))
    conn.execute(
        """
        INSERT INTO poi.place AS p
            (id, name, name_norm, brand, category_id, source_category, country_iso2,
             admin_area_id, geom, h3_r9, address, website, phone, operating_status,
             confidence, source_count, first_seen_at, last_seen_at, content_hash)
        SELECT t.id, t.name, t.name_norm, t.brand, t.category_id, t.source_category, %(iso2)s,
               %(area)s, ST_SetSRID(ST_MakePoint(t.lon, t.lat), 4326), t.h3::h3index,
               t.address, t.website, t.phone, t.operating_status, t.confidence, 1,
               current_date, current_date,
               md5(concat_ws('|', t.name, t.category_id, round(t.lon::numeric, 6),
                             round(t.lat::numeric, 6), t.phone, t.website, t.operating_status))
          FROM tmp_place t
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name, name_norm = EXCLUDED.name_norm, brand = EXCLUDED.brand,
            category_id = EXCLUDED.category_id, source_category = EXCLUDED.source_category,
            admin_area_id = EXCLUDED.admin_area_id, geom = EXCLUDED.geom, h3_r9 = EXCLUDED.h3_r9,
            address = EXCLUDED.address, website = EXCLUDED.website, phone = EXCLUDED.phone,
            operating_status = EXCLUDED.operating_status, confidence = EXCLUDED.confidence,
            last_seen_at = EXCLUDED.last_seen_at,
            updated_at = CASE WHEN p.content_hash <> EXCLUDED.content_hash THEN now()
                              ELSE p.updated_at END,
            content_hash = EXCLUDED.content_hash
        """,
        {"iso2": iso2, "area": city_area_id},
    )
    conn.execute(
        """
        INSERT INTO poi.place_source (source_id, source_record_id, dataset_version_id, place_id,
                                      match_score, match_method)
        SELECT %s, t.gers_id, %s, t.id, 1.0, 'seed' FROM tmp_place t
        ON CONFLICT DO NOTHING
        """,
        (SOURCE_ID, dv),
    )
    conn.execute(
        """
        UPDATE meta.dataset_version SET status = 'superseded'
         WHERE source_id = %s AND country_id = %s AND status = 'active' AND id <> %s
        """,
        (SOURCE_ID, country_id, dv),
    )
    conn.execute("UPDATE meta.dataset_version SET status = 'active' WHERE id = %s", (dv,))
    conn.execute("UPDATE geo.country SET last_ingested_at = now() WHERE id = %s", (country_id,))
    conn.commit()
    return {"dataset_version_id": dv, "loaded": len(places)}


def recently_loaded(conn, country_id: int, hours: int = 12) -> dict[str, Any] | None:
    return conn.execute(
        """
        SELECT source_version, row_count, ingested_at FROM meta.dataset_version
         WHERE source_id = %s AND country_id = %s AND status = 'active'
           AND ingested_at > now() - make_interval(hours => %s)
         ORDER BY ingested_at DESC LIMIT 1
        """,
        (SOURCE_ID, country_id, hours),
    ).fetchone()


def run(conn, city: City, force: bool = False) -> dict[str, Any]:
    t0 = time.time()
    timings: dict[str, float] = {}
    got = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (ADVISORY_LOCK,)).fetchone()
    if not got["ok"]:
        return {"status": "busy", "message": "bootstrap already running"}
    try:
        migrated = ensure_schema(conn)
        ids = seed(conn, city)
        timings["schema_seed_s"] = round(time.time() - t0, 1)
        recent = recently_loaded(conn, ids["country_id"])
        if recent and not force:
            return {"status": "fresh", "migrated": migrated, **recent}
        con = _duckdb()
        release = latest_release(con)
        timings["release_s"] = round(time.time() - t0, 1)
        rows = extract_city(con, release, city)
        timings["extract_s"] = round(time.time() - t0, 1)
        places = [p for p in (to_place(r, city.country_iso2) for r in rows) if p]
        stats = load_places(conn, places, release=release, iso2=city.country_iso2,
                            country_id=ids["country_id"], city_area_id=ids["city_area_id"])
        timings["load_s"] = round(time.time() - t0, 1)
        mapped = sum(1 for p in places if p["category_id"])
        return {"status": "loaded", "migrated": migrated, "release": release,
                "rows_read": len(rows), "places": len(places), "mapped_categories": mapped,
                **stats, "timings": timings}
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK,))
        conn.commit()
