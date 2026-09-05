"""T-011 — the rule expression language (docs/05 §1)."""

from __future__ import annotations

import pytest

from app.expr import MISSING, ExpressionError, compile_rule, evaluate, identifiers

CTX = {
    "member.status": "ACTIVE",
    "member.age": 41,
    "member.tenure_months": 110,
    "requested.tenor": 36,
    "requested.amount": 8000,
    "requested.purpose": "EDUCATION",
    "product.purposes_allowed": ["PERSONAL", "EDUCATION"],
    "identity.verified": True,
    "affordability.dsr": 0.42,
    "affordability.dsr_limit": 0.60,
    "affordability.stress": [
        {"case": "a", "dsr": 0.50, "pass": True},
        {"case": "b", "dsr": 0.58, "pass": True},
    ],
    "opened_on": "2026-01-01",
    "today": "2026-03-02",
}


@pytest.mark.parametrize(
    "rule,expected",
    [
        ("member.status == 'ACTIVE'", True),
        ("member.status != 'ACTIVE'", False),
        ("member.age >= 18", True),
        ("member.age > 41", False),
        ("member.age <= 41 and member.tenure_months >= 6", True),
        ("member.age < 18 or member.tenure_months >= 6", True),
        ("not identity.verified", False),
        ("identity.verified == true", True),
        ("identity.verified == false", False),
        ("member.age + requested.tenor / 12 <= 65", True),
        ("requested.amount * 2 == 16000", True),
        ("requested.amount - 8000 == 0", True),
        ("min(1, 2) == 1", True),
        ("max(1, 2) == 2", True),
        ("abs(0 - 5) == 5", True),
        ("round(0.456, 2) == 0.46", True),
        ("clamp(150, 0, 100) == 100", True),
        ("len(product.purposes_allowed) == 2", True),
        ("in(requested.purpose, product.purposes_allowed)", True),
        ("in('VEHICLE', product.purposes_allowed)", False),
        ("member.status in ['ACTIVE', 'DORMANT']", True),
        ("member.status not in ['CLOSED']", True),
        ("all(affordability.stress, dsr <= affordability.dsr_limit + 0.05)", True),
        ("any(affordability.stress, dsr > 0.55)", True),
        ("count(affordability.stress, dsr > 0.55) == 1", True),
        ("days_between(opened_on, today) == 60", True),
        ("coalesce(member.missing, 'fallback') == 'fallback'", True),
    ],
)
def test_documented_grammar_evaluates(rule: str, expected: bool) -> None:
    assert evaluate(rule, CTX) == expected


def test_a_rule_over_a_missing_key_is_false_never_true() -> None:
    """CLAUDE.md §2.7 — absent data must not let a case through."""
    assert evaluate("member.unknown > 5", CTX) is False
    assert evaluate("member.unknown <= 5", CTX) is False
    assert evaluate("member.unknown == 'ACTIVE'", CTX) is False


def test_arithmetic_over_a_missing_key_propagates_absence() -> None:
    assert evaluate("member.unknown + 1", CTX) is MISSING
    assert evaluate("member.unknown + 1 > 0", CTX) is False


def test_division_by_zero_does_not_raise() -> None:
    assert evaluate("requested.amount / 0 > 1", {"requested.amount": 5}) is False


@pytest.mark.parametrize(
    "rule",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd').read()",
        "(lambda: 1)()",
        "[x for x in range(3)]",
        "member.status.upper()",
        "exec('x=1')",
    ],
)
def test_expressions_outside_the_grammar_are_refused(rule: str) -> None:
    """The language is a whitelist, so a pack cannot smuggle in code."""
    with pytest.raises(ExpressionError):
        compile_rule(rule)


def test_a_syntax_error_names_the_rule() -> None:
    with pytest.raises(ExpressionError, match="cannot parse"):
        compile_rule("member.status ==")


def test_identifiers_reports_the_keys_a_rule_reads() -> None:
    found = identifiers("member.age >= 18 and member.age + requested.tenor / 12 <= 65")
    assert found == {"member.age", "requested.tenor"}


def test_identifiers_ignores_boolean_literals() -> None:
    assert identifiers("identity.verified == true") == {"identity.verified"}


def test_quantifiers_bind_the_item_fields_in_scope() -> None:
    context = {"rows": [{"n": 1}, {"n": 2}, {"n": 3}], "limit": 2}
    assert evaluate("all(rows, n <= 3)", context) is True
    assert evaluate("all(rows, n <= limit)", context) is False
    assert evaluate("count(rows, n > limit) == 1", context) is True


def test_a_quantifier_over_a_missing_list_is_false() -> None:
    assert evaluate("all(rows, n > 0)", {}) is False
