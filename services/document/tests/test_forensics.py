"""T-024 — forensics and reconciliation (docs/07 §1.4-§1.5).

Each check is pinned to an explicit case. The corpus-wide score lives in
`synthetic/detection.py`, which measures against the injected manifest.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.forensics import (
    CRITICAL_FIELDS,
    PHASH_THRESHOLD,
    check_arithmetic,
    check_layout_signature,
    check_metadata,
    check_readability,
    check_reused_image,
)
from app.reconcile import (
    INCOME_VARIANCE_HIGH,
    INCOME_VARIANCE_MEDIUM,
    MemberRecord,
    duplicate_identities,
    reconcile,
)

BALANCED = {"gross_salary": "3,360.00", "total_deductions": "1,008.00", "net_salary": "2,352.00"}


# ---------------------------------------------------------------------------
# arithmetic
# ---------------------------------------------------------------------------
def test_a_payslip_that_adds_up_raises_nothing() -> None:
    assert check_arithmetic(BALANCED) == []


def test_an_inflated_net_is_a_high_finding() -> None:
    """A forger raises the net without recomputing the deduction line."""
    findings = check_arithmetic({**BALANCED, "net_salary": "2,700.00"})
    assert len(findings) == 1
    assert findings[0].code == "INT-01"
    assert findings[0].severity == "HIGH"
    assert findings[0].detail["deviation"] == 348.0


def test_an_understated_net_is_reported_less_loudly() -> None:
    """More likely an OCR slip than a forgery, so it is not a HIGH."""
    findings = check_arithmetic({**BALANCED, "net_salary": "1,900.00"})
    assert findings and findings[0].severity == "MEDIUM"


def test_a_rounding_difference_is_tolerated() -> None:
    assert check_arithmetic({**BALANCED, "net_salary": "2,352.40"}) == []


def test_the_tolerance_scales_with_the_amounts() -> None:
    """A mis-read digit on a large payslip must not look like a forgery."""
    small = check_arithmetic({"gross_salary": "500.00", "total_deductions": "100.00", "net_salary": "412.00"})
    assert small, "a 12 unit gap on a 500 unit payslip is material"
    large = check_arithmetic(
        {"gross_salary": "20,000.00", "total_deductions": "5,000.00", "net_salary": "15,012.00"}
    )
    assert large == [], "the same 12 units on 20,000 is not"


def test_a_badly_read_figure_is_never_used_to_accuse() -> None:
    """CLAUDE.md §2.7 — an unreadable document is a DOC-04 problem."""
    assert check_arithmetic({**BALANCED, "net_salary": "2,700.00"}, confidences={"net_salary": 0.4}) == []


def test_a_missing_figure_raises_nothing() -> None:
    assert check_arithmetic({"gross_salary": "3,360.00"}) == []


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------
def test_a_document_dated_before_its_period_is_flagged() -> None:
    findings = check_metadata({"letter_date": "2026-02-01", "period": "2026-04"})
    assert findings[0].code == "INT-01"
    assert findings[0].detail["check"] == "date_before_period"


def test_a_stale_confirmation_letter_is_flagged() -> None:
    findings = check_metadata({"letter_date": "2025-11-01"}, application_date=date(2026, 4, 30))
    assert findings[0].detail["check"] == "stale_confirmation_letter"
    assert findings[0].detail["days_old"] > 45


def test_a_recent_letter_is_not_flagged() -> None:
    assert check_metadata({"letter_date": "2026-04-05"}, application_date=date(2026, 4, 30)) == []


def test_a_letter_dated_after_the_application_is_flagged() -> None:
    findings = check_metadata({"letter_date": "2026-06-01"}, application_date=date(2026, 4, 30))
    assert findings[0].detail["check"] == "letter_dated_after_application"


# ---------------------------------------------------------------------------
# reused images
# ---------------------------------------------------------------------------
def _document(**overrides: object) -> dict:
    base = {
        "document_id": "DOC-1",
        "member_id": "M-000001",
        "type": "BANK_STATEMENT_3M",
        "sha256": "a" * 64,
        "phash": "0" * 64,
        "account_holder": "Tui Sanuro",
    }
    base.update(overrides)
    return base


def test_identical_bytes_under_two_members_is_high() -> None:
    other = _document(document_id="DOC-2", member_id="M-000002")
    findings = check_reused_image(_document(), [other])
    assert findings[0].code == "INT-02"
    assert findings[0].severity == "HIGH"
    assert findings[0].detail["check"] == "identical_bytes"


def test_the_same_member_resubmitting_is_not_a_finding() -> None:
    other = _document(document_id="DOC-2")
    assert check_reused_image(_document(), [other]) == []


def test_documents_of_different_types_are_not_compared() -> None:
    other = _document(document_id="DOC-2", member_id="M-000002", type="IDENTITY")
    assert check_reused_image(_document(), [other]) == []


def test_an_unconfirmed_hash_match_is_only_a_lead() -> None:
    """T-022 measured that a layout alone can put two pages inside the
    threshold, so a hash match without matching content is not a finding."""
    other = _document(
        document_id="DOC-2", member_id="M-000002", sha256="b" * 64, account_holder="Someone Else"
    )
    findings = check_reused_image(_document(), [other], content_key="account_holder")
    assert findings[0].severity == "LOW"
    assert findings[0].detail["content_confirmed"] is False


def test_a_hash_match_confirmed_by_content_is_high() -> None:
    other = _document(document_id="DOC-2", member_id="M-000002", sha256="b" * 64)
    findings = check_reused_image(_document(), [other], content_key="account_holder")
    assert findings[0].severity == "HIGH"
    assert findings[0].detail["content_confirmed"] is True


def test_the_threshold_is_the_documented_one() -> None:
    assert PHASH_THRESHOLD == 6


# ---------------------------------------------------------------------------
# template
# ---------------------------------------------------------------------------
def test_the_wrong_employer_layout_is_flagged() -> None:
    findings = check_layout_signature(expected_template="tpl-03", observed_template="tpl-07")
    assert findings[0].code == "INT-07"


def test_the_right_layout_raises_nothing() -> None:
    assert check_layout_signature(expected_template="tpl-03", observed_template="tpl-03") == []


def test_an_unknown_template_raises_nothing() -> None:
    assert check_layout_signature(expected_template=None, observed_template="tpl-03") == []


# ---------------------------------------------------------------------------
# readability
# ---------------------------------------------------------------------------
def test_an_unread_critical_field_asks_for_more_information() -> None:
    findings = check_readability("IDENTITY", {"name": "Tui Sanuro", "dob": "1990-01-01"})
    assert findings[0].code == "DOC-04"
    assert "id_number" in findings[0].detail["unread"]


def test_a_faint_critical_field_asks_for_more_information() -> None:
    findings = check_readability(
        "IDENTITY", {"id_number": "AA1234567", "name": "Tui Sanuro", "dob": "1990-01-01"}, {"id_number": 0.5}
    )
    assert findings[0].code == "DOC-04"
    assert "id_number" in findings[0].detail["below_confidence"]


def test_a_fully_read_document_raises_nothing() -> None:
    assert (
        check_readability(
            "IDENTITY",
            {"id_number": "AA1234567", "name": "Tui Sanuro", "dob": "1990-01-01"},
            {"id_number": 0.95, "name": 0.95, "dob": 0.95},
        )
        == []
    )


def test_the_critical_fields_match_policy() -> None:
    assert set(CRITICAL_FIELDS["PAYSLIP_LATEST_3"]) == {
        "net_salary",
        "gross_salary",
        "employer_name",
        "period",
    }
    assert set(CRITICAL_FIELDS["IDENTITY"]) == {"id_number", "name", "dob"}


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------
def _member(**overrides: object) -> MemberRecord:
    base = {
        "member_id": "M-000042",
        "name": "Tui Sanuro",
        "dob": "1990-12-27",
        "employer_name": "Summit Utilities 102",
        "deductions": {"2026-04": 2400.0, "2026-03": 2400.0, "2026-02": 2400.0},
    }
    base.update(overrides)
    return MemberRecord(**base)  # type: ignore[arg-type]


def _payslip(net: str = "2,400.00") -> dict:
    return {
        "net_salary": net,
        "period": "2026-04",
        "net_salary_prior_1": net,
        "period_prior_1": "2026-03",
        "net_salary_prior_2": net,
        "period_prior_2": "2026-02",
        "employer_name": "Summit Utilities 102",
    }


def test_a_matching_payslip_raises_nothing() -> None:
    assert reconcile(member=_member(), payslip=_payslip()) == []


def test_income_below_the_employer_record_is_flagged() -> None:
    """Scenario S2: the payslip sits 6 % under what the employer reported."""
    findings = reconcile(member=_member(), payslip=_payslip("2,256.00"))
    assert findings[0].code == "INT-03"
    assert findings[0].severity == "MEDIUM"
    assert findings[0].detail["variance"] == pytest.approx(0.06, abs=0.005)


def test_a_large_income_gap_is_high() -> None:
    findings = reconcile(member=_member(), payslip=_payslip("1,600.00"))
    assert findings[0].severity == "HIGH"
    assert findings[0].detail["variance"] > INCOME_VARIANCE_HIGH


def test_a_small_income_gap_is_tolerated() -> None:
    findings = reconcile(member=_member(), payslip=_payslip("2,376.00"))
    assert not findings, f"a 1 % gap is inside the {INCOME_VARIANCE_MEDIUM:.0%} band"


def test_only_matching_cycles_are_compared() -> None:
    """A March payslip against what the employer reported for March."""
    member = _member(deductions={"2025-01": 100.0})
    assert reconcile(member=member, payslip=_payslip()) == []


def test_a_badly_read_payslip_is_not_compared() -> None:
    findings = reconcile(member=_member(), payslip=_payslip("1,600.00"), confidences={"net_salary": 0.3})
    assert findings == []


def test_a_different_employer_is_flagged() -> None:
    payslip = {**_payslip(), "employer_name": "Coastal Academy 050"}
    findings = reconcile(member=_member(), payslip=payslip)
    assert any(f.code == "INT-06" for f in findings)


def test_an_identity_name_mismatch_is_critical() -> None:
    findings = reconcile(
        member=_member(), identity={"name": "Someone Entirely Different", "dob": "1990-12-27"}
    )
    assert findings[0].code == "INT-08"
    assert findings[0].severity == "CRITICAL"


def test_a_date_of_birth_mismatch_is_critical() -> None:
    findings = reconcile(member=_member(), identity={"name": "Tui Sanuro", "dob": "1985-01-01"})
    assert any(f.code == "INT-08" and f.detail["check"] == "identity_dob_mismatch" for f in findings)


def test_a_name_that_differs_only_in_spacing_is_accepted() -> None:
    assert reconcile(member=_member(), identity={"name": "tui  sanuro", "dob": "1990-12-27"}) == []


# ---------------------------------------------------------------------------
# duplicate identities
# ---------------------------------------------------------------------------
def test_the_same_number_on_two_members_flags_both() -> None:
    """Whichever arrived first is as much a party to a duplicate."""
    findings = duplicate_identities(
        [
            {"document_id": "DOC-1", "member_id": "M-1", "id_number": "AA1234567"},
            {"document_id": "DOC-2", "member_id": "M-2", "id_number": "AA1234567"},
        ]
    )
    assert {f.document_id for f in findings} == {"DOC-1", "DOC-2"}
    assert all(f.code == "INT-04" and f.severity == "HIGH" for f in findings)


def test_one_member_with_two_documents_is_not_a_duplicate() -> None:
    assert (
        duplicate_identities(
            [
                {"document_id": "DOC-1", "member_id": "M-1", "id_number": "AA1234567"},
                {"document_id": "DOC-2", "member_id": "M-1", "id_number": "AA1234567"},
            ]
        )
        == []
    )


def test_a_single_mis_read_character_does_not_defeat_the_check() -> None:
    findings = duplicate_identities(
        [
            {"document_id": "DOC-1", "member_id": "M-1", "id_number": "AA1234567"},
            {"document_id": "DOC-2", "member_id": "M-2", "id_number": "AA1234S67"},
        ]
    )
    assert len(findings) == 2


def test_genuinely_different_numbers_are_not_grouped() -> None:
    assert (
        duplicate_identities(
            [
                {"document_id": "DOC-1", "member_id": "M-1", "id_number": "AA1234567"},
                {"document_id": "DOC-2", "member_id": "M-2", "id_number": "BB7654321"},
            ]
        )
        == []
    )


def test_documents_without_a_number_are_ignored() -> None:
    assert (
        duplicate_identities(
            [
                {"document_id": "DOC-1", "member_id": "M-1", "id_number": None},
                {"document_id": "DOC-2", "member_id": "M-2", "id_number": ""},
            ]
        )
        == []
    )
