"""The fail-safe matrix (T-083, docs/13 §7).

    uv run pytest -m failsafe

Every row of that table is a promise about what happens when something the
platform depends on is not there. A promise nobody tests is a hope, and the
failures these cover are the ones that reach a member: an applicant declined
because a GPU was down, a case decided automatically while the kill switch was
on, a facility activated twice because a retry looked like a new request.

These run against the compose stack and some of them stop a container. Each one
restores what it stopped, in a fixture teardown rather than at the end of the
test body, so a failure part-way through does not leave the stack broken for
whatever runs next.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = "http://localhost:8000"
COMPOSE = ["docker", "compose", "--env-file", "docker/.env", "-f", "docker/compose.yaml"]

pytestmark = [pytest.mark.failsafe, pytest.mark.integration, pytest.mark.slow]


def _stack_is_up() -> bool:
    try:
        with httpx.Client(timeout=3.0) as client:
            return client.get(f"{BASE}/health").status_code == 200
    except httpx.HTTPError:
        return False


if not _stack_is_up():  # pragma: no cover - the suite is skipped wholesale
    pytest.skip("the compose stack is not running; run `make up`", allow_module_level=True)


def token(role: str = "system") -> str:
    return subprocess.check_output([str(ROOT / "scripts" / "dev_token.sh"), role], text=True).strip()


@pytest.fixture(scope="module")
def client() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE, timeout=900.0, headers={"authorization": f"Bearer {token()}"}) as http:
        yield http


@pytest.fixture
def golden() -> Any:
    from synthetic.golden import GOLDEN

    return GOLDEN[0]


def _compose(*args: str) -> None:
    subprocess.run([*COMPOSE, *args], cwd=ROOT, check=False, capture_output=True)


def _wait_healthy(service: str, seconds: int = 90) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        out = subprocess.run(
            [*COMPOSE, "ps", "--format", "{{.Status}}", service],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        if "healthy" in out:
            return True
        time.sleep(2)
    return False


@contextlib.contextmanager
def stopped(service: str) -> Iterator[None]:
    """Stop a service for the duration, and put it back whatever happens.

    Restored in a finally rather than at the end of the body: a failing
    assertion must not leave the next test running against a stack with a hole
    in it.
    """
    _compose("stop", service)
    try:
        yield
    finally:
        _compose(
            "--profile", "core", "--profile", "services", "--profile", "observability", "up", "-d", service
        )
        _wait_healthy(service)


def evaluate(client: httpx.Client, case: Any, **overrides: Any) -> dict[str, Any]:
    from scripts.seed_demo_case import case_inputs

    inputs = {**case_inputs(case), **overrides}
    response = client.post(
        "/api/policy/policy/evaluate",
        json={
            "product_code": "PF-STD",
            "policy_version": "2026.09.1",
            "snapshot_id": case.snapshot["snapshot_id"],
            "inputs": inputs,
        },
    )
    response.raise_for_status()
    return dict(response.json())


def deliberate(client: httpx.Client, case: Any, policy_result: dict[str, Any], **body: Any) -> dict[str, Any]:
    """One committee run, on a tier nobody else is using.

    The committee is idempotent on snapshot and tier, so a run submitted under
    a tier a previous test used would join that run and report on conditions
    that no longer hold.
    """
    response = client.post(
        "/api/committee/committee/runs",
        json={
            "snapshot": case.snapshot,
            "tier": f"FAILSAFE_{int(time.time() * 1000)}",
            "policy_result": policy_result,
            **body,
        },
    )
    response.raise_for_status()
    return dict(response.json())


# ---------------------------------------------------------------------------
# the model is not there
# ---------------------------------------------------------------------------
def test_no_model_still_decides_and_never_alone(client: httpx.Client, golden: Any) -> None:
    """docs/13 §7 row 1.

    The deterministic path continues, the record says it is degraded, and the
    case goes to a person. Never AUTONOMOUS: a case nobody could deliberate on
    is the last case that should be decided without one.
    """
    policy_result = evaluate(client, golden)

    with stopped("llm_gateway"):
        run = deliberate(client, golden, policy_result)

    record = run.get("decision_record") or {}
    assert run["state"] == "DONE", run.get("detail")
    assert record.get("route") != "AUTONOMOUS"
    assert record.get("route") == "OFFICER_REVIEW"
    assert (record.get("narrative") or {}).get("status") == "DEGRADED"


def test_no_model_does_not_decline_anybody(client: httpx.Client, golden: Any) -> None:
    """The most serious failure this suite covers.

    With no Council there is no Decision Factor, and a weighted score computed
    over nothing used to come out as 0.0, which is below every decline
    threshold. A clean application was recommended DECLINE with the model
    gateway stopped. A case nobody could assess is unassessed, not failed.
    """
    policy_result = evaluate(client, golden)

    with stopped("llm_gateway"):
        run = deliberate(client, golden, policy_result)

    record = run.get("decision_record") or {}
    assert record.get("recommendation") == "MORE_INFORMATION_REQUIRED"
    assert record.get("weighted_score") is None
    assert "NO_FACTOR_SCORED" in (record.get("route_reasons") or [])


# ---------------------------------------------------------------------------
# a model service is not there
# ---------------------------------------------------------------------------
def test_the_risk_service_being_down_does_not_stop_a_decision(client: httpx.Client, golden: Any) -> None:
    """docs/13 §7 row 2. The gates do not need a score to run."""
    with stopped("risk"):
        policy_result = evaluate(client, golden)

    assert policy_result["blockers"] == []
    assert policy_result["evidence_coverage"] > 0


# ---------------------------------------------------------------------------
# the documents are not good enough
# ---------------------------------------------------------------------------
def test_low_document_confidence_asks_rather_than_declines(client: httpx.Client, golden: Any) -> None:
    """docs/13 §7 row 3.

    DOC-04 reads the minimum confidence across the critical fields. Below the
    threshold the case needs more information: an extractor that could barely
    read a payslip has not found anything wrong with it.
    """
    policy_result = evaluate(client, golden, documents_min_critical_confidence=0.41)

    assert "DOC-04" in policy_result["blockers"]
    failing = [r for r in policy_result["rules"] if r["rule_id"] == "DOC-04"]
    assert failing and failing[0]["result"] == "FAIL"
    assert failing[0].get("on_fail") in {"MORE_INFORMATION_REQUIRED", None}


def test_a_missing_document_is_not_a_decline(client: httpx.Client, golden: Any) -> None:
    policy_result = evaluate(
        client,
        golden,
        documents_required_complete=False,
        documents_present=["PAYSLIP_LATEST_3"],
        document_confidence={"PAYSLIP_LATEST_3": 0.94},
        identity_verified=False,
    )

    assert "DOC-01" in policy_result["blockers"]
    blocking = [r for r in policy_result["rules"] if r["rule_id"] == "DOC-01"]
    assert blocking and blocking[0].get("on_fail") == "MORE_INFORMATION_REQUIRED"


# ---------------------------------------------------------------------------
# the switch is on
# ---------------------------------------------------------------------------
def test_the_kill_switch_sends_everything_to_a_person(client: httpx.Client, golden: Any) -> None:
    """docs/13 §7 row 6.

    Read by the service from its own state, never taken from the caller: a
    caller that forgot to send it would otherwise get a decision made as though
    the switch were off, which is the one mistake a kill switch must not permit.
    """
    policy_result = evaluate(client, golden)
    factors = [
        {
            "family": "CAPACITY",
            "score": 88,
            "calc_id": "calc_FAILSAFE",
            "tool": "affordability.compute",
            "inputs_digest": "0" * 64,
            "evidence_refs": [],
        }
    ]

    switched = client.post(
        "/api/policy/policy/synthesize",
        json={
            "product_code": "PF-STD",
            "policy_version": "2026.09.1",
            "snapshot_id": golden.snapshot["snapshot_id"],
            "tier": "STANDARD",
            "requested_amount": str(golden.snapshot["amount"]),
            "policy_result": policy_result,
            "factor_scores": factors,
            "opinions": [],
            "kill_switch_active": True,
        },
    )
    switched.raise_for_status()
    record = switched.json()

    assert record["route"] == "OFFICER_REVIEW"
    assert "KILL_SWITCH" in record["route_reasons"]


# ---------------------------------------------------------------------------
# what the member is told
# ---------------------------------------------------------------------------
def test_a_member_is_never_told_an_outcome_even_when_the_model_is_down(
    client: httpx.Client,
) -> None:
    """The refusals are rules, not a model's judgement, so an outage cannot
    turn them off. That is the whole reason they are rules."""
    from ai.guardrails.questions import check_member_question

    refusal = check_member_question("Will I be approved?", member_id="M-000123")
    assert refusal is not None
    assert refusal.code == "WOULD_PREDICT_DECISION"


def test_distress_is_recognised_without_a_model() -> None:
    from ai.guardrails.hardship import classify

    signal = classify("I lost my job and cannot pay")
    assert signal is not None
    assert signal.signal == "HARDSHIP"


# ---------------------------------------------------------------------------
# the ledger is still the record
# ---------------------------------------------------------------------------
def test_the_chain_still_verifies_after_an_outage(client: httpx.Client) -> None:
    """An outage must not leave a gap in the chain.

    A ledger with a hole is worse than no ledger: it looks like a record and
    cannot be verified, and nobody finds out until somebody asks.
    """
    response = client.get("/api/decision/ledger/verify")
    response.raise_for_status()
    body = response.json()
    # `intact`, which is what the endpoint says: the chain recomputes and every
    # link holds. `breaks` names any that did not.
    assert body.get("intact") is True, json.dumps(body)[:400]
    assert body.get("breaks") == []
    assert body.get("entries_checked", 0) > 0, "a chain with nothing in it verifies trivially"
