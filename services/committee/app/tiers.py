"""Choosing how much deliberation a case gets (docs/06 §8, docs/05 §2).

Three tiers, and the choice is made from the case rather than from a model's
appetite. A clean small application does not need six agents arguing about it,
and a case with a fraud finding does not become simple because it is small.

Tier selection happens before any agent runs, so the cost of a case is decided
by policy rather than discovered afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["TIERS", "Tier", "TierDecision", "select_tier"]

#: docs/05 §2 — the three tiers and what each costs.
TIERS = ("FAST", "STANDARD", "EXTENDED")

#: docs/06 §8 — per-agent timeout as a share of the tier budget.
AGENT_TIMEOUT_SHARE = 0.4

#: Budgets per tier: seconds of wall clock, and tokens across the run.
BUDGETS: dict[str, dict[str, int]] = {
    "FAST": {"seconds": 90, "tokens": 12_000},
    "STANDARD": {"seconds": 300, "tokens": 60_000},
    "EXTENDED": {"seconds": 600, "tokens": 120_000},
}

Tier = str


@dataclass(frozen=True, slots=True)
class TierDecision:
    """Which tier, and every reason that put the case there."""

    tier: Tier
    reasons: tuple[str, ...]
    budget: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "reasons": list(self.reasons),
            "budget": dict(self.budget),
            "agent_timeout_seconds": round(self.budget["seconds"] * AGENT_TIMEOUT_SHARE, 1),
        }


_SEVERITY = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def _at_least(level: str | None, minimum: str) -> bool:
    return _SEVERITY.get(str(level or "NONE").upper(), 0) >= _SEVERITY[minimum]


def select_tier(
    *,
    tier_rules: dict[str, Any],
    requested_amount: float,
    policy_result: dict[str, Any] | None = None,
    fraud_level: str | None = None,
    identity_mismatch: bool = False,
    active_hardship: bool = False,
    clean_12m: bool = True,
    top_band: bool = False,
    model_health: str = "GREEN",
) -> TierDecision:
    """The tier this case gets, and why.

    Extended triggers are checked first: a case that qualifies for the fast
    path on amount can still be pulled up by a finding, and the reverse is
    never true.
    """
    reasons: list[str] = []
    extended = tier_rules.get("extended_triggers") or {}

    if extended.get("top_band") and top_band:
        reasons.append("AMOUNT_TOP_BAND")
    minimum = extended.get("fraud_level_min")
    if minimum and _at_least(fraud_level, str(minimum).upper()):
        reasons.append(f"FRAUD_LEVEL_{str(fraud_level).upper()}")
    if extended.get("identity_mismatch") and identity_mismatch:
        reasons.append("IDENTITY_MISMATCH")
    if extended.get("active_hardship") and active_hardship:
        reasons.append("ACTIVE_HARDSHIP")
    if extended.get("contradiction") and (policy_result or {}).get("contradictions"):
        reasons.append("CONTRADICTION")
    if reasons:
        return TierDecision("EXTENDED", tuple(reasons), BUDGETS["EXTENDED"])

    # A model the platform does not trust is a reason for more deliberation,
    # not less: the Council is what compensates for it (docs/05 §5).
    if model_health.upper() != "GREEN":
        return TierDecision("STANDARD", (f"MODEL_HEALTH_{model_health.upper()}",), BUDGETS["STANDARD"])

    requires = tier_rules.get("fast_path_requires") or {}
    maximum = float(tier_rules.get("fast_path_max_amount") or 0)
    blocked: list[str] = []
    if requested_amount > maximum:
        blocked.append("AMOUNT_ABOVE_FAST_PATH")
    if requires.get("clean_12m") and not clean_12m:
        blocked.append("ARREARS_IN_12M")
    if requires.get("no_findings") and _at_least(fraud_level, "LOW"):
        blocked.append("FINDINGS_PRESENT")
    if requires.get("all_gates_pass") and (policy_result or {}).get("blockers"):
        blocked.append("GATE_FAILED")

    if blocked:
        return TierDecision("STANDARD", tuple(blocked), BUDGETS["STANDARD"])
    return TierDecision("FAST", ("FAST_PATH_ELIGIBLE",), BUDGETS["FAST"])
