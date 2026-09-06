"""T-053 — reconstructing a case (docs/09 §6).

The timeline a person answers questions from. It is a read across services
rather than a store of its own, because a second copy of the history is a
second thing that can disagree with the first, and the value of this screen is
that it does not.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import CASE_ID, entry


@pytest.fixture
def upstreams(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stand in for the services a reconstruction reads from.

    The route's own outbound read is replaced rather than httpx itself:
    patching the library would also intercept the test client's calls into the
    app, and the test would be exercising its own stub.
    """
    from app import routes

    state: dict[str, Any] = {
        "ledger": {
            "entries": [
                {
                    "seq": 1,
                    "entry_id": "ent_01JQZK7M8N9P0Q1R2S3T4V5W6X",
                    "kind": "DECISION_RECORD",
                    "created_at": "2026-09-06T09:00:00Z",
                    "hash": "a" * 64,
                    "prev_hash": "0" * 64,
                    "payload": {"committee_run_id": "run_01JQZK7M8N9P0Q1R2S3T4V5W6X"},
                }
            ]
        },
        "opinions": {
            "opinions": [
                {
                    "body": {
                        "agent_id": "credit_risk",
                        "stance": "SUPPORT",
                        "created_at": "2026-09-06T08:59:00Z",
                    }
                }
            ]
        },
        "documents": {"documents": [{"document_id": "doc_1", "type": "IDENTITY"}]},
        "findings": {"findings": []},
        "actions": {"actions": [{"action_id": "act_1", "state": "EXECUTED"}]},
        "verify": {"verified": True, "entries_checked": 1, "breaks": []},
        "down": set(),
    }

    async def fake_get(_client: Any, url: str, **params: Any) -> Any:
        for fragment, key in (
            ("/ledger/verify", "verify"),
            ("/ledger", "ledger"),
            ("/opinions", "opinions"),
            ("/documents", "documents"),
            ("/findings", "findings"),
            ("/actions", "actions"),
        ):
            if fragment in url:
                return None if key in state["down"] else state[key]
        return None

    monkeypatch.setattr(routes, "_get", fake_get)
    return state


async def test_a_case_comes_back_as_one_timeline(client: AsyncClient, upstreams: dict[str, Any]) -> None:
    await client.post("/audit", json=entry())
    response = await client.get(f"/reconstruct/{CASE_ID}")
    assert response.status_code == 200, response.text
    body = response.json()

    sources = {event["source"] for event in body["timeline"]}
    assert sources == {"ledger", "committee", "audit"}
    assert body["documents"], "the paper the decision rested on is part of the story"
    assert body["actions"][0]["state"] == "EXECUTED"


async def test_the_timeline_is_in_the_order_the_case_happened(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    """The opinion was written before the record it fed, and reads that way."""
    await client.post("/audit", json=entry())
    body = (await client.get(f"/reconstruct/{CASE_ID}")).json()
    kinds = [event["kind"] for event in body["timeline"]]
    assert kinds.index("OPINION") < kinds.index("DECISION_RECORD")


async def test_the_chain_verification_travels_with_it(client: AsyncClient, upstreams: dict[str, Any]) -> None:
    """A timeline without a verification badge invites the reader to trust it
    on sight, which is the one thing a ledger view must not ask for."""
    await client.post("/audit", json=entry())
    body = (await client.get(f"/reconstruct/{CASE_ID}")).json()
    assert body["chain"]["verified"] is True


async def test_a_service_that_is_down_is_named_not_hidden(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    """A timeline with a silent gap is worse than no timeline: the reader takes
    the gap for an absence of events."""
    await client.post("/audit", json=entry())
    upstreams["down"].add("documents")

    body = (await client.get(f"/reconstruct/{CASE_ID}")).json()
    assert "documents" in body["unavailable"]
    assert body["documents"] == []


async def test_a_case_with_nothing_recorded_is_a_not_found(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    upstreams["ledger"] = {"entries": []}
    response = await client.get("/reconstruct/case_01JQZK7M8N9P0Q1R2S3T4V5W99")
    assert response.status_code == 404


async def test_the_audit_entries_carry_their_own_hashes(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    """So a reader can check the trail as well as read it."""
    await client.post("/audit", json=entry())
    body = (await client.get(f"/reconstruct/{CASE_ID}")).json()
    audit = [e for e in body["timeline"] if e["source"] == "audit"]
    assert audit[0]["hash"] and audit[0]["prev_hash"]


async def test_the_ledgers_own_word_for_intact_is_normalised(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    """The decision service reports `intact`. A reader of this timeline should
    not have to know that, and a screen looking for the wrong key would show
    "not verified" on a chain that verifies."""
    await client.post("/audit", json=entry())
    upstreams["verify"] = {"intact": True, "entries_checked": 9, "breaks": []}

    body = (await client.get(f"/reconstruct/{CASE_ID}")).json()
    assert body["chain"]["verified"] is True
    assert body["chain"]["entries_checked"] == 9


async def test_a_broken_chain_travels_with_the_timeline(
    client: AsyncClient, upstreams: dict[str, Any]
) -> None:
    await client.post("/audit", json=entry())
    upstreams["verify"] = {
        "intact": False,
        "entries_checked": 9,
        "breaks": [{"seq": 4, "entry_id": "ent_x", "reason": "does not hash", "expected": "a", "found": "b"}],
    }

    body = (await client.get(f"/reconstruct/{CASE_ID}")).json()
    assert body["chain"]["verified"] is False
    assert body["chain"]["breaks"][0]["seq"] == 4
