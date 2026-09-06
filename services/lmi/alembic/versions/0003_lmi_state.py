"""lmi: member state, transitions and alerts (docs/07 §4.5, §4.7)

Revision ID: 0003_lmi
Revises: 0002_lmi
"""

from __future__ import annotations

from alembic import op

from cio_common.outbox import ddl_statements

revision = "0003_lmi"
down_revision = "0002_lmi"
branch_labels = None
depends_on = None

TABLES = """
CREATE TABLE IF NOT EXISTS app_lmi.member_state (
  member_id  text PRIMARY KEY,
  state      text NOT NULL,
  since      date NOT NULL,
  rule       text NOT NULL,
  reason     text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_lmi.state_transition (
  transition_id text PRIMARY KEY,
  member_id     text NOT NULL,
  at            date NOT NULL,
  from_state    text NOT NULL,
  to_state      text NOT NULL,
  rule          text NOT NULL,
  reason        text NOT NULL,
  corroboration jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence_ids  jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS transition_by_member ON app_lmi.state_transition (member_id, at DESC);

CREATE TABLE IF NOT EXISTS app_lmi.alert (
  alert_id      text PRIMARY KEY,
  member_id     text NOT NULL,
  account_id    text,
  state         text NOT NULL,
  signals       jsonb NOT NULL DEFAULT '[]'::jsonb,
  rank_value    double precision NOT NULL DEFAULT 0,
  why_now       text NOT NULL,
  p90           double precision,
  exposure      numeric(18,2) NOT NULL DEFAULT 0,
  change_point  date,
  corroboration jsonb NOT NULL DEFAULT '{}'::jsonb,
  case_id       text,
  opened_at     date NOT NULL DEFAULT current_date,
  closed_at     date,
  close_reason  text
);
CREATE INDEX IF NOT EXISTS alert_open ON app_lmi.alert (rank_value DESC) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS alert_by_member ON app_lmi.alert (member_id, opened_at DESC);
"""


def upgrade() -> None:
    for statement in ddl_statements(TABLES):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_lmi.alert")
    op.execute("DROP TABLE IF EXISTS app_lmi.state_transition")
    op.execute("DROP TABLE IF EXISTS app_lmi.member_state")
