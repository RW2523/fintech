"""Gate evaluation: rules in, PolicyResult out (docs/05 §3).

Categories run in a fixed order. A failing rule either blocks, raises a flag, or
sets a routing hint. Nothing here consults a model: this is the deterministic
floor that the Council sits on top of and can never outvote (CLAUDE.md §2.1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.affordability import AffordabilityInputs, AffordabilityResult, compute
from app.context import PolicyInputs, build_context, evidence_for
from app.expr import evaluate as run_rule
from app.packs import PolicyPack

__all__ = ["BLOCKING_OUTCOMES", "evaluate_case", "route_for_blockers"]

#: `on_fail` values that stop the normal path (docs/05 §3.3).
BLOCKING_OUTCOMES = frozenset(
    {
        "INELIGIBLE",
        "BLOCK_NORMAL_PATH",
        "MORE_INFORMATION_REQUIRED",
        "POLICY_EXCEPTION_OR_DECLINE",
        "COMPLIANCE_REVIEW",
    }
)

#: blocker outcome -> (recommendation, route). docs/05 §3.6.
_BLOCKER_ROUTES: dict[str, tuple[str, str]] = {
    "INELIGIBLE": ("DECLINE", "OFFICER_REVIEW"),
    "BLOCK_NORMAL_PATH": ("COMPLIANCE_REVIEW", "COMPLIANCE"),
    "COMPLIANCE_REVIEW": ("COMPLIANCE_REVIEW", "COMPLIANCE"),
    "MORE_INFORMATION_REQUIRED": ("MORE_INFORMATION_REQUIRED", "OFFICER_REVIEW"),
    "POLICY_EXCEPTION_OR_DECLINE": ("REVIEW", "SENIOR_REVIEW"),
}

#: Most severe first. A case with several blockers takes the worst.
_SEVERITY = [
    "BLOCK_NORMAL_PATH",
    "COMPLIANCE_REVIEW",
    "INELIGIBLE",
    "POLICY_EXCEPTION_OR_DECLINE",
    "MORE_INFORMATION_REQUIRED",
]


def route_for_blockers(outcomes: list[str]) -> tuple[str, str]:
    """The recommendation and route implied by the worst blocker present."""
    for outcome in _SEVERITY:
        if outcome in outcomes:
            return _BLOCKER_ROUTES[outcome]
    return ("REVIEW", "OFFICER_REVIEW")


def _affordability(pack: PolicyPack, inputs: PolicyInputs) -> AffordabilityResult:
    terms = pack.policy["product_terms"]
    rate = Decimal(str(terms.get("profit_rate", terms.get("markup_rate", 0))))
    affordability = pack.policy["affordability"]
    return compute(
        AffordabilityInputs(
            income_verified_monthly=inputs.income_verified_monthly,
            commitments_monthly=inputs.commitments_monthly,
            requested_amount=inputs.requested_amount,
            tenor_months=inputs.requested_tenor,
            profit_rate=rate,
            dsr_limit=Decimal(str(affordability["dsr_limit"])),
            residual_income_min=Decimal(str(affordability["residual_income_min"])),
            stress_cases=tuple(affordability["stress"]),
        )
    )


def _coverage(pack: PolicyPack, inputs: PolicyInputs, context: dict[str, Any]) -> float:
    """Share of the required inputs that are actually present and confident.

    Computed over the union of every rule's `reads`, plus the required document
    list (docs/05 §3.5).
    """
    read_keys = {key for rule in pack.rules() for key in rule["reads"]}
    resolved = sum(1 for key in read_keys if key in context)

    required_documents = pack.policy["documents"]["required"]
    minimum = pack.policy["documents"]["min_critical_confidence"]
    confident = sum(
        1
        for document in required_documents
        if document in inputs.documents_present
        and (inputs.document_confidence or {}).get(document, 1.0) >= minimum
    )

    total = len(read_keys) + len(required_documents)
    if total == 0:
        return 1.0
    return round((resolved + confident) / total, 4)


def evaluate_case(
    pack: PolicyPack,
    inputs: PolicyInputs,
    *,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Run every gate and return a PolicyResult (docs/03 §5)."""
    affordability = _affordability(pack, inputs)
    exposure_limit = pack.exposure_limit(inputs.member_grade)

    context = build_context(
        inputs,
        pack.policy,
        affordability={
            "dsr": affordability.dsr,
            "instalment": affordability.instalment,
            "stress": [s.as_dict() for s in affordability.stress],
        },
        exposure_limit=Decimal(str(exposure_limit)) if exposure_limit is not None else None,
    )

    rules: list[dict[str, Any]] = []
    blockers: list[str] = []
    blocker_outcomes: list[str] = []
    flags: list[str] = []

    for rule in pack.rules():
        refs = evidence_for(rule["reads"], context, pack.policy_version)
        outcome = run_rule(rule["rule"], context)
        passed = bool(outcome)

        entry: dict[str, Any] = {
            "rule_id": rule["id"],
            "category": rule["category"],
            "result": "PASS" if passed else "FAIL",
            "evidence_refs": [r["evidence_id"] for r in refs],
        }
        if not passed:
            on_fail = rule["on_fail"]
            entry["on_fail"] = on_fail
            entry["reason_code"] = rule["reason_code"]
            if on_fail.startswith("FLAG:"):
                flags.append(on_fail.removeprefix("FLAG:"))
            elif on_fail in BLOCKING_OUTCOMES:
                blockers.append(rule["id"])
                blocker_outcomes.append(on_fail)
        elif rule.get("reason_code"):
            entry["reason_code"] = rule["reason_code"]
        rules.append(entry)

    routing_hint: str | None = None
    for rule in pack.routing_rules():
        refs = evidence_for([], context, pack.policy_version)
        triggered = bool(run_rule(rule["when"], context))
        entry = {
            "rule_id": rule["id"],
            "category": "ROUTING",
            "result": "FAIL" if triggered else "PASS",
            "evidence_refs": [r["evidence_id"] for r in refs],
        }
        if rule.get("on_fail"):
            entry["on_fail"] = rule["on_fail"]
        rules.append(entry)
        if not triggered:
            continue
        if rule.get("on_fail") in BLOCKING_OUTCOMES:
            blockers.append(rule["id"])
            blocker_outcomes.append(rule["on_fail"])
        if rule["route"] != "ESCALATE" and routing_hint is None:
            routing_hint = rule["route"]

    # Authority: the smallest band that covers the amount, raised to the
    # exception approver when a policy exception is in play (docs/05 §3.4).
    required_authority = pack.authority_for(float(inputs.requested_amount))
    if "POLICY_EXCEPTION_OR_DECLINE" in blocker_outcomes:
        ladder = [b["role"] for b in pack.policy["authority"]["bands"]]
        approver = pack.policy["authority"]["exception_approver"]
        if ladder.index(approver) > ladder.index(required_authority):
            required_authority = approver

    exposure_now = float(inputs.member_total_exposure)
    requested = float(inputs.requested_amount)

    return {
        "schema": "policy_result/1.0",
        "policy_version": pack.policy_version,
        "product_code": pack.product,
        "evaluated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "snapshot_id": snapshot_id,
        "rules": rules,
        "blockers": blockers,
        "flags": flags,
        "affordability": affordability.as_contract(),
        "exposure": {
            "current": f"{exposure_now:.2f}",
            "requested": f"{requested:.2f}",
            "resulting": f"{exposure_now + requested:.2f}",
            "limit": f"{exposure_limit:.2f}" if exposure_limit is not None else "0.00",
            "calc_id": affordability.calc_id,
        },
        "required_authority": required_authority,
        "routing_hint": routing_hint,
        "evidence_coverage": _coverage(pack, inputs, context),
        "capacity_score": affordability.capacity_score,
    }
