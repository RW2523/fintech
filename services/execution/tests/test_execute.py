"""T-052 — carrying out an approved action (docs/08 §7).

This is the only service that changes anything outside the platform, so the
tests are about the checks before the write and the recovery after one fails,
rather than about the write itself.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import text

from app.clients import CoreRefusedError, TokenRefusedError, UpstreamError
from tests.conftest import ACTION_ID, TOKEN_ID, proposal


async def propose(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    return (await client.post("/action-proposals", json=proposal(**overrides))).json()


async def execute(client: AsyncClient, action_id: str = ACTION_ID, **body: Any) -> Any:
    return await client.post(f"/actions/{action_id}/execute", json={"token_id": TOKEN_ID, **body})


# ---------------------------------------------------------------------------
# a proposal is not an instruction
# ---------------------------------------------------------------------------
async def test_a_proposal_is_recorded_and_nothing_is_executed(client: AsyncClient, fake: Any) -> None:
    body = await propose(client)
    assert body["state"] == "PROPOSED"
    assert not [c for c in fake.calls if c[0].startswith("core_")]


async def test_the_same_proposal_twice_is_one_action(client: AsyncClient) -> None:
    """Proposals arrive from the committee and from agents, and both retry. An
    id derived from what was proposed makes the retry land on the same row
    rather than raising a second request for the same document."""
    first = await propose(client)
    second = await propose(client)
    assert first["action_id"] == second["action_id"]


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------
async def test_an_approved_financing_is_activated_then_approved(client: AsyncClient, fake: Any) -> None:
    await propose(client)
    response = await execute(client)
    assert response.status_code == 200

    body = response.json()
    assert body["state"] == "EXECUTED"
    assert body["core_refs"][0]["kind"] == "account"

    written = [c[0] for c in fake.calls if c[0].startswith("core_")]
    assert written == ["core_activate", "core_status"], "the order of the two writes matters"


async def test_the_token_is_validated_and_consumed(client: AsyncClient, fake: Any) -> None:
    await propose(client)
    await execute(client)
    assert [c[0] for c in fake.calls if "token" in c[0]] == ["validate_token", "redeem_token"]


async def test_the_idempotency_key_derives_from_the_action(client: AsyncClient, fake: Any) -> None:
    """A key generated per attempt would open a second facility for the same
    decision, which is the exact failure the key exists to prevent."""
    await propose(client)
    await execute(client)
    activate = next(c[1] for c in fake.calls if c[0] == "core_activate")
    assert activate["idempotency_key"] == f"exec-{ACTION_ID}-activate"


async def test_every_step_is_kept(client: AsyncClient, db: Any) -> None:
    await propose(client)
    await execute(client)
    names = (
        (
            await db.execute(
                text("SELECT name FROM app_execution.saga_step WHERE action_id = :id ORDER BY started_at"),
                {"id": ACTION_ID},
            )
        )
        .scalars()
        .all()
    )
    assert list(names) == ["core.activate", "core.status"]


async def test_the_before_and_after_reach_the_outbox(client: AsyncClient, db: Any) -> None:
    """docs/08 §7 — audit before and after. The audit service owns the table
    and arrives with T-053; an event in the outbox is durable and ordered, so
    nothing is lost in the meantime."""
    await propose(client)
    await execute(client)
    row = (
        (
            await db.execute(
                text("SELECT name, payload FROM events.outbox WHERE key = :k ORDER BY created_at DESC"),
                {"k": ACTION_ID},
            )
        )
        .mappings()
        .first()
    )
    assert row is not None
    assert row["name"] == "action.executed"
    payload = row["payload"]
    assert payload["before"]["state"] == "EXECUTING"
    assert payload["after"]["state"] == "EXECUTED"


# ---------------------------------------------------------------------------
# the acceptance: a replayed token, a duplicate execute, a core that failed
# ---------------------------------------------------------------------------
async def test_a_replayed_token_is_rejected(client: AsyncClient, fake: Any) -> None:
    """The decision service consumes a token exactly once. A second action
    presenting the same one is refused here rather than written to the core."""
    await propose(client)
    await propose(client, action_id="act_01JQZK7M8N9P0Q1R2S3T4V5W6Y")
    await execute(client)

    fake.redeemed[TOKEN_ID] = TokenRefusedError("token has already been used")
    fake.tokens[TOKEN_ID] = TokenRefusedError("token has already been used")
    response = await execute(client, action_id="act_01JQZK7M8N9P0Q1R2S3T4V5W6Y")

    assert response.status_code == 403
    assert "already been used" in response.json()["error"]["message"]
    assert [c[0] for c in fake.calls].count("core_activate") == 1


async def test_a_duplicate_execute_is_a_no_op(client: AsyncClient, fake: Any) -> None:
    """A caller asking twice usually means they did not hear the first answer,
    not that they want the money moved twice."""
    await propose(client)
    first = (await execute(client)).json()
    second = await execute(client)

    assert second.status_code == 200
    body = second.json()
    assert body["replayed"] is True
    assert body["core_refs"] == first["core_refs"]
    assert [c[0] for c in fake.calls].count("core_activate") == 1


async def test_a_core_failure_leaves_the_case_pending_and_a_retry_succeeds_once(
    client: AsyncClient, fake: Any
) -> None:
    """The whole point of the saga. The status write fails, the activation is
    rolled back, the action stays retryable, and the retry finishes it."""
    await propose(client)
    # The approval write fails; the rollback still works, which is the case
    # the saga exists for.
    fake.status_by_value["APPROVED"] = CoreRefusedError("core rejected the status change")

    failed = await execute(client)
    assert failed.status_code == 409
    assert failed.json()["error"]["details"]["retryable"] is True
    assert failed.json()["error"]["details"]["compensated"] is True

    pending = (await client.get(f"/actions/{ACTION_ID}")).json()
    assert pending["state"] == "FAILED", "the case stays pending, not abandoned"

    fake.status_by_value.clear()
    retried = await execute(client)
    assert retried.status_code == 200
    assert retried.json()["state"] == "EXECUTED"

    # Twice through activate: once in the failed attempt and once in the retry.
    # Both carry the same idempotency key, so the core opened one facility.
    activations = [c[1] for c in fake.calls if c[0] == "core_activate"]
    assert len({a["idempotency_key"] for a in activations}) == 1


async def test_a_failed_status_write_rolls_the_account_back(client: AsyncClient, fake: Any) -> None:
    """An account created and never approved is a member holding a facility
    nobody decided to give them."""
    await propose(client)
    fake.status_by_value["APPROVED"] = CoreRefusedError("core rejected the status change")
    await execute(client)

    rollback = [c[1] for c in fake.calls if c[0] == "core_status" and c[1]["status"] == "ROLLBACK"]
    assert rollback, "the activation was left standing"


async def test_a_failed_activation_is_not_compensated(client: AsyncClient, fake: Any) -> None:
    """There is nothing to undo, and calling the core to roll back an account
    that was never opened would be the saga inventing state."""
    await propose(client)
    fake.activate = CoreRefusedError("core rejected the activation")
    await execute(client)

    assert not [c for c in fake.calls if c[0] == "core_status"]


async def test_a_failed_rollback_is_reported_loudly(client: AsyncClient, fake: Any) -> None:
    """A compensation that cannot run is the worst state the platform can be
    in: somebody has to go and look at that account by hand."""
    await propose(client)
    # Every status write fails, so the compensation fails too.
    fake.status = CoreRefusedError("core is unwell")

    response = await execute(client)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["compensated"] is False

    stored = (await client.get(f"/actions/{ACTION_ID}")).json()
    assert "the rollback failed too" in (stored["last_error"] or "")
    assert "is open and unapproved" in (stored["last_error"] or "")


# ---------------------------------------------------------------------------
# the checks before the write
# ---------------------------------------------------------------------------
async def test_the_kill_switch_stops_everything(client: AsyncClient, fake: Any) -> None:
    await propose(client)
    fake.stopped.add("PF-STD")

    response = await execute(client)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "KILL_SWITCH"
    assert not [c for c in fake.calls if c[0].startswith("core_")]


async def test_the_kill_switch_is_checked_before_the_token_is_spent(client: AsyncClient, fake: Any) -> None:
    """A stop must not burn an approval that a person will have to issue
    again."""
    await propose(client)
    fake.stopped.add("PF-STD")
    await execute(client)

    assert not [c for c in fake.calls if c[0] == "redeem_token"]


async def test_a_refused_token_never_reaches_the_core(client: AsyncClient, fake: Any) -> None:
    await propose(client)
    fake.tokens[TOKEN_ID] = TokenRefusedError("token is scoped to a different case")

    response = await execute(client)
    assert response.status_code == 403
    assert not [c for c in fake.calls if c[0].startswith("core_")]


async def test_a_retry_may_not_present_a_different_token(client: AsyncClient, fake: Any) -> None:
    """A second approval must not be spendable on an action that already has
    one."""
    await propose(client)
    fake.status_by_value["APPROVED"] = CoreRefusedError("core rejected the status change")
    await execute(client)

    fake.status_by_value.clear()
    response = await execute(client, token_id="tok_01JQZK7M8N9P0Q1R2S3T4V5W6Z")
    assert response.status_code == 403


async def test_a_retry_does_not_need_a_second_token(client: AsyncClient, fake: Any) -> None:
    """Asking for one would mean a person re-approving a decision they already
    approved because a core write timed out."""
    await propose(client)
    fake.status_by_value["APPROVED"] = CoreRefusedError("core rejected the status change")
    await execute(client)
    before = [c[0] for c in fake.calls].count("redeem_token")

    fake.status_by_value.clear()
    await execute(client)
    assert [c[0] for c in fake.calls].count("redeem_token") == before


async def test_an_unexecutable_action_type_is_refused(client: AsyncClient, fake: Any) -> None:
    """The platform carrying out something nobody wrote a saga for is how an
    unreviewed side effect happens."""
    await propose(client, type="RESTRUCTURE")
    response = await execute(client)
    assert response.status_code == 403
    assert not [c for c in fake.calls if c[0].startswith("core_")]


async def test_an_unknown_action_is_a_not_found(client: AsyncClient) -> None:
    response = await execute(client, action_id="act_01JQZK7M8N9P0Q1R2S3T4V5W99")
    assert response.status_code == 404


async def test_an_outage_leaves_the_action_retryable(client: AsyncClient, fake: Any) -> None:
    """A service that could not answer is not a decision. The action waits."""
    await propose(client)
    fake.activate = UpstreamError("core_stub: connection refused")

    response = await execute(client)
    assert response.status_code == 409
    stored = (await client.get(f"/actions/{ACTION_ID}")).json()
    assert stored["state"] == "FAILED"


# ---------------------------------------------------------------------------
# L1 side effects
# ---------------------------------------------------------------------------
async def test_a_document_request_is_sent_and_nothing_is_written_to_the_core(
    client: AsyncClient, fake: Any
) -> None:
    await propose(client, level="L1", type="REQUEST_DOCUMENT", parameters={"evidence": "PAYSLIP"})
    response = await execute(client)

    assert response.status_code == 200
    assert [c[0] for c in fake.calls if c[0] == "notify"]
    assert not [c for c in fake.calls if c[0].startswith("core_")]
