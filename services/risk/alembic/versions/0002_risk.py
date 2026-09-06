"""risk: recorded model runs (docs/04 §4)

Revision ID: 0002_risk
Revises: 0001_risk
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schema import ddl_statements  # noqa: E402

revision = "0002_risk"
down_revision = "0001_risk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in ddl_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS model_run_immutable ON app_risk.model_run")
    op.execute("DROP TABLE IF EXISTS app_risk.model_run CASCADE")
