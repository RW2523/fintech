"""Turning platform events into audit entries (docs/04 §6).

Some things that must be audited happen after an irreversible side effect. A
facility is activated in the core, and then the platform records that it did.
Writing that record synchronously would mean an audit service that is down can
fail a write which already happened, and swallowing the failure would mean an
action nobody can account for. Neither is acceptable.

So those writers emit through the transactional outbox, in the same transaction
as the thing they are recording, and this consumer turns each event into an
audit entry. The event cannot be lost, the write cannot be blocked, and a
redelivery is a no-op because the consumer offset says it was already handled.
"""

from __future__ import annotations

from typing import Any

from app import trail
from cio_common.outbox import Event

__all__ = ["AUDITED", "consume", "entry_from"]

#: Which events become audit entries, and what the entry is called. Listed
#: rather than auditing everything: an outbox carries workflow chatter as well
#: as consequential acts, and an audit trail nobody can read is one nobody
#: checks.
AUDITED: dict[str, str] = {
    "action.executed": "action.executed",
    "action.failed": "action.failed",
    "autonomy.setting_changed": "autonomy.setting_changed",
    "kill_switch.activated": "kill_switch.activated",
    "kill_switch.released": "kill_switch.released",
    "decision.recorded": "decision.recorded",
    "human_decision.recorded": "human_decision.recorded",
    "token.issued": "token.issued",
    "sample.reviewed": "sample.reviewed",
}


def entry_from(event: Event) -> dict[str, Any]:
    """One audit entry, built from what the producer said happened.

    The actor is the producing service unless the payload names a person. A
    service acting on its own behalf says so rather than borrowing the identity
    of whoever last touched the case.
    """
    payload = dict(event.payload or {})
    actor_id = (
        payload.get("actor_id")
        or payload.get("decided_by")
        or payload.get("reviewer_id")
        or payload.get("activated_by")
        or payload.get("released_by")
    )
    actor = (
        {"id": str(actor_id), "role": str(payload.get("actor_role") or "UNKNOWN"), "kind": "PERSON"}
        if actor_id
        else {"id": f"svc-{event.producer}", "role": "SYSTEM", "kind": "SERVICE"}
    )

    return {
        "actor": actor,
        "action": AUDITED.get(event.name, event.name),
        "service": event.producer,
        "case_id": event.case_id or payload.get("case_id"),
        "run_id": payload.get("committee_run_id") or payload.get("run_id"),
        "object_ref": {
            key: payload[key]
            for key in (
                "action_id",
                "decision_record_id",
                "human_decision_id",
                "token_id",
                "sample_id",
                "product_code",
                "autonomy_version",
            )
            if key in payload
        },
        "before": payload.get("before"),
        "after": payload.get("after"),
        "policy_version": payload.get("policy_version"),
        "model_versions": payload.get("model_versions") or {},
        "trace_id": event.trace_id,
    }


async def consume(session_factory: Any, event: Event) -> None:
    """Append one audit entry for an event worth auditing."""
    if event.name not in AUDITED:
        return
    async with session_factory() as db:
        await trail.append(db, entry_from(event))
        await db.commit()
