"""lmi: materialised temporal features (docs/07 §4.2)

Revision ID: 0002_lmi
Revises: 0001_lmi
"""

from __future__ import annotations

from alembic import op

from cio_common.outbox import ddl_statements

revision = "0002_lmi"
down_revision = "0001_lmi"
branch_labels = None
depends_on = None

TABLES = """
CREATE TABLE IF NOT EXISTS app_lmi.temporal_features (
  member_id   text NOT NULL,
  as_of       date NOT NULL,
  features    jsonb NOT NULL,
  baselines   jsonb NOT NULL DEFAULT '{}'::jsonb,
  seasonal    jsonb NOT NULL DEFAULT '{}'::jsonb,
  due_events  int NOT NULL DEFAULT 0,
  digest      text NOT NULL,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (member_id, as_of)
);
CREATE INDEX IF NOT EXISTS temporal_by_as_of ON app_lmi.temporal_features (as_of);

CREATE TABLE IF NOT EXISTS app_lmi.materialisation (
  run_id      text PRIMARY KEY,
  as_of       date NOT NULL,
  members     int NOT NULL DEFAULT 0,
  computed    int NOT NULL DEFAULT 0,
  skipped     int NOT NULL DEFAULT 0,
  failed      int NOT NULL DEFAULT 0,
  seconds     double precision NOT NULL DEFAULT 0,
  detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS materialisation_by_as_of ON app_lmi.materialisation (as_of DESC);
"""


def upgrade() -> None:
    for statement in ddl_statements(TABLES):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_lmi.temporal_features")
    op.execute("DROP TABLE IF EXISTS app_lmi.materialisation")
