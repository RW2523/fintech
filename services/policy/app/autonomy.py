"""The Autonomy Dial and routing (docs/05 §6).

Autonomy is bounded and revocable. Every condition in `autonomous_conditions`
must hold before a case may route AUTONOMOUS; each one that fails is named in
`route_reasons`, so an auditor can see exactly why a human was involved.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

__all__ = ["AutonomyInputs", "RouteDecision", "band_for", "route", "sampling_draw"]

#: recommendation -> the route it takes before the dial is consulted.
_BASE_ROUTE = {
    "APPROVE": "OFFICER_REVIEW",
    "DECLINE": "OFFICER_REVIEW",
    "REVIEW": "OFFICER_REVIEW",
    "MORE_INFORMATION_REQUIRED": "OFFICER_REVIEW",
    "ENHANCED_ASSESSMENT": "ENHANCED_ASSESSMENT",
    "COMPLIANCE_REVIEW": "COMPLIANCE",
    "INTERVENE": "OFFICER_REVIEW",
    "MONITOR": "OFFICER_REVIEW",
    "DE_ESCALATE": "OFFICER_REVIEW",
}

_AUTHORITY_ROUTE = {
    "SENIOR_OFFICER": "SENIOR_REVIEW",
    "CREDIT_COMMITTEE": "COMMITTEE",
}

_SEVERITY_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


@dataclass(frozen=True, slots=True)
class AutonomyInputs:
    """Everything the dial consults beyond the record itself."""

    recommendation: str
    confidence: float | None
    disagreement: float | None
    challenger_open: bool
    required_authority: str
    requested_amount: float
    case_type: str
    snapshot_id: str
    hard_gate_exceptions: int = 0
    max_open_integrity_severity: str = "LOW"
    member_watchlist: bool = False
    active_hardship_arrangement: bool = False
    model_health: str = "GREEN"
    kill_switch_active: bool = False


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: str
    reasons: list[str] = field(default_factory=list)
    sampled: bool = False
    failed_conditions: list[str] = field(default_factory=list)


def band_for(amount: float, bands: list[dict[str, Any]]) -> dict[str, Any]:
    """The first band whose ceiling covers ``amount``."""
    for band in bands:
        ceiling = band.get("max_amount")
        if ceiling is None or amount <= ceiling:
            return band
    return bands[-1]


def sampling_draw(snapshot_id: str, rate: float) -> bool:
    """Deterministic per-snapshot draw, so a demo reproduces exactly.

    docs/05 §6 calls for `random_draw(seed=snapshot_id) < rate`; seeding from the
    snapshot means the same case always lands the same way.
    """
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(snapshot_id.encode("utf-8")).digest()
    draw = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return draw < rate


def route(
    autonomy: dict[str, Any], inputs: AutonomyInputs, dff: dict[str, Any] | None = None
) -> RouteDecision:
    """Decide where the case goes (docs/05 §6)."""
    reasons: list[str] = []

    # 1. The kill switch overrides everything, including the dial.
    if inputs.kill_switch_active:
        return RouteDecision(route="OFFICER_REVIEW", reasons=["KILL_SWITCH"])

    # 2. The base route comes from the recommendation, then authority raises it.
    base = _BASE_ROUTE.get(inputs.recommendation, "OFFICER_REVIEW")
    if inputs.recommendation not in ("COMPLIANCE_REVIEW", "ENHANCED_ASSESSMENT"):
        base = _AUTHORITY_ROUTE.get(inputs.required_authority, base)

    # 3. High disagreement escalates on its own.
    thresholds = (dff or {}).get("disagreement_thresholds", {})
    high = thresholds.get("enhanced_assessment", 0.60)
    moderate = thresholds.get("officer_review", 0.35)
    if inputs.disagreement is not None:
        if inputs.disagreement > high:
            base = "ENHANCED_ASSESSMENT"
            reasons.append("DISAGREEMENT_HIGH")
        elif inputs.disagreement > moderate and base == "AUTONOMOUS":
            base = "OFFICER_REVIEW"
            reasons.append("DISAGREEMENT_MODERATE")

    setting = autonomy.get("setting", "ADVISE")
    if setting != "AUTONOMOUS_WITHIN_LIMITS":
        return RouteDecision(route=base, reasons=[*reasons, f"SETTING:{setting}"])

    # 4. Every condition must hold (docs/05 §6).
    conditions = autonomy["autonomous_conditions"]
    band = band_for(inputs.requested_amount, autonomy["bands"])
    max_severity = conditions.get("integrity_findings_open_max_severity", "LOW")

    checks = {
        "BAND": bool(band.get("autonomous_eligible")),
        "REC": inputs.recommendation in conditions["recommendation_in"],
        "CONF": inputs.confidence is not None and inputs.confidence >= conditions["min_confidence"],
        "DISAGREE": inputs.disagreement is not None and inputs.disagreement <= conditions["max_disagreement"],
        "CHALLENGER": not inputs.challenger_open,
        "GATES": inputs.hard_gate_exceptions == conditions["hard_gate_exceptions"],
        "INTEGRITY": _SEVERITY_ORDER.index(inputs.max_open_integrity_severity)
        <= _SEVERITY_ORDER.index(max_severity),
        "WATCHLIST": inputs.member_watchlist is conditions["member_watchlist"],
        "HARDSHIP": inputs.active_hardship_arrangement is conditions["active_hardship_arrangement"],
        "MODEL_HEALTH": inputs.model_health == conditions["model_health"],
        "CASE_TYPE": inputs.case_type in conditions["case_type_in"],
    }

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        return RouteDecision(
            route=base,
            reasons=[*reasons, *(f"AUTONOMY_FAIL:{name}" for name in failed)],
            failed_conditions=failed,
        )

    sampled = sampling_draw(inputs.snapshot_id, autonomy["sampling"]["rate"])
    return RouteDecision(
        route="AUTONOMOUS",
        reasons=[*reasons, *(["SAMPLED"] if sampled else [])],
        sampled=sampled,
    )
