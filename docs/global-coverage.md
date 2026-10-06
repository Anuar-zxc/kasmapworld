# Global coverage

`kasmap status` (CLI) and the views `meta.coverage_by_country`, `meta.source_freshness`
are the coverage dashboard's data.  A web page `/admin/coverage` on top of them comes with
the API in Phase 2.

Rollout order (Blueprint §14): KZ → recall audit → test-5 (DE, US, AE, JP, BR) without manual
fixes → `kasmap registry sync` (all countries) → `kasmap ingest world` → workers.

Before `ingest world`: split very large countries into H3 sub-jobs (see `docs/ingestion.md`,
known limits) and measure disk: Parquet stays in the data lake, PostGIS keeps places and
admin areas only.
