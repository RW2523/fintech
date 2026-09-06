"""feature: registry, immutable snapshots and baselines (docs/04 §4)

Revision ID: 0002_feature
Revises: 0001_feature
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schema import ddl_statements  # noqa: E402

revision = "0002_feature"
down_revision = "0001_feature"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in ddl_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS feature_value_immutable "
               "ON app_feature.feature_value")
    for table in ("baseline", "feature_value", "feature_snapshot", "feature_def"):
        op.execute(f"DROP TABLE IF EXISTS app_feature.{table} CASCADE")
