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

-- A member asked for a person, or said something no assistant should be the
-- last responder to. The row is the promise: it exists before the member is
-- told anybody will call, and it is closed by a human rather than by a model.
CREATE TABLE IF NOT EXISTS app_notification.handoff (
  handoff_id  text PRIMARY KEY,
  member_id   text NOT NULL,
  case_id     text,
  -- ROUTINE, or one of the signals the assistant must never handle alone:
  -- HARDSHIP, COMPLAINT, BEREAVEMENT, VULNERABILITY.
  signal      text NOT NULL DEFAULT 'ROUTINE',
  urgency     text NOT NULL DEFAULT 'ROUTINE',
  reason      text NOT NULL,
  -- What the member actually wrote, kept because a paraphrase of a hardship
  -- disclosure loses the part a person needs to read.
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


async def apply_ddl(connection: Any) -> None:
    """Create the schema. Idempotent."""
    from sqlalchemy import text

    for statement in ddl_statements(DDL) + ddl_statements(OUTBOX_DDL):
        await connection.execute(text(statement))
