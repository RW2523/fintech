"""T-014 — human decisions, authority enforcement and approval tokens."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

import cio_contracts

RECORD_ID = "dr_01JQZK7M8N9P0Q1R2S3T4V5W6X"
CASE = "case_01JQZK7M8N9P0Q1R2S3T4V5W6X"
SNAPSHOT = "snap_01JQZK7M8N9P0Q1R2S3T4V5W6X"


def record(authority: str = "CREDIT_OFFICER", **overrides: Any) -> dict[str, Any]:
    body = {
        "schema": "decision_record/1.0",
        "decision_record_id": RECORD_ID,
        "committee_run_id": "run_01JQZK7M8N9P0Q1R2S3T4V5W6X",
        "snapshot_id": SNAPSHOT,
        "case_type": "ORIGINATION",
        "tier": "FAST",
        "hard_gates": [],
        "evidence_coverage": 1.0,
        "factor_scores": {},
        "weighted_score": 88.0,
        "recommendation": "APPROVE",
        "confidence": 0.95,
        "disagreement": 0.1,
        "challenger_open": False,
        "route": "OFFICER_REVIEW",
        "route_reasons": ["SETTING:ASSIST"],
        "required_authority": authority,
        "narrative": {k: {"text": "", "status": "NONE"} for k in ("member", "officer", "auditor")},
        "would_change_outcome": [],
        "proposed_actions": [],
        "opinions": [],
        "policy_version": "policy/PF-STD/2026.09.1",
        "dff_version": "dff/PF-STD/2026.09.1",
        "autonomy_version": "autonomy/PF-STD/2026.09.1",
        "model_versions": {},
        "budgets": {
            "tokens_used": 0,
            "seconds_used": 0.0,
            "tier_budget_tokens": 0,
            "tier_budget_seconds": 0.0,
            "exceeded": False,
        },
        "created_at": "2026-09-05T10:00:00Z",
        "hash": "0" * 64,
        "prev_hash": "0" * 64,
    }
    body.update(overrides)
    return body


async def append_record(client: AsyncClient, authority: str = "CREDIT_OFFICER") -> str:
    response = await client.post(
        "/recommendations",
        json={"decision_record": record(authority), "case_id": CASE, "member_id": "M-000042"},
    )
    assert response.status_code == 200, response.text
    return response.json()["decision_record_id"]


# ---------------------------------------------------------------------------
# recommendations
# ---------------------------------------------------------------------------
async def test_a_record_is_appended_and_linked(client: AsyncClient) -> None:
    body = (await client.post("/recommendations", json={"decision_record": record(), "case_id": CASE})).json()
    assert body["seq"] == 1
    assert body["prev_hash"] == "0" * 64
    assert len(body["hash"]) == 64


async def test_the_stored_record_keeps_its_ledger_link(client: AsyncClient) -> None:
    await append_record(client)
    stored = (await client.get(f"/decision-records/{RECORD_ID}")).json()
    chain = (await client.get(f"/ledger?case_id={CASE}")).json()["entries"]
    assert stored["hash"] == chain[0]["hash"]
    cio_contracts.validate({k: v for k, v in stored.items() if k != "superseded_by"}, "DecisionRecord")


async def test_a_record_missing_a_required_field_is_refused(client: AsyncClient) -> None:
    incomplete = {k: v for k, v in record().items() if k != "required_authority"}
    response = await client.post("/recommendations", json={"decision_record": incomplete})
    assert response.status_code == 422
    assert "required_authority" in response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# authority — the check the UI cannot bypass
# ---------------------------------------------------------------------------
async def test_an_officer_may_approve_within_their_authority(client: AsyncClient) -> None:
    await append_record(client, "CREDIT_OFFICER")
    response = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "APPROVE",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["authority_role"] == "CREDIT_OFFICER"
    cio_contracts.validate(body, "HumanDecision")


async def test_an_officer_may_not_approve_above_their_authority(client: AsyncClient) -> None:
    """docs/13 §1 — enforced here, whatever the UI allowed."""
    await append_record(client, "SENIOR_OFFICER")
    response = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "APPROVE",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert "SENIOR_OFFICER" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    "role,required,allowed",
    [
        ("officer", "CREDIT_OFFICER", True),
        ("officer", "SENIOR_OFFICER", False),
        ("officer", "CREDIT_COMMITTEE", False),
        ("senior_officer", "CREDIT_OFFICER", True),
        ("senior_officer", "SENIOR_OFFICER", True),
        ("senior_officer", "CREDIT_COMMITTEE", False),
        ("committee", "CREDIT_COMMITTEE", True),
        ("collections", "CREDIT_OFFICER", False),
        ("member", "CREDIT_OFFICER", False),
    ],
)
async def test_the_authority_matrix_is_enforced(
    client: AsyncClient, role: str, required: str, allowed: bool
) -> None:
    await append_record(client, required)
    response = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": role,
            "final_action": "APPROVE",
        },
    )
    assert (response.status_code == 200) is allowed, response.text


@pytest.mark.parametrize("action", ["REQUEST_INFO", "ESCALATE", "DEFER"])
async def test_non_committing_actions_are_open_to_any_reviewing_role(
    client: AsyncClient, action: str
) -> None:
    """Asking for more information commits the cooperative to nothing."""
    await append_record(client, "CREDIT_COMMITTEE")
    response = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": action,
        },
    )
    assert response.status_code == 200


async def test_a_decision_on_an_unknown_record_is_not_found(client: AsyncClient) -> None:
    response = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": "dr_01JQZK7M8N9P0Q1R2S3T4V5W6Z",
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "APPROVE",
        },
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# overrides
# ---------------------------------------------------------------------------
async def test_an_override_without_a_reason_is_refused(client: AsyncClient) -> None:
    """docs/03 §7 — the reason is what makes an override auditable."""
    await append_record(client)
    response = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "DECLINE",
            "override": True,
        },
    )
    assert response.status_code == 422


async def test_an_override_with_a_reason_is_recorded(client: AsyncClient) -> None:
    await append_record(client)
    response = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "DECLINE",
            "override": True,
            "override_reason": {"code": "OVR-04", "text": "Payslip figures look transposed; verifying."},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["override"] is True
    assert body["override_reason"]["code"] == "OVR-04"
    cio_contracts.validate(body, "HumanDecision")


async def test_a_reason_without_an_override_is_refused(client: AsyncClient) -> None:
    await append_record(client)
    response = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "APPROVE",
            "override": False,
            "override_reason": {"code": "OVR-01", "text": "x" * 30},
        },
    )
    assert response.status_code == 422


async def test_a_short_override_explanation_is_refused(client: AsyncClient) -> None:
    await append_record(client)
    response = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "DECLINE",
            "override": True,
            "override_reason": {"code": "OVR-04", "text": "too short"},
        },
    )
    assert response.status_code == 422


async def test_ovr_12_demands_a_longer_explanation(client: AsyncClient) -> None:
    await append_record(client)
    short = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "DECLINE",
            "override": True,
            "override_reason": {"code": "OVR-12", "text": "x" * 30},
        },
    )
    assert short.status_code == 422

    long = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "DECLINE",
            "override": True,
            "override_reason": {"code": "OVR-12", "text": "x" * 60},
        },
    )
    assert long.status_code == 200


async def test_an_unknown_override_code_is_refused(client: AsyncClient) -> None:
    await append_record(client)
    response = await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "DECLINE",
            "override": True,
            "override_reason": {"code": "OVR-99", "text": "x" * 30},
        },
    )
    assert response.status_code == 422


async def test_the_decision_lands_in_the_ledger_after_the_record(client: AsyncClient) -> None:
    await append_record(client)
    await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "APPROVE",
        },
    )

    chain = (await client.get(f"/ledger?case_id={CASE}")).json()["entries"]
    assert [e["kind"] for e in chain] == ["DECISION_RECORD", "HUMAN_DECISION"]
    assert chain[1]["prev_hash"] == chain[0]["hash"]


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------
TOKEN_BODY = {
    "action_id": "act_01JQZK7M8N9P0Q1R2S3T4V5W6X",
    "decision_record_id": RECORD_ID,
    "case_id": CASE,
    "member_id": "M-000042",
    "product_code": "PF-STD",
    "max_amount": "8000.00",
    "idempotency_key": "idem-token-0001",
}


async def test_a_token_is_issued_signed_and_scoped(client: AsyncClient) -> None:
    await append_record(client)
    token = (await client.post("/tokens", json=TOKEN_BODY)).json()
    assert token["token_id"].startswith("tok_")
    assert token["scope"]["case_id"] == CASE
    assert token["scope"]["max_amount"] == "8000.00"
    assert len(token["signature"]) == 64
    cio_contracts.validate(token, "ApprovalToken")


async def test_reissuing_with_the_same_key_returns_the_first_token(client: AsyncClient) -> None:
    await append_record(client)
    first = (await client.post("/tokens", json=TOKEN_BODY)).json()
    second = (await client.post("/tokens", json=TOKEN_BODY)).json()
    assert first["token_id"] == second["token_id"]


async def test_a_token_may_be_redeemed_once(client: AsyncClient) -> None:
    """docs/13 §3 — single use, so a replayed execution is refused."""
    await append_record(client)
    token = (await client.post("/tokens", json=TOKEN_BODY)).json()

    first = await client.post(f"/tokens/{token['token_id']}/redeem?case_id={CASE}")
    assert first.status_code == 200

    replay = await client.post(f"/tokens/{token['token_id']}/redeem?case_id={CASE}")
    assert replay.status_code == 403
    assert replay.json()["error"]["code"] == "TOKEN_INVALID"
    assert "already been used" in replay.json()["error"]["message"]


async def test_a_token_is_refused_for_another_case(client: AsyncClient) -> None:
    await append_record(client)
    token = (await client.post("/tokens", json=TOKEN_BODY)).json()
    response = await client.get(
        f"/tokens/{token['token_id']}/validate?case_id=case_01JQZK7M8N9P0Q1R2S3T4V5W6Z"
    )
    assert response.status_code == 403
    assert "different case" in response.json()["error"]["message"]


async def test_an_unknown_token_is_refused(client: AsyncClient) -> None:
    response = await client.get("/tokens/tok_01JQZK7M8N9P0Q1R2S3T4V5W6Z/validate")
    assert response.status_code == 403


async def test_a_token_lands_in_the_ledger_without_its_signature(client: AsyncClient) -> None:
    """The chain records that a token was issued, not the secret that signs it."""
    await append_record(client)
    await client.post("/tokens", json=TOKEN_BODY)

    entries = (await client.get(f"/ledger?case_id={CASE}")).json()["entries"]
    token_entry = next(e for e in entries if e["kind"] == "TOKEN")
    assert "signature" not in token_entry["payload"]
    assert token_entry["payload"]["scope"]["max_amount"] == "8000.00"


async def test_a_token_may_not_outlive_a_day(client: AsyncClient) -> None:
    await append_record(client)
    response = await client.post("/tokens", json={**TOKEN_BODY, "ttl_seconds": 90000})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# verification endpoint
# ---------------------------------------------------------------------------
async def test_verify_is_green_on_an_intact_chain(client: AsyncClient) -> None:
    await append_record(client)
    await client.post(
        "/human-decisions",
        json={
            "decision_record_id": RECORD_ID,
            "case_id": CASE,
            "actor_id": "u-1",
            "role": "officer",
            "final_action": "APPROVE",
        },
    )

    body = (await client.get("/ledger/verify")).json()
    assert body["intact"] is True
    assert body["breaks"] == []
    assert body["entries_checked"] == 2
