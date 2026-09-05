"""T-008 — notification answers the platform's meta endpoints."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://notification")


async def test_health_reports_ok_with_the_service_name() -> None:
    async with await _client() as http:
        body = (await http.get("/health")).json()
    assert body == {"status": "ok", "service": "notification", "version": "0.1.0"}


async def test_version_reports_the_environment() -> None:
    async with await _client() as http:
        body = (await http.get("/version")).json()
    assert body["service"] == "notification"
    assert body["environment"] in ("demo", "dev", "test", "pilot", "prod")


async def test_every_response_carries_a_trace_header() -> None:
    async with await _client() as http:
        response = await http.get("/health")
    assert response.headers["X-Trace-Id"]


async def test_an_incoming_trace_id_is_echoed_back() -> None:
    async with await _client() as http:
        response = await http.get("/health", headers={"X-Trace-Id": "abc123"})
    assert response.headers["X-Trace-Id"] == "abc123"
