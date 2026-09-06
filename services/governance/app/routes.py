"""governance-service endpoints (docs/07 §6, docs/08 §6)."""

from __future__ import annotations

import os
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
