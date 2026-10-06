"""Runtime configuration.

All settings come from environment variables (12-factor). No secrets are hard-coded;
see `.env.example` for the full list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PIPELINE_VERSION = "0.1.0"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: _env(
            "KASMAP_DATABASE_URL", "postgresql://kasmap:kasmap@localhost:5432/kasmap"
        )
        or ""
    )
    # Root of the data lake. Local path today; s3://bucket/prefix later (DuckDB reads both).
    data_root: Path = field(
        default_factory=lambda: Path(_env("KASMAP_DATA_ROOT", "./data") or "./data")
    )
    # Overture release to ingest. Pin explicitly: schemas change between releases.
    overture_release: str = field(
        default_factory=lambda: _env("KASMAP_OVERTURE_RELEASE", "2026-08-19.0") or ""
    )
    overture_s3_root: str = field(
        default_factory=lambda: _env(
            "KASMAP_OVERTURE_S3_ROOT", "s3://overturemaps-us-west-2/release"
        )
        or ""
    )
    overture_s3_region: str = field(
        default_factory=lambda: _env("KASMAP_OVERTURE_S3_REGION", "us-west-2") or ""
    )
    geofabrik_root: str = field(
        default_factory=lambda: _env("KASMAP_GEOFABRIK_ROOT", "https://download.geofabrik.de")
        or ""
    )
    worker_id: str = field(
        default_factory=lambda: _env("KASMAP_WORKER_ID", f"{os.uname().nodename}-{os.getpid()}")
        or "worker"
    )
    max_attempts: int = field(default_factory=lambda: int(_env("KASMAP_MAX_ATTEMPTS", "3") or 3))
    pipeline_version: str = PIPELINE_VERSION


def get_settings() -> Settings:
    return Settings()
