"""decision: append-only ledger, tokens and decision records (docs/04 §5)

Revision ID: 0002_decision
Revises: 0001_decision
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schema import ddl_statements  # noqa: E402

revision = "0002_decision"
down_revision = "0001_decision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in ddl_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ledger_no_update ON ledger.entry")
    op.execute("DROP TABLE IF EXISTS ledger.sample_review")
    op.execute("DROP TABLE IF EXISTS ledger.token")
    op.execute("DROP TABLE IF EXISTS ledger.entry")
    op.execute("DROP TABLE IF EXISTS app_decision.human_decision")
    op.execute("DROP TABLE IF EXISTS app_decision.decision_record")
