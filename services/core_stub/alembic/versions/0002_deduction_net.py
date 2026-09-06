"""core: the deduction record carries the reported net pay (docs/07 §1.5)

Revision ID: 0002_core_net
Revises: 0001_core
"""

from __future__ import annotations

from alembic import op

revision = "0002_core_net"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE core.deduction ADD COLUMN IF NOT EXISTS "
               "net_salary numeric(18,2)")


def downgrade() -> None:
    op.execute("ALTER TABLE core.deduction DROP COLUMN IF EXISTS net_salary")
