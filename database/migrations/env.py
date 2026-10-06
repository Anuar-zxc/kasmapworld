"""Alembic environment.  Migrations are plain SQL files under database/schemas/."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from kasmap.config import get_settings
from kasmap.db.connection import sqlalchemy_url


def run_migrations_online() -> None:
    engine = create_engine(sqlalchemy_url(get_settings().database_url))
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline mode is not supported; run against a database.")
run_migrations_online()
