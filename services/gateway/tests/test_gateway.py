"""T-008 — the gateway authenticates, routes and correlates (docs/13 §1)."""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routing import UPSTREAMS, upstream_for
from cio_common.auth import issue_token


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway")


# ---------------------------------------------------------------------------
# routing table
# ---------------------------------------------------------------------------
def test_every_domain_service_is_routable() -> None:
    """docs/01 §4 — the gateway must know every service but itself."""
    expected = {
        "application",
        "document",
        "member_intelligence",
        "policy",
        "feature",
        "risk",
        "fraud",
        "lmi",
        "committee",
        "core_stub",
        "agent_runtime",
        "decision",
        "execution",
        "notification",
        "audit",
        "governance",
        "llm_gateway",
    }
    assert set(UPSTREAMS) == expected


def test_upstream_urls_use_the_documented_ports() -> None:
    assert upstream_for("policy") == "http://policy:8004"
    assert upstream_for("llm_gateway") == "http://llm_gateway:8020"
    assert upstream_for("nonexistent") is None


async def test_the_service_map_is_published() -> None:
    async with await _client() as http:
        body = (await http.get("/api/services")).json()
    assert body["decision"] == 8012


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------
async def test_a_proxied_call_without_a_token_is_refused() -> None:
    async with await _client() as http:
        response = await http.get("/api/policy/health")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_a_proxied_call_with_a_bad_token_is_refused() -> None:
    async with await _client() as http:
        response = await http.get("/api/policy/health", headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 403


async def test_an_unknown_service_is_not_found() -> None:
    async with await _client() as http:
        response = await http.get(
            "/api/wizard/health", headers={"Authorization": f"Bearer {issue_token('u', 'officer')}"}
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# dev tokens
# ---------------------------------------------------------------------------
async def test_dev_token_mints_a_usable_token() -> None:
    async with await _client() as http:
        body = (
            await http.post("/api/auth/dev-token", json={"role": "senior_officer", "branch": "B-02"})
        ).json()

    from cio_common.auth import decode_token

    principal = decode_token(body["access_token"])
    assert principal.role == "senior_officer"
    assert principal.branch == "B-02"
    assert principal.authority_role == "SENIOR_OFFICER"


async def test_dev_token_refuses_an_unknown_role() -> None:
    async with await _client() as http:
        response = await http.post("/api/auth/dev-token", json={"role": "wizard"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION"


async def test_a_member_token_carries_its_member_id() -> None:
    from cio_common.auth import decode_token

    async with await _client() as http:
        body = (
            await http.post("/api/auth/dev-token", json={"role": "member", "member_id": "M-000042"})
        ).json()
    assert decode_token(body["access_token"]).member_id == "M-000042"


# ---------------------------------------------------------------------------
# proxying
# ---------------------------------------------------------------------------
@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Capture what the gateway sends upstream instead of dialling the network."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok", "service": "policy"})

    import app.main as gateway

    monkeypatch.setattr(gateway, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return seen


async def test_the_call_reaches_the_right_upstream_path(upstream: list[httpx.Request]) -> None:
    token = issue_token("u-1", "officer", branch="B-01")
    async with await _client() as http:
        response = await http.get(
            "/api/policy/policy/PF-STD/versions?limit=5", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    assert len(upstream) == 1
    assert str(upstream[0].url) == "http://policy:8004/policy/PF-STD/versions?limit=5"


async def test_the_principal_is_forwarded_to_the_service(upstream: list[httpx.Request]) -> None:
    """Services enforce ABAC from these headers rather than re-parsing the token."""
    token = issue_token("u-7", "collections", branch="B-03")
    async with await _client() as http:
        await http.get("/api/lmi/alerts", headers={"Authorization": f"Bearer {token}"})

    headers = upstream[0].headers
    assert headers["X-Principal-Sub"] == "u-7"
    assert headers["X-Principal-Role"] == "collections"
    assert headers["X-Principal-Branch"] == "B-03"


async def test_a_trace_id_is_created_and_returned(upstream: list[httpx.Request]) -> None:
    token = issue_token("u-1", "officer")
    async with await _client() as http:
        response = await http.get("/api/policy/health", headers={"Authorization": f"Bearer {token}"})

    trace = response.headers["X-Trace-Id"]
    assert trace and trace != "-"
    assert upstream[0].headers["X-Trace-Id"] == trace


async def test_a_caller_supplied_trace_id_is_preserved(upstream: list[httpx.Request]) -> None:
    token = issue_token("u-1", "officer")
    async with await _client() as http:
        response = await http.get(
            "/api/policy/health", headers={"Authorization": f"Bearer {token}", "X-Trace-Id": "trace-abc"}
        )

    assert response.headers["X-Trace-Id"] == "trace-abc"
    assert upstream[0].headers["X-Trace-Id"] == "trace-abc"


async def test_a_post_body_is_forwarded_intact(upstream: list[httpx.Request]) -> None:
    token = issue_token("u-1", "officer")
    async with await _client() as http:
        await http.post(
            "/api/policy/policy/evaluate",
            json={"snapshot_id": "snap_1"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert upstream[0].method == "POST"
    assert b'"snapshot_id"' in upstream[0].content
