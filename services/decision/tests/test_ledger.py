"""T-014 — the ledger is append-only, hash-chained and verifiable.

These are the guarantees the whole audit story rests on (docs/04 §5, docs/14 §4):
a decision reconstructs from the chain, and any alteration shows up.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app import ledger
from cio_common.hashing import GENESIS_HASH, chain_hash

CASE = "case_01JQZK7M8N9P0Q1R2S3T4V5W6X"


async def test_the_first_entry_links_to_genesis(db: AsyncSession) -> None:
    entry = await ledger.append(db, "SNAPSHOT", {"snapshot_id": "snap_1"}, case_id=CASE)
    assert entry.prev_hash == GENESIS_HASH
    assert entry.hash == chain_hash(GENESIS_HASH, {"snapshot_id": "snap_1"})
    assert entry.seq == 1


async def test_each_entry_links_to_the_one_before(db: AsyncSession) -> None:
    first = await ledger.append(db, "SNAPSHOT", {"n": 1}, case_id=CASE)
    second = await ledger.append(db, "COMMITTEE_RUN", {"n": 2}, case_id=CASE)
    third = await ledger.append(db, "DECISION_RECORD", {"n": 3}, case_id=CASE)

    assert second.prev_hash == first.hash
    assert third.prev_hash == second.hash


async def test_an_intact_chain_verifies(db: AsyncSession) -> None:
    for i in range(10):
        await ledger.append(db, "OPINION", {"n": i}, case_id=CASE)

    intact, breaks, checked = await ledger.verify(db)
    assert intact is True
    assert breaks == []
    assert checked == 10


async def test_an_empty_ledger_verifies(db: AsyncSession) -> None:
    intact, breaks, checked = await ledger.verify(db)
    assert (intact, breaks, checked) == (True, [], 0)


# ---------------------------------------------------------------------------
# the append-only guarantee
# ---------------------------------------------------------------------------
async def test_updating_an_entry_is_refused_by_the_database(db: AsyncSession) -> None:
    """CLAUDE.md §2.3 — ledger rows are never updated."""
    entry = await ledger.append(db, "SNAPSHOT", {"n": 1}, case_id=CASE)
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(
            text("UPDATE ledger.entry SET payload = '{}'::jsonb WHERE entry_id = :e"), {"e": entry.entry_id}
        )


async def test_deleting_an_entry_is_refused_by_the_database(db: AsyncSession) -> None:
    entry = await ledger.append(db, "SNAPSHOT", {"n": 1}, case_id=CASE)
    await db.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("DELETE FROM ledger.entry WHERE entry_id = :e"), {"e": entry.entry_id})


async def test_an_unknown_entry_kind_is_refused(db: AsyncSession) -> None:
    with pytest.raises(DBAPIError):
        await ledger.append(db, "GOSSIP", {"n": 1}, case_id=CASE)


# ---------------------------------------------------------------------------
# tamper detection
# ---------------------------------------------------------------------------
async def test_verification_detects_an_altered_payload(db: AsyncSession) -> None:
    """docs/14 §4 — the tamper test must go red.

    The trigger blocks UPDATE, so tampering is simulated the only way it could
    really happen: with the trigger disabled by someone with database rights.
    """
    for i in range(5):
        await ledger.append(db, "OPINION", {"n": i}, case_id=CASE)
    await db.commit()

    await db.execute(text("ALTER TABLE ledger.entry DISABLE TRIGGER ledger_no_update"))
    await db.execute(
        text("""
        UPDATE ledger.entry SET payload = '{"n": 99}'::jsonb WHERE seq = 3
    """)
    )
    await db.execute(text("ALTER TABLE ledger.entry ENABLE TRIGGER ledger_no_update"))

    intact, breaks, _ = await ledger.verify(db)
    assert intact is False
    assert any(b.seq == 3 and b.reason == "PAYLOAD_ALTERED" for b in breaks)


async def test_verification_detects_a_rewritten_link(db: AsyncSession) -> None:
    for i in range(5):
        await ledger.append(db, "OPINION", {"n": i}, case_id=CASE)
    await db.commit()

    await db.execute(text("ALTER TABLE ledger.entry DISABLE TRIGGER ledger_no_update"))
    await db.execute(text("UPDATE ledger.entry SET prev_hash = :h WHERE seq = 4"), {"h": "f" * 64})
    await db.execute(text("ALTER TABLE ledger.entry ENABLE TRIGGER ledger_no_update"))

    intact, breaks, _ = await ledger.verify(db)
    assert intact is False
    assert any(b.seq == 4 and b.reason == "BROKEN_LINK" for b in breaks)


async def test_verification_detects_a_removed_entry(db: AsyncSession) -> None:
    for i in range(5):
        await ledger.append(db, "OPINION", {"n": i}, case_id=CASE)
    await db.commit()

    await db.execute(text("ALTER TABLE ledger.entry DISABLE TRIGGER ledger_no_update"))
    await db.execute(text("DELETE FROM ledger.entry WHERE seq = 3"))
    await db.execute(text("ALTER TABLE ledger.entry ENABLE TRIGGER ledger_no_update"))

    intact, breaks, _ = await ledger.verify(db)
    assert intact is False
    assert any(b.reason == "BROKEN_LINK" for b in breaks)


# ---------------------------------------------------------------------------
# reconstruction
# ---------------------------------------------------------------------------
async def test_a_case_slice_returns_only_that_case_in_order(db: AsyncSession) -> None:
    other = "case_01JQZK7M8N9P0Q1R2S3T4V5W6Y"
    await ledger.append(db, "SNAPSHOT", {"n": 1}, case_id=CASE)
    await ledger.append(db, "SNAPSHOT", {"n": 2}, case_id=other)
    await ledger.append(db, "DECISION_RECORD", {"n": 3}, case_id=CASE)

    chain = await ledger.chain_for(db, case_id=CASE)
    assert [e.payload["n"] for e in chain] == [1, 3]
    assert [e.kind for e in chain] == ["SNAPSHOT", "DECISION_RECORD"]


async def test_the_full_reconstruction_order_is_preserved(db: AsyncSession) -> None:
    """docs/09 §6 — snapshot, run, opinions, record, decision, token, action."""
    order = [
        "SNAPSHOT",
        "COMMITTEE_RUN",
        "OPINION",
        "OPINION",
        "DECISION_RECORD",
        "HUMAN_DECISION",
        "TOKEN",
        "ACTION",
        "OUTCOME",
    ]
    for kind in order:
        await ledger.append(db, kind, {"kind": kind}, case_id=CASE)

    assert [e.kind for e in await ledger.chain_for(db, case_id=CASE)] == order


async def test_payloads_survive_the_round_trip_unchanged(db: AsyncSession) -> None:
    payload = {"amount": "12500.00", "nested": {"list": [1, 2, 3]}, "flag": True, "none": None}
    entry = await ledger.append(db, "DECISION_RECORD", payload, case_id=CASE)
    stored = (await ledger.chain_for(db, case_id=CASE))[0]
    assert stored.payload == payload
    assert stored.hash == entry.hash
