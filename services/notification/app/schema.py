"""notification-service tables (docs/07 §5, docs/08 §5).

Messages to members are durable state, not fire-and-forget. A member who says
"nobody told me" deserves an answer, and the answer is a row: what was sent, in
which language, on which channel, and when.

Drafts are stored too. An outreach an officer is composing is work in progress
that must survive them closing the tab, and one they approved is a record of a
decision to contact somebody.
"""

from __future__ import annotations

from typing import Any

from cio_common.outbox import DDL as OUTBOX_DDL
from cio_common.outbox import ddl_statements

__all__ = ["DDL", "apply_ddl"]

DDL = """
CREATE SCHEMA IF NOT EXISTS app_notification;

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
  -- DRAFT until somebody approves it, then QUEUED, then SENT. A message that
  -- was never approved must be distinguishable from one that was: the first is
  -- a suggestion and the second is something the cooperative said.
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

-- What a member did about a contact. The point of an outreach is the answer,
-- and a platform that records what it sent but not what came back cannot tell
-- whether any of it works.
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


async def apply_ddl(connection: Any) -> None:
    """Create the schema. Idempotent."""
    from sqlalchemy import text

    for statement in ddl_statements(DDL) + ddl_statements(OUTBOX_DDL):
        await connection.execute(text(statement))
