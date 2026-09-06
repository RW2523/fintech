"""The `app_document` schema (docs/04 §4)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

__all__ = ["DDL", "apply_ddl", "ddl_statements"]

DDL = """
CREATE SCHEMA IF NOT EXISTS app_document;

CREATE TABLE IF NOT EXISTS app_document.document (
  document_id     text PRIMARY KEY,
  case_id         text,
  member_id       text,
  type            text NOT NULL,
  declared_type   text,
  version         int NOT NULL DEFAULT 1,
  object_key      text NOT NULL,
  sha256          text,
  phash           text,
  pages           int NOT NULL DEFAULT 1,
  classified_conf numeric(5,4),
  classifier      text,
  status          text NOT NULL DEFAULT 'UPLOADED',
  needs_human     boolean NOT NULL DEFAULT false,
  uploaded_at     timestamptz NOT NULL DEFAULT now(),
  processed_at    timestamptz
);
CREATE INDEX IF NOT EXISTS document_by_case ON app_document.document (case_id);
CREATE INDEX IF NOT EXISTS document_by_phash ON app_document.document (phash);
CREATE INDEX IF NOT EXISTS document_by_sha ON app_document.document (sha256);

CREATE TABLE IF NOT EXISTS app_document.extraction (
  extraction_id text PRIMARY KEY,
  document_id   text NOT NULL REFERENCES app_document.document ON DELETE CASCADE,
  field         text NOT NULL,
  value         text,
  norm_value    jsonb,
  conf          numeric(5,4) NOT NULL DEFAULT 0,
  page          int NOT NULL DEFAULT 1,
  bbox          numeric[],
  method        text NOT NULL,
  superseded_by text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS extraction_by_document
  ON app_document.extraction (document_id, field);

CREATE TABLE IF NOT EXISTS app_document.finding (
  finding_id   text PRIMARY KEY,
  case_id      text,
  document_id  text,
  code         text NOT NULL,
  severity     text NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
  detail       jsonb NOT NULL DEFAULT '{}',
  evidence_refs jsonb NOT NULL DEFAULT '[]',
  status       text NOT NULL DEFAULT 'OPEN',
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS finding_by_case ON app_document.finding (case_id);
CREATE INDEX IF NOT EXISTS finding_by_document ON app_document.finding (document_id);

-- Synthetic only: what the generator knows the document says, used to score
-- extraction accuracy. Never consulted by the extraction path itself.
CREATE TABLE IF NOT EXISTS app_document.ground_truth (
  document_id text PRIMARY KEY,
  body        jsonb NOT NULL
);
"""


def ddl_statements(ddl: str = DDL) -> list[str]:
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
    for statement in ddl_statements():
        await connection.execute(text(statement))
