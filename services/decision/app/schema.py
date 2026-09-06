"""The `ledger` schema: append-only and hash-chained (docs/04 §5).

Rows are never updated or deleted. A trigger enforces that at the database
level, so the guarantee does not depend on application code behaving.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

__all__ = ["DDL", "ENTRY_KINDS", "apply_ddl", "ddl_statements"]

ENTRY_KINDS = (
    "SNAPSHOT",
    "COMMITTEE_RUN",
    "OPINION",
    "DECISION_RECORD",
    "HUMAN_DECISION",
    "TOKEN",
    "ACTION",
    "OUTCOME",
    "AUTONOMY_CHANGE",
    "KILL_SWITCH",
    "SAMPLE_REVIEW",
)

DDL = f"""
CREATE SCHEMA IF NOT EXISTS ledger;
CREATE SCHEMA IF NOT EXISTS app_decision;

CREATE TABLE IF NOT EXISTS ledger.entry (
  seq        bigserial PRIMARY KEY,
  entry_id   text UNIQUE NOT NULL,
  kind       text NOT NULL CHECK (kind IN ({", ".join(f"'{k}'" for k in ENTRY_KINDS)})),
  case_id    text,
  member_id  text,
  payload    jsonb NOT NULL,
  hash       text NOT NULL,
  prev_hash  text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS entry_by_case ON ledger.entry (case_id, seq);
CREATE INDEX IF NOT EXISTS entry_by_kind ON ledger.entry (kind, seq);

-- The ledger is append-only. This is enforced here rather than in the service,
-- so no code path and no operator can quietly rewrite history.
CREATE OR REPLACE FUNCTION ledger.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'ledger is append-only: % on ledger.% is refused', TG_OP, TG_TABLE_NAME;
END $$;

DROP TRIGGER IF EXISTS ledger_no_update ON ledger.entry;
CREATE TRIGGER ledger_no_update BEFORE UPDATE OR DELETE ON ledger.entry
  FOR EACH ROW EXECUTE FUNCTION ledger.reject_mutation();

CREATE TABLE IF NOT EXISTS ledger.token (
  token_id           text PRIMARY KEY,
  action_id          text NOT NULL,
  decision_record_id text NOT NULL,
  human_decision_id  text,
  issued_to          jsonb NOT NULL,
  scope              jsonb NOT NULL,
  idempotency_key    text UNIQUE NOT NULL,
  issued_at          timestamptz NOT NULL DEFAULT now(),
  expires_at         timestamptz NOT NULL,
  used_at            timestamptz,
  signature          text NOT NULL
);
CREATE INDEX IF NOT EXISTS token_by_record ON ledger.token (decision_record_id);

CREATE TABLE IF NOT EXISTS ledger.sample_review (
  sample_id          text PRIMARY KEY,
  decision_record_id text NOT NULL,
  assigned_role      text NOT NULL,
  due_at             timestamptz NOT NULL,
  reviewed_by        text,
  verdict            text,
  notes              text,
  created_at         timestamptz NOT NULL DEFAULT now()
);

-- Fast lookup of the current record for a case, without walking the chain.
CREATE TABLE IF NOT EXISTS app_decision.decision_record (
  decision_record_id text PRIMARY KEY,
  case_id            text,
  snapshot_id        text NOT NULL,
  committee_run_id   text,
  recommendation     text NOT NULL,
  route              text NOT NULL,
  required_authority text NOT NULL,
  superseded_by      text,
  body               jsonb NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS record_by_case ON app_decision.decision_record (case_id, created_at);

CREATE TABLE IF NOT EXISTS app_decision.human_decision (
  human_decision_id  text PRIMARY KEY,
  decision_record_id text NOT NULL,
  case_id            text NOT NULL,
  actor_id           text NOT NULL,
  authority_role     text NOT NULL,
  final_action       text NOT NULL,
  override           boolean NOT NULL DEFAULT false,
  body               jsonb NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS human_by_record
  ON app_decision.human_decision (decision_record_id);
"""


def ddl_statements(ddl: str = DDL) -> list[str]:
    """Split on statement boundaries, respecting $$-quoted function bodies."""
    statements: list[str] = []
    current: list[str] = []
    in_dollar = False
    for line in ddl.splitlines():
        if line.count("$$") % 2 == 1:
            in_dollar = not in_dollar
        current.append(line)
        if not in_dollar and line.rstrip().endswith(";"):
            statement = "\n".join(current).strip()
            if statement:
                statements.append(statement.rstrip(";"))
            current = []
    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail.rstrip(";"))
    return statements


async def apply_ddl(connection: Any) -> None:
    from cio_common.outbox import DDL as OUTBOX_DDL

    # The outbox travels with this schema: decision emits the events the audit
    # trail is built from, and a service that cannot write its outbox cannot
    # record what it did.
    for statement in ddl_statements() + ddl_statements(OUTBOX_DDL):
        await connection.execute(text(statement))
