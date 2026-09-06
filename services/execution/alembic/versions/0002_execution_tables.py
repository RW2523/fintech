"""execution: action proposals and saga steps (docs/08 §7)

Revision ID: 0002_execution
Revises: 0001_execution
"""

from __future__ import annotations

from alembic import op

revision = "0002_execution"
down_revision = "0001_execution"
branch_labels = None
depends_on = None

TABLES = """
CREATE TABLE IF NOT EXISTS app_execution.action (
  action_id          text PRIMARY KEY,
  case_id            text,
  member_id          text,
  decision_record_id text,
  human_decision_id  text,
  level              text NOT NULL,
  type               text NOT NULL,
  parameters         jsonb NOT NULL DEFAULT '{}'::jsonb,
  rationale          jsonb NOT NULL DEFAULT '{}'::jsonb,
  requires           text NOT NULL DEFAULT 'OFFICER',
  proposed_by        text NOT NULL,
  state              text NOT NULL DEFAULT 'PROPOSED',
  token_id           text,
  idempotency_key    text,
  core_refs          jsonb NOT NULL DEFAULT '[]'::jsonb,
  result             jsonb,
  attempts           int NOT NULL DEFAULT 0,
  last_error         text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  executed_at        timestamptz
);
CREATE INDEX IF NOT EXISTS action_by_case ON app_execution.action (case_id, created_at);
CREATE INDEX IF NOT EXISTS action_by_state ON app_execution.action (state)
  WHERE state IN ('PROPOSED', 'EXECUTING', 'FAILED');

CREATE TABLE IF NOT EXISTS app_execution.saga_step (
  step_id     text PRIMARY KEY,
  action_id   text NOT NULL REFERENCES app_execution.action (action_id),
  attempt     int NOT NULL,
  name        text NOT NULL,
  state       text NOT NULL,
  request     jsonb,
  response    jsonb,
  error       text,
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS step_by_action ON app_execution.saga_step (action_id, started_at);
"""


def upgrade() -> None:
    for statement in [s.strip() for s in TABLES.split(";") if s.strip()]:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_execution.saga_step")
    op.execute("DROP TABLE IF EXISTS app_execution.action")
