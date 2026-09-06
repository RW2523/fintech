"""Tools over the Longitudinal Member Intelligence engine (docs/06 §4).

What the four longitudinal agents are allowed to see: a member's own baseline,
what departed from it and when, what the models say about the next ninety days,
and where the state machine has put them.

Every one of these is a read. The longitudinal agents reason about a member who
has not applied for anything, and nothing they can call may change that
member's standing.
"""

from __future__ import annotations

from typing import Any

from ai.tools.client import services
from ai.tools.schemas import ARRAY_OF_OBJECTS, MEMBER_ID, OBJECT, inputs, optional
from cio_tools.registry import tool
from cio_tools.spec import EvidenceSpec, PermittedUse, SideEffect

READ = SideEffect.READ

#: Servicing and collections, not underwriting. A member's early-warning state
#: is not an input to a new application: the case for lending to somebody is
#: made on the application's own evidence.
WATCHING = {PermittedUse.SERVICING, PermittedUse.COLLECTIONS}

#: ANALYTIC_RESULT rather than a new evidence type: a temporal feature set is
#: a computation over core records, and the contract's list of evidence types
#: is what every consumer already knows how to read. Adding a type nobody
#: renders would make the evidence panel silently skip it.
FEATURE_EVIDENCE = EvidenceSpec(
    type="ANALYTIC_RESULT",
    source_system="lmi",
    source_record_path="member_id",
    locator_from={"field_path": "as_of"},
)


@tool(
    "features.temporal",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID, as_of=optional({"type": "string"})),
    output_schema=OBJECT,
    purpose_tags=WATCHING,
    side_effects=READ,
    backing_service="lmi",
    evidence=FEATURE_EVIDENCE,
)
async def features_temporal(member_id: str, as_of: str | None = None) -> dict[str, Any]:
    """A member's behaviour on a day, measured against their own history.

    `stale_days` matters as much as the numbers: features computed a fortnight
    ago describe a fortnight ago, and an agent reasoning about today from them
    should say so.
    """
    body = await services().get("lmi", f"/lmi/features/{member_id}", **({"as_of": as_of} if as_of else {}))
    return {
        "member_id": member_id,
        "as_of": body.get("as_of"),
        "stale_days": body.get("stale_days"),
        "due_events": body.get("due_events"),
        "features": body.get("features") or {},
        "seasonal": body.get("seasonal") or {},
    }


@tool(
    "baseline.get",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID, as_of=optional({"type": "string"})),
    output_schema=OBJECT,
    purpose_tags=WATCHING,
    side_effects=READ,
    backing_service="lmi",
    evidence=FEATURE_EVIDENCE,
)
async def baseline_get(member_id: str, as_of: str | None = None) -> dict[str, Any]:
    """What normal looks like for this member.

    The thing an officer argues with. "Is nine days late unusual" is answered
    by this and by nothing else on the case, and an agent that discusses a
    departure without it is discussing a number.
    """
    body = await services().get("lmi", f"/lmi/baseline/{member_id}", **({"as_of": as_of} if as_of else {}))
    return {
        "member_id": member_id,
        "as_of": body.get("as_of"),
        "baselines": body.get("baselines") or {},
    }


@tool(
    "changepoints.get",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID, as_of=optional({"type": "string"})),
    output_schema=OBJECT,
    purpose_tags=WATCHING,
    side_effects=READ,
    backing_service="lmi",
)
async def changepoints_get(member_id: str, as_of: str | None = None) -> dict[str, Any]:
    """Where this member's behaviour changed, and when it was noticed.

    Two dates per change and they are months apart: `cp_date` is where the
    drift began, which is what an agent should talk about, and `detected_at` is
    the earliest the platform could have said so.
    """
    body = await services().post(
        "lmi",
        "/lmi/detect",
        {"member_ids": [member_id], **({"as_of": as_of} if as_of else {})},
    )
    return {
        "member_id": member_id,
        "as_of": body.get("as_of"),
        "change_points": [
            {
                "signal": alarm.get("signal"),
                "began": alarm.get("cp_date"),
                "detected": alarm.get("detected_at"),
                "confirmed": (alarm.get("stats") or {}).get("confirmed"),
                "statistic": (alarm.get("stats") or {}).get("cusum"),
            }
            for alarm in (body.get("detections") or [])
        ],
    }


@tool(
    "state.get",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID),
    output_schema=OBJECT,
    purpose_tags=WATCHING,
    side_effects=READ,
    backing_service="lmi",
)
async def state_get(member_id: str) -> dict[str, Any]:
    """Where the state machine has put this member, and every move behind it.

    The transitions travel with the state because a label on its own is not an
    answer: an agent asked why a member is ELEVATED must be able to say which
    rule fired and what it saw.
    """
    body = await services().get("lmi", f"/lmi/state/{member_id}")
    return {
        "member_id": member_id,
        "state": body.get("state"),
        "since": body.get("since"),
        "rule": body.get("rule"),
        "reason": body.get("reason"),
        "evaluated": body.get("evaluated"),
        "transitions": body.get("transitions") or [],
    }


@tool(
    "lmi.score",
    version="1.0",
    input_schema=inputs(member_id=MEMBER_ID, as_of=optional({"type": "string"})),
    output_schema=OBJECT,
    purpose_tags=WATCHING,
    side_effects=READ,
    backing_service="lmi",
    evidence=EvidenceSpec(
        type="MODEL_OUTPUT",
        source_system="lmi",
        source_record_path="model_version",
        locator_from={"field_path": "member_id"},
    ),
)
async def lmi_score(member_id: str, as_of: str | None = None) -> dict[str, Any]:
    """Calibrated probabilities at 7, 30, 60 and 90 days, with intervals.

    The interval is on the rate among members scored alike, not on this
    member's outcome, and an agent quoting the point estimate without it is
    claiming a precision the model does not have.
    """
    body = await services().post(
        "lmi", "/lmi/score", {"member_id": member_id, **({"as_of": as_of} if as_of else {})}
    )
    return {
        "member_id": member_id,
        "model_version": body.get("model_version"),
        "stale_days": body.get("stale_days"),
        "features_missing": body.get("features_missing") or [],
        "scores": body.get("scores") or [],
    }


@tool(
    "survival.get",
    version="1.0",
    input_schema=inputs(member_id=optional(MEMBER_ID)),
    output_schema=OBJECT,
    purpose_tags=WATCHING,
    side_effects=READ,
    backing_service="lmi",
)
async def survival_get(member_id: str | None = None) -> dict[str, Any]:
    """Time to the next late payment, and time back from one.

    Hazard ratios rather than a black box, because "a missed deduction
    multiplies the hazard by 1.35" is a sentence an officer can act on and
    argue with.
    """
    body = await services().get("lmi", "/lmi/model")
    return {
        "member_id": member_id,
        "model_version": body.get("version"),
        "survival": body.get("survival") or {},
    }


@tool(
    "alerts.open",
    version="1.0",
    input_schema=inputs(member_id=optional(MEMBER_ID)),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=WATCHING,
    side_effects=READ,
    backing_service="lmi",
)
async def alerts_open(member_id: str | None = None) -> list[dict[str, Any]]:
    """What is already open on this member.

    So an agent proposing an intervention can see whether somebody is already
    acting on it. Two officers calling the same member about the same drift is
    the cheapest mistake this platform can make and the easiest to avoid.
    """
    body = await services().get("lmi", "/lmi/alerts", params={"member_id": member_id} if member_id else None)
    return list(body.get("alerts") or [])
