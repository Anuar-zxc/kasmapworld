"""`kasmap` command line.

    kasmap db migrate | seed
    kasmap registry sync [--countries KZ,DE]
    kasmap ingest country KZ [--source overture]
    kasmap ingest region test-5
    kasmap ingest world
    kasmap update
    kasmap worker [-c 4] [--forever]
    kasmap retry-failed [--reset-attempts]
    kasmap status
    kasmap categories unmapped KZ
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import sys
from collections import Counter
from pathlib import Path

import click

from kasmap.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
        stream=sys.stderr,
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True)
def main(verbose: bool) -> None:
    """KasMap data platform."""
    _setup_logging(verbose)


# ───────────────────────────── db ─────────────────────────────


@main.group()
def db() -> None:
    """Database schema and seed data."""


@db.command("migrate")
def db_migrate() -> None:
    """Apply Alembic migrations (alembic upgrade head)."""
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "database" / "migrations"))
    command.upgrade(cfg, "head")
    click.echo("migrated")


@db.command("seed")
def db_seed() -> None:
    """Write sources, category taxonomy and category mappings."""
    from psycopg.types.json import Jsonb  # noqa: PLC0415

    from kasmap.db.connection import connect  # noqa: PLC0415
    from kasmap.db.loader import upsert_sources  # noqa: PLC0415
    from kasmap.ingestion.sources import SOURCE_METAS  # noqa: PLC0415
    from kasmap.processing.categories import OSM_MAP, OVERTURE_MAP, TAXONOMY  # noqa: PLC0415

    with connect(get_settings()) as conn:
        upsert_sources(conn, SOURCE_METAS.values())
        for c in TAXONOMY:  # parents come first in TAXONOMY
            conn.execute(
                """
                INSERT INTO poi.category (id, parent_id, name) VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET parent_id = EXCLUDED.parent_id, name = EXCLUDED.name
                """,
                (c.id, c.parent_id, Jsonb({"ru": c.ru, "kk": c.kk, "en": c.en})),
            )
        for source_id, mapping in (("overture_places", OVERTURE_MAP), ("osm", OSM_MAP)):
            for raw, cat in mapping.items():
                conn.execute(
                    """
                    INSERT INTO poi.category_map (source_id, source_category, category_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (source_id, source_category)
                    DO UPDATE SET category_id = EXCLUDED.category_id
                    """,
                    (source_id, raw, cat),
                )
    click.echo(f"seeded {len(SOURCE_METAS)} sources, {len(TAXONOMY)} categories")


# ───────────────────────────── registry ─────────────────────────────


@main.group()
def registry() -> None:
    """Country registry (geo.country)."""


@registry.command("sync")
@click.option("--countries", default=None, help="Comma-separated ISO2 codes; default: all")
def registry_sync(countries: str | None) -> None:
    """Load country polygons and names from Overture divisions."""
    from kasmap.db.connection import connect  # noqa: PLC0415
    from kasmap.db.countries import upsert_country  # noqa: PLC0415
    from kasmap.ingestion.sources import overture  # noqa: PLC0415

    settings = get_settings()
    con = overture.connect(settings)
    if countries:
        rows = [overture.fetch_country(con, settings, settings.overture_release, c.strip())
                for c in countries.split(",")]
        missing = [c for c, r in zip(countries.split(","), rows, strict=True) if r is None]
        if missing:
            raise click.ClickException(f"Not found in Overture: {', '.join(missing)}")
    else:
        rows = overture.list_countries(con, settings, settings.overture_release)
    with connect(settings) as conn:
        for r in rows:
            upsert_country(conn, r)
    click.echo(f"registry: {len(rows)} countries synced from Overture {settings.overture_release}")


# ───────────────────────────── ingest ─────────────────────────────


@main.group()
def ingest() -> None:
    """Queue ingestion jobs (run them with `kasmap worker`)."""


def _enqueue(iso2_list: list[str], sources: tuple[str, ...] | None, run: bool) -> None:
    from kasmap.db.connection import connect  # noqa: PLC0415
    from kasmap.ingestion.orchestrator import enqueue_country, work  # noqa: PLC0415

    settings = get_settings()
    with connect(settings) as conn:
        for iso2 in iso2_list:
            ids = enqueue_country(conn, settings, iso2.upper(), sources)
            click.echo(f"{iso2.upper()}: jobs {ids}")
    if run:
        n = work(settings)
        click.echo(f"processed {n} jobs")


@ingest.command("country")
@click.argument("iso2")
@click.option("--source", "sources", multiple=True,
              help="overture | osm | divisions (repeatable). Default: all")
@click.option("--run/--no-run", default=True, help="Run a worker right away")
def ingest_country(iso2: str, sources: tuple[str, ...], run: bool) -> None:
    _enqueue([iso2], sources or None, run)


@ingest.command("region")
@click.argument("name")
@click.option("--run/--no-run", default=False)
def ingest_region(name: str, run: bool) -> None:
    from kasmap.ingestion.regions import REGION_GROUPS  # noqa: PLC0415

    if name not in REGION_GROUPS:
        raise click.ClickException(f"Unknown region '{name}'. Known: {sorted(REGION_GROUPS)}")
    _enqueue(list(REGION_GROUPS[name]), None, run)


@ingest.command("world")
@click.option("--source", "sources", multiple=True)
def ingest_world(sources: tuple[str, ...]) -> None:
    """Queue every country in the registry (run `registry sync` first)."""
    from kasmap.db.connection import connect  # noqa: PLC0415
    from kasmap.db.countries import all_iso2  # noqa: PLC0415

    with connect(get_settings()) as conn:
        codes = all_iso2(conn)
    if not codes:
        raise click.ClickException("Registry is empty: run `kasmap registry sync`")
    if not sources:
        # OSM extracts are mapped only for onboarded countries; Overture covers the world.
        sources = ("divisions", "overture")
    _enqueue(codes, sources, run=False)
    click.echo(f"queued {len(codes)} countries; start workers with `kasmap worker -c N`")


@main.command("update")
def update() -> None:
    """Re-queue every ingested country at the configured (newer) releases."""
    from kasmap.db.connection import connect  # noqa: PLC0415

    with connect(get_settings()) as conn:
        codes = [r["iso2"] for r in conn.execute(
            "SELECT iso2 FROM geo.country WHERE last_ingested_at IS NOT NULL ORDER BY iso2")]
    _enqueue(codes, None, run=False)


# ───────────────────────────── workers & status ─────────────────────────────


def _worker_proc(forever: bool) -> int:
    from kasmap.ingestion.orchestrator import work  # noqa: PLC0415

    _setup_logging(False)
    return work(get_settings(), until_empty=not forever)


@main.command("worker")
@click.option("-c", "--concurrency", default=1, show_default=True)
@click.option("--forever", is_flag=True, help="Keep polling when the queue is empty")
def worker(concurrency: int, forever: bool) -> None:
    """Run ingestion workers (each process claims jobs with SKIP LOCKED)."""
    if concurrency == 1:
        click.echo(f"processed {_worker_proc(forever)} jobs")
        return
    ctx = mp.get_context("spawn")
    with ctx.Pool(concurrency) as pool:
        counts = pool.map(_worker_proc, [forever] * concurrency)
    click.echo(f"processed {sum(counts)} jobs")


@main.command("retry-failed")
@click.option("--reset-attempts", is_flag=True)
def retry_failed(reset_attempts: bool) -> None:
    from kasmap.db.connection import connect  # noqa: PLC0415
    from kasmap.db.jobs import JobRepository  # noqa: PLC0415

    with connect(get_settings()) as conn:
        n = JobRepository(conn).retry_failed(reset_attempts)
    click.echo(f"{n} jobs re-queued")


@main.command("status")
@click.option("--json", "as_json", is_flag=True)
def status(as_json: bool) -> None:
    """Global coverage summary."""
    from kasmap.db.connection import connect  # noqa: PLC0415
    from kasmap.db.jobs import JobRepository  # noqa: PLC0415

    with connect(get_settings()) as conn:
        s = JobRepository(conn).summary()
        totals = conn.execute(
            """
            SELECT (SELECT count(*) FROM poi.place)                         AS places,
                   (SELECT count(*) FROM geo.admin_area WHERE level = 'city') AS cities,
                   (SELECT count(DISTINCT h3_r9) FROM poi.place)            AS h3_cells,
                   (SELECT count(*) FROM feat.dirty_cell)                   AS dirty_cells
            """
        ).fetchone()
        fresh = conn.execute("SELECT * FROM meta.source_freshness ORDER BY source_id").fetchall()
    if as_json:
        click.echo(json.dumps({**s, "totals": totals, "freshness": fresh}, default=str, indent=2))
        return
    by = s["by_status"]
    click.echo(f"Countries: {s['countries']}")
    for key in ("LOADED", "PROCESSING", "PENDING", "FAILED", "NOT_QUEUED"):
        click.echo(f"  {key.title():<11} {by.get(key, 0)}")
    click.echo(f"Places: {totals['places']}   Cities: {totals['cities']}   "
               f"H3 cells (r9): {totals['h3_cells']}   Dirty cells: {totals['dirty_cells']}")
    for f in fresh:
        click.echo(f"Freshness {f['source_id']}: {f['latest_version']} "
                   f"({int(f['days_since_ingest'] or 0)} days ago)")
    for j in s["failed_jobs"]:
        click.echo(f"FAILED {j['job_key']} (attempts {j['attempts']}): {j['message']}")


# ───────────────────────────── categories ─────────────────────────────


@main.group()
def categories() -> None:
    """Category mapping tools."""


@categories.command("unmapped")
@click.argument("iso2")
@click.option("--top", default=40, show_default=True)
def categories_unmapped(iso2: str, top: int) -> None:
    """Most frequent source categories that have no KasMap mapping yet."""
    from kasmap.ingestion import storage  # noqa: PLC0415

    settings = get_settings()
    root = settings.data_root / "processed"
    files = [p for p in root.glob(f"*/*/{iso2.upper()}/places.*") if "resolved" not in p.parts]
    if not files:
        raise click.ClickException(f"No processed files for {iso2} under {root}")
    for f in files:
        counts: Counter[str] = Counter()
        total = 0
        for r in storage.read_records(f):
            total += 1
            if r.category_id is None:
                counts[r.source_category or "<none>"] += 1
        unmapped = sum(counts.values())
        click.echo(f"\n{f} — unmapped {unmapped}/{total} ({unmapped / max(total, 1):.0%})")
        for cat, n in counts.most_common(top):
            click.echo(f"  {n:>7}  {cat}")


if __name__ == "__main__":
    main()
