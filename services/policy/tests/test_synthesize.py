"""T-012 — the Synthesizer hierarchy and the Autonomy Dial (docs/05 §5, §6).

The hierarchy is fixed: gates, then evidence, then authority, then the weighted
score, then confidence and disagreement, then the route. CLAUDE.md §8 forbids
reordering it, so each step is tested for the branch it owns and for the fact
that it stops the ones below it.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

import pytest

import cio_contracts
from app.autonomy import AutonomyInputs, route, sampling_draw
from app.context import PolicyInputs
from app.evaluate import evaluate_case
from app.factors import FactorScore
from app.packs import PolicyPack
from app.synthesize import (
    SynthesisInputs,
    confidence_of,
    disagreement_of,
    synthesize,
    weighted_score_of,
)
from tests.conftest import clean_inputs

SNAPSHOT = "snap_01JQZK7M8N9P0Q1R2S3T4V5W6X"
RUN = "run_01JQZK7M8N9P0Q1R2S3T4V5W6X"
CALC = "calc_01JQZK7M8N9P0Q1R2S3T4V5W6X"
MODELS = {"risk": "1.0", "fraud": "1.0", "delinquency": "1.0", "document_ai": "1.0", "embed": "1.0"}

COUNCIL = [
    "document_evidence",
    "policy_affordability",
    "credit_risk",
    "fraud_integrity",
    "member_relationship",
]


def factor(family: str, score: int, level: str | None = None) -> FactorScore:
    return FactorScore(
        family=family,
        score=score,
        calc_id=CALC,
        tool="test",
        inputs_digest="0" * 64,
        level=level or ("LOW" if family == "INTEGRITY" else None),
    )


def factors(**scores: int) -> tuple[FactorScore, ...]:
    base = {"CAPACITY": 100, "CONDUCT": 81, "COMMITMENT": 97, "CONDITIONS": 100, "INTEGRITY": 100}
    base.update(scores)
    return tuple(factor(f, s) for f, s in base.items())


def opinions(
    stances: list[str] | None = None,
    *,
    confidence: float = 0.9,
    unresolved: list[dict[str, Any]] | None = None,
    challenger_unresolved: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], ...]:
    stances = stances or ["SUPPORT"] * 5
    council = [
        {
            "opinion_id": f"op_01JQZK7M8N9P0Q1R2S3T4V5W{i:02d}",
            "agent_id": agent,
            "stance": stance,
            "confidence": confidence,
            "unresolved": (unresolved or []) if i == 0 else [],
        }
        for i, (agent, stance) in enumerate(zip(COUNCIL, stances, strict=False))
    ]
    if challenger_unresolved is not None:
        council.append(
            {
                "opinion_id": "op_01JQZK7M8N9P0Q1R2S3T4V5W99",
                "agent_id": "challenger",
                "stance": "REVIEW",
                "confidence": 0.8,
                "unresolved": challenger_unresolved,
            }
        )
    return tuple(council)


def synth(pack: PolicyPack, *, inputs: PolicyInputs | None = None, **overrides: Any) -> dict:
    policy_result = overrides.pop("policy_result", None) or evaluate_case(pack, inputs or clean_inputs())
    body = {
        "snapshot_id": SNAPSHOT,
        "case_type": "ORIGINATION",
        "tier": "FAST",
        "product_code": pack.product,
        "requested_amount": 8000.0,
        "policy_result": policy_result,
        "factors": factors(),
        "opinions": opinions(),
        "dff": pack.dff,
        "autonomy": pack.autonomy,
        "model_versions": MODELS,
        "committee_run_id": RUN,
    }
    body.update(overrides)
    return synthesize(SynthesisInputs(**body))


def as_contract(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in ("deciding_step", "sampled")}


# ---------------------------------------------------------------------------
# the record is always a valid contract
# ---------------------------------------------------------------------------
def test_a_clean_case_is_approved_and_valid(std_pack: PolicyPack) -> None:
    record = synth(std_pack)
    assert record["recommendation"] == "APPROVE"
    assert record["deciding_step"] == "WEIGHTED_SCORE"
    cio_contracts.validate(as_contract(record), "DecisionRecord")


@pytest.mark.parametrize(
    "case",
    [
        "clean",
        "gated",
        "evidence-gap",
        "integrity-critical",
        "low-score",
    ],
)
def test_every_branch_produces_a_valid_record(std_pack: PolicyPack, case: str) -> None:
    if case == "clean":
        record = synth(std_pack)
    elif case == "gated":
        record = synth(std_pack, inputs=clean_inputs(member_status="DORMANT"))
    elif case == "evidence-gap":
        record = synth(
            std_pack, opinions=opinions(unresolved=[{"question": "confirm salary", "blocking": True}])
        )
    elif case == "integrity-critical":
        record = synth(std_pack, factors=(*factors(INTEGRITY=0), factor("INTEGRITY", 0, level="CRITICAL")))
    else:
        record = synth(std_pack, factors=factors(CAPACITY=10, CONDUCT=10, COMMITMENT=10))
    cio_contracts.validate(as_contract(record), "DecisionRecord")


# ---------------------------------------------------------------------------
# 1. hard gates come first and stop everything below
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "overrides,recommendation,route_name",
    [
        ({"member_status": "DORMANT"}, "DECLINE", "OFFICER_REVIEW"),
        ({"identity_verified": False}, "COMPLIANCE_REVIEW", "COMPLIANCE"),
        ({"documents_required_complete": False}, "MORE_INFORMATION_REQUIRED", "OFFICER_REVIEW"),
        (
            {"income_verified_monthly": Decimal("2000"), "commitments_monthly": Decimal("900")},
            "REVIEW",
            "SENIOR_REVIEW",
        ),
    ],
)
def test_a_hard_gate_decides_before_anything_is_scored(
    std_pack: PolicyPack, overrides: dict, recommendation: str, route_name: str
) -> None:
    record = synth(std_pack, inputs=clean_inputs(**overrides))
    assert record["deciding_step"] == "HARD_GATES"
    assert record["recommendation"] == recommendation
    assert record["route"] == route_name
    assert record["weighted_score"] is None, "a gated case must never be scored"
    assert record["factor_scores"] == {}
    assert any(r.startswith("HARD_GATE:") for r in record["route_reasons"])


def test_a_unanimous_council_cannot_outvote_a_gate(std_pack: PolicyPack) -> None:
    """CLAUDE.md §8 — gates are never outvoted."""
    record = synth(
        std_pack,
        inputs=clean_inputs(member_status="DORMANT"),
        factors=factors(CAPACITY=100, CONDUCT=100, COMMITMENT=100),
        opinions=opinions(["SUPPORT"] * 5, confidence=1.0),
    )
    assert record["recommendation"] == "DECLINE"


def test_a_critical_integrity_finding_is_a_gate_not_a_deduction(std_pack: PolicyPack) -> None:
    record = synth(
        std_pack,
        factors=(
            factor("CAPACITY", 100),
            factor("CONDUCT", 100),
            factor("COMMITMENT", 100),
            factor("CONDITIONS", 100),
            factor("INTEGRITY", 100, level="CRITICAL"),
        ),
    )
    assert record["deciding_step"] == "INTEGRITY_CRITICAL"
    assert record["recommendation"] == "COMPLIANCE_REVIEW"
    assert record["route"] == "COMPLIANCE"
    assert record["weighted_score"] is None


# ---------------------------------------------------------------------------
# 2. evidence validity
# ---------------------------------------------------------------------------
def test_a_blocking_unresolved_item_asks_for_more_information(std_pack: PolicyPack) -> None:
    record = synth(
        std_pack, opinions=opinions(unresolved=[{"question": "confirm current salary", "blocking": True}])
    )
    assert record["deciding_step"] == "EVIDENCE_VALIDITY"
    assert record["recommendation"] == "MORE_INFORMATION_REQUIRED"
    assert record["route_reasons"] == ["BLOCKING_EVIDENCE_GAP"]
    assert record["weighted_score"] is None


def test_a_non_blocking_unresolved_item_does_not_stop_scoring(std_pack: PolicyPack) -> None:
    record = synth(
        std_pack, opinions=opinions(unresolved=[{"question": "confirm current salary", "blocking": False}])
    )
    assert record["deciding_step"] == "WEIGHTED_SCORE"
    assert record["weighted_score"] is not None


def test_coverage_below_the_minimum_asks_for_more_information(std_pack: PolicyPack) -> None:
    policy_result = evaluate_case(std_pack, clean_inputs())
    policy_result["evidence_coverage"] = 0.5
    record = synth(std_pack, policy_result=policy_result)
    assert record["recommendation"] == "MORE_INFORMATION_REQUIRED"
    assert record["route_reasons"] == ["EVIDENCE_COVERAGE_BELOW_MINIMUM"]


# ---------------------------------------------------------------------------
# 3. authority is attached, never bypassed
# ---------------------------------------------------------------------------
def test_authority_from_the_policy_result_is_carried_into_the_record(std_pack: PolicyPack) -> None:
    record = synth(
        std_pack,
        inputs=clean_inputs(requested_amount=Decimal("50000"), income_verified_monthly=Decimal("30000")),
        requested_amount=50000.0,
    )
    assert record["required_authority"] == "SENIOR_OFFICER"
    assert record["route"] == "SENIOR_REVIEW"


# ---------------------------------------------------------------------------
# 4. weighted score and the recommendation bands
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "score,recommendation",
    [
        (100, "APPROVE"),
        (70, "APPROVE"),
        (69, "REVIEW"),
        (45, "REVIEW"),
        (44, "DECLINE"),
        (0, "DECLINE"),
    ],
)
def test_the_recommendation_follows_the_thresholds(
    std_pack: PolicyPack, score: int, recommendation: str
) -> None:
    """approve >= 70, decline < 45, otherwise REVIEW (docs/05 §4)."""
    flat = tuple(factor(f, score) for f in ("CAPACITY", "CONDUCT", "COMMITMENT", "CONDITIONS", "INTEGRITY"))
    record = synth(std_pack, factors=flat)
    assert record["weighted_score"] == float(score)
    assert record["recommendation"] == recommendation


def test_the_weighted_score_uses_the_pack_weights(std_pack: PolicyPack) -> None:
    record = synth(
        std_pack, factors=factors(CAPACITY=100, CONDUCT=0, COMMITMENT=0, CONDITIONS=0, INTEGRITY=0)
    )
    assert record["weighted_score"] == 35.0
    assert record["factor_scores"]["CAPACITY"]["weight"] == 0.35
    assert record["factor_scores"]["CAPACITY"]["weighted"] == 35.0


def test_exactly_one_family_is_marked_decisive(std_pack: PolicyPack) -> None:
    scores = synth(std_pack)["factor_scores"]
    assert sum(1 for v in scores.values() if v["decisive"]) == 1


def test_the_decisive_family_is_the_one_furthest_from_the_mean(std_pack: PolicyPack) -> None:
    record = synth(std_pack, factors=factors(CONDUCT=0))
    decisive = [k for k, v in record["factor_scores"].items() if v["decisive"]]
    assert decisive == ["CAPACITY"], "the largest weighted contribution swing decides"


def test_weighted_score_normalises_over_the_families_present() -> None:
    weights = {"CAPACITY": 0.35, "CONDUCT": 0.30, "COMMITMENT": 0.20, "CONDITIONS": 0.10, "INTEGRITY": 0.05}
    assert weighted_score_of((factor("CAPACITY", 80),), weights) == 80.0
    assert weighted_score_of((factor("CAPACITY", 80), factor("CONDUCT", 40)), weights) == pytest.approx(
        61.5, abs=0.1
    )


# ---------------------------------------------------------------------------
# 5. confidence
# ---------------------------------------------------------------------------
def test_confidence_is_the_geometric_mean_of_its_three_parts() -> None:
    value = confidence_of(1.0, "GREEN", opinions(confidence=0.8))
    assert value == pytest.approx(0.928, abs=0.002)


@pytest.mark.parametrize("health,expected_order", [("GREEN", 0), ("AMBER", 1), ("RED", 2)])
def test_worse_model_health_lowers_confidence(health: str, expected_order: int) -> None:
    values = [confidence_of(1.0, h, opinions()) for h in ("GREEN", "AMBER", "RED")]
    assert values == sorted(values, reverse=True)
    assert values[expected_order] == confidence_of(1.0, health, opinions())


def test_confidence_is_zero_when_a_part_is_zero() -> None:
    assert confidence_of(0.0, "GREEN", opinions()) == 0.0
    assert confidence_of(1.0, "GREEN", opinions(confidence=0.0)) == 0.0


def test_the_challenger_does_not_count_towards_confidence() -> None:
    without = confidence_of(1.0, "GREEN", opinions(confidence=0.9))
    with_challenger = confidence_of(1.0, "GREEN", opinions(confidence=0.9, challenger_unresolved=[]))
    assert without == with_challenger


# ---------------------------------------------------------------------------
# 6. disagreement
# ---------------------------------------------------------------------------
def test_a_unanimous_council_has_no_disagreement(std_pack: PolicyPack) -> None:
    assert disagreement_of(opinions(["SUPPORT"] * 5), std_pack.dff["reliability_weights"]) == 0.0


def test_a_split_council_has_high_disagreement(std_pack: PolicyPack) -> None:
    split = disagreement_of(
        opinions(["SUPPORT", "SUPPORT", "OPPOSE", "OPPOSE", "REVIEW"]), std_pack.dff["reliability_weights"]
    )
    assert split is not None and split > 0.6


def test_disagreement_grows_as_the_council_spreads(std_pack: PolicyPack) -> None:
    weights = std_pack.dff["reliability_weights"]
    ordered = [
        disagreement_of(opinions(["SUPPORT"] * 5), weights),
        disagreement_of(opinions(["SUPPORT", "SUPPORT", "SUPPORT", "SUPPORT", "LEAN_SUPPORT"]), weights),
        disagreement_of(opinions(["SUPPORT", "SUPPORT", "REVIEW", "LEAN_OPPOSE", "OPPOSE"]), weights),
    ]
    assert ordered == sorted(ordered)  # type: ignore[type-var]


def test_the_challenger_is_excluded_from_disagreement(std_pack: PolicyPack) -> None:
    """Its job is to dissent; counting it would make every case look contested."""
    weights = std_pack.dff["reliability_weights"]
    assert (
        disagreement_of(
            opinions(["SUPPORT"] * 5, challenger_unresolved=[{"question": "q", "blocking": False}]), weights
        )
        == 0.0
    )


def test_stances_outside_the_scale_are_ignored(std_pack: PolicyPack) -> None:
    """BLOCK and NEED_MORE_EVIDENCE are handled by earlier steps (docs/05 §5)."""
    weights = std_pack.dff["reliability_weights"]
    assert (
        disagreement_of(opinions(["SUPPORT", "SUPPORT", "BLOCK", "NEED_MORE_EVIDENCE", "SUPPORT"]), weights)
        == 0.0
    )


def test_disagreement_is_none_without_any_scored_opinion(std_pack: PolicyPack) -> None:
    assert disagreement_of((), std_pack.dff["reliability_weights"]) is None


# ---------------------------------------------------------------------------
# 7. routing and the Autonomy Dial condition matrix
# ---------------------------------------------------------------------------
AUTONOMOUS_BASE = AutonomyInputs(
    recommendation="APPROVE",
    confidence=0.95,
    disagreement=0.10,
    challenger_open=False,
    required_authority="CREDIT_OFFICER",
    requested_amount=6000.0,
    case_type="ORIGINATION",
    snapshot_id=SNAPSHOT,
)


@pytest.fixture
def dial(std_pack: PolicyPack) -> dict:
    return {**std_pack.autonomy, "setting": "AUTONOMOUS_WITHIN_LIMITS"}


def test_a_clean_small_case_routes_autonomous(dial: dict, std_pack: PolicyPack) -> None:
    decision = route(dial, AUTONOMOUS_BASE, std_pack.dff)
    assert decision.route == "AUTONOMOUS"
    assert decision.failed_conditions == []


@pytest.mark.parametrize(
    "override,condition",
    [
        ({"requested_amount": 50000.0}, "BAND"),
        ({"recommendation": "REVIEW"}, "REC"),
        ({"confidence": 0.80}, "CONF"),
        ({"disagreement": 0.40}, "DISAGREE"),
        ({"challenger_open": True}, "CHALLENGER"),
        ({"hard_gate_exceptions": 1}, "GATES"),
        ({"max_open_integrity_severity": "MEDIUM"}, "INTEGRITY"),
        ({"member_watchlist": True}, "WATCHLIST"),
        ({"active_hardship_arrangement": True}, "HARDSHIP"),
        ({"model_health": "AMBER"}, "MODEL_HEALTH"),
        ({"case_type": "EARLY_WARNING"}, "CASE_TYPE"),
    ],
)
def test_each_autonomy_condition_alone_blocks_autonomous(
    dial: dict, std_pack: PolicyPack, override: dict, condition: str
) -> None:
    """docs/05 §6 — every condition must hold; each one is sufficient to stop it."""
    decision = route(dial, dataclasses.replace(AUTONOMOUS_BASE, **override), std_pack.dff)
    assert decision.route != "AUTONOMOUS"
    assert condition in decision.failed_conditions
    assert f"AUTONOMY_FAIL:{condition}" in decision.reasons


def test_the_kill_switch_overrides_the_dial(dial: dict, std_pack: PolicyPack) -> None:
    decision = route(dial, dataclasses.replace(AUTONOMOUS_BASE, kill_switch_active=True), std_pack.dff)
    assert decision.route == "OFFICER_REVIEW"
    assert decision.reasons == ["KILL_SWITCH"]


@pytest.mark.parametrize("setting", ["ADVISE", "ASSIST", "ACT_WITH_APPROVAL"])
def test_settings_below_autonomous_never_route_autonomous(std_pack: PolicyPack, setting: str) -> None:
    decision = route({**std_pack.autonomy, "setting": setting}, AUTONOMOUS_BASE, std_pack.dff)
    assert decision.route == "OFFICER_REVIEW"
    assert f"SETTING:{setting}" in decision.reasons


def test_high_disagreement_escalates_to_enhanced_assessment(std_pack: PolicyPack) -> None:
    decision = route(std_pack.autonomy, dataclasses.replace(AUTONOMOUS_BASE, disagreement=0.7), std_pack.dff)
    assert decision.route == "ENHANCED_ASSESSMENT"
    assert "DISAGREEMENT_HIGH" in decision.reasons


def test_authority_raises_the_route_above_officer(std_pack: PolicyPack) -> None:
    senior = route(
        std_pack.autonomy,
        dataclasses.replace(AUTONOMOUS_BASE, required_authority="SENIOR_OFFICER"),
        std_pack.dff,
    )
    committee = route(
        std_pack.autonomy,
        dataclasses.replace(AUTONOMOUS_BASE, required_authority="CREDIT_COMMITTEE"),
        std_pack.dff,
    )
    assert senior.route == "SENIOR_REVIEW"
    assert committee.route == "COMMITTEE"


def test_the_sampling_draw_is_deterministic_per_snapshot() -> None:
    """A demo must reproduce exactly (docs/11 §3)."""
    assert sampling_draw(SNAPSHOT, 0.10) == sampling_draw(SNAPSHOT, 0.10)
    assert sampling_draw(SNAPSHOT, 0.0) is False
    assert sampling_draw(SNAPSHOT, 1.0) is True


def test_the_sampling_rate_is_honoured_across_many_cases() -> None:
    hits = sum(sampling_draw(f"snap_{i:026d}", 0.10) for i in range(3000))
    assert 0.07 < hits / 3000 < 0.13, f"sampled {hits}/3000, expected about 10%"


# ---------------------------------------------------------------------------
# counterfactuals
# ---------------------------------------------------------------------------
def test_would_change_outcome_names_a_reachable_change(std_pack: PolicyPack) -> None:
    """docs/05 §5.1 — a change is reported only when it is within 25 points.

    At a weighted 63 the approve threshold is 7 weighted points away, which
    CAPACITY (weight 0.35) can close with 20 raw points.
    """
    record = synth(
        std_pack,
        factors=tuple(
            factor(f, 63) for f in ("CAPACITY", "CONDUCT", "COMMITMENT", "CONDITIONS", "INTEGRITY")
        ),
    )
    assert record["recommendation"] == "REVIEW"
    assert record["weighted_score"] == 63.0
    entries = record["would_change_outcome"]
    assert entries, "a reachable change must be offered"
    assert all("new_recommendation" in e for e in entries)
    assert len(entries) <= 4
    assert any("CAPACITY" in e["condition"] for e in entries)


def test_an_unreachable_change_is_not_offered(std_pack: PolicyPack) -> None:
    """At a weighted 58.5 no single family can cross the threshold alone."""
    record = synth(
        std_pack, factors=factors(CAPACITY=60, CONDUCT=55, COMMITMENT=60, CONDITIONS=60, INTEGRITY=60)
    )
    assert record["recommendation"] == "REVIEW"
    assert record["weighted_score"] == 58.5
    assert not any("CAPACITY" in e["condition"] for e in record["would_change_outcome"])


def test_a_blocking_gap_is_offered_as_a_counterfactual(std_pack: PolicyPack) -> None:
    record = synth(
        std_pack, opinions=opinions(unresolved=[{"question": "confirm current salary", "blocking": True}])
    )
    conditions = [e["condition"] for e in record["would_change_outcome"]]
    assert any("confirm current salary" in c for c in conditions)


# ---------------------------------------------------------------------------
# reproducibility
# ---------------------------------------------------------------------------
def test_the_same_inputs_produce_the_same_decision(std_pack: PolicyPack) -> None:
    first, second = synth(std_pack), synth(std_pack)
    for field in (
        "recommendation",
        "weighted_score",
        "confidence",
        "disagreement",
        "route",
        "required_authority",
        "deciding_step",
    ):
        assert first[field] == second[field], f"{field} is not reproducible"


def test_the_record_is_hashed_over_its_own_content(std_pack: PolicyPack) -> None:
    record = synth(std_pack)
    assert len(record["hash"]) == 64
    assert record["prev_hash"] == "0" * 64, "the ledger relinks this on append"


# ---------------------------------------------------------------------------
# T-050 — a Challenger reservation, and what it does and does not change
# ---------------------------------------------------------------------------
def _autonomous(pack: PolicyPack) -> dict[str, Any]:
    """The dial set to act alone, so a reservation has something to stop."""
    autonomy = dict(pack.autonomy)
    autonomy["setting"] = "AUTONOMOUS_WITHIN_LIMITS"
    return autonomy


def test_a_blocking_reservation_stops_the_case_at_the_evidence_step(std_pack: PolicyPack) -> None:
    """docs/05 §5 — evidence validity sits above the weighted score.

    This is the S2 shape: the case is sound on its factors, and the Challenger
    has a reservation the run could not close. The case is not weighed at all,
    which is a stronger answer than weighing it and routing it to a person.
    """
    record = synth(
        std_pack,
        autonomy=_autonomous(std_pack),
        tier="EXTENDED",
        opinions=opinions(
            challenger_unresolved=[
                {
                    "question": "the payslip is not reconciled against the deduction file",
                    "requested_evidence": "DEDUCTION_SCHEDULE",
                    "blocking": True,
                }
            ]
        ),
    )

    assert record["challenger_open"] is True
    assert record["recommendation"] == "MORE_INFORMATION_REQUIRED"
    assert record["route"] == "OFFICER_REVIEW"
    assert record["route_reasons"] == ["BLOCKING_EVIDENCE_GAP"]
    assert record["weighted_score"] is None, "a case held for evidence must not be scored"
    cio_contracts.validate(as_contract(record), "DecisionRecord")


def test_the_reservation_appears_in_what_would_change_the_outcome(std_pack: PolicyPack) -> None:
    """And what it would change is computed by running the same hierarchy with
    the gap closed, not by assuming an approval."""
    record = synth(
        std_pack,
        autonomy=_autonomous(std_pack),
        tier="EXTENDED",
        opinions=opinions(challenger_unresolved=[{"question": "income is unconfirmed", "blocking": True}]),
    )

    entries = [e for e in record["would_change_outcome"] if e["condition"].startswith("resolved:")]
    assert entries, "the reservation holding the case is not named as changing it"
    assert entries[0]["new_recommendation"] == "APPROVE"
    assert entries[0]["new_route"] == "AUTONOMOUS"


def test_the_counterfactual_is_what_the_case_actually_scores_without_the_gap(
    std_pack: PolicyPack,
) -> None:
    """Not an assumed approval, and not a second derivation either.

    The record's claim is checked against the same case run without the
    reservation, which is the only thing that makes the claim true.
    """
    weak = {
        "factors": factors(CAPACITY=40, CONDUCT=41, COMMITMENT=40, CONDITIONS=30, INTEGRITY=40),
        "autonomy": _autonomous(std_pack),
        "tier": "EXTENDED",
    }
    held = synth(
        std_pack,
        **weak,
        opinions=opinions(
            ["OPPOSE"] * 5,
            challenger_unresolved=[{"question": "income is unconfirmed", "blocking": True}],
        ),
    )
    resolved = synth(std_pack, **weak, opinions=opinions(["OPPOSE"] * 5))

    assert held["recommendation"] == "MORE_INFORMATION_REQUIRED"
    assert resolved["recommendation"] == "DECLINE", "the weak case must not score an approval"

    entries = [e for e in held["would_change_outcome"] if e["condition"].startswith("resolved:")]
    assert entries
    assert all(e["new_recommendation"] == resolved["recommendation"] for e in entries)


def test_only_a_blocking_reservation_is_offered_as_a_counterfactual(std_pack: PolicyPack) -> None:
    """A doubt the Challenger did not think fatal is not a thing whose
    resolution changes the outcome, and listing it would crowd out the ones
    that do."""
    record = synth(
        std_pack,
        autonomy=_autonomous(std_pack),
        tier="EXTENDED",
        opinions=opinions(
            challenger_unresolved=[{"question": "the employer letter is undated", "blocking": False}]
        ),
    )

    assert record["challenger_open"] is True
    assert not [e for e in record["would_change_outcome"] if e["condition"].startswith("resolved:")]


def test_a_non_blocking_reservation_still_stops_autonomy(std_pack: PolicyPack) -> None:
    """docs/05 §6 — the condition is that the Challenger has nothing open, not
    that what it has open is blocking. A reservation it did not think fatal is
    still a reservation nobody has answered."""
    record = synth(
        std_pack,
        autonomy=_autonomous(std_pack),
        tier="EXTENDED",
        opinions=opinions(
            challenger_unresolved=[{"question": "the employer letter is undated", "blocking": False}]
        ),
    )

    assert record["challenger_open"] is True
    assert record["route"] == "OFFICER_REVIEW"


def test_high_disagreement_escalates_beyond_an_officer(std_pack: PolicyPack) -> None:
    """docs/05 §6 — disagreement above the enhanced threshold routes on its own,
    whatever the dial is set to."""
    record = synth(
        std_pack,
        tier="EXTENDED",
        opinions=opinions(["SUPPORT", "SUPPORT", "OPPOSE", "OPPOSE", "BLOCK"]),
    )

    assert record["disagreement"] is not None
    assert record["disagreement"] > std_pack.dff["disagreement_thresholds"]["enhanced_assessment"]
    assert record["route"] == "ENHANCED_ASSESSMENT"
    assert "DISAGREEMENT_HIGH" in record["route_reasons"]


def test_moderate_disagreement_takes_an_autonomous_case_to_an_officer(std_pack: PolicyPack) -> None:
    record = synth(
        std_pack,
        autonomy=_autonomous(std_pack),
        tier="EXTENDED",
        opinions=opinions(["SUPPORT", "SUPPORT", "SUPPORT", "LEAN_SUPPORT", "REVIEW"]),
    )

    disagreement = record["disagreement"]
    thresholds = std_pack.dff["disagreement_thresholds"]
    assert thresholds["officer_review"] < disagreement <= thresholds["enhanced_assessment"]
    assert record["route"] == "OFFICER_REVIEW"


def test_a_repair_proposal_reaches_the_record(std_pack: PolicyPack) -> None:
    """The evidence a Tier 2 repair could not fetch is a request for a person,
    and the officer sees it on the case rather than in a log."""
    proposal = {
        "schema": "action_proposal/1.0",
        "action_id": "act_01JQZK7M8N9P0Q1R2S3T4V5W6X",
        "level": "L1",
        "type": "REQUEST_DOCUMENT",
        "parameters": {"evidence": "DEDUCTION_SCHEDULE", "blocking": True},
        "rationale": {"text": "the payslip is not reconciled", "evidence_refs": []},
        "requires": "OFFICER",
        "proposed_by": "challenger",
        "state": "PROPOSED",
    }
    record = synth(std_pack, tier="EXTENDED", proposed_actions=(proposal,))

    assert record["proposed_actions"] == [proposal]
    cio_contracts.validate(as_contract(record), "DecisionRecord")


# ---------------------------------------------------------------------------
# T-064 — an early-warning case (docs/08 §8.2, docs/05 §6)
# ---------------------------------------------------------------------------
def _intervention(level: str = "L2", action_type: str = "OFFICER_OUTREACH") -> dict[str, Any]:
    return {
        "schema": "action_proposal/1.0",
        "action_id": f"act_01JQZK7M8N9P0Q1R2S3T4V5W{level[-1]}X",
        "level": level,
        "type": action_type,
        "parameters": {},
        "rationale": {"text": "the member's payment timing has drifted", "evidence_refs": []},
        "requires": "OFFICER",
        "proposed_by": "intervention_planner",
        "state": "PROPOSED",
    }


def test_an_early_warning_case_cannot_approve_or_decline(std_pack: PolicyPack) -> None:
    """The member has a facility already and is not asking for another, so the
    only question is whether somebody should reach out, keep watching, or
    stop."""
    record = synth(std_pack, case_type="EARLY_WARNING", tier="STANDARD")
    assert record["recommendation"] in ("INTERVENE", "MONITOR", "DE_ESCALATE")


def test_a_member_behaving_well_stands_the_concern_down(std_pack: PolicyPack) -> None:
    record = synth(std_pack, case_type="EARLY_WARNING", tier="STANDARD")
    assert record["recommendation"] == "DE_ESCALATE"


def test_a_member_scoring_badly_is_worth_reaching_out_to(std_pack: PolicyPack) -> None:
    record = synth(
        std_pack,
        case_type="EARLY_WARNING",
        tier="STANDARD",
        factors=factors(CAPACITY=30, CONDUCT=31, COMMITMENT=30, CONDITIONS=30, INTEGRITY=40),
    )
    assert record["recommendation"] == "INTERVENE"


def test_an_early_warning_case_may_never_carry_an_l3_action(std_pack: PolicyPack) -> None:
    """A platform that can restructure a facility on the strength of a drift it
    noticed is a platform acting against somebody who never asked it to look."""
    record = synth(
        std_pack,
        case_type="EARLY_WARNING",
        tier="STANDARD",
        proposed_actions=(_intervention("L3", "RESTRUCTURE"), _intervention("L2")),
    )
    levels = {action["level"] for action in record["proposed_actions"]}
    assert levels == {"L2"}


def test_an_origination_case_keeps_its_l3_actions(std_pack: PolicyPack) -> None:
    record = synth(std_pack, proposed_actions=(_intervention("L3", "APPROVE_FINANCING"),))
    assert [action["level"] for action in record["proposed_actions"]] == ["L3"]


def test_an_early_warning_record_is_a_valid_contract(std_pack: PolicyPack) -> None:
    record = synth(
        std_pack,
        case_type="EARLY_WARNING",
        tier="STANDARD",
        proposed_actions=(_intervention("L2"), _intervention("L1", "REQUEST_DOCUMENT")),
    )
    cio_contracts.validate(as_contract(record), "DecisionRecord")


# ---------------------------------------------------------------------------
# when nothing was scored (T-082, docs/13 §7)
# ---------------------------------------------------------------------------
def test_no_factor_scored_is_not_a_score_of_zero() -> None:
    """A case nobody could assess is not a case that failed.

    `weighted_score_of` returned 0.0 when no factor was present, and 0.0 is
    below every decline threshold. Measured in the outage drill, with the model
    gateway stopped and a clean application submitted: recommendation DECLINE,
    on no evidence whatsoever. A member declined because a GPU was down is the
    failure this platform exists to make impossible.
    """
    assert weighted_score_of((), {"CAPACITY": 0.35}) is None
    assert weighted_score_of((factor("CAPACITY", 80),), {"CAPACITY": 0.35}) == 80.0


def test_a_case_with_no_factors_asks_for_more_rather_than_declining(std_pack: PolicyPack) -> None:
    record = synth(std_pack, factors=(), opinions=())

    assert record["recommendation"] == "MORE_INFORMATION_REQUIRED"
    assert record["route"] == "OFFICER_REVIEW"
    assert "NO_FACTOR_SCORED" in record["route_reasons"]
    # And no number is claimed. A weighted score of 0 would be a statement.
    assert record["weighted_score"] is None


def test_the_kill_switch_is_still_named_when_nothing_was_scored(std_pack: PolicyPack) -> None:
    """The route is the same either way; the reason is not.

    An operator reading a record has to be able to see that the switch is on.
    """
    record = synth(std_pack, factors=(), opinions=(), kill_switch_active=True)

    assert record["route_reasons"] == ["NO_FACTOR_SCORED", "KILL_SWITCH"]
