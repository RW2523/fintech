"""T-054 — override analytics and the compliance list (docs/07 §6, docs/09 §6).

An override is not a fault. It is the platform being told it was wrong, which
is the most useful signal it produces, so what matters here is that the count
is honest and that every case on the compliance list says why it is there.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


@pytest.fixture
def decision_service(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stand in for the decision service the analytics read from."""
    import httpx

    from app import routes

    state: dict[str, Any] = {
        "human-decisions": {
            "decisions": [
                {
                    "human_decision_id": "hd_1",
                    "decision_record_id": "dr_1",
                    "case_id": "case_1",
                    "actor_id": "u-senior",
                    "final_action": "DECLINE",
                    "override": True,
                    "override_reason_code": "OVR-05",
                    "recommendation": "APPROVE",
                    "decided_at": "2026-09-06T09:00:00+00:00",
                },
                {
                    "human_decision_id": "hd_2",
                    "decision_record_id": "dr_2",
                    "case_id": "case_2",
                    "actor_id": "u-officer",
                    "final_action": "APPROVE",
                    "override": False,
                    "recommendation": "APPROVE",
                    "decided_at": "2026-09-06T10:00:00+00:00",
                },
            ]
        },
        "queue": {
            "decisions": [
                {
                    "decision_record_id": "dr_3",
                    "case_id": "case_3",
                    "route": "AUTONOMOUS",
                    "disagreement": 0.1,
                    "created_at": "2026-09-06T08:00:00+00:00",
                },
                {
                    "decision_record_id": "dr_4",
                    "case_id": "case_4",
                    "route": "ENHANCED_ASSESSMENT",
                    "disagreement": 0.8,
                    "created_at": "2026-09-06T07:00:00+00:00",
                },
            ]
        },
        "samples": {"samples": []},
        "down": set(),
    }

    real_get = httpx.AsyncClient.get

    async def fake_get(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", str(url))
        for fragment, key in (
            ("human-decisions", "human-decisions"),
            ("queue", "queue"),
            ("samples", "samples"),
        ):
            if fragment in str(url):
                if key in state["down"]:
                    raise httpx.ConnectError("refused", request=request)
                return httpx.Response(200, json=state[key], request=request)
        # Anything else is the test client calling into the app, and must go
        # through: intercepting it would have the test exercise its own stub.
        return await real_get(self, url, **kwargs)  # type: ignore[no-any-return]

    monkeypatch.setattr(routes.httpx.AsyncClient, "get", fake_get)
    return state


async def test_the_rate_is_a_share_not_a_count(client: AsyncClient, decision_service: dict[str, Any]) -> None:
    """A count on its own says more about how busy the month was than about
    how often people disagreed."""
    body = (await client.get("/governance/overrides")).json()
    assert body["decisions"] == 2
    assert body["overrides"] == 1
    assert body["override_rate"] == 0.5


async def test_every_reason_code_is_reported_even_at_zero(
    client: AsyncClient, decision_service: dict[str, Any]
) -> None:
    """A code omitted because nobody used it reads as "never asked"."""
    body = (await client.get("/governance/overrides")).json()
    assert set(body["by_reason"]) == {f"OVR-{i:02d}" for i in range(1, 13)}
    assert body["by_reason"]["OVR-05"] == 1
    assert body["by_reason"]["OVR-01"] == 0


async def test_no_decisions_gives_no_rate_rather_than_zero(
    client: AsyncClient, decision_service: dict[str, Any]
) -> None:
    """A rate of zero means nobody overrode; no rate means nobody decided, and
    reporting the second as the first would be a claim about people's judgement
    that nothing supports."""
    decision_service["human-decisions"] = {"decisions": []}
    body = (await client.get("/governance/overrides")).json()
    assert body["override_rate"] is None


async def test_the_compliance_list_says_why_each_case_is_on_it(
    client: AsyncClient, decision_service: dict[str, Any]
) -> None:
    body = (await client.get("/governance/attention")).json()
    reasons = {row["why"] for row in body["cases"]}
    assert reasons == {"OVERRIDE", "HIGH_DISAGREEMENT", "DECIDED_ALONE"}
    for row in body["cases"]:
        assert row["detail"], "a case id alone tells a compliance officer nothing"


async def test_an_unreadable_source_is_named(client: AsyncClient, decision_service: dict[str, Any]) -> None:
    decision_service["down"].add("queue")
    body = (await client.get("/governance/attention")).json()
    assert "queue" in body["unavailable"]


async def test_a_decision_service_that_is_down_fails_the_series(
    client: AsyncClient, decision_service: dict[str, Any]
) -> None:
    """Reported rather than answered with zeros: an override rate of zero
    computed from nothing is the most misleading number this screen could
    show."""
    decision_service["down"].add("human-decisions")
    response = await client.get("/governance/overrides")
    assert response.status_code >= 400
