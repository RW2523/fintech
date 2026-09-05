"""The Synthesizer: deterministic code turns opinions into a DecisionRecord.

The hierarchy in docs/05 §5 is fixed and must not be reordered (CLAUDE.md §8):

    hard gates -> evidence validity -> authority -> weighted score
    -> confidence -> disagreement -> Autonomy Dial route

An LLM never reaches this module. Opinions arrive as structured data; the
numbers come from tools; this code decides.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.autonomy import AutonomyInputs, route
from app.evaluate import route_for_blockers
from app.factors import FactorScore
from cio_common.hashing import GENESIS_HASH, canonical_json, sha256
from cio_common.ids import new_id

__all__ = [
    "STANCE_VALUES",
    "SynthesisInputs",
    "confidence_of",
    "disagreement_of",
    "model_reliability",
    "synthesize",
    "weighted_score_of",
]

#: docs/05 §5. BLOCK and NEED_MORE_EVIDENCE carry no numeric stance: they are
#: handled by the gate and evidence steps, not averaged into disagreement.
STANCE_VALUES = {
    "SUPPORT": 1.0,
    "LEAN_SUPPORT": 0.5,
    "REVIEW": 0.0,
    "LEAN_OPPOSE": -0.5,
    "OPPOSE": -1.0,
}

_MODEL_RELIABILITY = {"GREEN": 1.0, "AMBER": 0.85, "RED": 0.6}

#: The step of the hierarchy that decided, recorded for the officer UI.
_STEP_GATES = "HARD_GATES"
_STEP_INTEGRITY = "INTEGRITY_CRITICAL"
_STEP_EVIDENCE = "EVIDENCE_VALIDITY"
_STEP_SCORE = "WEIGHTED_SCORE"


@dataclass(frozen=True, slots=True)
class SynthesisInputs:
    """Everything the Synthesizer reads. Nothing else may influence a decision."""

    snapshot_id: str
    case_type: str
    tier: str
    product_code: str
    requested_amount: float
    policy_result: dict[str, Any]
    factors: tuple[FactorScore, ...]
    opinions: tuple[dict[str, Any], ...]
    dff: dict[str, Any]
    autonomy: dict[str, Any]
    model_versions: dict[str, str]
    committee_run_id: str | None = None
    model_health: str = "GREEN"
    kill_switch_active: bool = False
    member_watchlist: bool = False
    active_hardship_arrangement: bool = False
    budgets: dict[str, Any] = field(default_factory=dict)


def model_reliability(model_health: str) -> float:
    """GREEN 1.0, AMBER 0.85, RED 0.6 (docs/05 §5)."""
    return _MODEL_RELIABILITY.get(model_health, 0.6)


def weighted_score_of(factors: tuple[FactorScore, ...], weights: dict[str, float]) -> float:
    """Weight-normalised score over the families that were actually scored."""
    present = [f for f in factors if f.family in weights]
    total_weight = sum(weights[f.family] for f in present)
    if total_weight <= 0:
        return 0.0
    return round(sum(weights[f.family] * f.score for f in present) / total_weight, 1)


def confidence_of(evidence_coverage: float, model_health: str, opinions: tuple[dict[str, Any], ...]) -> float:
    """Geometric mean of evidence coverage, model reliability and agent confidence."""
    agent_values = [
        float(o["confidence"])
        for o in opinions
        if o.get("agent_id") != "challenger" and o.get("confidence") is not None
    ]
    agent_confidence = sum(agent_values) / len(agent_values) if agent_values else 0.0
    parts = [max(0.0, evidence_coverage), model_reliability(model_health), agent_confidence]
    if any(p <= 0 for p in parts):
        return 0.0
    return round(math.exp(sum(math.log(p) for p in parts) / len(parts)), 3)


def disagreement_of(
    opinions: tuple[dict[str, Any], ...], reliability_weights: dict[str, float]
) -> float | None:
    """Reliability-weighted standard deviation of the Council's stances.

    The Challenger is excluded: its job is to dissent, so counting it would
    make every case look contested (docs/05 §5).
    """
    points = [
        (STANCE_VALUES[o["stance"]], float(reliability_weights.get(o["agent_id"], 1.0)))
        for o in opinions
        if o.get("agent_id") != "challenger" and o.get("stance") in STANCE_VALUES
    ]
    if len(points) < 2:
        return 0.0 if points else None

    total_weight = sum(w for _, w in points)
    mean = sum(v * w for v, w in points) / total_weight
    variance = sum(w * (v - mean) ** 2 for v, w in points) / total_weight
    return round(math.sqrt(variance), 3)


def _mark_decisive(factor_scores: dict[str, dict[str, Any]]) -> None:
    """The family furthest from the mean contribution decided it (docs/05 §4)."""
    if not factor_scores:
        return
    contributions = {k: v["weighted"] for k, v in factor_scores.items()}
    mean = sum(contributions.values()) / len(contributions)
    decisive = max(contributions, key=lambda k: abs(contributions[k] - mean))
    for family, body in factor_scores.items():
        body["decisive"] = family == decisive


def _counterfactuals(
    inputs: SynthesisInputs,
    factor_scores: dict[str, dict[str, Any]],
    weighted: float | None,
) -> list[dict[str, Any]]:
    """What would have to change for the outcome to change (docs/05 §5.1)."""
    entries: list[dict[str, Any]] = []
    thresholds = inputs.dff["thresholds"]
    weights = inputs.dff["weights"]

    if weighted is not None and factor_scores:
        for target, recommendation in (("approve", "APPROVE"), ("decline", "DECLINE")):
            boundary = thresholds[target]
            gap = boundary - weighted
            if (recommendation == "APPROVE" and gap <= 0) or (recommendation == "DECLINE" and gap >= 0):
                continue
            # Highest weight first: it needs the smallest raw change, so the
            # first family within range is the closest one.
            for family in sorted(factor_scores, key=lambda f: -factor_scores[f]["weight"]):
                weight = weights[family]
                if weight <= 0:
                    continue
                points = gap / weight
                if abs(points) <= 25:
                    direction = "rose" if points > 0 else "fell"
                    entries.append(
                        {
                            "condition": f"{family} {direction} by {abs(points):.0f} points",
                            "new_recommendation": recommendation,
                        }
                    )
                    break

    for opinion in inputs.opinions:
        for unresolved in opinion.get("unresolved") or []:
            if unresolved.get("blocking"):
                entries.append(
                    {
                        "condition": f"resolved: {unresolved['question']}",
                        "new_recommendation": "APPROVE",
                    }
                )

    if "THIN_HEADROOM" in inputs.policy_result.get("flags", []):
        entries.append(
            {
                "condition": "income confirmed at the stated level",
                "new_recommendation": "APPROVE",
            }
        )

    return entries[:4]


def _empty_narrative() -> dict[str, Any]:
    """Narratives are generated last, by the orchestrator (docs/06 §8)."""
    blank = {"text": "", "status": "NONE"}
    return {"member": dict(blank), "officer": dict(blank), "auditor": dict(blank)}


def _skeleton(inputs: SynthesisInputs) -> dict[str, Any]:
    policy_result = inputs.policy_result
    return {
        "schema": "decision_record/1.0",
        "decision_record_id": new_id("dr"),
        "committee_run_id": inputs.committee_run_id,
        "snapshot_id": inputs.snapshot_id,
        "case_type": inputs.case_type,
        "tier": inputs.tier,
        "hard_gates": [
            {"rule_id": r["rule_id"], "result": r["result"], "evidence_refs": r.get("evidence_refs", [])}
            for r in policy_result["rules"]
            if r["category"] != "ROUTING"
        ],
        "evidence_coverage": policy_result["evidence_coverage"],
        "factor_scores": {},
        "weighted_score": None,
        "recommendation": "REVIEW",
        "confidence": None,
        "disagreement": None,
        "challenger_open": False,
        "route": "OFFICER_REVIEW",
        "route_reasons": [],
        "required_authority": policy_result["required_authority"],
        "narrative": _empty_narrative(),
        "would_change_outcome": [],
        "proposed_actions": [],
        "opinions": [o["opinion_id"] for o in inputs.opinions if "opinion_id" in o],
        "policy_version": policy_result["policy_version"],
        "dff_version": f"dff/{inputs.product_code}/{inputs.dff['version']}",
        "autonomy_version": f"autonomy/{inputs.product_code}/{inputs.autonomy['version']}",
        "model_versions": dict(inputs.model_versions),
        "budgets": {
            "tokens_used": 0,
            "seconds_used": 0.0,
            "tier_budget_tokens": 0,
            "tier_budget_seconds": 0.0,
            "exceeded": False,
            **inputs.budgets,
        },
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "prev_hash": GENESIS_HASH,
    }


def _finalise(record: dict[str, Any], deciding_step: str) -> dict[str, Any]:
    """Seal the record. decision-service relinks prev_hash when it appends."""
    record["deciding_step"] = deciding_step
    body = {k: v for k, v in record.items() if k not in ("hash", "deciding_step")}
    record["hash"] = sha256(canonical_json(body))
    return record


def synthesize(inputs: SynthesisInputs) -> dict[str, Any]:
    """Run the hierarchy and return a DecisionRecord without narratives."""
    record = _skeleton(inputs)
    policy_result = inputs.policy_result

    def route_now(recommendation: str, disagreement: float | None, confidence: float | None) -> None:
        decision = route(
            inputs.autonomy,
            AutonomyInputs(
                recommendation=recommendation,
                confidence=confidence,
                disagreement=disagreement,
                challenger_open=record["challenger_open"],
                required_authority=record["required_authority"],
                requested_amount=inputs.requested_amount,
                case_type=inputs.case_type,
                snapshot_id=inputs.snapshot_id,
                hard_gate_exceptions=len(policy_result["blockers"]),
                max_open_integrity_severity=_integrity_level(inputs.factors),
                member_watchlist=inputs.member_watchlist,
                active_hardship_arrangement=inputs.active_hardship_arrangement,
                model_health=inputs.model_health,
                kill_switch_active=inputs.kill_switch_active,
            ),
            inputs.dff,
        )
        record["route"] = decision.route
        record["route_reasons"] = [*record["route_reasons"], *decision.reasons]
        record["sampled"] = decision.sampled

    record["challenger_open"] = any(
        o.get("agent_id") == "challenger" and o.get("unresolved") for o in inputs.opinions
    )

    # ---- 1. hard gates -----------------------------------------------------
    if policy_result["blockers"]:
        outcomes = [
            r["on_fail"]
            for r in policy_result["rules"]
            if r["rule_id"] in policy_result["blockers"] and r.get("on_fail")
        ]
        recommendation, base_route = route_for_blockers(outcomes)
        record["recommendation"] = recommendation
        record["route"] = base_route
        record["route_reasons"] = ["HARD_GATE:" + ",".join(policy_result["blockers"])]
        if inputs.kill_switch_active:
            record["route"] = "OFFICER_REVIEW"
            record["route_reasons"].append("KILL_SWITCH")
        return _finalise(record, _STEP_GATES)

    # A critical integrity finding is a gate, never a weighted deduction.
    if inputs.dff.get("integrity_critical_is_gate", True) and _integrity_level(inputs.factors) == "CRITICAL":
        record["recommendation"] = "COMPLIANCE_REVIEW"
        record["route"] = "COMPLIANCE"
        record["route_reasons"] = ["INTEGRITY_CRITICAL"]
        return _finalise(record, _STEP_INTEGRITY)

    # ---- 2. evidence validity ---------------------------------------------
    blocking = [u for o in inputs.opinions for u in (o.get("unresolved") or []) if u.get("blocking")]
    minimum_coverage = inputs.dff["min_evidence_coverage"]
    if blocking or policy_result["evidence_coverage"] < minimum_coverage:
        record["recommendation"] = "MORE_INFORMATION_REQUIRED"
        record["route"] = "OFFICER_REVIEW"
        record["route_reasons"] = (
            ["BLOCKING_EVIDENCE_GAP"] if blocking else ["EVIDENCE_COVERAGE_BELOW_MINIMUM"]
        )
        record["would_change_outcome"] = _counterfactuals(inputs, {}, None)
        if inputs.kill_switch_active:
            record["route_reasons"].append("KILL_SWITCH")
        return _finalise(record, _STEP_EVIDENCE)

    # ---- 3. authority is attached, never bypassed -------------------------
    record["required_authority"] = policy_result["required_authority"]

    # ---- 4. weighted score ------------------------------------------------
    weights = inputs.dff["weights"]
    weighted = weighted_score_of(inputs.factors, weights)
    record["factor_scores"] = {
        f.family: {
            "score": f.score,
            "weight": weights[f.family],
            "weighted": round(weights[f.family] * f.score, 3),
            "decisive": False,
            "calc_id": f.calc_id,
        }
        for f in inputs.factors
        if f.family in weights
    }
    _mark_decisive(record["factor_scores"])
    record["weighted_score"] = weighted

    thresholds = inputs.dff["thresholds"]
    record["recommendation"] = (
        "APPROVE"
        if weighted >= thresholds["approve"]
        else "DECLINE"
        if weighted < thresholds["decline"]
        else "REVIEW"
    )

    # ---- 5. confidence -----------------------------------------------------
    record["confidence"] = confidence_of(
        policy_result["evidence_coverage"], inputs.model_health, inputs.opinions
    )

    # ---- 6. disagreement ---------------------------------------------------
    record["disagreement"] = disagreement_of(inputs.opinions, inputs.dff.get("reliability_weights", {}))

    # ---- 7. route ----------------------------------------------------------
    route_now(record["recommendation"], record["disagreement"], record["confidence"])
    record["would_change_outcome"] = _counterfactuals(inputs, record["factor_scores"], weighted)
    return _finalise(record, _STEP_SCORE)


def _integrity_level(factors: tuple[FactorScore, ...]) -> str:
    for factor in factors:
        if factor.family == "INTEGRITY" and factor.level:
            return factor.level
    return "LOW"
