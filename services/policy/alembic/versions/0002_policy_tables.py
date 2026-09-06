"""policy: versions, calcs, kill switch, sandbox and replay inputs (docs/04 §4)

Revision ID: 0002_policy
Revises: 0001_policy
"""

from __future__ import annotations

from alembic import op

from app.schema import DDL as TABLES

revision = "0002_policy"
down_revision = "0001_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from cio_common.outbox import ddl_statements

    for statement in ddl_statements(TABLES):
        op.execute(statement)


def downgrade() -> None:
    for table in ("replay_case", "sandbox_run", "kill_switch", "calc", "policy_version"):
        op.execute(f"DROP TABLE IF EXISTS app_policy.{table}")
