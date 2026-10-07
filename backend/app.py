"""KasMap API + web app (Vercel Functions, Python runtime; Root Directory = backend).

    GET  /                         web app (map)
    GET  /v1/status                data coverage
    GET  /v1/cities/{city}/meta    categories with counts, data release
    GET  /v1/cities/{city}/scores  Opportunity Score v0 for every H3 cell
    GET  /v1/cities/{city}/best    top-N cells with reasons
    GET  /v1/cities/{city}/places  places of a category (GeoJSON)
    GET  /v1/cells/{h3}            one cell: score, reasons, competitors and anchors nearby
    POST /v1/admin/bootstrap       load / refresh a city from Overture (idempotent, 12 h guard)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse

from kasmap_api import categories as cats
from kasmap_api import db, scoring
from kasmap_api.cities import CITIES, get_city

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="KasMap API", version="0.2.0")
WEB = Path(__file__).resolve().parent / "web"
CATEGORY_NAMES = {c.id: {"ru": c.ru, "kk": c.kk, "en": c.en} for c in cats.TAXONOMY}
_score_cache: dict[tuple[str, str, int], tuple[float, scoring.CityScores]] = {}
CACHE_TTL_S = 600


def _ring(cell: str, k: int):
    import h3  # noqa: PLC0415

    return h3.grid_ring(cell, k)


def _city_or_404(slug: str):
    try:
        return get_city(slug)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


def _cache_headers(response: Response, seconds: int = 300) -> None:
    response.headers["Cache-Control"] = f"public, s-maxage={seconds}, stale-while-revalidate=600"


# ───────────────────────────── web ─────────────────────────────


@app.get("/", include_in_schema=False)
def web_app() -> FileResponse:
    return FileResponse(WEB / "index.html", media_type="text/html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


# ───────────────────────────── data status ─────────────────────────────


def _active_version(conn, iso2: str) -> dict[str, Any] | None:
    return conn.execute(
        """
        SELECT dv.id, dv.source_id, dv.source_version, dv.row_count, dv.ingested_at
          FROM meta.dataset_version dv JOIN geo.country c ON c.id = dv.country_id
         WHERE c.iso2 = %s AND dv.status = 'active' AND dv.source_id = 'overture_places'
         ORDER BY dv.ingested_at DESC LIMIT 1
        """,
        (iso2,),
    ).fetchone()


@app.get("/v1/status")
def status() -> dict:
    if not db.database_url():
        return {"database": "not_configured"}
    import psycopg  # noqa: PLC0415

    try:
        with db.connect() as conn:
            if not conn.execute("SELECT to_regclass('poi.place') IS NOT NULL AS ok").fetchone()["ok"]:
                return {"database": "connected", "schema": "not_migrated"}
            totals = conn.execute(
                """
                SELECT (SELECT count(*) FROM geo.country) AS countries,
                       (SELECT count(*) FROM poi.place)   AS places,
                       (SELECT count(*) FROM geo.admin_area WHERE level = 'city') AS cities
                """
            ).fetchone()
            freshness = conn.execute(
                "SELECT source_id, latest_version, days_since_ingest FROM meta.source_freshness"
            ).fetchall()
    except psycopg.OperationalError as exc:
        raise HTTPException(503, "database unavailable") from exc
    return {"database": "connected", "schema": "ready", "totals": totals, "freshness": freshness}


@app.get("/v1/cities")
def cities() -> list[dict]:
    return [{"slug": c.slug, "name": c.name_ru, "center": c.center, "zoom": c.zoom,
             "bbox": [c.xmin, c.ymin, c.xmax, c.ymax]} for c in CITIES.values()]


@app.get("/v1/cities/{city}/meta")
def city_meta(city: str, response: Response) -> dict:
    c = _city_or_404(city)
    base = {"city": {"slug": c.slug, "name": c.name_ru, "center": c.center, "zoom": c.zoom,
                     "bbox": [c.xmin, c.ymin, c.xmax, c.ymax]}}
    if not db.database_url():
        return {**base, "ready": False, "reason": "database_not_configured"}
    with db.connect() as conn:
        if not conn.execute("SELECT to_regclass('poi.place') IS NOT NULL AS ok").fetchone()["ok"]:
            return {**base, "ready": False, "reason": "no_data"}
        version = _active_version(conn, c.country_iso2)
        rows = conn.execute(
            """
            SELECT p.category_id, count(*) AS n
              FROM poi.place p JOIN geo.admin_area a ON a.id = p.admin_area_id
             WHERE a.gers_id = %s GROUP BY 1
            """,
            (f"kasmap:city:{c.slug}",),
        ).fetchall()
    counts = {r["category_id"]: r["n"] for r in rows}
    total = sum(counts.values())
    if not total:
        return {**base, "ready": False, "reason": "no_data"}
    categories = []
    for cat in cats.TAXONOMY:
        if cat.parent_id is None:
            continue
        n = sum(v for k, v in counts.items() if k and (k == cat.id or k.startswith(cat.id + ".")))
        categories.append({"id": cat.id, "name": cat.ru, "count": n,
                           "group": CATEGORY_NAMES[cat.id.split(".")[0]]["ru"]})
    _cache_headers(response, 300)
    return {**base, "ready": True, "places": total, "unmapped": counts.get(None, 0),
            "categories": categories,
            "data": {"source": "Overture Maps Places", "release": version["source_version"],
                     "loaded_at": version["ingested_at"]} if version else None}


def _scores(city_slug: str, category: str) -> scoring.CityScores:
    if category not in cats.CATEGORY_IDS:
        raise HTTPException(400, f"unknown category '{category}'")
    c = _city_or_404(city_slug)
    with db.connect() as conn:
        version = _active_version(conn, c.country_iso2)
        if version is None:
            raise HTTPException(409, "no data loaded for this city yet")
        key = (c.slug, category, version["id"])
        hit = _score_cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            return hit[1]
        rows = conn.execute(
            """
            SELECT p.h3_r9::text AS h3, p.category_id
              FROM poi.place p JOIN geo.admin_area a ON a.id = p.admin_area_id
             WHERE a.gers_id = %s AND p.operating_status <> 'closed'
            """,
            (f"kasmap:city:{c.slug}",),
        ).fetchall()
    result = scoring.score_city(((r["h3"], r["category_id"]) for r in rows), category, _ring)
    _score_cache[key] = (time.time(), result)
    return result


@app.get("/v1/cities/{city}/scores")
def city_scores(city: str, response: Response, category: str = Query(...)) -> dict:
    s = _scores(city, category)
    _cache_headers(response, 600)
    return {
        "category": category,
        "medians": s.medians,
        "method": "opportunity-score-v0: percentile within city; not a success probability",
        "cells": [[c.h3, c.score, c.activity, c.anchors, c.competitors] for c in s.cells],
        "columns": ["h3", "score", "activity", "anchors", "competitors"],
    }


def _cell_payload(c: scoring.CellScore) -> dict:
    import h3  # noqa: PLC0415

    lat, lon = h3.cell_to_latlng(c.h3)
    return {"h3": c.h3, "score": c.score, "center": [lon, lat], "activity": c.activity,
            "anchors": c.anchors, "competitors": c.competitors, "components": c.components,
            "why": c.why}


@app.get("/v1/cities/{city}/best")
def best(city: str, response: Response, category: str = Query(...),
         limit: int = Query(5, ge=1, le=20)) -> dict:
    s = _scores(city, category)
    _cache_headers(response, 600)
    return {"category": category, "medians": s.medians,
            "results": [_cell_payload(c) for c in s.top(limit, ring=_ring)]}


@app.get("/v1/cells/{cell}")
def cell_detail(cell: str, city: str = Query("almaty"), category: str = Query(...)) -> dict:
    import h3  # noqa: PLC0415

    if not h3.is_valid_cell(cell):
        raise HTTPException(400, "invalid H3 cell")
    s = _scores(city, category)
    found = next((c for c in s.cells if c.h3 == cell), None)
    near1 = list(h3.grid_disk(cell, 1))
    near2 = list(h3.grid_disk(cell, 2))
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT name, category_id, h3_r9::text AS h3, ST_X(geom) AS lon, ST_Y(geom) AS lat
              FROM poi.place
             WHERE h3_r9 = ANY(%s::h3index[]) AND operating_status <> 'closed'
            """,
            (near2,),
        ).fetchall()
    lat0, lon0 = h3.cell_to_latlng(cell)
    from math import cos, radians, sqrt  # noqa: PLC0415

    def dist(r):
        dx = (r["lon"] - lon0) * 111_320 * cos(radians(lat0))
        dy = (r["lat"] - lat0) * 111_320
        return round(sqrt(dx * dx + dy * dy))

    competitors = sorted(
        ({"name": r["name"], "distance_m": dist(r), "category": r["category_id"]}
         for r in rows if r["h3"] in near1 and scoring.competitor_weight(category, r["category_id"])),
        key=lambda x: x["distance_m"])
    anchors = sorted(
        ({"name": r["name"], "distance_m": dist(r),
          "category": CATEGORY_NAMES.get(r["category_id"] or "", {}).get("ru")}
         for r in rows if scoring.is_anchor(r["category_id"])),
        key=lambda x: x["distance_m"])
    payload = _cell_payload(found) if found else {"h3": cell, "score": None,
                                                    "center": [lon0, lat0], "why": []}
    return {**payload, "medians": s.medians, "competitors_nearby": competitors[:15],
            "anchors_nearby": anchors[:10], "places_nearby": len(rows)}


@app.get("/v1/cities/{city}/places")
def places(city: str, response: Response, category: str = Query(...),
           limit: int = Query(3000, le=10000)) -> dict:
    c = _city_or_404(city)
    if category not in cats.CATEGORY_IDS:
        raise HTTPException(400, f"unknown category '{category}'")
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT p.name, p.category_id, ST_X(p.geom) AS lon, ST_Y(p.geom) AS lat
              FROM poi.place p JOIN geo.admin_area a ON a.id = p.admin_area_id
             WHERE a.gers_id = %s AND (p.category_id = %s OR p.category_id LIKE %s)
             LIMIT %s
            """,
            (f"kasmap:city:{c.slug}", category, category + ".%", limit),
        ).fetchall()
    _cache_headers(response, 600)
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
         "properties": {"name": r["name"], "category": r["category_id"]}} for r in rows]}


# ───────────────────────────── admin ─────────────────────────────


@app.post("/v1/admin/bootstrap")
def admin_bootstrap(city: str = Query("almaty"), force: bool = Query(False)) -> dict:
    """Load or refresh a city from Overture.  Safe to call repeatedly: a load newer than
    12 hours is reused, and only one run can hold the lock at a time."""
    from kasmap_api import bootstrap  # noqa: PLC0415

    c = _city_or_404(city)
    try:
        with db.connect(admin=True, timeout=15) as conn:
            result = bootstrap.run(conn, c, force=force)
    except db.NotConfigured as exc:
        raise HTTPException(503, "database is not configured") from exc
    _score_cache.clear()
    return result
