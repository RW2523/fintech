"""governance: service schema (docs/04)

Revision ID: 0001_governance
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_governance"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = ['app_governance']


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")


def downgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
