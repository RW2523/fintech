"""T-053 — the audit trail (docs/04 §6).

The trail answers a different question from the Decision Ledger. The ledger
says what the platform decided; the trail says what it did, who asked, and what
the state was either side. These tests are about the property that makes it
worth having: an entry cannot be changed after the fact without the change
showing.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.conftest import CASE_ID, entry


async def write(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    return (await client.post("/audit", json=entry(**overrides))).json()


# ---------------------------------------------------------------------------
# the chain
# ---------------------------------------------------------------------------
async def test_an_entry_is_appended_and_linked(client: AsyncClient) -> None:
    first = await write(client)
    second = await write(client, action="token.issued")
    assert first["seq"] < second["seq"]

    read = (await client.get("/audit", params={"case_id": CASE_ID})).json()
    assert [e["entry_id"] for e in read["entries"]] == [first["entry_id"], second["entry_id"]]
    assert read["entries"][1]["prev_hash"] == first["hash"]


async def test_the_first_entry_starts_from_genesis(client: AsyncClient) -> None:
    from cio_common.hashing import GENESIS_HASH

    await write(client)
    read = (await client.get("/audit")).json()
    assert read["entries"][0]["prev_hash"] == GENESIS_HASH


async def test_a_verified_chain_is_green(client: AsyncClient) -> None:
    for action in ("decision.approved", "token.issued", "action.executed"):
        await write(client, action=action)

    verified = (await client.get("/audit/verify")).json()
    assert verified["verified"] is True
    assert verified["entries_checked"] == 3
    assert verified["breaks"] == []


async def test_an_altered_entry_turns_the_chain_red(client: AsyncClient, db: Any) -> None:
    """The tamper test. Changing a stored field must not go unnoticed, even
    when whoever changed it also updated the hash to match."""
    await write(client)
    await write(client, action="token.issued")

    # The trigger refuses an UPDATE, so tampering is done the way somebody with
    # the database password would have to: by disabling it first.
    await db.execute(text("ALTER TABLE audit.entry DISABLE TRIGGER audit_no_mutation"))
    await db.execute(text('UPDATE audit.entry SET after = \'{"state": "DECLINED"}\'::jsonb WHERE seq = 1'))
    await db.commit()
    try:
        verified = (await client.get("/audit/verify")).json()
    finally:
        await db.execute(text("ALTER TABLE audit.entry ENABLE TRIGGER audit_no_mutation"))
        await db.commit()

    assert verified["verified"] is False
    assert verified["breaks"], "an altered entry passed verification"
    assert verified["breaks"][0]["seq"] == 1
    assert "does not hash" in verified["breaks"][0]["reason"]


async def test_a_removed_entry_turns_the_chain_red(client: AsyncClient, db: Any) -> None:
    """Deleting the middle of the chain leaves the entry after it pointing at
    something that is no longer there."""
    await write(client)
    await write(client, action="token.issued")
    await write(client, action="action.executed")

    await db.execute(text("ALTER TABLE audit.entry DISABLE TRIGGER audit_no_mutation"))
    await db.execute(text("DELETE FROM audit.entry WHERE seq = 2"))
    await db.commit()
    try:
        verified = (await client.get("/audit/verify")).json()
    finally:
        await db.execute(text("ALTER TABLE audit.entry ENABLE TRIGGER audit_no_mutation"))
        await db.commit()

    assert verified["verified"] is False
    assert any("prev_hash" in b["reason"] for b in verified["breaks"])


async def test_the_table_refuses_an_update(client: AsyncClient, db: Any) -> None:
    await write(client)
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("UPDATE audit.entry SET action = 'nothing' WHERE seq = 1"))
    await db.rollback()


async def test_the_table_refuses_a_delete(client: AsyncClient, db: Any) -> None:
    await write(client)
    with pytest.raises(DBAPIError, match="append-only"):
        await db.execute(text("DELETE FROM audit.entry WHERE seq = 1"))
    await db.rollback()


# ---------------------------------------------------------------------------
# what an entry carries
# ---------------------------------------------------------------------------
async def test_the_before_and_after_are_both_kept(client: AsyncClient) -> None:
    """An audit entry that says only what the state became cannot answer the
    question an auditor actually asks, which is what it was."""
    await write(client)
    read = (await client.get("/audit", params={"case_id": CASE_ID})).json()
    assert read["entries"][0]["before"] == {"state": "OFFICER_REVIEW"}
    assert read["entries"][0]["after"] == {"state": "APPROVED"}


async def test_the_trace_id_is_taken_from_the_call_when_absent(client: AsyncClient) -> None:
    """So an entry can always be tied back to the request that produced it."""
    await client.post("/audit", json=entry(), headers={"x-trace-id": "trace-abc"})
    read = (await client.get("/audit")).json()
    assert read["entries"][0]["trace_id"] == "trace-abc"


async def test_entries_filter_by_actor_and_action(client: AsyncClient) -> None:
    await write(client)
    await write(
        client, actor={"id": "svc-execution", "role": "SYSTEM", "kind": "SERVICE"}, action="action.executed"
    )

    by_actor = (await client.get("/audit", params={"actor_id": "svc-execution"})).json()
    assert by_actor["count"] == 1

    by_action = (await client.get("/audit", params={"action": "decision.approved"})).json()
    assert by_action["count"] == 1


async def test_an_entry_missing_its_action_is_refused(client: AsyncClient) -> None:
    body = entry()
    del body["action"]
    assert (await client.post("/audit", json=body)).status_code == 422
