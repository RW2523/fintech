"""The `app_risk` schema (docs/04 §4).

Every score is recorded. A DecisionRecord cites a `model_run_id`, and the
question "what did the model say, on what, with which version" has to be
answerable years later without rerunning anything.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

__all__ = ["DDL", "apply_ddl", "ddl_statements"]

DDL = """
CREATE SCHEMA IF NOT EXISTS app_risk;

CREATE TABLE IF NOT EXISTS app_risk.model_run (
  model_run_id     text PRIMARY KEY,
  snapshot_id      text NOT NULL,
  member_id        text NOT NULL,
  model_version    text NOT NULL,
  inputs_digest    text NOT NULL,
  champion_pd      double precision NOT NULL,
  champion_grade   text NOT NULL,
  challenger_pd    double precision NOT NULL,
  challenger_grade text NOT NULL,
  ood_score        double precision,
  conduct_score    int NOT NULL,
  conduct_calc_id  text NOT NULL,
  reason_codes     text[] NOT NULL DEFAULT '{}',
  drivers          jsonb NOT NULL DEFAULT '[]',
  evidence_refs    jsonb NOT NULL DEFAULT '[]',
  latency_ms       double precision,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS model_run_by_snapshot
  ON app_risk.model_run (snapshot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS model_run_by_member
  ON app_risk.model_run (member_id, created_at DESC);

-- A recorded score is what a decision cites, so it is frozen once written.
CREATE OR REPLACE FUNCTION app_risk.reject_run_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'a model run is immutable: % is refused', TG_OP;
END $$;

DROP TRIGGER IF EXISTS model_run_immutable ON app_risk.model_run;
CREATE TRIGGER model_run_immutable BEFORE UPDATE ON app_risk.model_run
  FOR EACH ROW EXECUTE FUNCTION app_risk.reject_run_mutation();
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
