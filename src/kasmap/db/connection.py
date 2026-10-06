"""PostgreSQL connections (psycopg 3).  Parameterized queries only — never string-format values."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from kasmap.config import Settings


@contextmanager
def connect(settings: Settings, autocommit: bool = False) -> Iterator:
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    conn = psycopg.connect(settings.database_url, autocommit=autocommit, row_factory=dict_row)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def Jsonb(value):  # noqa: N802 — mirrors psycopg's adapter name
    """Lazy wrapper so modules importing this one stay importable without psycopg."""
    from psycopg.types.json import Jsonb as _Jsonb  # noqa: PLC0415

    return _Jsonb(value)


def sqlalchemy_url(database_url: str) -> str:
    """Alembic/SQLAlchemy URL using the psycopg 3 driver."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix):]
    return database_url
