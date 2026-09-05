"""T-011 — hard gates, affordability, exposure and authority (docs/05 §3).

Golden cases for every rule family plus the boundaries, because a gate that is
off by one is a gate that approves the wrong case.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import cio_contracts
from app.evaluate import evaluate_case, route_for_blockers
from app.packs import PolicyPack
from tests.conftest import clean_inputs


def result_for(pack: PolicyPack, **overrides: object) -> dict:
    return evaluate_case(pack, clean_inputs(**overrides))


def failed(result: dict) -> set[str]:
    return {r["rule_id"] for r in result["rules"] if r["result"] == "FAIL"}


# ---------------------------------------------------------------------------
# the clean case
# ---------------------------------------------------------------------------
def test_a_clean_case_passes_every_gate(std_pack: PolicyPack) -> None:
    result = result_for(std_pack)
    assert result["blockers"] == []
    assert result["flags"] == []
    assert result["required_authority"] == "CREDIT_OFFICER"
    assert result["routing_hint"] is None


def test_the_result_satisfies_the_contract(std_pack: PolicyPack) -> None:
    result = result_for(std_pack)
    body = {k: v for k, v in result.items() if k not in ("snapshot_id", "capacity_score")}
    cio_contracts.validate(body, "PolicyResult")


def test_every_rule_reports_the_evidence_it_read(std_pack: PolicyPack) -> None:
    """docs/05 §3.1 — evidence for exactly the inputs a rule touched."""
    for rule in result_for(std_pack)["rules"]:
        if rule["category"] != "ROUTING":
            assert rule["evidence_refs"], f"{rule['rule_id']} cites no evidence"
            assert all(e.startswith("ev_") for e in rule["evidence_refs"])


def test_evaluation_is_reproducible(std_pack: PolicyPack) -> None:
    first, second = result_for(std_pack), result_for(std_pack)
    assert failed(first) == failed(second)
    assert first["affordability"]["dsr"] == second["affordability"]["dsr"]
    assert first["required_authority"] == second["required_authority"]


# ---------------------------------------------------------------------------
# eligibility
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "overrides,rule_id,outcome",
    [
        ({"member_status": "DORMANT"}, "ELG-01", "INELIGIBLE"),
        ({"member_tenure_months": 5}, "ELG-02", "INELIGIBLE"),
        ({"identity_verified": False}, "ELG-03", "BLOCK_NORMAL_PATH"),
        ({"member_age": 17}, "ELG-04", "INELIGIBLE"),
        ({"requested_amount": Decimal("999")}, "ELG-05", "INELIGIBLE"),
        ({"requested_amount": Decimal("150001")}, "ELG-05", "INELIGIBLE"),
        ({"requested_tenor": 5}, "ELG-05", "INELIGIBLE"),
        ({"requested_tenor": 85}, "ELG-05", "INELIGIBLE"),
    ],
)
def test_eligibility_gates_block(std_pack: PolicyPack, overrides: dict, rule_id: str, outcome: str) -> None:
    result = result_for(std_pack, **overrides)
    assert rule_id in result["blockers"]
    entry = next(r for r in result["rules"] if r["rule_id"] == rule_id)
    assert entry["on_fail"] == outcome
    assert entry["reason_code"]


@pytest.mark.parametrize("tenure,blocked", [(5, True), (6, False), (7, False)])
def test_tenure_boundary_is_inclusive(std_pack: PolicyPack, tenure: int, blocked: bool) -> None:
    """ELG-02 is `>= 6`, so six months qualifies."""
    assert ("ELG-02" in result_for(std_pack, member_tenure_months=tenure)["blockers"]) is blocked


@pytest.mark.parametrize(
    "age,tenor,blocked",
    [
        (41, 24, False),
        (63, 24, False),  # 63 + 2 = 65, exactly at the ceiling
        (64, 24, True),  # 64 + 2 = 66, over
        (18, 84, False),
        (17, 12, True),
    ],
)
def test_age_plus_tenor_ceiling(std_pack: PolicyPack, age: int, tenor: int, blocked: bool) -> None:
    result = result_for(std_pack, member_age=age, requested_tenor=tenor)
    assert ("ELG-04" in result["blockers"]) is blocked


@pytest.mark.parametrize(
    "amount,blocked",
    [
        (Decimal("1000"), False),
        (Decimal("999.99"), True),
        (Decimal("150000"), False),
        (Decimal("150000.01"), True),
    ],
)
def test_amount_boundaries_are_inclusive(std_pack: PolicyPack, amount: Decimal, blocked: bool) -> None:
    result = result_for(std_pack, requested_amount=amount, income_verified_monthly=Decimal("40000"))
    assert ("ELG-05" in result["blockers"]) is blocked


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------
def test_missing_documents_ask_for_more_information(std_pack: PolicyPack) -> None:
    result = result_for(std_pack, documents_required_complete=False)
    assert "DOC-01" in result["blockers"]
    entry = next(r for r in result["rules"] if r["rule_id"] == "DOC-01")
    assert entry["on_fail"] == "MORE_INFORMATION_REQUIRED"
    assert entry["reason_code"] == "DOC-01"


@pytest.mark.parametrize("confidence,blocked", [(0.85, False), (0.8499, True), (0.99, False)])
def test_critical_field_confidence_boundary(std_pack: PolicyPack, confidence: float, blocked: bool) -> None:
    result = result_for(std_pack, documents_min_critical_confidence=confidence)
    assert ("DOC-04" in result["blockers"]) is blocked


# ---------------------------------------------------------------------------
# affordability
# ---------------------------------------------------------------------------
def test_a_dsr_over_the_limit_is_a_policy_exception(std_pack: PolicyPack) -> None:
    """S4 in docs/11: DSR 0.68 against a 0.60 limit."""
    result = result_for(std_pack, income_verified_monthly=Decimal("2000"), commitments_monthly=Decimal("900"))
    assert "AFF-01" in result["blockers"]
    assert result["affordability"]["dsr"] > 0.60
    assert result["required_authority"] == "SENIOR_OFFICER", (
        "a policy exception must be raised to the exception approver"
    )


def test_the_dsr_limit_boundary_passes_at_exactly_the_limit(std_pack: PolicyPack) -> None:
    # instalment on 8000 over 24m at 6.5% is 376.67
    result = result_for(
        std_pack, income_verified_monthly=Decimal("2000"), commitments_monthly=Decimal("823.33")
    )
    assert result["affordability"]["dsr"] == 0.6
    assert "AFF-01" not in result["blockers"]


def test_thin_headroom_raises_a_flag_not_a_blocker(std_pack: PolicyPack) -> None:
    """AFF-02 fails to FLAG:THIN_HEADROOM, which must not block (docs/05 §3.3)."""
    result = result_for(std_pack, income_verified_monthly=Decimal("1400"), commitments_monthly=Decimal("450"))
    assert "THIN_HEADROOM" in result["flags"]
    assert "AFF-02" not in result["blockers"]


def test_residual_income_floor_blocks(std_pack: PolicyPack) -> None:
    result = result_for(std_pack, income_verified_monthly=Decimal("1200"), commitments_monthly=Decimal("100"))
    assert "AFF-03" in result["blockers"]


def test_unverified_income_asks_for_more_information(std_pack: PolicyPack) -> None:
    result = result_for(std_pack, income_verified=False)
    assert "AFF-04" in result["blockers"]
    assert next(r for r in result["rules"] if r["rule_id"] == "AFF-04")["reason_code"] == "CAP-04"


def test_every_stress_case_is_reported(std_pack: PolicyPack) -> None:
    stress = result_for(std_pack)["affordability"]["stress"]
    assert {s["case"] for s in stress} == {"income-10%", "rate+2%", "commit+10%"}
    assert all("pass" in s and "dsr" in s for s in stress)


# ---------------------------------------------------------------------------
# exposure and authority
# ---------------------------------------------------------------------------
def test_exposure_above_the_grade_limit_blocks(std_pack: PolicyPack) -> None:
    result = result_for(
        std_pack,
        member_grade="E",
        member_total_exposure=Decimal("29000"),
        income_verified_monthly=Decimal("40000"),
    )
    assert "EXP-01" in result["blockers"]
    assert result["exposure"]["limit"] == "30000.00"


def test_exposure_at_exactly_the_limit_passes(std_pack: PolicyPack) -> None:
    result = result_for(
        std_pack,
        member_grade="E",
        member_total_exposure=Decimal("22000"),
        requested_amount=Decimal("8000"),
        income_verified_monthly=Decimal("40000"),
    )
    assert "EXP-01" not in result["blockers"]
    assert result["exposure"]["resulting"] == "30000.00"


@pytest.mark.parametrize(
    "amount,authority",
    [
        (Decimal("20000"), "CREDIT_OFFICER"),
        (Decimal("20001"), "SENIOR_OFFICER"),
        (Decimal("75000"), "SENIOR_OFFICER"),
        (Decimal("75001"), "CREDIT_COMMITTEE"),
    ],
)
def test_required_authority_follows_the_amount_band(
    std_pack: PolicyPack, amount: Decimal, authority: str
) -> None:
    result = result_for(std_pack, requested_amount=amount, income_verified_monthly=Decimal("60000"))
    assert result["required_authority"] == authority


def test_a_policy_exception_never_lowers_the_required_authority(std_pack: PolicyPack) -> None:
    """The exception approver raises authority; it must not reduce it."""
    result = result_for(
        std_pack,
        requested_amount=Decimal("90000"),
        income_verified_monthly=Decimal("3000"),
        commitments_monthly=Decimal("900"),
    )
    assert "AFF-01" in result["blockers"]
    assert result["required_authority"] == "CREDIT_COMMITTEE"


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "overrides,hint",
    [
        ({"fraud_level": "HIGH"}, "COMPLIANCE_REVIEW"),
        ({"identity_mismatch": "CRITICAL"}, "ENHANCED_ASSESSMENT"),
        ({"history_arrears_12m": 2}, "ENHANCED_ASSESSMENT"),
    ],
)
def test_routing_rules_set_the_hint(std_pack: PolicyPack, overrides: dict, hint: str) -> None:
    assert result_for(std_pack, **overrides)["routing_hint"] == hint


def test_critical_fraud_blocks_the_normal_path(std_pack: PolicyPack) -> None:
    result = result_for(std_pack, fraud_level="CRITICAL")
    assert "RT-02" in result["blockers"]
    assert route_for_blockers(["BLOCK_NORMAL_PATH"]) == ("COMPLIANCE_REVIEW", "COMPLIANCE")


def test_a_clean_case_triggers_no_routing_rule(std_pack: PolicyPack) -> None:
    routing = [r for r in result_for(std_pack)["rules"] if r["category"] == "ROUTING"]
    assert routing and all(r["result"] == "PASS" for r in routing)


# ---------------------------------------------------------------------------
# blocker precedence
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "outcomes,expected",
    [
        (["INELIGIBLE"], ("DECLINE", "OFFICER_REVIEW")),
        (["MORE_INFORMATION_REQUIRED"], ("MORE_INFORMATION_REQUIRED", "OFFICER_REVIEW")),
        (["POLICY_EXCEPTION_OR_DECLINE"], ("REVIEW", "SENIOR_REVIEW")),
        (["BLOCK_NORMAL_PATH"], ("COMPLIANCE_REVIEW", "COMPLIANCE")),
        (["COMPLIANCE_REVIEW"], ("COMPLIANCE_REVIEW", "COMPLIANCE")),
        # the worst blocker wins, whatever order they arrive in
        (["MORE_INFORMATION_REQUIRED", "BLOCK_NORMAL_PATH"], ("COMPLIANCE_REVIEW", "COMPLIANCE")),
        (["INELIGIBLE", "MORE_INFORMATION_REQUIRED"], ("DECLINE", "OFFICER_REVIEW")),
    ],
)
def test_the_worst_blocker_decides_the_route(outcomes: list[str], expected: tuple) -> None:
    assert route_for_blockers(outcomes) == expected


# ---------------------------------------------------------------------------
# shariah
# ---------------------------------------------------------------------------
def test_a_non_permitted_purpose_is_ineligible_under_shariah(shariah_pack: PolicyPack) -> None:
    result = evaluate_case(shariah_pack, clean_inputs(requested_purpose="DEBT_CONSOLIDATION"))
    assert "SHR-01" in result["blockers"]
    assert next(r for r in result["rules"] if r["rule_id"] == "SHR-01")["reason_code"] == "SHR-02"


def test_a_permitted_purpose_passes_under_shariah(shariah_pack: PolicyPack) -> None:
    result = evaluate_case(shariah_pack, clean_inputs(requested_purpose="EDUCATION"))
    assert "SHR-01" not in result["blockers"]


def test_a_rate_increase_restructure_is_ineligible_under_shariah(shariah_pack: PolicyPack) -> None:
    result = evaluate_case(shariah_pack, clean_inputs(restructure_type="RATE_INCREASE"))
    assert "SHR-04" in result["blockers"]


# ---------------------------------------------------------------------------
# evidence coverage
# ---------------------------------------------------------------------------
def test_coverage_falls_when_a_required_document_is_absent(std_pack: PolicyPack) -> None:
    full = result_for(std_pack)["evidence_coverage"]
    partial = result_for(
        std_pack,
        documents_present=("IDENTITY",),
        document_confidence={"IDENTITY": 0.97},
    )["evidence_coverage"]
    assert partial < full


def test_coverage_falls_when_a_document_is_below_confidence(std_pack: PolicyPack) -> None:
    low = result_for(
        std_pack,
        document_confidence={"IDENTITY": 0.4, "PAYSLIP_LATEST_3": 0.4, "EMPLOYMENT_CONFIRMATION": 0.4},
    )["evidence_coverage"]
    assert low < result_for(std_pack)["evidence_coverage"]
