"""agent_runtime: member conversations and the outbox (T-071, docs/06 §2.3)

A member assistant without a log is an assistant nobody can be held to. When a
member says "your app told me my payment was fine", the answer has to be a row
saying what it actually told them.

Revision ID: 0002_agent_runtim
Revises: 0001_agent_runtim
"""

from __future__ import annotations

from alembic import op

from cio_common.outbox import DDL as OUTBOX_DDL
from cio_common.outbox import ddl_statements

revision = "0002_agent_runtim"
down_revision = "0001_agent_runtim"
branch_labels = None
depends_on = None

TABLES = """
CREATE TABLE IF NOT EXISTS app_agent.conversation (
  conversation_id text PRIMARY KEY,
  member_id       text NOT NULL,
  channel         text NOT NULL DEFAULT 'WEB',
  started_at      timestamptz NOT NULL DEFAULT now(),
  last_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversation_by_member
  ON app_agent.conversation (member_id, last_at DESC);

CREATE TABLE IF NOT EXISTS app_agent.conversation_turn (
  turn_id         text PRIMARY KEY,
  conversation_id text NOT NULL REFERENCES app_agent.conversation(conversation_id),
  member_id       text NOT NULL,
  -- MEMBER or ASSISTANT. Both sides are kept: an answer without its question
  -- cannot be judged, and a complaint is usually about the pair.
  role            text NOT NULL,
  said            text NOT NULL,
  -- Which tools the answer rested on, so a member who was told a number can be
  -- shown where it came from.
  tools_used      jsonb NOT NULL DEFAULT '[]'::jsonb,
  refusal         jsonb,
  signal          text,
  handoff_id      text,
  at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS turn_by_conversation
  ON app_agent.conversation_turn (conversation_id, at);
CREATE INDEX IF NOT EXISTS turn_by_member
  ON app_agent.conversation_turn (member_id, at DESC);
"""


def upgrade() -> None:
    for statement in ddl_statements(TABLES) + ddl_statements(OUTBOX_DDL):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_agent.conversation_turn")
    op.execute("DROP TABLE IF EXISTS app_agent.conversation")
