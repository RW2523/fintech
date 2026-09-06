"""committee: runs and opinions (docs/04 §4)

Revision ID: 0002_committee
Revises: 0001_committee
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schema import ddl_statements  # noqa: E402

revision = "0002_committee"
down_revision = "0001_committee"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in ddl_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS opinion_immutable ON app_committee.opinion")
    for table in ("opinion", "run"):
        op.execute(f"DROP TABLE IF EXISTS app_committee.{table} CASCADE")
