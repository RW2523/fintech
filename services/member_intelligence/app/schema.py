"""The `app_member` schema: the member event store (docs/04 §3).

Events are the raw material the Longitudinal Member Intelligence engine reads.
The table is partitioned by month because a member's timeline is always queried
as a window, and because the nightly job sweeps one month at a time.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text

__all__ = ["DDL", "apply_ddl", "ddl_statements", "partition_statements"]

DDL = """
CREATE SCHEMA IF NOT EXISTS app_member;

CREATE TABLE IF NOT EXISTS app_member.member_event (
  event_id         text NOT NULL,
  member_id        text NOT NULL,
  account_id       text,
  occurred_at      timestamptz NOT NULL,
  ingested_at      timestamptz NOT NULL DEFAULT now(),
  event_type       text NOT NULL,
  source_system    text NOT NULL,
  source_record_id text NOT NULL,
  payload          jsonb NOT NULL,
  data_quality     jsonb NOT NULL,
  permitted_uses   text[] NOT NULL,
  evidence_refs    jsonb NOT NULL DEFAULT '[]',
  PRIMARY KEY (member_id, occurred_at, event_id)
) PARTITION BY RANGE (occurred_at);

CREATE INDEX IF NOT EXISTS member_event_by_type
  ON app_member.member_event (member_id, event_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS member_event_by_account
  ON app_member.member_event (account_id, occurred_at DESC);

-- Anything outside the created partitions lands here rather than being refused.
CREATE TABLE IF NOT EXISTS app_member.member_event_default
  PARTITION OF app_member.member_event DEFAULT;

CREATE TABLE IF NOT EXISTS app_member.profile_projection (
  member_id  text PRIMARY KEY,
  version    text NOT NULL,
  body       jsonb NOT NULL,
  as_of      timestamptz NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_member.member_state (
  member_id  text NOT NULL,
  account_id text NOT NULL,
  state      text NOT NULL,
  since      timestamptz NOT NULL,
  reason     jsonb NOT NULL DEFAULT '{}',
  prev_state text,
  PRIMARY KEY (member_id, account_id)
);

-- How far the change-feed import has read, so it can resume.
CREATE TABLE IF NOT EXISTS app_member.import_cursor (
  source     text PRIMARY KEY,
  last_seq   bigint NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
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


def partition_statements(start: date, months: int) -> list[str]:
    """Monthly partitions covering ``months`` from ``start`` (docs/04 §7)."""
    statements: list[str] = []
    for offset in range(months):
        total = start.month - 1 + offset
        begin = date(start.year + total // 12, total % 12 + 1, 1)
        total_next = total + 1
        end = date(start.year + total_next // 12, total_next % 12 + 1, 1)
        name = f"member_event_{begin.year:04d}_{begin.month:02d}"
        statements.append(
            f"CREATE TABLE IF NOT EXISTS app_member.{name} "
            f"PARTITION OF app_member.member_event "
            f"FOR VALUES FROM ('{begin.isoformat()}') TO ('{end.isoformat()}')"
        )
    return statements


async def apply_ddl(connection: Any, *, partitions_from: date | None = None, months: int = 48) -> None:
    """Create the schema and, when asked, the monthly partitions."""
    for statement in ddl_statements():
        await connection.execute(text(statement))
    if partitions_from is not None:
        for statement in partition_statements(partitions_from, months):
            await connection.execute(text(statement))
