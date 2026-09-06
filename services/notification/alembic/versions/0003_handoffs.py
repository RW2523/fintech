"""notification: handoffs to a person (T-071, docs/06 §2.3)

A member assistant that answers everything is worse than one that knows when to
stop. This is the table it stops into.

Revision ID: 0003_notification
Revises: 0002_notification
"""

from __future__ import annotations

from alembic import op

from cio_common.outbox import ddl_statements

revision = "0003_notification"
down_revision = "0002_notification"
branch_labels = None
depends_on = None

TABLES = """
CREATE TABLE IF NOT EXISTS app_notification.handoff (
  handoff_id  text PRIMARY KEY,
  member_id   text NOT NULL,
  case_id     text,
  signal      text NOT NULL DEFAULT 'ROUTINE',
  urgency     text NOT NULL DEFAULT 'ROUTINE',
  reason      text NOT NULL,
  said        text,
  state       text NOT NULL DEFAULT 'OPEN',
  raised_by   text NOT NULL DEFAULT 'member_assistant',
  closed_by   text,
  closed_at   timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS handoff_by_member ON app_notification.handoff (member_id, created_at DESC);
CREATE INDEX IF NOT EXISTS handoff_open ON app_notification.handoff (created_at DESC) WHERE state = 'OPEN';
"""


def upgrade() -> None:
    for statement in ddl_statements(TABLES):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_notification.handoff")
