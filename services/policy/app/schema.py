"""policy-service tables (docs/04 §4).

The DDL lives here rather than only in the migration, so tests can create the
schema in the test database the same way every other service does. The
migration imports it, which keeps one definition rather than two that drift.
"""

from __future__ import annotations

from typing import Any

from cio_common.outbox import DDL as OUTBOX_DDL
from cio_common.outbox import ddl_statements

__all__ = ["DDL", "apply_ddl"]

DDL = """
CREATE SCHEMA IF NOT EXISTS app_policy;
CREATE TABLE IF NOT EXISTS app_policy.policy_version (
  version        text PRIMARY KEY,
  product_code   text NOT NULL,
  kind           text NOT NULL CHECK (kind IN ('policy', 'dff', 'autonomy')),
  body           jsonb NOT NULL,
  approved_by    text[] NOT NULL DEFAULT '{}',
  approved_at    timestamptz,
  effective_from timestamptz,
  status         text NOT NULL DEFAULT 'DRAFT',
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_policy.calc (
  calc_id       text PRIMARY KEY,
  tool          text NOT NULL,
  version       text NOT NULL,
  inputs_digest text NOT NULL,
  inputs        jsonb NOT NULL,
  outputs       jsonb NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS calc_by_digest ON app_policy.calc (inputs_digest);

CREATE TABLE IF NOT EXISTS app_policy.kill_switch (
  product_code text PRIMARY KEY,
  enabled      boolean NOT NULL DEFAULT false,
  activated_by text,
  activated_at timestamptz,
  reason       text
);

CREATE TABLE IF NOT EXISTS app_policy.sandbox_run (
  sandbox_id text PRIMARY KEY,
  product_code text NOT NULL,
  candidate  jsonb NOT NULL,
  range      jsonb NOT NULL,
  results    jsonb NOT NULL,
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- What the sandbox replays: the frozen inputs of a decided case, with the
-- baseline result it produced (docs/05 §7). No LLM is involved in a replay,
-- so the stored opinions are reused as-is.
CREATE TABLE IF NOT EXISTS app_policy.replay_case (
  snapshot_id     text PRIMARY KEY,
  case_id         text,
  product_code    text NOT NULL,
  policy_version  text NOT NULL,
  decided_at      timestamptz NOT NULL,
  inputs          jsonb NOT NULL,
  factor_scores   jsonb NOT NULL,
  opinions        jsonb NOT NULL DEFAULT '[]',
  baseline        jsonb NOT NULL,
  segment         jsonb NOT NULL DEFAULT '{}',
  pd_12m          double precision,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS replay_by_product_date
  ON app_policy.replay_case (product_code, decided_at);
"""


async def apply_ddl(connection: Any) -> None:
    """Create the schema. Idempotent."""
    from sqlalchemy import text

    for statement in ddl_statements(DDL) + ddl_statements(OUTBOX_DDL):
        await connection.execute(text(statement))
