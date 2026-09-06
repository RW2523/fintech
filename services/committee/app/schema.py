"""The `app_committee` schema (docs/04 §4).

A run and its opinions are kept because a DecisionRecord cites them. An
opinion is written as it arrives rather than at the end, so a run that times
out still shows what the agents that did answer said.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

__all__ = ["DDL", "apply_ddl", "ddl_statements"]

DDL = """
CREATE SCHEMA IF NOT EXISTS app_committee;

CREATE TABLE IF NOT EXISTS app_committee.run (
  run_id             text PRIMARY KEY,
  snapshot_id        text NOT NULL,
  case_id            text,
  case_type          text NOT NULL,
  tier               text NOT NULL,
  tier_reasons       text[] NOT NULL DEFAULT '{}',
  state              text NOT NULL,
  rounds             text[] NOT NULL DEFAULT '{}',
  repair_loops       int NOT NULL DEFAULT 0,
  budgets            jsonb NOT NULL DEFAULT '{}',
  decision_record_id text,
  timed_out          boolean NOT NULL DEFAULT false,
  detail             text,
  started_at         timestamptz NOT NULL DEFAULT now(),
  ended_at           timestamptz
);

-- docs/06 §8: idempotent on snapshot and tier. Re-submitting the same case at
-- the same tier joins the run that exists rather than starting a second one.
CREATE UNIQUE INDEX IF NOT EXISTS run_by_snapshot_tier
  ON app_committee.run (snapshot_id, tier);
CREATE INDEX IF NOT EXISTS run_by_case ON app_committee.run (case_id, started_at DESC);

CREATE TABLE IF NOT EXISTS app_committee.opinion (
  opinion_id    text PRIMARY KEY,
  run_id        text NOT NULL REFERENCES app_committee.run ON DELETE CASCADE,
  agent_id      text NOT NULL,
  agent_version text NOT NULL,
  round         text NOT NULL,
  stance        text NOT NULL,
  confidence    double precision NOT NULL,
  degraded      boolean NOT NULL DEFAULT false,
  body          jsonb NOT NULL,
  latency_ms    double precision,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS opinion_by_run ON app_committee.opinion (run_id, round);
-- One opinion per agent per round: a re-invocation replaces nothing, it is a
-- new round.
CREATE UNIQUE INDEX IF NOT EXISTS opinion_by_agent_round
  ON app_committee.opinion (run_id, agent_id, round);

CREATE OR REPLACE FUNCTION app_committee.reject_opinion_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'an opinion is immutable: % is refused', TG_OP;
END $$;

DROP TRIGGER IF EXISTS opinion_immutable ON app_committee.opinion;
CREATE TRIGGER opinion_immutable BEFORE UPDATE ON app_committee.opinion
  FOR EACH ROW EXECUTE FUNCTION app_committee.reject_opinion_mutation();
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
