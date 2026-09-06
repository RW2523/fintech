"""member_intelligence: partitioned event store and projections (docs/04 §3)

Revision ID: 0002_member
Revises: 0001_member_intel
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schema import ddl_statements, partition_statements  # noqa: E402

revision = "0002_member"
down_revision = "0001_member_intel"
branch_labels = None
depends_on = None

#: The seeded window plus headroom (docs/04 §7): two years before the demo
#: window opens and two after it closes.
_PARTITIONS_FROM = date(2023, 9, 1)
_PARTITION_MONTHS = 60


def upgrade() -> None:
    for statement in ddl_statements():
        op.execute(statement)
    for statement in partition_statements(_PARTITIONS_FROM, _PARTITION_MONTHS):
        op.execute(statement)


def downgrade() -> None:
    for table in ("import_cursor", "member_state", "profile_projection",
                  "member_event"):
        op.execute(f"DROP TABLE IF EXISTS app_member.{table} CASCADE")
