"""core schema: systems-of-record stub (docs/04 §2)

Revision ID: 0001_core
Revises:
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schema import DROP, ddl_statements  # noqa: E402

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in ddl_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute(DROP)
