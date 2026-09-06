"""document: documents, extractions, findings (docs/04 §4)

Revision ID: 0002_document
Revises: 0001_document
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schema import ddl_statements  # noqa: E402

revision = "0002_document"
down_revision = "0001_document"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in ddl_statements():
        op.execute(statement)


def downgrade() -> None:
    for table in ("ground_truth", "finding", "extraction", "document"):
        op.execute(f"DROP TABLE IF EXISTS app_document.{table} CASCADE")
