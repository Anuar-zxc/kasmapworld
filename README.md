# KasMap

Global location intelligence platform: **where** a business should open, and **why**.

This repository holds the data foundation (Phase 0–1 of the Technical Blueprint v0.1):
country registry, ingestion of Overture Maps and OpenStreetMap, validation, H3 indexing,
entity resolution and loading into PostgreSQL/PostGIS.  Scoring, API, AI and the new web
app arrive in later phases (see `docs/architecture.md`).

## Quick start: ingest Kazakhstan on your machine

Requirements: Docker, Python 3.11+, [uv](https://docs.astral.sh/uv/) (or pip), ~10 GB disk,
internet access to `s3.us-west-2.amazonaws.com` (Overture) and `download.geofabrik.de`.

```bash
# 1. database: PostgreSQL 16 + PostGIS + h3-pg + pgvector
docker compose -f infrastructure/docker-compose.yml up -d --build

# 2. python env
uv venv && source .venv/bin/activate
uv pip install -e ".[osm,dev]"
cp .env.example .env && set -a && source .env && set +a

# 3. schema and reference data
kasmap db migrate
kasmap db seed

# 4. Kazakhstan: country polygon → jobs → worker
kasmap registry sync --countries KZ
kasmap ingest country KZ          # queues divisions + overture + osm + resolve, then runs them
kasmap status
kasmap categories unmapped KZ     # which source categories still need a mapping
```

A job that fails can be resumed: `kasmap retry-failed && kasmap worker`.
Downloads are cached under `data/raw/…`; a restarted job skips finished steps.

### What a successful run looks like

`kasmap status` shows KZ as `Loaded`, the number of places, cities and H3 cells.  Then check
the data in SQL, for example places per category in Almaty:

```sql
SELECT p.category_id, count(*)
FROM poi.place p JOIN geo.admin_area a ON a.id = p.admin_area_id
WHERE a.name ILIKE 'Алматы%' OR a.name ILIKE 'Almaty%'
GROUP BY 1 ORDER BY 2 DESC;
```

## Tests

```bash
pytest            # or, without dependencies: PYTHONPATH=src:tests python -m unittest discover -s tests
```

## Layout

```
src/kasmap/
  ingestion/   sources (overture, osm), orchestrator, storage (data lake), regions
  processing/  normalization, validation, categories, H3, dedup (entity resolution)
  intelligence/ scoring (Phase 2)
  db/          connections, job queue, country registry, loader
  cli.py       `kasmap` command
database/      schemas/*.sql + Alembic migrations
infrastructure/ docker-compose, Postgres image
docs/          architecture, data sources and licenses, ingestion runbook, ADRs
frontend/ backend/  placeholders for Phase 2–3
```

## Data licensing

Every source is documented in `docs/data-licenses.md` before production use.  2GIS data is
**not** stored (its offer forbids storage and caching) — see `docs/adr/0001-open-data-foundation.md`.
Attribution «© OpenStreetMap contributors» and Overture attribution must be shown wherever
data derived from them is displayed.
