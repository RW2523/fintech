"""The `app_fraud` schema (docs/04 §4).

An assessment is recorded with the findings it produced and the graph it read,
because a decision cites the assessment and an officer later has to see the
same picture the decision was made on.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

__all__ = ["DDL", "apply_ddl", "ddl_statements"]

DDL = """
CREATE SCHEMA IF NOT EXISTS app_fraud;

CREATE TABLE IF NOT EXISTS app_fraud.assessment (
  assessment_id   text PRIMARY KEY,
  case_id         text NOT NULL,
  snapshot_id     text,
  member_id       text NOT NULL,
  level           text NOT NULL,
  integrity_score int NOT NULL,
  calc_id         text NOT NULL,
  graph_ref       text NOT NULL,
  anomaly_score   double precision,
  rules_version   text NOT NULL,
  evidence_refs   jsonb NOT NULL DEFAULT '[]',
  latency_ms      double precision,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS assessment_by_case
  ON app_fraud.assessment (case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS assessment_by_member
  ON app_fraud.assessment (member_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app_fraud.finding (
  finding_id    text NOT NULL,
  assessment_id text NOT NULL REFERENCES app_fraud.assessment
                ON DELETE CASCADE,
  code          text NOT NULL,
  severity      text NOT NULL,
  rule          text NOT NULL,
  advisory      boolean NOT NULL DEFAULT false,
  detail        jsonb NOT NULL DEFAULT '{}',
  members       text[] NOT NULL DEFAULT '{}',
  documents     text[] NOT NULL DEFAULT '{}',
  evidence_refs jsonb NOT NULL DEFAULT '[]',
  PRIMARY KEY (assessment_id, finding_id)
);
CREATE INDEX IF NOT EXISTS finding_by_code ON app_fraud.finding (code, severity);

CREATE TABLE IF NOT EXISTS app_fraud.case_graph (
  graph_ref  text PRIMARY KEY,
  case_id    text NOT NULL,
  member_id  text NOT NULL,
  hops       int NOT NULL,
  nodes      jsonb NOT NULL DEFAULT '[]',
  edges      jsonb NOT NULL DEFAULT '[]',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS case_graph_by_case ON app_fraud.case_graph (case_id);

-- An assessment is what a decision cites, so it is frozen once written.
CREATE OR REPLACE FUNCTION app_fraud.reject_assessment_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'a fraud assessment is immutable: % is refused', TG_OP;
END $$;

DROP TRIGGER IF EXISTS assessment_immutable ON app_fraud.assessment;
CREATE TRIGGER assessment_immutable BEFORE UPDATE ON app_fraud.assessment
  FOR EACH ROW EXECUTE FUNCTION app_fraud.reject_assessment_mutation();

DROP TRIGGER IF EXISTS finding_immutable ON app_fraud.finding;
CREATE TRIGGER finding_immutable BEFORE UPDATE ON app_fraud.finding
  FOR EACH ROW EXECUTE FUNCTION app_fraud.reject_assessment_mutation();
"""


def ddl_statements(ddl: str = DDL) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_body = False
    for line in ddl.splitlines():
        if "$$" in line:
            in_body = not in_body if line.count("$$") == 1 else in_body
        current.append(line)
        if line.rstrip().endswith(";") and not in_body:
            statement = "\n".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


async def apply_ddl(connection: Any) -> None:
    for statement in ddl_statements():
        await connection.execute(text(statement))
