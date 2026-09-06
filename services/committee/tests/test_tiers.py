"""T-044 — tier selection (docs/05 §2, docs/06 §8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.tiers import AGENT_TIMEOUT_SHARE, BUDGETS, TIERS, select_tier

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def rules() -> dict[str, Any]:
    pack = yaml.safe_load((ROOT / "policy_packs" / "PF-STD" / "2026.09.1" / "policy.yaml").read_text())
    return dict(pack["tier_selection"])


def test_a_clean_small_case_takes_the_fast_path(rules: Any) -> None:
    decision = select_tier(tier_rules=rules, requested_amount=8000)
    assert decision.tier == "FAST"
    assert decision.reasons == ("FAST_PATH_ELIGIBLE",)


def test_an_amount_above_the_band_leaves_the_fast_path(rules: Any) -> None:
    decision = select_tier(tier_rules=rules, requested_amount=25000)
    assert decision.tier == "STANDARD"
    assert "AMOUNT_ABOVE_FAST_PATH" in decision.reasons


def test_a_finding_pulls_a_small_case_up(rules: Any) -> None:
    """A case with a fraud finding does not become simple because it is small."""
    decision = select_tier(tier_rules=rules, requested_amount=1000, fraud_level="MEDIUM")
    assert decision.tier == "EXTENDED"


def test_a_failed_gate_leaves_the_fast_path(rules: Any) -> None:
    decision = select_tier(
        tier_rules=rules, requested_amount=5000, policy_result={"blockers": [{"rule_id": "AFF-01"}]}
    )
    assert decision.tier == "STANDARD"
    assert "GATE_FAILED" in decision.reasons


def test_arrears_leave_the_fast_path(rules: Any) -> None:
    decision = select_tier(tier_rules=rules, requested_amount=5000, clean_12m=False)
    assert "ARREARS_IN_12M" in decision.reasons


def test_an_identity_mismatch_goes_to_the_top_tier(rules: Any) -> None:
    decision = select_tier(tier_rules=rules, requested_amount=5000, identity_mismatch=True)
    assert decision.tier == "EXTENDED"


def test_an_active_hardship_arrangement_goes_to_the_top_tier(rules: Any) -> None:
    decision = select_tier(tier_rules=rules, requested_amount=5000, active_hardship=True)
    assert decision.tier == "EXTENDED"


def test_a_model_the_platform_distrusts_gets_more_deliberation(rules: Any) -> None:
    """docs/05 §5 — the Council is what compensates for a degraded model."""
    for health in ("AMBER", "RED"):
        decision = select_tier(tier_rules=rules, requested_amount=5000, model_health=health)
        assert decision.tier == "STANDARD"
        assert f"MODEL_HEALTH_{health}" in decision.reasons


def test_every_reason_that_applies_is_recorded(rules: Any) -> None:
    decision = select_tier(
        tier_rules=rules, requested_amount=200000, fraud_level="HIGH", identity_mismatch=True
    )
    assert len(decision.reasons) >= 2


def test_a_higher_tier_gets_a_larger_budget() -> None:
    assert BUDGETS["FAST"]["seconds"] < BUDGETS["STANDARD"]["seconds"] < BUDGETS["EXTENDED"]["seconds"]
    assert BUDGETS["FAST"]["tokens"] < BUDGETS["EXTENDED"]["tokens"]


def test_an_agent_gets_a_share_of_the_tier_budget(rules: Any) -> None:
    """docs/06 §8 — 40% of the tier budget, so one slow agent cannot eat it."""
    decision = select_tier(tier_rules=rules, requested_amount=8000)
    assert decision.as_dict()["agent_timeout_seconds"] == round(
        decision.budget["seconds"] * AGENT_TIMEOUT_SHARE, 1
    )


def test_every_tier_is_one_of_the_declared_three(rules: Any) -> None:
    for amount in (1000, 20000, 500000):
        assert select_tier(tier_rules=rules, requested_amount=amount).tier in TIERS
