"""audit-service tables (docs/04 §6).

The same shape as the Decision Ledger and for the same reason: an append-only
chain where each row's hash covers the one before it, so a row altered out of
band cannot be made to fit again without rewriting everything after it.
"""

from __future__ import annotations

from typing import Any

from cio_common.outbox import DDL as OUTBOX_DDL
from cio_common.outbox import ddl_statements

__all__ = ["DDL", "apply_ddl"]

DDL = """
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS audit.entry (
  seq            bigserial PRIMARY KEY,
  entry_id       text UNIQUE NOT NULL,
  actor          jsonb NOT NULL,
  action         text NOT NULL,
  service        text NOT NULL,
  case_id        text,
  run_id         text,
  object_ref     jsonb NOT NULL DEFAULT '{}'::jsonb,
  before         jsonb,
  after          jsonb,
  policy_version text,
  model_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
  trace_id       text,
  ip             text,
  hash           text NOT NULL,
  prev_hash      text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_by_case ON audit.entry (case_id, seq);
CREATE INDEX IF NOT EXISTS audit_by_action ON audit.entry (action, created_at);
CREATE INDEX IF NOT EXISTS audit_by_actor ON audit.entry ((actor ->> 'id'), created_at);

-- The same protection the ledger has. An UPDATE or DELETE is refused by the
-- database, so tampering has to be a deliberate act against Postgres itself
-- rather than a stray query.
CREATE OR REPLACE FUNCTION audit.reject_mutation() RETURNS trigger
  LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit trail is append-only'; END $$;
DROP TRIGGER IF EXISTS audit_no_mutation ON audit.entry;
CREATE TRIGGER audit_no_mutation BEFORE UPDATE OR DELETE ON audit.entry
  FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();

-- What has already been written to the WORM bucket, so a re-run does not
-- export the same day twice and a gap is visible as a missing row.
CREATE TABLE IF NOT EXISTS audit.export (
  export_id   text PRIMARY KEY,
  day         date UNIQUE NOT NULL,
  first_seq   bigint NOT NULL,
  last_seq    bigint NOT NULL,
  entries     int NOT NULL,
  object_key  text NOT NULL,
  sha256      text NOT NULL,
  head_hash   text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
"""


async def apply_ddl(connection: Any) -> None:
    """Create the schema. Idempotent."""
    from sqlalchemy import text

    for statement in ddl_statements(DDL) + ddl_statements(OUTBOX_DDL):
        await connection.execute(text(statement))
