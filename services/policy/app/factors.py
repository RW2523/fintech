"""Decision Factor scoring (docs/05 §4).

Each family's score comes from the tool that owns it. This module holds the
deterministic formulas those tools run, so a score is reproducible from its
inputs and carries a `calc_id`. A model never produces one (CLAUDE.md §2.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from cio_common.hashing import canonical_json, sha256
from cio_common.ids import new_id

__all__ = [
    "FAMILIES",
    "FAMILY_TOOLS",
    "FactorScore",
    "commitment_score",
    "conditions_score",
    "conduct_score",
    "integrity_score",
    "score_family",
]

FAMILIES = ("CAPACITY", "CONDUCT", "COMMITMENT", "CONDITIONS", "INTEGRITY")

#: family -> the tool that owns it (docs/05 §4, docs/06 §2.1)
FAMILY_TOOLS = {
    "CAPACITY": "affordability.compute",
    "CONDUCT": "risk.score",
    "COMMITMENT": "member.commitment_score",
    "CONDITIONS": "portfolio.conditions",
    "INTEGRITY": "fraud.assess",
}

#: docs/05 §4 INTEGRITY formula. CRITICAL is a hard gate, never a deduction.
SEVERITY_POINTS = {"LOW": 5, "MEDIUM": 20, "HIGH": 45}

_GRADE_POINTS = {"A": 20, "B": 16, "C": 12, "D": 6, "E": 0}


@dataclass(frozen=True, slots=True)
class FactorScore:
    family: str
    score: int
    calc_id: str
    tool: str
    inputs_digest: str
    evidence_refs: tuple[str, ...] = ()
    level: str | None = None

    def as_contract(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "family": self.family,
            "score": self.score,
            "calc_id": self.calc_id,
            "tool": self.tool,
            "inputs_digest": self.inputs_digest,
            "evidence_refs": list(self.evidence_refs),
        }
        if self.level is not None:
            body["level"] = self.level
        return body


def _clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def _finish(
    family: str,
    score: float,
    inputs: dict[str, Any],
    evidence_refs: tuple[str, ...],
    level: str | None = None,
) -> FactorScore:
    return FactorScore(
        family=family,
        score=_clamp(score),
        calc_id=new_id("calc"),
        tool=FAMILY_TOOLS[family],
        inputs_digest=sha256(canonical_json(inputs)),
        evidence_refs=evidence_refs,
        level=level,
    )


def conduct_score(
    *,
    ontime_rate_24m: float,
    any_arrears: bool,
    months_since_last_arrears: float,
    restructures_36m: int,
    grade: str,
    history_months: int,
    evidence_refs: tuple[str, ...] = (),
) -> FactorScore:
    """CONDUCT — demonstrated repayment behaviour (docs/05 §4)."""
    inputs = {
        "ontime_rate_24m": ontime_rate_24m,
        "any_arrears": any_arrears,
        "months_since_last_arrears": months_since_last_arrears,
        "restructures_36m": restructures_36m,
        "grade": grade,
        "history_months": history_months,
    }
    if history_months < 6:
        # too little history to judge; the framework says park at neutral
        return _finish("CONDUCT", 55, inputs, evidence_refs)

    value = 40.0 * ontime_rate_24m
    value += min(25.0, months_since_last_arrears / 24 * 25) if any_arrears else 25.0
    value -= 15.0 * restructures_36m
    value += _GRADE_POINTS.get(grade, 0)
    return _finish("CONDUCT", value, inputs, evidence_refs)


def commitment_score(
    *,
    tenure_years: float,
    savings_balance: Decimal,
    proposed_instalment: Decimal,
    share_capital_units: int,
    product_min_share_units: int,
    savings_paused_months: int,
    evidence_refs: tuple[str, ...] = (),
) -> FactorScore:
    """COMMITMENT — the member's standing with the cooperative (docs/05 §4)."""
    inputs = {
        "tenure_years": tenure_years,
        "savings_balance": savings_balance,
        "proposed_instalment": proposed_instalment,
        "share_capital_units": share_capital_units,
        "product_min_share_units": product_min_share_units,
        "savings_paused_months": savings_paused_months,
    }
    value = min(30.0, tenure_years * 3)

    denominator = Decimal(12) * proposed_instalment
    if denominator > 0:
        value += min(40.0, 40.0 * float(savings_balance / denominator))

    if product_min_share_units > 0:
        if share_capital_units >= product_min_share_units:
            value += 30.0
        else:
            value += 30.0 * share_capital_units / product_min_share_units
    else:
        value += 30.0

    if savings_paused_months >= 3:
        value -= 10.0
    return _finish("COMMITMENT", value, inputs, evidence_refs)


def integrity_score(
    *,
    open_findings: list[dict[str, Any]],
    evidence_refs: tuple[str, ...] = (),
) -> FactorScore:
    """INTEGRITY — deductions per open finding; CRITICAL is a gate, not a score."""
    inputs = {"open_findings": open_findings}
    severities = [str(f.get("severity", "LOW")).upper() for f in open_findings]
    deduction = sum(SEVERITY_POINTS.get(s, 0) for s in severities)

    level = "LOW"
    for candidate in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        if candidate in severities:
            level = candidate
            break
    if not severities:
        level = "LOW"

    return _finish("INTEGRITY", 100 - deduction, inputs, evidence_refs, level=level)


def conditions_score(
    *,
    employer_share_of_portfolio: float,
    sector_stress_flag: bool,
    evidence_refs: tuple[str, ...] = (),
) -> FactorScore:
    """CONDITIONS — portfolio concentration and sector stress (docs/05 §4)."""
    inputs = {
        "employer_share_of_portfolio": employer_share_of_portfolio,
        "sector_stress_flag": sector_stress_flag,
    }
    value = 100.0
    if employer_share_of_portfolio > 0.08:
        value -= 20.0
    elif employer_share_of_portfolio > 0.05:
        value -= 10.0
    if sector_stress_flag:
        value -= 15.0
    return _finish("CONDITIONS", value, inputs, evidence_refs)


def capacity_from_affordability(
    capacity_score: int,
    inputs_digest: str,
    calc_id: str,
    evidence_refs: tuple[str, ...] = (),
) -> FactorScore:
    """CAPACITY is produced by affordability.compute; this only wraps it."""
    return FactorScore(
        family="CAPACITY",
        score=capacity_score,
        calc_id=calc_id,
        tool=FAMILY_TOOLS["CAPACITY"],
        inputs_digest=inputs_digest,
        evidence_refs=evidence_refs,
    )


_SCORERS = {
    "CONDUCT": conduct_score,
    "COMMITMENT": commitment_score,
    "INTEGRITY": integrity_score,
    "CONDITIONS": conditions_score,
}


def score_family(family: str, inputs: dict[str, Any]) -> FactorScore:
    """Score one family from its named inputs."""
    if family == "CAPACITY":
        return capacity_from_affordability(**inputs)
    scorer = _SCORERS.get(family)
    if scorer is None:
        raise KeyError(f"unknown factor family {family!r}; known: {', '.join(FAMILIES)}")
    return scorer(**inputs)  # type: ignore[operator]
