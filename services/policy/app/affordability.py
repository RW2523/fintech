"""The affordability calculation (docs/05 §2, §4).

Every number a decision rests on is produced here and carries a ``calc_id``.
An agent may report these figures; it may never compute them (CLAUDE.md §2.1).
Money is Decimal throughout and crosses the wire as a string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from cio_common.hashing import canonical_json, sha256
from cio_common.ids import new_id

__all__ = ["AffordabilityInputs", "AffordabilityResult", "StressOutcome", "compute", "instalment_for"]

_CENTS = Decimal("0.01")


def _money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP)


def instalment_for(amount: Decimal, tenor_months: int, rate: Decimal) -> Decimal:
    """`amount * (1 + rate * tenor / 12) / tenor` (docs/05 §2 product_terms)."""
    if tenor_months <= 0:
        raise ValueError("tenor must be positive")
    total = amount * (Decimal(1) + rate * Decimal(tenor_months) / Decimal(12))
    return _money(total / Decimal(tenor_months))


@dataclass(frozen=True, slots=True)
class StressOutcome:
    case: str
    dsr: float
    residual: Decimal
    passed: bool

    def as_dict(self) -> dict[str, Any]:
        # `pass` is the contract's field name (docs/03 §5)
        return {"case": self.case, "dsr": self.dsr, "pass": self.passed}


@dataclass(frozen=True, slots=True)
class AffordabilityInputs:
    """Everything the calculation needs, and nothing else."""

    income_verified_monthly: Decimal
    commitments_monthly: Decimal
    requested_amount: Decimal
    tenor_months: int
    profit_rate: Decimal
    dsr_limit: Decimal
    residual_income_min: Decimal
    stress_cases: tuple[dict[str, Any], ...] = ()
    instalment_override: Decimal | None = None

    def digest(self) -> str:
        """Stable hash of the inputs; recorded so a calc can be reproduced."""
        return sha256(
            canonical_json(
                {
                    "income_verified_monthly": self.income_verified_monthly,
                    "commitments_monthly": self.commitments_monthly,
                    "requested_amount": self.requested_amount,
                    "tenor_months": self.tenor_months,
                    "profit_rate": self.profit_rate,
                    "dsr_limit": self.dsr_limit,
                    "residual_income_min": self.residual_income_min,
                    "stress_cases": list(self.stress_cases),
                    "instalment_override": self.instalment_override,
                }
            )
        )


@dataclass(frozen=True, slots=True)
class AffordabilityResult:
    calc_id: str
    inputs_digest: str
    instalment: Decimal
    dsr: float
    dsr_limit: float
    headroom: float
    residual: Decimal
    stress: tuple[StressOutcome, ...]
    capacity_score: int
    stress_failures: int
    evidence_refs: tuple[str, ...] = field(default=())

    def as_contract(self) -> dict[str, Any]:
        """The `affordability` block of PolicyResult (docs/03 §5)."""
        return {
            "dsr": self.dsr,
            "dsr_limit": self.dsr_limit,
            "headroom": self.headroom,
            "residual": f"{self.residual:.2f}",
            "stress": [s.as_dict() for s in self.stress],
            "instalment": f"{self.instalment:.2f}",
            "calc_id": self.calc_id,
            "evidence_refs": list(self.evidence_refs),
        }


def _ratio(numerator: Decimal, denominator: Decimal) -> float:
    if denominator <= 0:
        # No verified income means no capacity, not an error: the DOC/AFF gates
        # catch it and the case routes to a human.
        return float("inf")
    return round(float(numerator / denominator), 4)


def compute(inputs: AffordabilityInputs, *, evidence_refs: tuple[str, ...] = ()) -> AffordabilityResult:
    """Debt-service ratio, residual income, stress cases and the capacity score."""
    instalment = inputs.instalment_override or instalment_for(
        inputs.requested_amount, inputs.tenor_months, inputs.profit_rate
    )

    dsr = _ratio(inputs.commitments_monthly + instalment, inputs.income_verified_monthly)
    residual = _money(inputs.income_verified_monthly - inputs.commitments_monthly - instalment)
    tolerance = float(inputs.dsr_limit) + 0.05

    stress: list[StressOutcome] = []
    for case in inputs.stress_cases:
        income = inputs.income_verified_monthly * Decimal(str(case.get("income_factor", 1)))
        commitments = inputs.commitments_monthly * Decimal(str(case.get("commitments_factor", 1)))
        rate = inputs.profit_rate + Decimal(
            str(case.get("profit_rate_delta", case.get("markup_rate_delta", 0)))
        )
        case_instalment = inputs.instalment_override or instalment_for(
            inputs.requested_amount, inputs.tenor_months, rate
        )
        case_dsr = _ratio(commitments + case_instalment, income)
        stress.append(
            StressOutcome(
                case=str(case["case"]),
                dsr=case_dsr,
                residual=_money(income - commitments - case_instalment),
                passed=case_dsr <= tolerance,
            )
        )

    failures = sum(1 for s in stress if not s.passed)

    # dff.yaml CAPACITY formula, evaluated deterministically here.
    base = 100.0 - 120.0 * max(0.0, dsr - 0.35) if dsr != float("inf") else 0.0
    base -= 10.0 * failures
    if residual < inputs.residual_income_min * Decimal("1.25"):
        base -= 10.0
    capacity_score = max(0, min(100, round(base)))

    return AffordabilityResult(
        calc_id=new_id("calc"),
        inputs_digest=inputs.digest(),
        instalment=instalment,
        dsr=dsr,
        dsr_limit=float(inputs.dsr_limit),
        headroom=round(float(inputs.dsr_limit) - dsr, 4) if dsr != float("inf") else 0.0,
        residual=residual,
        stress=tuple(stress),
        capacity_score=capacity_score,
        stress_failures=failures,
        evidence_refs=evidence_refs,
    )
