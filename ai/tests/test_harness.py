"""T-080 — the evaluation harness (docs/12 §3).

A harness is the thing everything else is judged by, so the properties tested
here are the ones that would let it certify a platform it never checked:

- a metric nothing exercised is reported as not exercised, never as perfect;
- a run that skipped something says so;
- an adversarial case is pass or fail, with no partial credit;
- the corpus is well-formed, because a case with a typo in its `kind` is a case
  that silently never runs.
"""

from __future__ import annotations

import json

import pytest

from ai.evals.harness import (
    NEEDS_STACK,
    THRESHOLDS,
    Run,
    adversarial_cases,
    failed_rules,
    numbers_in,
    report,
    rounded_forms,
)

KINDS = {"injection", "member_chat", "manager_chat", "gate", "route"}


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------
def test_the_corpus_is_not_empty() -> None:
    assert len(adversarial_cases()) >= 20


@pytest.mark.parametrize("case", adversarial_cases(), ids=lambda c: c["id"])
def test_every_case_is_well_formed(case: dict) -> None:
    """A case with a `kind` nothing handles never runs and never fails.

    That is the worst shape a corpus entry can take: it looks like coverage in
    the file and is absent from the report.
    """
    assert case["kind"] in KINDS, f"{case['id']} has an unknown kind"
    assert case.get("why", "").strip(), f"{case['id']} does not say why it is here"

    if case["kind"] == "injection":
        assert case.get("text")
    elif case["kind"] == "member_chat":
        assert case["expect"] in {"refused", "handoff", "answered"}
        if case["expect"] == "refused":
            assert case.get("code")
        if case["expect"] == "handoff":
            assert case.get("signal")
    elif case["kind"] == "manager_chat":
        assert case.get("code")
    elif case["kind"] == "gate":
        assert case.get("expect_blocked") and case.get("inputs")
    elif case["kind"] == "route":
        assert case.get("expect_not_route") and case.get("inputs")


def test_the_corpus_includes_questions_that_must_be_answered() -> None:
    """A guardrail that refuses everything scores perfectly on the attacks.

    Without these the corpus rewards a platform that has stopped working.
    """
    answered = [c for c in adversarial_cases() if c.get("expect") == "answered"]
    assert len(answered) >= 3


def test_every_case_id_is_unique() -> None:
    ids = [case["id"] for case in adversarial_cases()]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def test_a_metric_nothing_exercised_is_not_reported_as_perfect() -> None:
    """The property that stops a harness certifying a set it never ran."""
    run = Run()
    assert run.rate("grounded_claim_rate") is None

    text, body, passed = report(run, which="golden", provider="fake")
    assert body["measured"]["grounded_claim_rate"] is None
    assert "not exercised" in text
    # And an empty run does not breach: it has measured nothing, which is
    # different from having measured something bad.
    assert passed


def test_a_metric_below_its_threshold_is_a_breach() -> None:
    run = Run()
    run.add("S1", "routing_accuracy", passed=True)
    run.add("S2", "routing_accuracy", passed=False, detail="REVIEW (expected APPROVE)")

    _text, body, passed = report(run, which="golden", provider="fake")
    assert not passed
    assert body["measured"]["routing_accuracy"] == 0.5
    assert any("routing_accuracy" in b for b in body["breaches"])


def test_an_adversarial_case_has_no_partial_credit() -> None:
    run = Run()
    for index in range(19):
        run.add(f"case{index}", "injection_neutralised", passed=True)
    run.add("case19", "injection_neutralised", passed=False)

    _text, body, passed = report(run, which="adversarial", provider="fake")
    assert not passed
    assert body["measured"]["injection_neutralised"] == 0.95


def test_what_was_skipped_is_named_in_the_report() -> None:
    """A security check reported as passing because nothing ran it is worse
    than one that failed."""
    run = Run()
    run.skipped.append("gate_missing_identity (gate): needs a running stack")

    text, body, _passed = report(run, which="adversarial", provider="fake")
    assert "Not run" in text
    assert "gate_missing_identity" in text
    assert body["skipped"]


def test_every_threshold_says_why_it_is_where_it_is() -> None:
    for metric, (threshold, why) in THRESHOLDS.items():
        assert 0 < threshold <= 1, metric
        assert len(why) > 20, f"{metric} does not say why"


# ---------------------------------------------------------------------------
# reading the platform's own answers
# ---------------------------------------------------------------------------
def test_failed_rules_reads_both_fields() -> None:
    """The evaluator returns `blockers` and `rules`, not `hard_gates`.

    Reading a key the endpoint does not return gave an empty set for every
    case, which the harness reported as "no gate failed" for six adversarial
    cases that were all correctly blocked.
    """
    assert failed_rules({"blockers": ["AFF-01"]}) == {"AFF-01"}
    assert failed_rules({"rules": [{"rule_id": "DOC-04", "result": "FAIL"}]}) == {"DOC-04"}
    assert failed_rules({"rules": [{"rule_id": "DOC-04", "result": "PASS"}]}) == set()
    assert failed_rules({}) == set()


def test_a_number_may_be_written_the_ways_a_sentence_writes_it() -> None:
    forms = rounded_forms("0.25")
    assert "0.25" in forms
    assert "25.0" in forms or "25" in forms


def test_a_year_is_not_a_claim_about_a_case() -> None:
    assert numbers_in("decided in 2026") == set()
    assert numbers_in("the balance is 1,364.87") == {"1364.87"}


def test_the_cases_that_need_a_stack_are_named() -> None:
    """So `--offline` skips exactly those and nothing else quietly stops."""
    assert {"gate", "route"} == NEEDS_STACK
    for case in adversarial_cases():
        if case["kind"] in NEEDS_STACK:
            assert "inputs" in case, f"{case['id']} is marked as needing a stack but sends nothing"


def test_the_corpus_is_valid_json_on_disk() -> None:
    from ai.evals.harness import ADVERSARIAL

    for path in sorted(ADVERSARIAL.glob("*.json")):
        json.loads(path.read_text())
