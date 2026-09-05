"""T-005 — transactional outbox: atomicity, at-least-once delivery, idempotency.

These run against the compose PostgreSQL (see conftest); they skip when it is
not up. The guarantees they prove are what makes event replay safe (docs/04 §1).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cio_common.ids import is_id
from cio_common.outbox import Event, claim_batch, dispatch_once, emit, mark_processed


async def _emit(db: AsyncSession, name: str = "application.submitted", **kw: object) -> str:
    return await emit(
        db,
        name,
        kw.pop("payload", {"member_id": "M-000001"}),  # type: ignore[arg-type]
        key=kw.pop("key", "case_1"),  # type: ignore[arg-type]
        producer=kw.pop("producer", "application"),  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


async def test_emit_writes_an_envelope_with_a_generated_id(db: AsyncSession) -> None:
    event_id = await _emit(db, case_id="case_abc", trace_id="t-1")
    await db.commit()

    assert is_id(event_id, "evt")
    row = (
        (await db.execute(text("SELECT * FROM events.outbox WHERE event_id = :e"), {"e": event_id}))
        .mappings()
        .one()
    )
    assert row["name"] == "application.submitted"
    assert row["version"] == 1
    assert row["case_id"] == "case_abc"
    assert row["trace_id"] == "t-1"
    assert row["dispatched_at"] is None


async def test_a_rolled_back_transaction_emits_nothing(db: AsyncSession) -> None:
    """The point of an outbox: no event without its state change."""
    await _emit(db)
    await db.rollback()

    count = (await db.execute(text("SELECT count(*) FROM events.outbox"))).scalar_one()
    assert count == 0


async def test_claim_batch_returns_only_undispatched_events_in_order(db: AsyncSession) -> None:
    ids = [await _emit(db, payload={"i": i}) for i in range(3)]
    await db.commit()

    claimed = await claim_batch(db)
    assert [e.event_id for e in claimed] == ids

    await db.execute(
        text("UPDATE events.outbox SET dispatched_at = now() WHERE event_id = :e"), {"e": ids[0]}
    )
    await db.commit()
    assert [e.event_id for e in await claim_batch(db)] == ids[1:]


async def test_dispatch_delivers_each_event_once_to_each_consumer(db: AsyncSession) -> None:
    ids = [await _emit(db, payload={"i": i}) for i in range(4)]
    await db.commit()

    seen_a: list[str] = []
    seen_b: list[str] = []

    async def a(event: Event) -> None:
        seen_a.append(event.event_id)

    async def b(event: Event) -> None:
        seen_b.append(event.event_id)

    delivered = await dispatch_once(db, {"a": a, "b": b})
    await db.commit()

    assert delivered == 4
    assert seen_a == ids
    assert seen_b == ids


async def test_redelivery_is_a_no_op_for_a_consumer_that_already_ran(db: AsyncSession) -> None:
    """At-least-once delivery is only safe because consumers are idempotent."""
    event_id = await _emit(db)
    await db.commit()

    calls: list[str] = []

    async def consumer(event: Event) -> None:
        calls.append(event.event_id)

    await dispatch_once(db, {"reporting": consumer})
    await db.commit()

    # simulate a crash after handling but before the outbox row was marked
    await db.execute(
        text("UPDATE events.outbox SET dispatched_at = NULL WHERE event_id = :e"), {"e": event_id}
    )
    await db.commit()

    await dispatch_once(db, {"reporting": consumer})
    await db.commit()

    assert calls == [event_id], "the consumer must not run twice for one event"
    dispatched = (
        await db.execute(text("SELECT dispatched_at FROM events.outbox WHERE event_id = :e"), {"e": event_id})
    ).scalar_one()
    assert dispatched is not None, "the event must still end up marked dispatched"


async def test_a_failing_consumer_leaves_the_event_for_retry(db: AsyncSession) -> None:
    """Fail safe: a broken consumer must not silently drop an event."""
    event_id = await _emit(db)
    await db.commit()

    attempts: list[str] = []

    async def flaky(event: Event) -> None:
        attempts.append(event.event_id)
        if len(attempts) == 1:
            raise RuntimeError("downstream unavailable")

    delivered = await dispatch_once(db, {"flaky": flaky})
    await db.commit()
    assert delivered == 0
    still_pending = (
        await db.execute(text("SELECT dispatched_at FROM events.outbox WHERE event_id = :e"), {"e": event_id})
    ).scalar_one()
    assert still_pending is None

    delivered = await dispatch_once(db, {"flaky": flaky})
    await db.commit()
    assert delivered == 1
    assert len(attempts) == 2


async def test_one_failing_consumer_does_not_re_run_a_successful_one(db: AsyncSession) -> None:
    await _emit(db)
    await db.commit()

    good_calls: list[str] = []

    async def good(event: Event) -> None:
        good_calls.append(event.event_id)

    async def bad(event: Event) -> None:
        raise RuntimeError("still down")

    await dispatch_once(db, {"good": good, "bad": bad})
    await db.commit()
    await dispatch_once(db, {"good": good, "bad": bad})
    await db.commit()

    assert len(good_calls) == 1, "a healthy consumer must not be re-invoked on retry"


async def test_mark_processed_reports_whether_it_was_new(db: AsyncSession) -> None:
    assert await mark_processed(db, "c", "evt_1") is True
    assert await mark_processed(db, "c", "evt_1") is False
    assert await mark_processed(db, "other", "evt_1") is True


async def test_payload_survives_the_round_trip(db: AsyncSession) -> None:
    payload = {"amount": "12500.00", "nested": {"list": [1, 2, 3]}, "flag": True, "none": None}
    occurred = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    await emit(db, "payment.received", payload, key="acct_1", producer="core_stub", occurred_at=occurred)
    await db.commit()

    event = (await claim_batch(db))[0]
    assert event.payload == payload
    assert event.occurred_at == occurred
    assert event.name == "payment.received"


async def test_the_insert_trigger_notifies_listeners(db: AsyncSession) -> None:
    """LISTEN/NOTIFY is what keeps dispatch latency low (docs/04 §1)."""
    exists = (
        await db.execute(
            text("""
        SELECT 1 FROM pg_trigger WHERE tgname = 'outbox_notify' AND NOT tgisinternal
    """)
        )
    ).first()
    assert exists, "events.outbox has no NOTIFY trigger"
