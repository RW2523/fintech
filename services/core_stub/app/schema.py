"""The `core` schema: the systems-of-record stub (docs/04 §2).

This stands in for the cooperative's core banking system. The platform reads
from it broadly and writes to it only through execution-service, which must
present an approval token (CLAUDE.md §2.6).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

__all__ = ["DDL", "apply_ddl", "ddl_statements", "drop_ddl"]

DDL = """
CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE IF NOT EXISTS core.employer (
  employer_id   text PRIMARY KEY,
  name          text NOT NULL,
  sector        text NOT NULL,
  template_id   text,
  deduction_day int CHECK (deduction_day BETWEEN 1 AND 28),
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.member (
  member_id          text PRIMARY KEY,
  name_token         text NOT NULL,
  dob                date,
  joined_at          date NOT NULL,
  status             text NOT NULL DEFAULT 'ACTIVE',
  branch_id          text,
  employer_id        text REFERENCES core.employer,
  salary_monthly     numeric(18,2),
  identity_verified  boolean NOT NULL DEFAULT false,
  contact_updated_at timestamptz,
  language           text NOT NULL DEFAULT 'en',
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS member_by_employer ON core.member (employer_id);
CREATE INDEX IF NOT EXISTS member_by_branch ON core.member (branch_id);

CREATE TABLE IF NOT EXISTS core.account (
  account_id      text PRIMARY KEY,
  member_id       text NOT NULL REFERENCES core.member,
  product_code    text NOT NULL,
  principal       numeric(18,2) NOT NULL,
  profit_rate     numeric(6,4) NOT NULL,
  tenor_months    int NOT NULL,
  instalment      numeric(18,2) NOT NULL,
  due_day         int CHECK (due_day BETWEEN 1 AND 28),
  opened_at       date NOT NULL,
  status          text NOT NULL DEFAULT 'ACTIVE',
  restructured_at date,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS account_by_member ON core.account (member_id);

CREATE TABLE IF NOT EXISTS core.schedule (
  schedule_id text PRIMARY KEY,
  account_id  text NOT NULL REFERENCES core.account,
  seq         int NOT NULL,
  due_date    date NOT NULL,
  amount_due  numeric(18,2) NOT NULL,
  UNIQUE (account_id, seq)
);
CREATE INDEX IF NOT EXISTS schedule_by_account_due ON core.schedule (account_id, due_date);

CREATE TABLE IF NOT EXISTS core.payment (
  payment_id  text PRIMARY KEY,
  schedule_id text NOT NULL REFERENCES core.schedule,
  paid_at     timestamptz NOT NULL,
  amount_paid numeric(18,2) NOT NULL,
  channel     text,
  reversed    boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS payment_by_schedule ON core.payment (schedule_id);

CREATE TABLE IF NOT EXISTS core.deduction (
  deduction_id    text PRIMARY KEY,
  member_id       text NOT NULL REFERENCES core.member,
  employer_id     text NOT NULL REFERENCES core.employer,
  cycle           text NOT NULL,
  expected_amount numeric(18,2) NOT NULL,
  received_amount numeric(18,2),
  received_at     timestamptz,
  UNIQUE (member_id, cycle)
);
CREATE INDEX IF NOT EXISTS deduction_by_member ON core.deduction (member_id, cycle);

CREATE TABLE IF NOT EXISTS core.savings (
  member_id text NOT NULL REFERENCES core.member,
  as_of     date NOT NULL,
  balance   numeric(18,2) NOT NULL,
  PRIMARY KEY (member_id, as_of)
);

CREATE TABLE IF NOT EXISTS core.share_capital (
  member_id text NOT NULL REFERENCES core.member,
  as_of     date NOT NULL,
  units     int NOT NULL,
  value     numeric(18,2) NOT NULL,
  PRIMARY KEY (member_id, as_of)
);

CREATE TABLE IF NOT EXISTS core.guarantor (
  account_id          text NOT NULL REFERENCES core.account,
  guarantor_member_id text NOT NULL REFERENCES core.member,
  since               date NOT NULL,
  PRIMARY KEY (account_id, guarantor_member_id)
);
CREATE INDEX IF NOT EXISTS guarantor_by_member ON core.guarantor (guarantor_member_id);

CREATE TABLE IF NOT EXISTS core.bureau (
  member_id     text PRIMARY KEY REFERENCES core.member,
  grade         text NOT NULL,
  adverse_flags text[] NOT NULL DEFAULT '{}',
  as_of         date NOT NULL
);

CREATE TABLE IF NOT EXISTS core.outage_window (
  system  text NOT NULL,
  from_ts timestamptz NOT NULL,
  to_ts   timestamptz NOT NULL,
  PRIMARY KEY (system, from_ts)
);

CREATE TABLE IF NOT EXISTS core.arrangement (
  arrangement_id text PRIMARY KEY,
  account_id     text NOT NULL REFERENCES core.account,
  type           text NOT NULL,
  from_date      date NOT NULL,
  to_date        date
);
CREATE INDEX IF NOT EXISTS arrangement_by_account ON core.arrangement (account_id);

CREATE TABLE IF NOT EXISTS core.outcome (
  account_id  text NOT NULL REFERENCES core.account,
  month       date NOT NULL,
  late7       boolean NOT NULL DEFAULT false,
  late30      boolean NOT NULL DEFAULT false,
  late60      boolean NOT NULL DEFAULT false,
  late90      boolean NOT NULL DEFAULT false,
  cure        boolean NOT NULL DEFAULT false,
  restructure boolean NOT NULL DEFAULT false,
  charge_off  boolean NOT NULL DEFAULT false,
  PRIMARY KEY (account_id, month)
);

CREATE TABLE IF NOT EXISTS core.application_ext (
  application_id text PRIMARY KEY,
  member_id      text NOT NULL REFERENCES core.member,
  product_code   text NOT NULL,
  amount         numeric(18,2) NOT NULL,
  tenor_months   int NOT NULL,
  status         text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.change_feed (
  seq        bigserial PRIMARY KEY,
  table_name text NOT NULL,
  pk         text NOT NULL,
  op         text NOT NULL CHECK (op IN ('INSERT','UPDATE','DELETE')),
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS change_feed_by_seq ON core.change_feed (seq);

CREATE TABLE IF NOT EXISTS core.write_log (
  id             bigserial PRIMARY KEY,
  action         text NOT NULL,
  payload        jsonb NOT NULL,
  approval_token text,
  idempotency_key text UNIQUE,
  result         jsonb,
  at             timestamptz NOT NULL DEFAULT now()
);

-- The key column is passed as a trigger argument and read through jsonb, so one
-- function serves every table. A CASE over NEW.<column> will not do: plpgsql
-- resolves every branch, so it fails on tables lacking the other columns.
CREATE OR REPLACE FUNCTION core.record_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  key_column text := TG_ARGV[0];
  key_value  text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    key_value := to_jsonb(OLD) ->> key_column;
    INSERT INTO core.change_feed (table_name, pk, op)
    VALUES (TG_TABLE_NAME, COALESCE(key_value, '-'), TG_OP);
    RETURN OLD;
  END IF;
  key_value := to_jsonb(NEW) ->> key_column;
  INSERT INTO core.change_feed (table_name, pk, op)
  VALUES (TG_TABLE_NAME, COALESCE(key_value, '-'), TG_OP);
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS member_changes ON core.member;
CREATE TRIGGER member_changes AFTER INSERT OR UPDATE ON core.member
  FOR EACH ROW EXECUTE FUNCTION core.record_change('member_id');

DROP TRIGGER IF EXISTS account_changes ON core.account;
CREATE TRIGGER account_changes AFTER INSERT OR UPDATE ON core.account
  FOR EACH ROW EXECUTE FUNCTION core.record_change('account_id');

DROP TRIGGER IF EXISTS payment_changes ON core.payment;
CREATE TRIGGER payment_changes AFTER INSERT ON core.payment
  FOR EACH ROW EXECUTE FUNCTION core.record_change('payment_id');
"""

DROP = "DROP SCHEMA IF EXISTS core CASCADE;"


def ddl_statements(ddl: str = DDL) -> list[str]:
    """Split on statement boundaries, respecting $$-quoted function bodies."""
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
    """Create the core schema. Idempotent."""
    for statement in ddl_statements():
        await connection.execute(text(statement))


async def drop_ddl(connection: Any) -> None:
    await connection.execute(text(DROP))
