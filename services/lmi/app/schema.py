"""lmi-service tables (docs/07 §4).

Temporal features are materialised rather than computed on demand. A member's
behaviour over 365 days is a few hundred rows to read and a second to reduce,
and the early-warning run touches every member nightly. Computing it on the
read path would put a second of work in front of every officer opening a case.

The materialisation is keyed by (member_id, as_of): a feature set states what
was known on a day, and recomputing yesterday must not overwrite what
yesterday's alert was raised from.
"""

from __future__ import annotations

from typing import Any

from cio_common.outbox import DDL as OUTBOX_DDL
from cio_common.outbox import ddl_statements

__all__ = ["DDL", "apply_ddl"]

DDL = """
CREATE SCHEMA IF NOT EXISTS app_lmi;

CREATE TABLE IF NOT EXISTS app_lmi.temporal_features (
  member_id   text NOT NULL,
  as_of       date NOT NULL,
  features    jsonb NOT NULL,
  baselines   jsonb NOT NULL DEFAULT '{}'::jsonb,
  seasonal    jsonb NOT NULL DEFAULT '{}'::jsonb,
  due_events  int NOT NULL DEFAULT 0,
  digest      text NOT NULL,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (member_id, as_of)
);
CREATE INDEX IF NOT EXISTS temporal_by_as_of ON app_lmi.temporal_features (as_of);

-- What a nightly run did, so a run that covered half the book is visible as
-- such rather than as a quiet night.
CREATE TABLE IF NOT EXISTS app_lmi.materialisation (
  run_id      text PRIMARY KEY,
  as_of       date NOT NULL,
  members     int NOT NULL DEFAULT 0,
  computed    int NOT NULL DEFAULT 0,
  skipped     int NOT NULL DEFAULT 0,
  failed      int NOT NULL DEFAULT 0,
  seconds     double precision NOT NULL DEFAULT 0,
  detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS materialisation_by_as_of ON app_lmi.materialisation (as_of DESC);
"""


async def apply_ddl(connection: Any) -> None:
    """Create the schema. Idempotent."""
    from sqlalchemy import text

    for statement in ddl_statements(DDL) + ddl_statements(OUTBOX_DDL):
        await connection.execute(text(statement))
