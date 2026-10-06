"""Global ingestion orchestrator (World Coverage Engine §5, §7, §8).

Unit of work: one country × one source × one release ("source" job), then one
"resolve" job per country that merges all sources into canonical places.  Jobs live in
PostgreSQL; any number of worker processes pull from the queue.

    kasmap ingest country KZ     → divisions + overture + osm jobs, then resolve
    kasmap worker -c 4           → run jobs until the queue is empty
"""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kasmap.config import Settings
from kasmap.ingestion import storage
from kasmap.ingestion.base import CountryRef, JobContext
from kasmap.ingestion.sources import DEFAULT_SOURCES, get_source, source_priority
from kasmap.ingestion.sources.osm import GEOFABRIK_PATHS
from kasmap.processing.dedup import resolve

log = logging.getLogger(__name__)

STEP_DOWNLOAD = "download"
STEP_PROCESS = "process"
STEP_RESOLVE = "resolve"


class QualityGateError(RuntimeError):
    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


MIN_QUALITY_SCORE = 0.8


def quality_passes(report: dict[str, Any], min_score: float = MIN_QUALITY_SCORE) -> bool:
    return (report.get("total", 0) > 0 and report.get("score", 0) >= min_score
            and not report.get("delta_exceeded"))


# ───────────────────────────── enqueue ─────────────────────────────


def ensure_country(conn, settings: Settings, iso2: str) -> CountryRef:
    from kasmap.db import countries  # noqa: PLC0415

    ref = countries.get_country(conn, iso2)
    if ref is not None and ref.geom_wkb:
        return ref
    from kasmap.ingestion.sources import overture  # noqa: PLC0415

    con = overture.connect(settings)
    data = overture.fetch_country(con, settings, settings.overture_release, iso2)
    if data is None:
        raise ValueError(f"Country {iso2} not found in Overture divisions {settings.overture_release}")
    countries.upsert_country(conn, data)
    conn.commit()
    ref = countries.get_country(conn, iso2)
    assert ref is not None
    return ref


def enqueue_country(conn, settings: Settings, iso2: str,
                    sources: tuple[str, ...] | None = None) -> dict[str, int]:
    """Create (idempotently) the source jobs and the resolve job for one country."""
    from kasmap.db.jobs import JobRepository  # noqa: PLC0415

    repo = JobRepository(conn)
    country = ensure_country(conn, settings, iso2)
    assert country.db_id is not None
    job_ids: dict[str, int] = {}
    versions: dict[str, str] = {}
    for sid in sources or DEFAULT_SOURCES:
        source = get_source(sid)
        if source.source_id == "osm" and not (country.geofabrik_path
                                              or GEOFABRIK_PATHS.get(country.iso2)):
            log.warning("skip osm for %s: no Geofabrik extract configured", country.iso2)
            continue
        version = source.resolve_version(settings, country)
        versions[source.source_id] = version
        job_ids[source.source_id] = repo.enqueue(
            iso2=country.iso2, country_id=country.db_id, kind="source",
            source_id=source.source_id, source_version=version,
            pipeline_version=settings.pipeline_version,
        )
    resolve_version = "|".join(f"{k}={v}" for k, v in sorted(versions.items()))
    job_ids["resolve"] = repo.enqueue(
        iso2=country.iso2, country_id=country.db_id, kind="resolve", source_id=None,
        source_version=resolve_version, pipeline_version=settings.pipeline_version,
        depends_on=sorted(job_ids.values()),
    )
    conn.commit()
    return job_ids


# ───────────────────────────── run ─────────────────────────────


@dataclass
class Runner:
    conn: Any
    settings: Settings

    @property
    def lake(self) -> storage.DataLake:
        return storage.DataLake(self.settings.data_root)

    def run(self, job) -> dict[str, Any]:
        from kasmap.db.countries import get_country_by_id  # noqa: PLC0415

        country = get_country_by_id(self.conn, job.country_id)
        if job.kind == "source":
            return self._run_source(job, country)
        if job.kind == "resolve":
            return self._run_resolve(job, country)
        raise ValueError(f"Unsupported job kind {job.kind}")

    def _run_source(self, job, country: CountryRef) -> dict[str, Any]:
        from kasmap.db import loader  # noqa: PLC0415
        from kasmap.db.jobs import JobRepository  # noqa: PLC0415

        repo = JobRepository(self.conn)
        source = get_source(job.source_id)
        ctx = JobContext(
            country=country, version=job.source_version, settings=self.settings, lake=self.lake,
            previous_count=loader.previous_row_count(self.conn, source.source_id, job.country_id),
        )
        raw_dir = ctx.lake.raw(source.source_id, ctx.version, ctx.iso2)

        if job.step is None:
            if storage.is_done(raw_dir, STEP_DOWNLOAD):
                info = storage.read_manifest(raw_dir, STEP_DOWNLOAD)
            else:
                info = source.download(ctx)
                storage.mark_done(raw_dir, STEP_DOWNLOAD, info)
            repo.log_step(job.id, STEP_DOWNLOAD, self.settings.worker_id, "ok", info)
            repo.checkpoint(job.id, STEP_DOWNLOAD, stats={"download": info})
            job.step = STEP_DOWNLOAD
        else:
            info = storage.read_manifest(raw_dir, STEP_DOWNLOAD)

        if job.step == STEP_DOWNLOAD:
            if source.produces_places:
                processed_dir = ctx.lake.processed(source.source_id, ctx.version, ctx.iso2)
                if storage.is_done(processed_dir, STEP_PROCESS):  # crashed after writing
                    manifest = storage.read_manifest(processed_dir, STEP_PROCESS)
                    path, report = manifest["path"], manifest["report"]
                else:
                    result = source.process(ctx)
                    path, report = str(result.path), result.report.to_dict()
                dv = loader.register_dataset_version(
                    self.conn, source_id=source.source_id, version=ctx.version,
                    country_id=job.country_id, pipeline_version=self.settings.pipeline_version,
                    raw_uri=info.get("file"), processed_uri=path,
                    checksum=info.get("sha256"), row_count=report["valid"], report=report,
                )
                self.conn.commit()
                repo.set_dataset_version(job.id, dv)
                if not quality_passes(report):
                    raise QualityGateError(
                        f"Quality gate failed for {source.source_id} {ctx.iso2}: "
                        f"score={report['score']} delta={report['delta']}",
                        report,
                    )
                stats = {"process": report}
            else:
                # Admin areas: loaded straight into geo.admin_area.
                dv = loader.register_dataset_version(
                    self.conn, source_id=source.source_id, version=ctx.version,
                    country_id=job.country_id, pipeline_version=self.settings.pipeline_version,
                    raw_uri=info.get("file"), processed_uri=None, checksum=None,
                    row_count=info.get("rows"), report=None,
                )
                n = loader.load_admin_areas(self.conn, ctx.iso2, dv,
                                            source.iter_admin_areas(ctx))  # type: ignore[attr-defined]
                loader.activate_versions(self.conn, [dv])
                self.conn.commit()
                repo.set_dataset_version(job.id, dv)
                stats = {"admin_areas": n}
            repo.log_step(job.id, STEP_PROCESS, self.settings.worker_id, "ok", stats)
            repo.checkpoint(job.id, STEP_PROCESS, stats=stats)
            return stats
        return {}

    def _run_resolve(self, job, country: CountryRef) -> dict[str, Any]:
        from kasmap.db import loader  # noqa: PLC0415
        from kasmap.db.jobs import JobRepository  # noqa: PLC0415

        repo = JobRepository(self.conn)
        deps = self.conn.execute(
            """
            SELECT j.source_id, j.source_version, j.dataset_version_id, dv.processed_uri
              FROM meta.ingest_job j
              JOIN meta.dataset_version dv ON dv.id = j.dataset_version_id
             WHERE j.id = ANY(%s)
            """,
            (job.depends_on,),
        ).fetchall()
        records = []
        dataset_versions: dict[str, int] = {}
        for d in deps:
            if not d["processed_uri"]:
                continue  # divisions: no places
            dataset_versions[d["source_id"]] = d["dataset_version_id"]
            records.extend(storage.read_records(Path(d["processed_uri"])))
        t0 = time.time()
        result = resolve(records, source_priority())
        stats: dict[str, Any] = {"resolve": result.stats,
                                 "resolve_seconds": round(time.time() - t0, 1)}
        log.info("resolved %s: %s", country.iso2, result.stats)
        stats["load"] = loader.load_entities(
            self.conn, iso2=country.iso2, entities=result.entities,
            dataset_versions=dataset_versions,
        )
        stats["review_pairs_saved"] = loader.save_review_pairs(
            self.conn, country.iso2, result.review_pairs
        )
        loader.activate_versions(self.conn, dataset_versions.values())
        loader.mark_country_ingested(self.conn, job.country_id)
        self.conn.commit()  # one transaction: the country switches to the new data atomically
        repo.log_step(job.id, STEP_RESOLVE, self.settings.worker_id, "ok", stats)
        return stats


# ───────────────────────────── worker ─────────────────────────────


def work(settings: Settings, until_empty: bool = True, idle_sleep_s: float = 10.0) -> int:
    """Claim and run jobs.  Returns the number of jobs processed."""
    from kasmap.db.connection import connect  # noqa: PLC0415
    from kasmap.db.jobs import JobRepository  # noqa: PLC0415

    done = 0
    with connect(settings) as conn:
        repo = JobRepository(conn)
        runner = Runner(conn, settings)
        while True:
            job = repo.claim(settings.worker_id, max_attempts=settings.max_attempts)
            if job is None:
                if until_empty:
                    return done
                time.sleep(idle_sleep_s)
                continue
            log.info("job %s (%s) attempt %d from step %s", job.id, job.job_key, job.attempts,
                     job.step)
            try:
                stats = runner.run(job)
                repo.complete(job.id, stats)
                done += 1
            except Exception as exc:  # noqa: BLE001 — every failure is recorded on the job
                error = {"type": type(exc).__name__, "message": str(exc),
                         "traceback": traceback.format_exc(limit=20)}
                if isinstance(exc, QualityGateError):
                    error["report"] = exc.report
                log.error("job %s failed: %s", job.job_key, exc)
                repo.fail(job.id, error)
                repo.log_step(job.id, job.step or STEP_DOWNLOAD, settings.worker_id, "failed",
                              error=error)
