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
    "DocumentOutcome",
    "FeatureOutcome",
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
    #: The terms the facility would be written on. Carried from the frozen
    #: snapshot so execution writes the amount that was decided, not the one
    #: the core happens to hold when it is asked.
    tenor_months: int = 0
    instalment: str = "0.00"
    profit_rate: str = "0"


@dataclass
class PolicyOutcome:
    policy_result: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    required_authority: str = "CREDIT_OFFICER"
    evidence_coverage: float = 0.0


@dataclass
class ModelOutcome:
    """What the risk and fraud services said about this case.

    `available` is false when either could not answer. It is not a detail: the
    workflow forces a human route on it, because a model that did not run is
    not a model that found nothing (docs/13 §7).
    """

    available: bool = True
    risk: dict[str, Any] = field(default_factory=dict)
    fraud: dict[str, Any] = field(default_factory=dict)
    unavailable: list[str] = field(default_factory=list)

    @property
    def fraud_level(self) -> str:
        return str(self.fraud.get("level") or "NONE")

    @property
    def model_run_id(self) -> str | None:
        return self.risk.get("model_run_id")


@dataclass
class DocumentOutcome:
    """What the document service holds for this case."""

    documents: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    available: bool = True

    @property
    def identity_mismatch(self) -> bool:
        return any(str(f.get("code")) == "INT-08" for f in self.findings)


@dataclass
class FeatureOutcome:
    """The frozen feature snapshot the models scored."""

    snapshot_id: str = ""
    features: dict[str, Any] = field(default_factory=dict)
    available: bool = True


@dataclass
class CommitteeOutcome:
    """What the Council produced, or why it did not."""

    tier: str = "STANDARD"
    run_id: str | None = None
    factor_scores: list[dict[str, Any]] = field(default_factory=list)
    opinions: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    decision_record: dict[str, Any] = field(default_factory=dict)
    tier_reasons: list[str] = field(default_factory=list)
    timed_out: bool = False


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
    #: Set when the decision was made autonomously and drawn for review.
    sample_id: str | None = None
    action_id: str | None = None
    #: EXECUTED, or FAILED when the core refused and the action is waiting to
    #: be retried. The case is not closed until this reads EXECUTED.
    execution_state: str | None = None


@dataclass
class HumanDecisionSignal:
    """What an officer sends back into a waiting workflow."""

    actor_id: str
    role: str
    final_action: str
    conditions: list[str] = field(default_factory=list)
    override: bool = False
    override_reason: dict[str, Any] | None = None
