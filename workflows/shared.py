"""Types shared between the underwriting workflow and its activities.

Kept free of imports the workflow sandbox forbids, so both sides can use them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "TASK_QUEUE",
    "CaseRef",
    "CommitteeOutcome",
    "DecisionOutcome",
    "HumanDecisionSignal",
    "ModelOutcome",
    "PolicyOutcome",
]

TASK_QUEUE = "cio-underwriting"


@dataclass
class CaseRef:
    """Which case is being underwritten, and under which frozen snapshot."""

    case_id: str
    snapshot_id: str = ""
    member_id: str = ""
    product_code: str = "PF-STD"
    requested_amount: str = "0.00"
    application_id: str = ""


@dataclass
class PolicyOutcome:
    policy_result: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    required_authority: str = "CREDIT_OFFICER"
    evidence_coverage: float = 0.0


@dataclass
class ModelOutcome:
    """Placeholder until the real services land in P3."""

    available: bool = True
    risk: dict[str, Any] = field(default_factory=dict)
    fraud: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommitteeOutcome:
    """Placeholder until the Council lands in P4."""

    tier: str = "STANDARD"
    run_id: str | None = None
    factor_scores: list[dict[str, Any]] = field(default_factory=list)
    opinions: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False


@dataclass
class DecisionOutcome:
    decision_record_id: str = ""
    recommendation: str = "REVIEW"
    route: str = "OFFICER_REVIEW"
    required_authority: str = "CREDIT_OFFICER"
    ledger_entry_id: str = ""
    token_id: str | None = None
    human_decision_id: str | None = None
    final_action: str | None = None


@dataclass
class HumanDecisionSignal:
    """What an officer sends back into a waiting workflow."""

    actor_id: str
    role: str
    final_action: str
    conditions: list[str] = field(default_factory=list)
    override: bool = False
    override_reason: dict[str, Any] | None = None
