"""T-040 — the gateway's endpoints (docs/06 §6, docs/08 §5)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.config import ROUTES
from app.providers import FakeProvider, ProviderError
from tests.conftest import OPINION_SCHEMA


# ---------------------------------------------------------------------------
# completion
# ---------------------------------------------------------------------------
async def test_a_completion_returns_the_documented_shape(client: AsyncClient) -> None:
    body = (
        await client.post(
            "/llm/complete",
            json={
                "route": "agent",
                "messages": [{"role": "user", "content": "assess"}],
                "json_schema": OPINION_SCHEMA,
            },
        )
    ).json()

    assert set(body) >= {
        "content",
        "model",
        "provider",
        "route",
        "usage",
        "latency_ms",
        "masked_fields",
        "json",
    }
    assert set(body["usage"]) == {"in", "out", "total"}


async def test_an_unknown_route_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/llm/complete", json={"route": "wizardry", "messages": [{"role": "user", "content": "x"}]}
    )
    assert response.status_code == 422
    assert set(response.json()["error"]["details"]["routes"]) == set(ROUTES)


async def test_a_message_with_an_unknown_role_is_refused(client: AsyncClient) -> None:
    response = await client.post("/llm/complete", json={"messages": [{"role": "oracle", "content": "x"}]})
    assert response.status_code == 422


async def test_an_empty_conversation_is_refused(client: AsyncClient) -> None:
    assert (await client.post("/llm/complete", json={"messages": []})).status_code == 422


async def test_a_schema_failure_is_a_client_error_not_an_outage(client: AsyncClient, fake: Any) -> None:
    """The model answered, wrongly. The caller degrades rather than retries."""
    fake.replies = ['{"stance": "MAYBE"}', '{"stance": "STILL_MAYBE"}']
    response = await client.post(
        "/llm/complete",
        json={"messages": [{"role": "user", "content": "assess"}], "json_schema": OPINION_SCHEMA},
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["attempts"] == 2


async def test_a_provider_failure_is_reported_as_unavailable(client: AsyncClient, fake: Any) -> None:
    fake.replies = [ProviderError("connection refused")]
    response = await client.post("/llm/complete", json={"messages": [{"role": "user", "content": "assess"}]})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_UNAVAILABLE"


async def test_an_exhausted_budget_is_refused_with_the_numbers(client: AsyncClient) -> None:
    from app.budget import usage_ledger

    usage_ledger().record("run_x", 995)
    response = await client.post(
        "/llm/complete",
        json={
            "messages": [{"role": "user", "content": "assess"}],
            "run_id": "run_x",
            "budget": {"tokens": 1000},
        },
    )
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details["spent"] == 995
    assert details["budget"] == 1000


# ---------------------------------------------------------------------------
# vision
# ---------------------------------------------------------------------------
async def test_vision_accepts_an_image(client: AsyncClient, fake: Any) -> None:
    body = (
        await client.post(
            "/llm/vision",
            json={
                "prompt": "What kind of document is this?",
                "images": [{"b64": "aGVsbG8=", "mime": "image/png"}],
            },
        )
    ).json()
    assert body["route"] == "vision"
    parts = fake.calls[0]["messages"][0]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_vision_accepts_a_url(client: AsyncClient, fake: Any) -> None:
    await client.post(
        "/llm/vision", json={"prompt": "read it", "images": [{"url": "https://example.invalid/a.png"}]}
    )
    assert fake.calls[0]["messages"][0]["content"][1]["image_url"]["url"] == "https://example.invalid/a.png"


async def test_an_image_without_content_is_refused(client: AsyncClient) -> None:
    response = await client.post("/llm/vision", json={"prompt": "read it", "images": [{"mime": "image/png"}]})
    assert response.status_code == 422


async def test_vision_needs_at_least_one_image(client: AsyncClient) -> None:
    assert (await client.post("/llm/vision", json={"prompt": "read it", "images": []})).status_code == 422


# ---------------------------------------------------------------------------
# embedding and reranking
# ---------------------------------------------------------------------------
async def test_embedding_returns_one_vector_per_text(client: AsyncClient) -> None:
    body = (await client.post("/llm/embed", json={"texts": ["one", "two", "three"]})).json()
    assert len(body["vectors"]) == 3
    assert all(len(v) == len(body["vectors"][0]) for v in body["vectors"])


async def test_embedding_masks_what_it_is_given(client: AsyncClient, fake: Any) -> None:
    await client.post("/llm/embed", json={"texts": ["member M-000042 asked"]})
    assert "M-000042" not in fake.calls[0]["texts"][0]


async def test_embedding_the_same_text_gives_the_same_vector(client: AsyncClient) -> None:
    first = (await client.post("/llm/embed", json={"texts": ["stable"]})).json()
    second = (await client.post("/llm/embed", json={"texts": ["stable"]})).json()
    assert first["vectors"] == second["vectors"]


async def test_reranking_scores_every_passage(client: AsyncClient) -> None:
    body = (
        await client.post(
            "/llm/rerank",
            json={"query": "membership tenure", "passages": ["tenure of six months", "unrelated"]},
        )
    ).json()
    assert len(body["scores"]) == 2
    assert body["scores"][0] > body["scores"][1]


async def test_reranking_says_what_it_actually_is(client: AsyncClient) -> None:
    """docs/02 §4.1 puts a cross-encoder here. Until it is loaded, the endpoint
    says plainly that it is word overlap."""
    body = (await client.post("/llm/rerank", json={"query": "x", "passages": ["y"]})).json()
    assert "not a cross-encoder" in body["note"]


# ---------------------------------------------------------------------------
# health and warmup
# ---------------------------------------------------------------------------
async def test_health_reports_every_route(client: AsyncClient) -> None:
    body = (await client.get("/llm/health")).json()
    assert {r["route"] for r in body["routes"]} == set(ROUTES)
    assert body["total"] == len(ROUTES)


async def test_health_never_reports_a_key(client: AsyncClient) -> None:
    body = (await client.get("/llm/health")).json()
    assert "api_key" not in str(body).casefold()


async def test_health_shows_a_route_going_down(client: AsyncClient, fake: Any) -> None:
    from app.breaker import FAILURE_THRESHOLD

    fake.replies = [ProviderError("down")] * FAILURE_THRESHOLD
    for _ in range(FAILURE_THRESHOLD):
        await client.post("/llm/complete", json={"messages": [{"role": "user", "content": "x"}]})
    agent = next(r for r in (await client.get("/llm/health")).json()["routes"] if r["route"] == "agent")
    assert agent["breaker"]["open"] is True
    assert agent["available"] is False


async def test_warmup_touches_every_route(client: AsyncClient) -> None:
    body = (await client.post("/llm/warmup")).json()
    assert {r["route"] for r in body["routes"]} == set(ROUTES)
    assert body["ready"] == body["total"]


async def test_warmup_reports_a_broken_route_rather_than_failing(client: AsyncClient, fake: Any) -> None:
    """A half-ready gateway is still worth knowing about."""
    fake.replies = [ProviderError("model still loading")] * 10
    body = (await client.post("/llm/warmup")).json()
    assert body["ready"] < body["total"]
    broken = [r for r in body["routes"] if not r["ready"]]
    assert broken and "detail" in broken[0]


async def test_prompts_are_not_logged_by_default(client: AsyncClient) -> None:
    """docs/06 §6 — raw prompts only with CIO_DEBUG_PROMPTS=1, in development."""
    assert (await client.get("/llm/health")).json()["debug_prompts"] is False


# ---------------------------------------------------------------------------
# provider selection
# ---------------------------------------------------------------------------
def test_each_provider_name_maps_to_an_adapter(monkeypatch: Any) -> None:
    from app.config import RouteConfig
    from app.providers import Anthropic, OpenAICompatible, provider_for

    assert isinstance(provider_for(RouteConfig("agent", "fake", "")), FakeProvider)
    assert isinstance(
        provider_for(RouteConfig("agent", "vllm", "m", base_url="http://x/v1")), OpenAICompatible
    )
    assert isinstance(provider_for(RouteConfig("agent", "anthropic", "m", api_key="k")), Anthropic)


def test_a_provider_without_its_credential_is_refused() -> None:
    import pytest

    from app.config import RouteConfig
    from app.providers import provider_for

    with pytest.raises(ProviderError, match="API key"):
        provider_for(RouteConfig("agent", "anthropic", "m"))
    with pytest.raises(ProviderError, match="base URL"):
        provider_for(RouteConfig("agent", "vllm", "m"))
    with pytest.raises(ProviderError, match="unknown provider"):
        provider_for(RouteConfig("agent", "telepathy", "m"))


def test_ollama_is_asked_for_json_in_the_prompt() -> None:
    """Its OpenAI-compatible endpoint ignores response_format, so the schema is
    asked for in words and validated here."""
    from app.config import RouteConfig
    from app.providers import provider_for

    adapter = provider_for(RouteConfig("agent", "ollama", "m", base_url="http://x/v1"))
    assert adapter._supports_schema is False
