"""Data lake layout (Blueprint §2, World Coverage Engine §4).

    {root}/raw/{source}/{version}/{ISO2}/...          untouched downloads
    {root}/processed/{source}/{version}/{ISO2}/...    validated, normalized PlaceRecords
    {root}/processed/resolved/{pipeline}/{ISO2}/...   canonical entities after dedup
    {root}/features/h3/...                             feature exports (later phases)

Paths are deterministic, so a restarted step finds the work of the previous attempt.
Every completed artifact gets a `_SUCCESS` manifest; a step whose manifest exists is
skipped.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kasmap.processing.records import PlaceRecord


@dataclass(frozen=True)
class DataLake:
    root: Path

    def raw(self, source: str, version: str, iso2: str) -> Path:
        return self._dir("raw", source, version, iso2.upper())

    def processed(self, source: str, version: str, iso2: str) -> Path:
        return self._dir("processed", source, version, iso2.upper())

    def resolved(self, pipeline_version: str, iso2: str) -> Path:
        return self._dir("processed", "resolved", pipeline_version, iso2.upper())

    def features(self, *parts: str) -> Path:
        return self._dir("features", *parts)

    def _dir(self, *parts: str) -> Path:
        p = self.root.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


# ─────────────────────────── manifests ───────────────────────────


def mark_done(directory: Path, step: str, info: dict[str, Any] | None = None) -> None:
    (directory / f"_SUCCESS.{step}.json").write_text(
        json.dumps(info or {}, ensure_ascii=False, default=str, indent=2)
    )


def is_done(directory: Path, step: str) -> bool:
    return (directory / f"_SUCCESS.{step}.json").exists()


def read_manifest(directory: Path, step: str) -> dict[str, Any]:
    path = directory / f"_SUCCESS.{step}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


# ─────────────────────────── record files ────────────────────────
# Parquet in production.  Gzipped JSON lines is a fallback used only when pyarrow is
# unavailable (unit tests in minimal environments).

_JSON_FIELDS = ("address", "opening_hours", "raw")


def _have_pyarrow() -> bool:
    try:
        import pyarrow  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def records_path(directory: Path, name: str) -> Path:
    return directory / (f"{name}.parquet" if _have_pyarrow() else f"{name}.jsonl.gz")


def write_records(path: Path, records: Iterable[PlaceRecord]) -> int:
    rows = [r.to_dict() for r in records]
    tmp = path.with_name(path.name + ".tmp")
    if path.suffix == ".parquet":
        import pyarrow as pa  # noqa: PLC0415
        import pyarrow.parquet as pq  # noqa: PLC0415

        for row in rows:
            for key in _JSON_FIELDS:
                row[key] = json.dumps(row[key], ensure_ascii=False, default=str) \
                    if row[key] is not None else None
        table = pa.Table.from_pylist(rows) if rows else pa.table({})
        pq.write_table(table, tmp, compression="zstd")
    else:
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    tmp.replace(path)  # atomic: a crash never leaves a half-written file under the real name
    return len(rows)


def read_records(path: Path) -> Iterator[PlaceRecord]:
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq  # noqa: PLC0415

        for batch in pq.ParquetFile(path).iter_batches(batch_size=50_000):
            for row in batch.to_pylist():
                for key in _JSON_FIELDS:
                    if isinstance(row.get(key), str):
                        row[key] = json.loads(row[key])
                yield PlaceRecord.from_dict(row)
    else:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                yield PlaceRecord.from_dict(json.loads(line))
