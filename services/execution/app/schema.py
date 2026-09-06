"""execution-service tables (docs/08 §7).

An action proposal is durable state, not a message in flight. The record of
what was proposed, what it was approved by, what was actually written to the
core and what came back has to survive the process that did it, or a failure
halfway through leaves nobody able to say whether the money moved.
"""

from __future__ import annotations

from typing import Any

from cio_common.outbox import DDL as OUTBOX_DDL
from cio_common.outbox import ddl_statements

__all__ = ["DDL", "apply_ddl"]

DDL = """
CREATE SCHEMA IF NOT EXISTS app_execution;

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
  -- PROPOSED -> EXECUTING -> EXECUTED, or -> FAILED when the core refused and
  -- the action is waiting to be retried. FAILED is not terminal on purpose:
  -- a half-written activation must be finishable, not abandoned.
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

-- Every step a saga took, so a compensation knows what to undo and an auditor
-- can see the order things happened in.
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


async def apply_ddl(connection: Any) -> None:
    """Create the schema. Idempotent."""
    from sqlalchemy import text

    for statement in ddl_statements(DDL) + ddl_statements(OUTBOX_DDL):
        await connection.execute(text(statement))
