# ADR 0003 — Hosting: Vercel for everything web-facing

Status: accepted · 2026-10-06 (team decision: «деплой всё на Vercel всегда»)

## Decision

| Component | Where | Why it fits |
|---|---|---|
| Frontend (Next.js) | **Vercel** | native |
| KasMap API (FastAPI, `/v1`, MVT tiles, AI orchestrator) | **Vercel Functions, Python runtime** | request/response work, seconds per call |
| Database (PostgreSQL + PostGIS + h3 + h3_postgis + pgvector) | **Neon** (Vercel Marketplace integration) | Neon supports postgis, h3, h3_postgis ([docs](https://neon.com/docs/extensions/postgis-related-extensions)) |
| Ingestion workers (`kasmap worker`) | **GitHub Actions** (scheduled + manual) or a local machine | see below |

## Why ingestion does not run in Vercel Functions

Vercel Functions limits ([docs](https://vercel.com/docs/functions/limitations), checked
2026-10-06): max duration 300 s on Hobby, 800 s on Pro (1800 s beta); memory 2 GB Hobby /
4 GB Pro; request/response body 4.5 MB.  A country ingest downloads hundreds of MB (OSM
extract, Overture rows), parses them with pyosmium/DuckDB and deduplicates in memory —
longer than any function limit and above its memory.  The job queue already makes
ingestion restartable, so a GitHub Actions runner (hours of runtime per job) can execute
`kasmap ingest country KZ` against the Neon database without any server of our own.

## Consequences

* Single Vercel region per project by default (`iad1`); set the region closest to Neon's
  region to keep API → DB latency low.
* Serverless functions open many short connections: use Neon's pooled connection string
  for the API, the direct one for migrations and ingestion.
* Personal data of Kazakhstan users (the `app` schema) may require storage inside KZ under
  the personal-data law — neither Vercel nor Neon has a KZ region.  Legal check before
  storing user accounts there; fallback: keep `app` in a KZ-hosted Postgres.
* `docker-compose` stays for local development only.
