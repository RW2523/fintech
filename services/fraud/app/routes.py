"""fraud-service endpoints (docs/07 §3, docs/08 §5)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app import repository
from app.anomaly import version_of
from app.assess import RULES_VERSION, assess
from app.db import session
from app.facts import GUARANTEE_HOPS, FactSource, HttpFactSource
from app.rules import ANOMALY_THRESHOLD, CONTACT_CHANGE_DAYS, VELOCITY_COUNT, VELOCITY_DAYS
from cio_common.errors import NotFound

router = APIRouter(tags=["fraud"])

#: Swapped for a static source in tests.
_source: FactSource | None = None


def fact_source() -> FactSource:
    global _source
    if _source is None:
        _source = HttpFactSource(
            core_url=os.environ.get("CORE_STUB_URL"), document_url=os.environ.get("DOCUMENT_URL")
        )
    return _source


def set_fact_source(source: FactSource | None) -> None:
    global _source
    _source = source


class AssessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=3, max_length=64)
    member_id: str = Field(pattern="^M-[0-9]{6}$")
    snapshot_id: str | None = None
    #: The isolation-forest score, when the caller has one. Advisory only.
    anomaly_score: float | None = Field(default=None, ge=0.0, le=1.0)


@router.post("/fraud/assess", summary="Assess a case for fraud and integrity")
async def assess_case(body: AssessRequest) -> dict[str, Any]:
    assessment = await assess(
        fact_source(),
        case_id=body.case_id,
        member_id=body.member_id,
        snapshot_id=body.snapshot_id,
        anomaly_score=body.anomaly_score,
    )
    async with session() as db:
        await repository.save(db, assessment)
    return assessment.as_contract()


@router.get("/fraud/signals/{case_id}", summary="The findings on a case")
async def signals(case_id: str) -> dict[str, Any]:
    async with session() as db:
        found = await repository.latest_for_case(db, case_id)
    if found is None:
        raise NotFound(f"no fraud assessment for case {case_id}")
    return found


@router.get("/fraud/assessments/{assessment_id}", summary="One recorded assessment")
async def get_assessment(assessment_id: str) -> dict[str, Any]:
    async with session() as db:
        found = await repository.find(db, assessment_id)
    if found is None:
        raise NotFound(f"no fraud assessment {assessment_id}")
    return found


@router.get("/fraud/graph/{case_id}", summary="The neighbourhood behind the findings")
async def case_graph(case_id: str) -> dict[str, Any]:
    """The subgraph an officer needs to judge a network finding.

    A guarantee ring cannot be read from one member's record, so the finding
    ships with the picture that makes it legible.
    """
    async with session() as db:
        found = await repository.load_graph(db, case_id=case_id)
    if found is None:
        raise NotFound(f"no fraud graph for case {case_id}")
    return found


def version_detail() -> dict[str, Any]:
    """What `GET /version` adds about the rules in force."""
    return {
        "version": RULES_VERSION,
        "service_build": "0.1.0",
        "available": True,
        "anomaly_model": version_of(),
        "rules": [
            "velocity",
            "contact_change",
            "duplicate_applicant",
            "reused_image",
            "identity_mismatch",
            "guarantor_cycle",
            "serial_guarantor",
            "guarantee_concentration",
            "anomaly",
        ],
        "thresholds": {
            "velocity": {"applications": VELOCITY_COUNT, "days": VELOCITY_DAYS},
            "contact_change_days": CONTACT_CHANGE_DAYS,
            "anomaly_score": ANOMALY_THRESHOLD,
            "guarantee_hops": GUARANTEE_HOPS,
        },
    }
