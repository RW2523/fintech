"""fraud: assessments, findings and case graphs (docs/04 §4)

Revision ID: 0002_fraud
Revises: 0001_fraud
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schema import ddl_statements  # noqa: E402

revision = "0002_fraud"
down_revision = "0001_fraud"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in ddl_statements():
        op.execute(statement)


def downgrade() -> None:
    for trigger, table in (("assessment_immutable", "assessment"),
                           ("finding_immutable", "finding")):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON app_fraud.{table}")
    for table in ("case_graph", "finding", "assessment"):
        op.execute(f"DROP TABLE IF EXISTS app_fraud.{table} CASCADE")
