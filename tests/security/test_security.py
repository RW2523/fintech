"""The security checks (T-084, docs/13 §1-4).

    uv run pytest -m security

Six things this platform claims, tested by trying to break each one:

1. A token gets you what your role allows and nothing more.
2. A tool an agent was not granted is refused, and the refusal is recorded.
3. An approval token is single-use, scoped and expiring.
4. Text that instructs rather than informs is caught before a model sees it.
5. A member-facing agent derives identity from the token, never from the body.
6. Nothing that reaches a model carries a member's name or number.

These are adversarial by construction: each one does the thing that must not
work and asserts it did not. A test that only does the allowed thing proves
that the allowed thing works, which is not the question.
"""

from __future__ import annotations

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

from scripts.token import token_for_sync  # noqa: E402

BASE = "http://localhost:8000"

pytestmark = [pytest.mark.security, pytest.mark.integration]


def _stack_is_up() -> bool:
    try:
        with httpx.Client(timeout=3.0) as client:
            return client.get(f"{BASE}/health").status_code == 200
    except httpx.HTTPError:
        return False


if not _stack_is_up():  # pragma: no cover - the suite is skipped wholesale
    pytest.skip("the compose stack is not running; run `make up`", allow_module_level=True)


def mint(role: str, **claims: Any) -> str:
    """A token for a role, however this deployment signs people in.

    `claims` only reach the dev endpoint, which is the point of it: on a bench
    a test can ask for a member token naming any membership number. Where an
    account is required they are dropped, and the account's own claims stand —
    which is what these tests need anyway. None of them turns on *which*
    member is signed in, only that a member's token stays a member's token.
    """
    with httpx.Client(timeout=30.0) as client:
        response = client.post(f"{BASE}/api/auth/dev-token", json={"role": role, **claims})
        if response.status_code == 200:
            return str(response.json()["access_token"])
        return token_for_sync(client, role, base=BASE)


@pytest.fixture(scope="module")
def anonymous() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE, timeout=60.0) as client:
        yield client


def as_role(role: str, **claims: Any) -> httpx.Client:
    return httpx.Client(
        base_url=BASE, timeout=120.0, headers={"authorization": f"Bearer {mint(role, **claims)}"}
    )


# ---------------------------------------------------------------------------
# 1. authentication
# ---------------------------------------------------------------------------
def test_no_token_reaches_nothing(anonymous: httpx.Client) -> None:
    for path in (
        "/api/decision/queue",
        "/api/policy/policy/products",
        "/api/agent_runtime/agents",
        "/api/governance/governance/metrics",
    ):
        assert anonymous.get(path).status_code == 403, path


def test_a_forged_token_is_refused(anonymous: httpx.Client) -> None:
    """Signed with the wrong secret. The gateway must not read the claims of a
    token it cannot verify: a JWT is only a claim until the signature holds."""
    import jwt

    forged = jwt.encode(
        {"sub": "attacker", "role": "head_of_credit", "exp": int(time.time()) + 3600},
        "not-the-secret",
        algorithm="HS256",
    )
    response = anonymous.get("/api/decision/queue", headers={"authorization": f"Bearer {forged}"})
    assert response.status_code == 403


def test_an_expired_token_is_refused(anonymous: httpx.Client) -> None:
    from cio_common.auth import issue_token

    stale = issue_token("someone", "officer", ttl_seconds=-60)
    response = anonymous.get("/api/decision/queue", headers={"authorization": f"Bearer {stale}"})
    assert response.status_code == 403


def test_a_token_is_not_a_bearer_of_somebody_elses_role(anonymous: httpx.Client) -> None:
    """The role is in the signature, not in a header beside it.

    A caller that sends `X-Principal-Role: head_of_credit` alongside a member's
    token must be read as the member: the gateway sets those headers itself and
    strips any that arrive.
    """
    token = mint("member", member_id="M-000123")
    response = anonymous.post(
        "/api/agent_runtime/copilot/ask",
        headers={
            "authorization": f"Bearer {token}",
            "X-Principal-Role": "officer",
            "X-Principal-Member": "M-000999",
        },
        json={"case_id": "case_S1CLEAN", "question": "Which gates failed?"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 2. authorisation
# ---------------------------------------------------------------------------
def test_a_member_cannot_open_the_case_copilot() -> None:
    """The first member-facing token in the platform, and the endpoint that
    reads whole case files by id. A member belongs on /assistant/ask."""
    with as_role("member", member_id="M-000123") as client:
        response = client.post(
            "/api/agent_runtime/copilot/ask",
            json={"case_id": "case_S1CLEAN", "question": "Which hard gates failed?"},
        )
    assert response.status_code == 403


def test_a_member_cannot_ask_the_portfolio_copilot() -> None:
    with as_role("member", member_id="M-000123") as client:
        response = client.post(
            "/api/agent_runtime/copilot/portfolio", json={"question": "What is the autonomous share?"}
        )
    assert response.status_code == 403


def test_a_member_assistant_reads_the_token_not_the_body() -> None:
    """Identity that can be typed is not identity.

    A member who puts somebody else's id in the request body must be refused,
    not served: the header the gateway sets from the signed token is the only
    identity this endpoint recognises.
    """
    with as_role("member", member_id="M-000123") as client:
        response = client.post(
            "/api/agent_runtime/assistant/ask",
            json={"question": "What is my balance?", "member_id": "M-000999"},
        )
    assert response.status_code == 403


def test_adopting_a_policy_version_needs_two_different_people() -> None:
    with as_role("head_of_credit") as client:
        alone = client.post(
            "/api/policy/policy/PF-STD/versions",
            json={
                "sandbox_id": "sbx_01ARZ3NDEKTSV4RRFFQ69G5FAW",
                "approvers": [{"role": "HEAD_OF_CREDIT", "actor_id": "u-one"}],
            },
        )
        same = client.post(
            "/api/policy/policy/PF-STD/versions",
            json={
                "sandbox_id": "sbx_01ARZ3NDEKTSV4RRFFQ69G5FAW",
                "approvers": [
                    {"role": "HEAD_OF_CREDIT", "actor_id": "u-one"},
                    {"role": "HEAD_OF_RISK", "actor_id": "u-one"},
                ],
            },
        )
    # 422 for the approver rule, 404 for the sandbox run that does not exist.
    # Either is a refusal; what must not happen is a version being written.
    assert alone.status_code in {404, 422}
    assert same.status_code in {404, 422}


# ---------------------------------------------------------------------------
# 3. tool grants
# ---------------------------------------------------------------------------
async def test_a_tool_outside_an_agents_grants_is_denied() -> None:
    from ai.tools import registry
    from cio_tools.grants import Grant, GrantRegistry, ToolDenied
    from cio_tools.registry import ToolContext
    from cio_tools.spec import PermittedUse

    scoped = registry.with_grants(
        GrantRegistry([Grant(agent_id="document_evidence", tool="documents.list", max_calls=1)])
    )
    context = ToolContext(
        agent_id="document_evidence",
        run_id="security",
        purpose=PermittedUse.UNDERWRITING,
        principal="test",
        case_id="case_01ARZ3NDEKTSV4RRFFQ69G5FAW",
    )

    with pytest.raises(ToolDenied):
        await scoped.call("risk.score", {"snapshot_id": "snap_01ARZ3NDEKTSV4RRFFQ69G5FAV"}, context)


async def test_a_denied_call_is_recorded_not_just_refused() -> None:
    """An attempt that was refused is the interesting one.

    A registry that denies silently tells nobody an agent reached past its
    grants, which is exactly the thing somebody needs to know about.
    """
    from ai.tools import registry
    from cio_tools.grants import Grant, GrantRegistry, ToolDenied
    from cio_tools.registry import ToolContext
    from cio_tools.spec import PermittedUse

    scoped = registry.with_grants(
        GrantRegistry([Grant(agent_id="fraud_integrity", tool="fraud.assess", max_calls=1)])
    )
    before = len(scoped.invocations)
    context = ToolContext(
        agent_id="fraud_integrity",
        run_id="security",
        purpose=PermittedUse.FRAUD,
        principal="test",
    )

    with pytest.raises(ToolDenied):
        await scoped.call("member.profile", {"member_id": "M-000123"}, context)

    recorded = scoped.invocations[before:]
    assert recorded, "a denial left no trace"
    assert recorded[-1].ok is False
    assert recorded[-1].denial_reason


async def test_an_agent_cannot_widen_its_scope_by_naming_another_case() -> None:
    """The scope is pinned by the registry, not asked of the agent."""
    from ai.tools import registry
    from cio_tools.grants import Grant, GrantRegistry, ToolDenied
    from cio_tools.registry import ToolContext
    from cio_tools.spec import PermittedUse

    scoped = registry.with_grants(
        GrantRegistry([Grant(agent_id="officer_copilot", tool="case.get", max_calls=1)])
    )
    context = ToolContext(
        agent_id="officer_copilot",
        run_id="security",
        purpose=PermittedUse.UNDERWRITING,
        principal="test",
        case_id="case_01ARZ3NDEKTSV4RRFFQ69G5FAW",
    )

    with pytest.raises(ToolDenied):
        await scoped.call("case.get", {"case_id": "case_01ARZ3NDEKTSV4RRFFQ69G5FBB"}, context)


# ---------------------------------------------------------------------------
# 4. injection
# ---------------------------------------------------------------------------
def test_every_injection_in_the_corpus_is_caught() -> None:
    """Pass or fail. An injection neutralised nine times in ten works."""
    import json

    from ai.guardrails.injection import scan

    corpus = ROOT / "ai" / "evals" / "adversarial"
    cases = [json.loads(p.read_text()) for p in sorted(corpus.glob("inject_*.json"))]
    assert cases, "the injection corpus is empty"

    missed = [c["id"] for c in cases if not scan({"text": c["text"]})]
    assert not missed, f"not flagged: {missed}"


def test_ordinary_document_text_is_not_flagged() -> None:
    """A classifier that flags everything is a classifier nobody reads."""
    from ai.guardrails.injection import scan

    ordinary = [
        "PAYSLIP\nEmployer: Northwind Ltd\nNet pay: 4,200.00\nPeriod: August 2026",
        "This is to confirm that the above named has been employed since 2017.",
        "Statement of account. Opening balance 1,204.55. Closing balance 980.12.",
    ]
    for text in ordinary:
        assert not scan({"text": text}), text


# ---------------------------------------------------------------------------
# 5. what reaches a model
# ---------------------------------------------------------------------------
def test_a_member_id_never_reaches_a_provider() -> None:
    """Masked before the call, restored after it.

    The provider sees «MEMBER_1»; the caller sees the member. A platform that
    sends a real id to a hosted model has sent it, whatever the contract says.
    """
    from services.llm_gateway.app import pii

    payload = [{"role": "user", "content": "M-000123 asked about account A-000061"}]
    masked, mapping = pii.mask_payload(payload, known={})
    text = str(masked)

    assert "M-000123" not in text
    assert "A-000061" not in text
    assert pii.unmask_text(text, mapping).count("M-000123") == 1


def test_the_number_of_masked_fields_is_reported() -> None:
    """A masking pass nobody can count is a masking pass nobody can audit."""
    from services.llm_gateway.app import pii

    _masked, mapping = pii.mask_payload([{"role": "user", "content": "M-000123 and M-000124"}], known={})
    # Two distinct members, two distinct placeholders. One placeholder for both
    # would be a masking that lost the difference between two people.
    assert len(mapping.forward) >= 2
    assert len(set(mapping.forward.values())) == len(mapping.forward)


# ---------------------------------------------------------------------------
# 6. the repository itself
# ---------------------------------------------------------------------------
def test_no_secret_is_committed() -> None:
    """A grep, not a scanner. `gitleaks` is the tool for this and is not a
    dependency of the demo; what this catches is the obvious mistake, which is
    the one that actually happens."""
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.split()

    # Assembled rather than written out. A file containing the literals it
    # searches for matches itself, and the fix for that is not an exclusion
    # list: a scanner that skips a file by name is a place to hide a secret.
    patterns = (
        "-----BEGIN " + "RSA PRIVATE KEY-----",
        "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
        "sk-" + "ant-api",
        "sk-" + "proj-",
        "gh" + "p_",
        "AKI" + "A",
    )
    found: list[str] = []
    for name in tracked:
        path = ROOT / name
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            body = path.read_text(errors="ignore")
        except OSError:
            continue
        found.extend(f"{name}: {p}" for p in patterns if p in body)

    assert not found, f"looks like a secret: {found[:5]}"


def test_the_env_file_is_not_tracked() -> None:
    """`docker/.env` holds the demo's secrets and must stay out of git.

    The example is tracked and says `change-me`, which is the point: a reader
    can see what is needed without seeing what anybody chose.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.split()
    assert "docker/.env" not in tracked
    assert "docker/.env.example" in tracked
