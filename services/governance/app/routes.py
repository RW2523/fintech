"""governance-service endpoints (docs/07 §6, docs/08 §6)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app import inventory as model_inventory
from app.explain import InventedIdentifierError, build_explanation
from app.sources import ExplainSource, HttpExplainSource
from cio_common.errors import CioError, NotFound
from ml.common.registry import ArtifactError

router = APIRouter(tags=["governance"])

#: Swapped for a static source in tests.
_source: ExplainSource | None = None


def explain_source() -> ExplainSource:
    global _source
    if _source is None:
        _source = HttpExplainSource(
            {
                "decision": os.environ.get("DECISION_URL", "http://decision:8012"),
                "risk": os.environ.get("RISK_URL", "http://risk:8006"),
                "fraud": os.environ.get("FRAUD_URL", "http://fraud:8007"),
                "document": os.environ.get("DOCUMENT_URL", "http://document:8002"),
            }
        )
    return _source


def set_explain_source(source: ExplainSource | None) -> None:
    global _source
    _source = source


class ExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_record_id: str = Field(min_length=3, max_length=64)


@router.post("/explain/factors", summary="The structured explanation of a decision")
async def explain_factors(body: ExplainRequest) -> dict[str, Any]:
    """docs/07 §6 — the structure is authoritative; narratives come from it.

    A narrator may use only what this returns, and what this returns is
    checked against the records it was built from.
    """
    bundle = await explain_source().gather(body.decision_record_id)
    try:
        explanation = build_explanation(bundle)
    except InventedIdentifierError as exc:
        # Never return an explanation that names something the run did not
        # produce: a narrative built on it would read as proof.
        raise CioError(str(exc), unknown=sorted(exc.unknown)) from exc
    return explanation.as_dict()


@router.get("/governance/models", summary="Every trained model and what serves")
async def models() -> dict[str, Any]:
    rows = model_inventory.inventory()
    return {"families": rows, "trained": sum(1 for row in rows if row["trained"]), "total": len(rows)}


@router.get("/governance/models/{family}/card", summary="The serving model's card")
async def serving_card(family: str) -> dict[str, Any]:
    try:
        return model_inventory.model_card(family)
    except (ArtifactError, FileNotFoundError) as exc:
        raise NotFound(str(exc)) from exc


@router.get("/governance/models/{family}/{version}/card", summary="One model card")
async def card(family: str, version: str) -> dict[str, Any]:
    try:
        return model_inventory.model_card(family, version)
    except (ArtifactError, FileNotFoundError) as exc:
        raise NotFound(str(exc)) from exc


# ---------------------------------------------------------------------------
# the autonomy programme, seen from oversight (docs/05 §6, docs/09 §6)
# ---------------------------------------------------------------------------
@router.get("/governance/autonomy", summary="Where the dial is set on every product")
async def autonomy_overview() -> dict[str, Any]:
    """One place to see how much the platform is doing on its own.

    Read from the policy service rather than kept here, because a second copy
    of the dial is a second answer to the only question that matters: what is
    the setting right now. A product whose service cannot be reached is
    reported as unreadable, not as ADVISE: not knowing is not the same as
    knowing it is safe.
    """
    policy_url = os.environ.get("POLICY_URL", "http://policy:8004").rstrip("/")
    products: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            listed = await client.get(f"{policy_url}/policy/products")
            names = list(listed.json().get("products") or []) if listed.status_code == 200 else []
        except httpx.HTTPError:
            names = []
        if not names:
            names = ["PF-STD", "PF-SHARIAH"]

        for product in names:
            try:
                response = await client.get(f"{policy_url}/autonomy/{product}")
                response.raise_for_status()
            except httpx.HTTPError as exc:
                products.append({"product_code": product, "readable": False, "detail": str(exc)})
                continue
            dial = response.json()
            products.append(
                {
                    "product_code": product,
                    "readable": True,
                    "setting": dial.get("setting"),
                    "effective_setting": dial.get("effective_setting"),
                    "autonomy_version": dial.get("autonomy_version"),
                    "kill_switch": dial.get("kill_switch"),
                    "sampling": dial.get("sampling"),
                    "last_change": (dial.get("history") or [{}])[0],
                }
            )

    acting = [p for p in products if p.get("effective_setting") == "AUTONOMOUS_WITHIN_LIMITS"]
    stopped = [p for p in products if (p.get("kill_switch") or {}).get("enabled")]
    return {
        "products": products,
        "acting_alone": [p["product_code"] for p in acting],
        "stopped": [p["product_code"] for p in stopped],
        "unreadable": [p["product_code"] for p in products if not p.get("readable")],
    }


# ---------------------------------------------------------------------------
# override analytics (docs/07 §6, docs/09 §6)
# ---------------------------------------------------------------------------
#: docs/03 §7 — what a person may give as their reason for departing from the
#: recommendation. Listed here so a series can report a code that nobody used
#: as zero rather than omitting it, which reads as "never asked".
OVERRIDE_CODES = tuple(f"OVR-{i:02d}" for i in range(1, 13))

#: Above this, the Council disagreed enough that the case is worth looking at
#: whatever was decided (docs/05 §6, matching the pack's enhanced threshold).
HIGH_DISAGREEMENT = 0.60


@router.get("/governance/overrides", summary="Where people departed from the recommendation")
async def overrides(days: int = 90, product_code: str | None = None) -> dict[str, Any]:
    """A series over time and a breakdown by reason.

    An override is not a fault. It is the platform being told it was wrong,
    which is the most useful signal it produces, and a rate that falls to zero
    is a warning rather than a success: it usually means people stopped
    reading.
    """
    decision_url = os.environ.get("DECISION_URL", "http://decision:8012").rstrip("/")
    since = (datetime.now(UTC) - timedelta(days=max(days, 1))).isoformat()

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            answered = await client.get(
                f"{decision_url}/human-decisions", params={"since": since, "limit": 5000}
            )
            answered.raise_for_status()
        except httpx.HTTPError as exc:
            raise CioError("the decision service is unreachable", detail=str(exc)) from exc

    decisions = list(answered.json().get("decisions") or [])
    if product_code:
        decisions = [d for d in decisions if str(d.get("product_code") or product_code) == product_code]

    overridden = [d for d in decisions if d.get("override")]
    by_day: dict[str, dict[str, int]] = {}
    for decision in decisions:
        day = str(decision.get("decided_at") or "")[:10]
        if not day:
            continue
        bucket = by_day.setdefault(day, {"decisions": 0, "overrides": 0})
        bucket["decisions"] += 1
        if decision.get("override"):
            bucket["overrides"] += 1

    by_reason = dict.fromkeys(OVERRIDE_CODES, 0)
    for decision in overridden:
        code = str(decision.get("override_reason_code") or "")
        if code:
            by_reason[code] = by_reason.get(code, 0) + 1

    by_action: dict[str, int] = {}
    for decision in overridden:
        action = str(decision.get("final_action") or "UNKNOWN")
        by_action[action] = by_action.get(action, 0) + 1

    return {
        "window_days": days,
        "decisions": len(decisions),
        "overrides": len(overridden),
        # Reported as a share, because the count on its own says more about how
        # busy the month was than about how often people disagreed.
        "override_rate": round(len(overridden) / len(decisions), 4) if decisions else None,
        "series": [{"day": day, **counts} for day, counts in sorted(by_day.items())],
        "by_reason": by_reason,
        "by_action": by_action,
        "recent": overridden[:50],
    }


@router.get("/governance/attention", summary="Cases a compliance officer should read")
async def attention(days: int = 30, limit: int = 100) -> dict[str, Any]:
    """docs/09 §6 — the compliance list.

    Three things, and the reason each is here is stated on the row rather than
    left to be inferred from a column: the Council disagreed sharply, a person
    departed from the recommendation, or the platform decided alone.
    """
    decision_url = os.environ.get("DECISION_URL", "http://decision:8012").rstrip("/")
    since = (datetime.now(UTC) - timedelta(days=max(days, 1))).isoformat()

    async with httpx.AsyncClient(timeout=20.0) as client:
        queued, human, samples = await asyncio.gather(
            _read(client, f"{decision_url}/queue", limit=limit * 5),
            _read(client, f"{decision_url}/human-decisions", since=since, limit=limit * 5),
            _read(client, f"{decision_url}/samples", reviewed=False, limit=limit),
        )

    rows: list[dict[str, Any]] = []
    for decision in (queued or {}).get("decisions") or []:
        disagreement = decision.get("disagreement")
        if disagreement is not None and float(disagreement) > HIGH_DISAGREEMENT:
            rows.append(
                {
                    "case_id": decision.get("case_id"),
                    "decision_record_id": decision.get("decision_record_id"),
                    "why": "HIGH_DISAGREEMENT",
                    "detail": f"the Council's disagreement was {disagreement}",
                    "at": decision.get("created_at"),
                }
            )
        if decision.get("route") == "AUTONOMOUS":
            rows.append(
                {
                    "case_id": decision.get("case_id"),
                    "decision_record_id": decision.get("decision_record_id"),
                    "why": "DECIDED_ALONE",
                    "detail": f"routed {decision.get('route')} with no person involved",
                    "at": decision.get("created_at"),
                }
            )

    for decision in (human or {}).get("decisions") or []:
        if not decision.get("override"):
            continue
        rows.append(
            {
                "case_id": decision.get("case_id"),
                "decision_record_id": decision.get("decision_record_id"),
                "why": "OVERRIDE",
                "detail": (
                    f"{decision.get('actor_id')} chose {decision.get('final_action')} "
                    f"against a recommendation of {decision.get('recommendation')} "
                    f"({decision.get('override_reason_code')})"
                ),
                "at": decision.get("decided_at"),
            }
        )

    unreviewed = [s for s in (samples or {}).get("samples") or [] if s.get("overdue")]
    for sample in unreviewed:
        rows.append(
            {
                "case_id": sample.get("case_id"),
                "decision_record_id": sample.get("decision_record_id"),
                "why": "SAMPLE_OVERDUE",
                "detail": f"drawn for review by {sample.get('assigned_role')} and past its deadline",
                "at": sample.get("due_at"),
            }
        )

    rows.sort(key=lambda row: str(row.get("at") or ""), reverse=True)
    return {
        "window_days": days,
        "count": len(rows),
        "unavailable": [
            name
            for name, value in (("queue", queued), ("human_decisions", human), ("samples", samples))
            if value is None
        ],
        "cases": rows[:limit],
    }


async def _read(client: httpx.AsyncClient, url: str, **params: Any) -> dict[str, Any] | None:
    """A read that reports absence rather than inventing an empty list."""
    try:
        response = await client.get(url, params=params)
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    return dict(response.json())
