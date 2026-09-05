"""Transactional outbox with LISTEN/NOTIFY dispatch (docs/04 §1).

An event is written in the same transaction as the state change that produced
it, so a committed change always has its event and a rolled-back one never
does. The dispatcher delivers at least once; consumers record what they have
processed in `events.consumer_offsets`, which makes replay idempotent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cio_common.ids import new_id

__all__ = [
    "DDL",
    "NOTIFY_CHANNEL",
    "Event",
    "Handler",
    "claim_batch",
    "ddl_statements",
    "dispatch_once",
    "emit",
    "mark_processed",
    "run_dispatcher",
]

log = logging.getLogger(__name__)

NOTIFY_CHANNEL = "cio_events"

DDL = """
CREATE SCHEMA IF NOT EXISTS events;

CREATE TABLE IF NOT EXISTS events.outbox (
  event_id     text PRIMARY KEY,
  name         text NOT NULL,
  version      int  NOT NULL,
  key          text NOT NULL,
  trace_id     text,
  case_id      text,
  producer     text NOT NULL,
  payload      jsonb NOT NULL,
  occurred_at  timestamptz NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  dispatched_at timestamptz
);

CREATE INDEX IF NOT EXISTS outbox_undispatched
  ON events.outbox (created_at) WHERE dispatched_at IS NULL;

CREATE TABLE IF NOT EXISTS events.consumer_offsets (
  consumer     text NOT NULL,
  event_id     text NOT NULL,
  processed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (consumer, event_id)
);

CREATE OR REPLACE FUNCTION events.notify_outbox() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('cio_events', NEW.event_id);
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS outbox_notify ON events.outbox;
CREATE TRIGGER outbox_notify AFTER INSERT ON events.outbox
  FOR EACH ROW EXECUTE FUNCTION events.notify_outbox();
"""


def ddl_statements(ddl: str = DDL) -> list[str]:
    """Split SQL on statement boundaries, respecting $$-quoted function bodies.

    A naive split on ";" cuts the NOTIFY trigger function in half.
    """
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
    """Create the outbox schema. Idempotent."""
    for statement in ddl_statements():
        await connection.execute(text(statement))


@dataclass(frozen=True, slots=True)
class Event:
    """One envelope from the outbox (docs/03 §12)."""

    event_id: str
    name: str
    version: int
    key: str
    producer: str
    payload: dict[str, Any]
    occurred_at: datetime
    trace_id: str | None = None
    case_id: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> Event:
        payload = row.payload
        return cls(
            event_id=row.event_id,
            name=row.name,
            version=row.version,
            key=row.key,
            producer=row.producer,
            payload=json.loads(payload) if isinstance(payload, str) else payload,
            occurred_at=row.occurred_at,
            trace_id=row.trace_id,
            case_id=row.case_id,
        )


Handler = Callable[[Event], Awaitable[None]]


async def emit(
    session: AsyncSession,
    name: str,
    payload: dict[str, Any],
    *,
    key: str,
    producer: str,
    version: int = 1,
    case_id: str | None = None,
    trace_id: str | None = None,
    occurred_at: datetime | None = None,
) -> str:
    """Append an event inside the caller's transaction. Returns the event id."""
    event_id = new_id("evt")
    await session.execute(
        text("""
            INSERT INTO events.outbox
              (event_id, name, version, key, trace_id, case_id, producer, payload, occurred_at)
            VALUES
              (:event_id, :name, :version, :key, :trace_id, :case_id, :producer,
               CAST(:payload AS jsonb), :occurred_at)
        """),
        {
            "event_id": event_id,
            "name": name,
            "version": version,
            "key": key,
            "trace_id": trace_id,
            "case_id": case_id,
            "producer": producer,
            "payload": json.dumps(payload),
            "occurred_at": occurred_at or datetime.now(UTC),
        },
    )
    return event_id


async def claim_batch(session: AsyncSession, *, limit: int = 100) -> list[Event]:
    """Take undispatched events, skipping rows another dispatcher holds."""
    rows = (
        (
            await session.execute(
                text("""
            SELECT event_id, name, version, key, trace_id, case_id, producer,
                   payload, occurred_at
            FROM events.outbox
            WHERE dispatched_at IS NULL
            ORDER BY created_at
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
        """),
                {"limit": limit},
            )
        )
        .mappings()
        .all()
    )
    return [Event.from_row(type("Row", (), dict(r))) for r in rows]


async def mark_processed(session: AsyncSession, consumer: str, event_id: str) -> bool:
    """Record that ``consumer`` handled ``event_id``.

    Returns False when it had already been recorded, which is how a redelivery
    becomes a no-op.
    """
    result = await session.execute(
        text("""
            INSERT INTO events.consumer_offsets (consumer, event_id)
            VALUES (:consumer, :event_id)
            ON CONFLICT (consumer, event_id) DO NOTHING
            RETURNING event_id
        """),
        {"consumer": consumer, "event_id": event_id},
    )
    return result.first() is not None


async def _already_processed(session: AsyncSession, consumer: str, event_id: str) -> bool:
    row = (
        await session.execute(
            text("SELECT 1 FROM events.consumer_offsets WHERE consumer = :c AND event_id = :e"),
            {"c": consumer, "e": event_id},
        )
    ).first()
    return row is not None


async def dispatch_once(
    session: AsyncSession,
    consumers: dict[str, Handler],
    *,
    limit: int = 100,
) -> int:
    """Deliver one batch. Returns how many events were delivered.

    Delivery is at least once: an event is marked dispatched only after every
    consumer has either handled it or recorded that it already had.
    """
    events = await claim_batch(session, limit=limit)
    delivered = 0

    for event in events:
        for consumer, handler in consumers.items():
            if await _already_processed(session, consumer, event.event_id):
                continue
            try:
                await handler(event)
            except Exception:
                log.exception(
                    "consumer %s failed on %s (%s); leaving it undispatched for retry",
                    consumer,
                    event.name,
                    event.event_id,
                )
                break
            await mark_processed(session, consumer, event.event_id)
        else:
            await session.execute(
                text("UPDATE events.outbox SET dispatched_at = now() WHERE event_id = :e"),
                {"e": event.event_id},
            )
            delivered += 1

    return delivered


async def run_dispatcher(
    session_ctx: Callable[[], Any],
    consumers: dict[str, Handler],
    *,
    poll_seconds: float = 1.0,
    stop: asyncio.Event | None = None,
) -> None:
    """Poll-and-deliver loop. NOTIFY shortens the wait; the poll is the safety net."""
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            async with session_ctx() as session:
                delivered = await dispatch_once(session, consumers)
            if delivered:
                continue  # drain before sleeping
        except Exception:
            log.exception("dispatcher batch failed; retrying after backoff")
            await asyncio.sleep(min(poll_seconds * 5, 15.0))
            continue
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
