"""The `app_feature` schema (docs/04 §4).

A feature snapshot is immutable: a model run cites the snapshot it scored, and
that snapshot has to still say the same thing when the decision is reconstructed
years later.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

__all__ = ["DDL", "apply_ddl", "ddl_statements"]

DDL = """
CREATE SCHEMA IF NOT EXISTS app_feature;

CREATE TABLE IF NOT EXISTS app_feature.feature_def (
  name           text PRIMARY KEY,
  family         text NOT NULL,
  description    text NOT NULL,
  source         text NOT NULL,
  window_days    int,
  permitted_uses text[] NOT NULL,
  dtype          text NOT NULL,
  version        text NOT NULL,
  monotone       int NOT NULL DEFAULT 0,
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_feature.feature_snapshot (
  snapshot_id      text PRIMARY KEY,
  member_id        text NOT NULL,
  account_id       text,
  as_of            timestamptz NOT NULL,
  window_set       text NOT NULL,
  registry_version text NOT NULL,
  inputs_digest    text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feature_snapshot_by_member
  ON app_feature.feature_snapshot (member_id, as_of DESC);

CREATE TABLE IF NOT EXISTS app_feature.feature_value (
  snapshot_id text NOT NULL REFERENCES app_feature.feature_snapshot
              ON DELETE CASCADE,
  name        text NOT NULL,
  value       double precision,
  text_value  text,
  provenance  jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (snapshot_id, name)
);

-- A snapshot is what a model run cites, so it is frozen once written.
CREATE OR REPLACE FUNCTION app_feature.reject_snapshot_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'feature snapshots are immutable: % is refused', TG_OP;
END $$;

DROP TRIGGER IF EXISTS feature_value_immutable ON app_feature.feature_value;
CREATE TRIGGER feature_value_immutable BEFORE UPDATE ON app_feature.feature_value
  FOR EACH ROW EXECUTE FUNCTION app_feature.reject_snapshot_mutation();

CREATE TABLE IF NOT EXISTS app_feature.baseline (
  member_id text NOT NULL,
  signal    text NOT NULL,
  as_of     date NOT NULL,
  median    double precision,
  mad       double precision,
  n         int NOT NULL DEFAULT 0,
  PRIMARY KEY (member_id, signal, as_of)
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
