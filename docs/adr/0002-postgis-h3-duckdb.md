# ADR 0002 — PostgreSQL + PostGIS + h3-pg, DuckDB for batch, no broker

Status: accepted · 2026-10-06

* **PostgreSQL 16 + PostGIS + h3-pg + pgvector** replace MySQL: spatial indexes, H3 types,
  similarity search in one database.
* **H3 r9 stored, r6–r8 generated columns** with B-tree indexes (cheap, indexable parents).
* **DuckDB over GeoParquet** for heavy batch reads (bbox pushdown on Overture S3); PostGIS
  holds only loaded countries — world-scale buildings stay in Parquet.
* **Job queue in PostgreSQL** (`SKIP LOCKED`) instead of Celery/Kafka: parallel workers,
  restartable steps, retry — with no extra infrastructure.  Revisit when a measured
  bottleneck appears.
* **Monorepo with one Python package** (`src/kasmap/{ingestion,processing,intelligence,db}`)
  instead of top-level `ingestion/`, `processing/` folders: one installable package, one
  test suite, imports without path hacks.  Folder names follow the World Coverage Engine plan.
