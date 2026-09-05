"""The `app_application` schema (docs/04 §4).

A CaseSnapshot is immutable once written: the whole decision story hangs off it,
so a trigger refuses to let one be edited or removed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from cio_common.outbox import DDL as OUTBOX_DDL

__all__ = ["DDL", "apply_ddl", "ddl_statements"]

DDL = """
CREATE SCHEMA IF NOT EXISTS app_application;

CREATE TABLE IF NOT EXISTS app_application.application (
  application_id text PRIMARY KEY,
  member_id      text NOT NULL,
  product_code   text NOT NULL,
  amount         numeric(18,2) NOT NULL,
  tenor_months   int NOT NULL,
  purpose        text NOT NULL,
  status         text NOT NULL DEFAULT 'DRAFT',
  created_at     timestamptz NOT NULL DEFAULT now(),
  submitted_at   timestamptz
);
CREATE INDEX IF NOT EXISTS application_by_member
  ON app_application.application (member_id, created_at);

CREATE TABLE IF NOT EXISTS app_application.case (
  case_id             text PRIMARY KEY,
  case_type           text NOT NULL,
  member_id           text NOT NULL,
  application_id      text REFERENCES app_application.application,
  account_id          text,
  state               text NOT NULL DEFAULT 'OPEN',
  opened_at           timestamptz NOT NULL DEFAULT now(),
  closed_at           timestamptz,
  current_snapshot_id text
);
CREATE INDEX IF NOT EXISTS case_by_application
  ON app_application.case (application_id);

CREATE TABLE IF NOT EXISTS app_application.case_snapshot (
  snapshot_id text PRIMARY KEY,
  case_id     text NOT NULL REFERENCES app_application.case,
  version     int NOT NULL,
  body        jsonb NOT NULL,
  hash        text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, version)
);
CREATE INDEX IF NOT EXISTS snapshot_by_case
  ON app_application.case_snapshot (case_id, version);

-- A snapshot is what every later step is judged against, so it is frozen.
CREATE OR REPLACE FUNCTION app_application.reject_snapshot_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'case snapshots are immutable: % is refused', TG_OP;
END $$;

DROP TRIGGER IF EXISTS snapshot_immutable ON app_application.case_snapshot;
CREATE TRIGGER snapshot_immutable BEFORE UPDATE OR DELETE
  ON app_application.case_snapshot
  FOR EACH ROW EXECUTE FUNCTION app_application.reject_snapshot_mutation();
"""


def ddl_statements(ddl: str | None = None) -> list[str]:
    """Split on statement boundaries, respecting $$-quoted function bodies."""
    statements: list[str] = []
    current: list[str] = []
    in_dollar = False
    for line in (ddl if ddl is not None else OUTBOX_DDL + DDL).splitlines():
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
    for statement in ddl_statements():
        await connection.execute(text(statement))
