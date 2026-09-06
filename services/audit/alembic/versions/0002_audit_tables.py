"""audit: the append-only trail and its write-once exports (docs/04 §6)

Revision ID: 0002_audit
Revises: 0001_audit
"""

from __future__ import annotations

from alembic import op

from cio_common.outbox import ddl_statements

revision = "0002_audit"
down_revision = "0001_audit"
branch_labels = None
depends_on = None

TABLES = """
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

CREATE OR REPLACE FUNCTION audit.reject_mutation() RETURNS trigger
  LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit trail is append-only'; END $$;
DROP TRIGGER IF EXISTS audit_no_mutation ON audit.entry;
CREATE TRIGGER audit_no_mutation BEFORE UPDATE OR DELETE ON audit.entry
  FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();

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


def upgrade() -> None:
    # Split with the shared helper, which respects $$-quoted function bodies.
    # A naive split on ";" cuts the append-only trigger in half.
    for statement in ddl_statements(TABLES):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_no_mutation ON audit.entry")
    op.execute("DROP FUNCTION IF EXISTS audit.reject_mutation()")
    op.execute("DROP TABLE IF EXISTS audit.export")
    op.execute("DROP TABLE IF EXISTS audit.entry")
