# Ingestion runbook

## Model

```
country × source × release  →  "source" job:  download → process (normalize · enrich/H3 · validate)
country                      →  "resolve" job: dedup across sources → load PostGIS (one transaction)
```

* Jobs: `meta.ingest_job` (status `PENDING → DOWNLOADING → PROCESSING → LOADED | FAILED`).
* Workers claim with `FOR UPDATE SKIP LOCKED`; `kasmap worker -c 4` runs 4 processes.
* Restart: each job stores its last completed `step`; raw downloads and processed files carry
  `_SUCCESS.<step>.json` manifests, so a rerun skips finished work.  A job whose worker died
  is reclaimed after 6 h (`locked_at` stale).
* Quality gate: a dataset whose quality score < 0.8 or whose row count moved > 15 % against
  the active version fails the job with the report in `meta.ingest_job.error`.

## Commands

| Command | What it does |
|---|---|
| `kasmap registry sync [--countries KZ,DE]` | country polygons from Overture divisions → `geo.country` |
| `kasmap ingest country KZ [--source overture]` | queue jobs for one country (and run a worker) |
| `kasmap ingest region test-5` | DE, US, AE, JP, BR (groups in `ingestion/regions.py`) |
| `kasmap ingest world` | queue every registry country (divisions + Overture; OSM only where an extract is mapped) |
| `kasmap update` | re-queue ingested countries at the configured release |
| `kasmap worker -c N [--forever]` | run jobs |
| `kasmap retry-failed [--reset-attempts]` | FAILED → PENDING |
| `kasmap status [--json]` | coverage, freshness, failed jobs |
| `kasmap categories unmapped KZ` | top unmapped source categories |

## Incremental updates

Place ids are stable across releases (an entity keeps the id its members had), rows change
only when their content hash changes, places missing from a new release are kept with an old
`last_seen_at`, and every touched H3 cell goes to `feat.dirty_cell` so features are recomputed
only there.  History of raw releases stays in the data lake (`data/raw/<source>/<release>/`).

## Onboarding a new country

1. `kasmap registry sync --countries XX`
2. If OSM matters there, add the Geofabrik path to `GEOFABRIK_PATHS` (`ingestion/sources/osm.py`).
3. `kasmap ingest country XX`, then `kasmap categories unmapped XX` and extend mappings.
4. Spot-check: places per category in the capital, `poi.match_review` sample, quality report.

## Known limits (next steps)

* Very large countries (US, RU, BR, IN) are processed as one job; dedup runs in memory.
  Next: split jobs by H3 res 3–4 cells (`BBox.split` is ready) before `ingest world`.
* Continents are not in the registry yet (`ingest region` uses explicit groups).
* Roads, buildings and transit features arrive with Phase 2 feature jobs.
