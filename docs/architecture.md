# Architecture

Source of truth: **KasMap Global — Technical Blueprint v0.1** (team doc).  This file tracks
what is implemented.

```
Overture (GeoParquet, S3) ─┐                       ┌─ geo.country / geo.admin_area
OSM (Geofabrik .pbf) ──────┼─ ingestion workers ──┼─ poi.place (+ H3 r6–r9) / poi.place_source
(more sources: adapters) ──┘   DuckDB · pyosmium   ├─ meta.dataset_version / ingest_job / pipeline_run
                               dedup · validation  └─ feat.dirty_cell (incremental recompute)
                               data lake: data/{raw,processed}/
```

| Phase | Scope | State |
|---|---|---|
| 0 | repo, schema, docs, ADRs, dev stack | done |
| 1 | country registry, Overture + OSM ingestion, H3, dedup, KZ load | implemented, first real run pending |
| 2 | H3 features, Opportunity Score v2, scoring profiles, FastAPI, MVT | next |
| 3 | "Find the best location", decisions logging, frontend migration | |
| 4 | AI analyst with tools | |
| 5 | test-5 countries → world | |
| 6 | proprietary outcomes, calibration | |

Hosting (ADR 0003): frontend and API on **Vercel**, database on **Neon** (PostGIS + h3),
ingestion on **GitHub Actions** (`.github/workflows/ingest.yml`) — function time and memory
limits make Vercel unsuitable for country-scale ingestion.

Decisions: see `docs/adr/`.
