"""Policy Sandbox: replay stored cases under a candidate pack (docs/05 §7).

The Board changes a weight and sees what it would have done to the last twelve
months before adopting it. Replay is deterministic and calls no model: stored
opinions are reused, gates and factor scoring re-run, and the Synthesizer
re-decides. The comparison is baseline versus candidate on the same cases.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.context import PolicyInputs
from app.evaluate import evaluate_case
from app.factors import FactorScore
from app.packs import PolicyPack
from app.synthesize import SynthesisInputs, synthesize

__all__ = ["ReplayCase", "ReplayReport", "apply_candidate", "replay", "summarise"]


@dataclass(frozen=True, slots=True)
class ReplayCase:
    """One decided case, frozen well enough to be re-decided."""

    snapshot_id: str
    product_code: str
    inputs: PolicyInputs
    factor_scores: tuple[FactorScore, ...]
    opinions: tuple[dict[str, Any], ...]
    baseline: dict[str, Any]
    segment: dict[str, str]
    pd_12m: float | None = None
    case_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReplayReport:
    baseline: dict[str, Any]
    candidate: dict[str, Any]
    segments: dict[str, dict[str, dict[str, Any]]]
    diffs: list[dict[str, Any]]
    cases_replayed: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "candidate": self.candidate,
            "segments": self.segments,
            "diffs": self.diffs,
            "cases_replayed": self.cases_replayed,
        }


def apply_candidate(pack: PolicyPack, candidate: dict[str, Any]) -> PolicyPack:
    """A pack with the candidate's patches applied. The original is untouched."""
    policy = copy.deepcopy(pack.policy)
    dff = copy.deepcopy(pack.dff)
    autonomy = copy.deepcopy(pack.autonomy)

    for kind, patch in candidate.items():
        if not isinstance(patch, dict):
            continue
        target = {"policy": policy, "dff": dff, "autonomy": autonomy}.get(kind)
        if target is None:
            continue
        _deep_update(target, patch)

    return PolicyPack(product=pack.product, version=pack.version, policy=policy, dff=dff, autonomy=autonomy)


def _deep_update(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _decide(pack: PolicyPack, case: ReplayCase) -> dict[str, Any]:
    policy_result = evaluate_case(pack, case.inputs, snapshot_id=case.snapshot_id)
    return synthesize(
        SynthesisInputs(
            snapshot_id=case.snapshot_id,
            case_type="ORIGINATION",
            tier="STANDARD",
            product_code=pack.product,
            requested_amount=float(case.inputs.requested_amount),
            policy_result=policy_result,
            factors=case.factor_scores,
            opinions=case.opinions,
            dff=pack.dff,
            autonomy=pack.autonomy,
            model_versions={},
        )
    )


def summarise(records: list[tuple[ReplayCase, dict[str, Any]]]) -> dict[str, Any]:
    """Portfolio-level effect of a set of decisions."""
    if not records:
        return {
            "cases": 0,
            "approval_rate": 0.0,
            "decline_rate": 0.0,
            "review_rate": 0.0,
            "autonomous_share": 0.0,
            "approved_exposure": "0.00",
            "projected_delinquency_12m": 0.0,
        }

    total = len(records)
    counts: dict[str, int] = defaultdict(int)
    approved_exposure = 0.0
    projected = 0.0
    autonomous = 0

    for case, record in records:
        counts[record["recommendation"]] += 1
        if record["route"] == "AUTONOMOUS":
            autonomous += 1
        if record["recommendation"] == "APPROVE":
            amount = float(case.inputs.requested_amount)
            approved_exposure += amount
            projected += (case.pd_12m or 0.0) * amount

    return {
        "cases": total,
        "approval_rate": round(counts["APPROVE"] / total, 4),
        "decline_rate": round(counts["DECLINE"] / total, 4),
        "review_rate": round(counts["REVIEW"] / total, 4),
        "more_information_rate": round(counts["MORE_INFORMATION_REQUIRED"] / total, 4),
        "compliance_rate": round(counts["COMPLIANCE_REVIEW"] / total, 4),
        "autonomous_share": round(autonomous / total, 4),
        "approved_exposure": f"{approved_exposure:.2f}",
        "projected_delinquency_12m": round(projected, 2),
    }


def _segment_summaries(
    records: list[tuple[ReplayCase, dict[str, Any]]], dimensions: tuple[str, ...]
) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for dimension in dimensions:
        buckets: dict[str, list[tuple[ReplayCase, dict[str, Any]]]] = defaultdict(list)
        for case, record in records:
            buckets[case.segment.get(dimension, "unknown")].append((case, record))
        out[dimension] = {key: summarise(rows) for key, rows in sorted(buckets.items())}
    return out


def replay(
    pack: PolicyPack,
    cases: list[ReplayCase],
    candidate: dict[str, Any],
    *,
    segments: tuple[str, ...] = ("grade", "branch", "employer_sector", "amount_band"),
    max_diffs: int = 200,
) -> ReplayReport:
    """Decide every case twice and report what moved."""
    candidate_pack = apply_candidate(pack, candidate)

    baseline_records: list[tuple[ReplayCase, dict[str, Any]]] = []
    candidate_records: list[tuple[ReplayCase, dict[str, Any]]] = []
    diffs: list[dict[str, Any]] = []

    for case in cases:
        before = case.baseline or _decide(pack, case)
        after = _decide(candidate_pack, case)
        baseline_records.append((case, before))
        candidate_records.append((case, after))

        if (before["recommendation"], before["route"]) != (
            after["recommendation"],
            after["route"],
        ) or before.get("weighted_score") != after.get("weighted_score"):
            decisive_before = _decisive(before)
            decisive_after = _decisive(after)
            diffs.append(
                {
                    "snapshot_id": case.snapshot_id,
                    "case_id": case.case_id,
                    "before": {
                        "recommendation": before["recommendation"],
                        "route": before["route"],
                        "weighted_score": before.get("weighted_score"),
                        "decisive": decisive_before,
                    },
                    "after": {
                        "recommendation": after["recommendation"],
                        "route": after["route"],
                        "weighted_score": after.get("weighted_score"),
                        "decisive": decisive_after,
                    },
                    "decisive_change": decisive_before != decisive_after,
                }
            )

    return ReplayReport(
        baseline=summarise(baseline_records),
        candidate=summarise(candidate_records),
        segments={
            "baseline": _segment_summaries(baseline_records, segments),
            "candidate": _segment_summaries(candidate_records, segments),
        },
        diffs=diffs[:max_diffs],
        cases_replayed=len(cases),
    )


def _decisive(record: dict[str, Any]) -> str | None:
    for family, body in (record.get("factor_scores") or {}).items():
        if body.get("decisive"):
            return family
    return None
