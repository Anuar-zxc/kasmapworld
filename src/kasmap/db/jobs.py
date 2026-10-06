"""Ingestion job queue on PostgreSQL (`meta.ingest_job`).

Workers claim jobs with `FOR UPDATE SKIP LOCKED`: parallel, restartable, no broker.
A job whose worker died (lock older than `stale_after`) is reclaimed and resumes from
its last completed step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kasmap.db.connection import Jsonb

STATUSES = ("PENDING", "DOWNLOADING", "PROCESSING", "LOADED", "FAILED")


def job_key(iso2: str, kind: str, source_id: str | None, version: str | None, pipeline: str) -> str:
    return ":".join([iso2.upper(), kind, source_id or "-", version or "-", pipeline])


@dataclass
class Job:
    id: int
    job_key: str
    kind: str
    country_id: int
    source_id: str | None
    source_version: str | None
    processing_version: str
    depends_on: list[int]
    status: str
    step: str | None
    attempts: int
    dataset_version_id: int | None
    stats: dict[str, Any]

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Job:
        return cls(**{k: row[k] for k in cls.__dataclass_fields__})


class JobRepository:
    def __init__(self, conn) -> None:
        self.conn = conn

    # ── enqueue ──
    def enqueue(
        self,
        *,
        iso2: str,
        country_id: int,
        kind: str,
        source_id: str | None,
        source_version: str | None,
        pipeline_version: str,
        depends_on: list[int] | None = None,
    ) -> int:
        """Idempotent: the same key returns the existing job id."""
        key = job_key(iso2, kind, source_id, source_version, pipeline_version)
        row = self.conn.execute(
            """
            INSERT INTO meta.ingest_job
                (job_key, kind, country_id, source_id, source_version, processing_version, depends_on)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (job_key) DO UPDATE SET job_key = EXCLUDED.job_key
            RETURNING id
            """,
            (key, kind, country_id, source_id, source_version, pipeline_version, depends_on or []),
        ).fetchone()
        return int(row["id"])

    # ── claim ──
    def claim(self, worker: str, stale_after_s: int = 6 * 3600, max_attempts: int = 3) -> Job | None:
        row = self.conn.execute(
            """
            WITH candidate AS (
                SELECT j.id
                FROM meta.ingest_job j
                WHERE (
                        j.status = 'PENDING'
                     OR (j.status IN ('DOWNLOADING','PROCESSING')
                         AND j.locked_at < now() - make_interval(secs => %(stale)s))
                      )
                  AND j.attempts < %(max_attempts)s
                  AND NOT EXISTS (
                        SELECT 1 FROM meta.ingest_job d
                        WHERE d.id = ANY (j.depends_on) AND d.status <> 'LOADED')
                ORDER BY (j.kind <> 'source'), j.created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE meta.ingest_job j
               SET locked_by  = %(worker)s,
                   locked_at  = now(),
                   attempts   = j.attempts + 1,
                   started_at = COALESCE(j.started_at, now()),
                   status     = CASE WHEN j.step IS NULL THEN 'DOWNLOADING'
                                     ELSE 'PROCESSING' END::meta.ingest_status,
                   error      = NULL
              FROM candidate
             WHERE j.id = candidate.id
            RETURNING j.*
            """,
            {"stale": stale_after_s, "worker": worker, "max_attempts": max_attempts},
        ).fetchone()
        self.conn.commit()
        return Job.from_row(row) if row else None

    # ── progress ──
    def checkpoint(self, job_id: int, step: str, status: str = "PROCESSING",
                   stats: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            """
            UPDATE meta.ingest_job
               SET step = %s, status = %s::meta.ingest_status, locked_at = now(),
                   stats = stats || %s
             WHERE id = %s
            """,
            (step, status, Jsonb(stats or {}), job_id),
        )
        self.conn.commit()

    def set_dataset_version(self, job_id: int, dataset_version_id: int) -> None:
        self.conn.execute(
            "UPDATE meta.ingest_job SET dataset_version_id = %s WHERE id = %s",
            (dataset_version_id, job_id),
        )
        self.conn.commit()

    def complete(self, job_id: int, stats: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            """
            UPDATE meta.ingest_job
               SET status = 'LOADED', step = 'done', finished_at = now(),
                   locked_by = NULL, locked_at = NULL, stats = stats || %s
             WHERE id = %s
            """,
            (Jsonb(stats or {}), job_id),
        )
        self.conn.commit()

    def fail(self, job_id: int, error: dict[str, Any]) -> None:
        self.conn.rollback()
        self.conn.execute(
            """
            UPDATE meta.ingest_job
               SET status = 'FAILED', error = %s, locked_by = NULL, locked_at = NULL
             WHERE id = %s
            """,
            (Jsonb(error), job_id),
        )
        self.conn.commit()

    def retry_failed(self, reset_attempts: bool = False) -> int:
        cur = self.conn.execute(
            """
            UPDATE meta.ingest_job
               SET status = 'PENDING',
                   attempts = CASE WHEN %s THEN 0 ELSE attempts END
             WHERE status = 'FAILED'
            """,
            (reset_attempts,),
        )
        self.conn.commit()
        return cur.rowcount

    def log_step(self, job_id: int, step: str, worker: str, status: str,
                 stats: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO meta.pipeline_run (job_id, step, worker, status, stats, error, finished_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            """,
            (job_id, step, worker, status, Jsonb(stats or {}), Jsonb(error) if error else None),
        )
        self.conn.commit()

    # ── reporting ──
    def summary(self) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT status, count(*) AS n FROM meta.coverage_by_country GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["n"] for r in rows}
        total = self.conn.execute("SELECT count(*) AS n FROM geo.country").fetchone()["n"]
        failed = self.conn.execute(
            """
            SELECT j.job_key, j.attempts, j.error->>'message' AS message
            FROM meta.ingest_job j WHERE j.status = 'FAILED' ORDER BY j.id
            """
        ).fetchall()
        return {"countries": total, "by_status": by_status, "failed_jobs": failed}
