"""KasMap API (Vercel Functions, Python runtime).

Phase 2 grows this into /v1/locations, /v1/find/best-locations, MVT tiles and the AI
orchestrator.  Today it exposes health and global coverage from the Neon database.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

app = FastAPI(title="KasMap API", version="0.1.0")


def _database_url() -> str | None:
    # KASMAP_DATABASE_URL (pooled Neon string) or the variable the Neon integration injects.
    return os.environ.get("KASMAP_DATABASE_URL") or os.environ.get("DATABASE_URL")


@app.get("/")
def root() -> dict:
    return {"service": "kasmap-api", "version": app.version, "docs": "/docs"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/v1/status")
def status() -> dict:
    """Global coverage: countries by ingestion status, places, data freshness."""
    url = _database_url()
    if not url:
        return {"database": "not_configured"}
    import psycopg
    from psycopg.rows import dict_row

    try:
        with psycopg.connect(url, row_factory=dict_row, connect_timeout=5) as conn:
            by_status = conn.execute(
                "SELECT status, count(*) AS n FROM meta.coverage_by_country GROUP BY status"
            ).fetchall()
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
    except psycopg.errors.UndefinedTable:
        return {"database": "connected", "schema": "not_migrated"}
    except psycopg.OperationalError as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {
        "database": "connected",
        "totals": totals,
        "countries_by_status": {r["status"]: r["n"] for r in by_status},
        "freshness": freshness,
    }
