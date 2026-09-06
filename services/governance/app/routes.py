"""governance-service endpoints (docs/07 §6, docs/08 §6)."""

from __future__ import annotations

import os
from typing import Any

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
