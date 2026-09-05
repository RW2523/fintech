"""Fixtures: a small registry standing in for the real tool catalogue."""

from __future__ import annotations

from typing import Any

import pytest

from cio_tools import (
    CallBudget,
    EvidenceSpec,
    Grant,
    GrantRegistry,
    PermittedUse,
    SideEffect,
    ToolContext,
    ToolRegistry,
    tool,
)

UNDERWRITING = PermittedUse.UNDERWRITING
COLLECTIONS = PermittedUse.COLLECTIONS


@pytest.fixture
def reg() -> ToolRegistry:
    """A registry with three tools that exercise the interesting paths."""
    registry = ToolRegistry(
        GrantRegistry(
            [
                Grant("credit_risk", "risk.score", max_calls=1),
                Grant("credit_risk", "history.get", max_calls=2),
                Grant("member_relationship", "history.get", max_calls=1),
                Grant("collections_copilot", "history.get", max_calls=1),
                Grant("intervention_planner", "evidence.request", max_calls=2),
            ]
        )
    )

    @tool(
        "risk.score",
        version="1.0",
        backing_service="risk",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["snapshot_id"],
            "properties": {"snapshot_id": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "required": ["model_run_id", "pd_12m", "grade"],
            "properties": {
                "model_run_id": {"type": "string"},
                "pd_12m": {"type": "number"},
                "grade": {"type": "string"},
                "conduct_score": {"type": "integer"},
            },
        },
        purpose_tags={UNDERWRITING},
        side_effects=SideEffect.READ,
        evidence=EvidenceSpec(
            type="MODEL_OUTPUT",
            source_system="risk-service",
            source_record_path="model_run_id",
            locator_from={"model_run_id": "model_run_id"},
            value_path="pd_12m",
        ),
        into=registry,
    )
    async def risk_score(snapshot_id: str) -> dict[str, Any]:
        """Calibrated probability of default and its conduct component."""
        return {
            "model_run_id": "mr_01JQZK7M8N9P0Q1R2S3T4V5W6X",
            "pd_12m": 0.021,
            "grade": "B",
            "conduct_score": 74,
        }

    @tool(
        "history.get",
        version="1.0",
        backing_service="member_intelligence",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["member_id"],
            "properties": {"member_id": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "required": ["member_id", "ontime_rate_24m"],
            "properties": {
                "member_id": {"type": "string"},
                "ontime_rate_24m": {"type": "number"},
                "arrears_12m": {"type": "integer"},
                "bureau_grade": {"type": "string"},
            },
        },
        purpose_tags={UNDERWRITING, COLLECTIONS},
        side_effects=SideEffect.READ,
        evidence=EvidenceSpec(
            type="CORE_FIELD",
            source_system="core.financing",
            source_record_path="member_id",
            locator_from={"field_path": "member_id"},
        ),
        # the bureau grade is an underwriting input, not a collections one
        field_purposes={"bureau_grade": frozenset({UNDERWRITING})},
        into=registry,
    )
    async def history_get(member_id: str) -> dict[str, Any]:
        """Repayment history for a member."""
        return {"member_id": member_id, "ontime_rate_24m": 0.96, "arrears_12m": 0, "bureau_grade": "A"}

    @tool(
        "evidence.request",
        version="1.0",
        backing_service="committee",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["question"],
            "properties": {"question": {"type": "string"}, "requested_evidence": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "required": ["recorded"],
            "properties": {"recorded": {"type": "boolean"}},
        },
        purpose_tags={UNDERWRITING, COLLECTIONS},
        side_effects=SideEffect.WRITE_PROPOSAL,
        into=registry,
    )
    async def evidence_request(question: str, requested_evidence: str | None = None) -> dict:
        """Register an evidence gap on the run. No side effects beyond the record."""
        return {"recorded": True}

    return registry


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(
        agent_id="credit_risk",
        run_id="run_01JQZK7M8N9P0Q1R2S3T4V5W6X",
        purpose=UNDERWRITING,
        principal="workflow",
        case_id="case_01JQZK7M8N9P0Q1R2S3T4V5W6X",
        member_id="M-000042",
        budget=CallBudget(),
    )
