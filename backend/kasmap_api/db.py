"""Database access for the API (Neon via the Vercel integration)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager


def database_url(admin: bool = False) -> str | None:
    """Pooled URL for requests; direct (unpooled) URL for schema changes and bulk loads."""
    keys = ["KASMAP_DATABASE_URL", "DATABASE_URL"]
    if admin:
        keys = ["DATABASE_URL_UNPOOLED", "POSTGRES_URL_NON_POOLING", *keys]
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None


class NotConfigured(RuntimeError):
    pass


@contextmanager
def connect(admin: bool = False, timeout: int = 10) -> Iterator:
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    url = database_url(admin)
    if not url:
        raise NotConfigured("database is not configured")
    conn = psycopg.connect(url, row_factory=dict_row, connect_timeout=timeout)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
