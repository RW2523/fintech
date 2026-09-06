"""Executing an approved action, and undoing it when the core refuses.

An activation is two writes to a core the platform does not own: create the
account and its schedule, then set the status. There is no transaction across
them, so the second failing leaves an account nobody decided to open. The saga
compensates: it sets the account back, records what it did, and leaves the
action retryable.

The rule everything here follows is that a half-finished write is worse than a
failed one. A failure that is recorded and retryable is recoverable. A write
that half-happened and was reported as failed is a member holding a facility
nobody knows about.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.clients import Clients, CoreRefusedError, UpstreamError
from cio_common.ids import new_id

__all__ = ["EXECUTABLE", "SagaFailureError", "SagaResult", "Step", "run_saga"]


@dataclass
class Step:
    """One call the saga made, kept whether it succeeded or not."""

    step_id: str
    name: str
    state: str
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "state": self.state,
            "request": self.request,
            "response": self.response,
            "error": self.error,
        }


@dataclass
class SagaResult:
    state: str = "RUNNING"
    core_refs: list[dict[str, Any]] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    detail: str | None = None
    compensated: bool = False


class SagaFailureError(RuntimeError):
    """The saga could not finish. Carries what it did before it stopped."""

    def __init__(self, message: str, result: SagaResult) -> None:
        super().__init__(message)
        self.result = result


async def _step(
    saga: SagaResult,
    name: str,
    request: dict[str, Any],
    call: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Run one call, recording it before and after.

    Recorded before it runs, not after: a step that never returns has to leave
    a trace, or a crash mid-write looks exactly like a write that never
    started.
    """
    step = Step(step_id=new_id("stp"), name=name, state="RUNNING", request=request)
    saga.steps.append(step)
    try:
        response = await call(request)
    except (CoreRefusedError, UpstreamError) as exc:
        step.state, step.error = "FAILED", str(exc)
        raise
    step.state = "DONE"
    step.response = dict(response)
    return dict(response)


def _key(action: dict[str, Any], suffix: str) -> str:
    """Idempotency keys derive from the action, never from the attempt.

    A retry must reach the same row in the core. A key generated per attempt
    would open a second facility for the same decision, which is the exact
    failure the key exists to prevent.
    """
    return f"exec-{action['action_id']}-{suffix}"


async def approve_financing(clients: Clients, action: dict[str, Any], token: str) -> SagaResult:
    """docs/08 §7 — activate, then set the status; compensate on failure."""
    saga = SagaResult()
    parameters = dict(action.get("parameters") or {})
    account_id: str | None = None

    try:
        activated = await _step(
            saga,
            "core.activate",
            {
                "member_id": action.get("member_id"),
                "product_code": parameters.get("product_code", "PF-STD"),
                "amount": str(parameters["amount"]),
                "tenor_months": int(parameters["tenor_months"]),
                "instalment": str(parameters["instalment"]),
                "profit_rate": str(parameters.get("profit_rate", "0")),
                "due_day": int(parameters.get("due_day", 1)),
                "idempotency_key": _key(action, "activate"),
            },
            lambda body: clients.core_activate(body, token),
        )
        account_id = activated.get("account_id")
        saga.core_refs.append({"system": "core", "kind": "account", "id": account_id})

        await _step(
            saga,
            "core.status",
            {
                "account_id": account_id,
                "status": "APPROVED",
                "idempotency_key": _key(action, "status"),
            },
            lambda body: clients.core_status(body, token),
        )

    except (CoreRefusedError, UpstreamError) as exc:
        saga.state = "FAILED"
        saga.detail = str(exc)
        # Only the second write needs undoing. If the first failed there is
        # nothing to compensate, and calling the core to roll back an account
        # that was never opened would be the saga inventing state.
        if account_id is not None:
            await _compensate(clients, saga, action, account_id, token)
        raise SagaFailureError(str(exc), saga) from exc

    saga.state = "DONE"
    return saga


async def _compensate(
    clients: Clients, saga: SagaResult, action: dict[str, Any], account_id: str, token: str
) -> None:
    """Put the account back, and say so if that fails too.

    A compensation that cannot run is the worst state the platform can be in,
    so it is recorded loudly rather than swallowed: somebody has to go and look
    at that account by hand.
    """
    request = {
        "account_id": account_id,
        "status": "ROLLBACK",
        "idempotency_key": _key(action, "rollback"),
    }
    try:
        await _step(saga, "core.compensate", request, lambda body: clients.core_status(body, token))
        saga.compensated = True
    except (CoreRefusedError, UpstreamError) as exc:
        saga.compensated = False
        saga.detail = (
            f"{saga.detail}; and the rollback failed too ({exc}): account {account_id} is open and unapproved"
        )


async def side_effect(clients: Clients, action: dict[str, Any], token: str) -> SagaResult:
    """L1 actions: ask somebody for something, or tell them something.

    One call, nothing to compensate. A reminder that was sent cannot be unsent,
    which is why L1 is limited to things it is safe to have sent twice.
    """
    saga = SagaResult()
    request = {
        "type": action["type"],
        "case_id": action.get("case_id"),
        "member_id": action.get("member_id"),
        "parameters": dict(action.get("parameters") or {}),
        "idempotency_key": _key(action, "notify"),
    }
    try:
        sent = await _step(saga, "notification.send", request, lambda body: clients.notify(body))
    except (CoreRefusedError, UpstreamError) as exc:
        saga.state = "FAILED"
        saga.detail = str(exc)
        raise SagaFailureError(str(exc), saga) from exc

    saga.core_refs.append({"system": "notification", "kind": "message", "id": sent.get("notification_id")})
    saga.state = "DONE"
    return saga


#: What execution-service will actually carry out, and how. An action type
#: absent from here is refused rather than attempted: the platform executing
#: something nobody wrote a saga for is how an unreviewed side effect happens.
EXECUTABLE: dict[str, Callable[..., Awaitable[SagaResult]]] = {
    "APPROVE_FINANCING": approve_financing,
    "REQUEST_DOCUMENT": side_effect,
    "SEND_REMINDER": side_effect,
    "CREATE_TASK": side_effect,
    "CREATE_NOTE": side_effect,
}


async def run_saga(clients: Clients, action: dict[str, Any], token: str) -> SagaResult:
    handler = EXECUTABLE.get(str(action["type"]))
    if handler is None:
        raise SagaFailureError(
            f"no saga is defined for {action['type']!r}",
            SagaResult(state="REFUSED", detail=f"unexecutable action type {action['type']!r}"),
        )
    return await handler(clients, action, token)
