"""Initial schema: meta, geo, poi, feat, score, app.

Revision ID: 0001
Revises:
Create Date: 2026-10-06
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[2] / "schemas" / "0001_initial.sql"


def upgrade() -> None:
    # exec_driver_sql: no SQLAlchemy bind-param parsing of the DDL text
    op.get_bind().exec_driver_sql(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    for schema in ("app", "score", "feat", "poi", "meta", "geo"):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
