"""application: applications, cases, immutable snapshots and the outbox

Revision ID: 0002_application
Revises: 0001_application
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schema import ddl_statements  # noqa: E402

revision = "0002_application"
down_revision = "0001_application"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in ddl_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS snapshot_immutable ON app_application.case_snapshot")
    for table in ("case_snapshot", "case", "application"):
        op.execute(f"DROP TABLE IF EXISTS app_application.{table} CASCADE")
