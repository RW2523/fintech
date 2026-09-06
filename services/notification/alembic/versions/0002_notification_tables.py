"""notification: messages and outcomes (docs/07 §5)

Revision ID: 0002_notification
Revises: 0001_notification
"""

from __future__ import annotations

from alembic import op

from cio_common.outbox import ddl_statements

revision = "0002_notification"
down_revision = "0001_notification"
branch_labels = None
depends_on = None

TABLES = """
CREATE TABLE IF NOT EXISTS app_notification.message (
  message_id   text PRIMARY KEY,
  member_id    text NOT NULL,
  account_id   text,
  case_id      text,
  template_id  text NOT NULL,
  language     text NOT NULL DEFAULT 'en',
  channel      text NOT NULL,
  subject      text,
  body         text NOT NULL,
  variables    jsonb NOT NULL DEFAULT '{}'::jsonb,
  state        text NOT NULL DEFAULT 'DRAFT',
  approved_by  text,
  approved_at  timestamptz,
  sent_at      timestamptz,
  scheduled_at timestamptz,
  cancelled_at timestamptz,
  cancel_reason text,
  created_by   text NOT NULL DEFAULT 'system',
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS message_by_member ON app_notification.message (member_id, created_at DESC);
CREATE INDEX IF NOT EXISTS message_due ON app_notification.message (scheduled_at)
  WHERE state = 'QUEUED' AND sent_at IS NULL AND cancelled_at IS NULL;

CREATE TABLE IF NOT EXISTS app_notification.outcome (
  outcome_id  text PRIMARY KEY,
  member_id   text NOT NULL,
  message_id  text,
  case_id     text,
  kind        text NOT NULL,
  promise_at  date,
  promise_kept boolean,
  note        text,
  recorded_by text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS outcome_by_member ON app_notification.outcome (member_id, recorded_at DESC);
"""


def upgrade() -> None:
    for statement in ddl_statements(TABLES):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_notification.outcome")
    op.execute("DROP TABLE IF EXISTS app_notification.message")
