"""T-040 — schema enforcement, budgets and the circuit breaker (docs/06 §6)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.breaker import FAILURE_THRESHOLD, breaker_for
from app.budget import Budget, BudgetExceededError, usage_ledger
from app.gateway import GatewayError, SchemaViolationError, complete, rerank
from app.providers import FakeProvider, ProviderError
from tests.conftest import OPINION_SCHEMA

GOOD = '{"stance": "SUPPORT", "confidence": 0.8}'
#: Comfortably above the agent route's output ceiling, which the budget is
#: checked against before a call rather than after it.
BUDGET = 8000
BAD = '{"stance": "MAYBE", "confidence": 0.8}'


async def ask(provider: Any, **kwargs: Any) -> Any:
    return await complete(
        route="agent",
        messages=[{"role": "user", "content": "assess"}],
        json_schema=OPINION_SCHEMA,
        provider=provider,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# schema enforcement
# ---------------------------------------------------------------------------
async def test_a_valid_answer_is_returned_as_parsed_json() -> None:
    result = await ask(FakeProvider(replies=[GOOD]))
    assert result.json_value == {"stance": "SUPPORT", "confidence": 0.8}
    assert result.attempts == 1


async def test_json_wrapped_in_a_fence_is_still_accepted() -> None:
    """Leniency about packaging, never about the schema."""
    result = await ask(FakeProvider(replies=[f"```json\n{GOOD}\n```"]))
    assert result.json_value["stance"] == "SUPPORT"


async def test_json_after_a_sentence_is_still_accepted() -> None:
    result = await ask(FakeProvider(replies=[f"Here is my view:\n{GOOD}"]))
    assert result.json_value["stance"] == "SUPPORT"


async def test_a_schema_failure_gets_exactly_one_corrective_turn() -> None:
    fake = FakeProvider(replies=[BAD, GOOD])
    result = await ask(fake)
    assert result.attempts == 2
    assert len(fake.calls) == 2
    correction = fake.calls[1]["messages"][-1]["content"]
    assert "did not satisfy the schema" in correction
    assert "MAYBE" in correction or "stance" in correction


async def test_two_failures_are_refused_rather_than_passed_on() -> None:
    with pytest.raises(SchemaViolationError) as caught:
        await ask(FakeProvider(replies=[BAD, BAD]))
    assert caught.value.attempts == 2
    assert caught.value.errors


async def test_a_non_json_answer_is_a_schema_failure() -> None:
    with pytest.raises(SchemaViolationError, match="not JSON"):
        await ask(FakeProvider(replies=["I would approve this.", "still prose"]))


async def test_without_a_schema_the_answer_is_returned_as_text() -> None:
    result = await complete(
        route="agent",
        messages=[{"role": "user", "content": "hello"}],
        provider=FakeProvider(replies=["a plain answer"]),
    )
    assert result.content == "a plain answer"
    assert result.json_value is None


async def test_tokens_are_counted_across_the_retry() -> None:
    """The corrective turn was spent whether or not it worked."""
    single = await ask(FakeProvider(replies=[GOOD]))
    retried = await ask(FakeProvider(replies=[BAD, GOOD]))
    assert retried.tokens_in + retried.tokens_out == 2 * (single.tokens_in + single.tokens_out)


# ---------------------------------------------------------------------------
# budgets
# ---------------------------------------------------------------------------
async def test_a_call_within_budget_is_allowed() -> None:
    result = await ask(FakeProvider(replies=[GOOD]), run_id="run_1", budget=Budget(tokens=BUDGET))
    assert result.run_usage["tokens"] == 60


async def test_a_call_that_would_exceed_the_budget_is_refused_before_it_runs() -> None:
    """Refusing after the tokens are spent would not be a budget."""
    fake = FakeProvider(replies=[GOOD])
    usage_ledger().record("run_2", BUDGET - 10)
    with pytest.raises(BudgetExceededError):
        await ask(fake, run_id="run_2", budget=Budget(tokens=BUDGET))
    assert fake.calls == []


async def test_spending_accumulates_across_calls_in_a_run() -> None:
    for _ in range(3):
        await ask(FakeProvider(replies=[GOOD]), run_id="run_3", budget=Budget(tokens=BUDGET))
    report = usage_ledger().report("run_3")
    assert report["calls"] == 3
    assert report["tokens"] == 3 * 60


async def test_a_failed_schema_still_costs_the_run() -> None:
    with pytest.raises(SchemaViolationError):
        await ask(FakeProvider(replies=[BAD, BAD]), run_id="run_4", budget=Budget(tokens=BUDGET))
    assert usage_ledger().report("run_4")["tokens"] > 0


async def test_no_budget_means_no_ceiling() -> None:
    usage_ledger().record("run_5", 10_000_000)
    result = await ask(FakeProvider(replies=[GOOD]), run_id="run_5")
    assert result.json_value


# ---------------------------------------------------------------------------
# the circuit breaker
# ---------------------------------------------------------------------------
async def test_repeated_provider_failures_open_the_circuit() -> None:
    """docs/13 §3 — a case with no opinion routes to a person, which beats a
    case that waits."""
    from app.breaker import CircuitOpenError

    fake = FakeProvider(replies=[ProviderError("down")] * FAILURE_THRESHOLD)
    for _ in range(FAILURE_THRESHOLD):
        with pytest.raises(GatewayError):
            await ask(fake)

    assert breaker_for("agent").is_open
    with pytest.raises(CircuitOpenError):
        await ask(FakeProvider(replies=[GOOD]))


async def test_one_failure_does_not_open_the_circuit() -> None:
    with pytest.raises(GatewayError):
        await ask(FakeProvider(replies=[ProviderError("blip")]))
    assert not breaker_for("agent").is_open
    assert (await ask(FakeProvider(replies=[GOOD]))).json_value


async def test_a_success_clears_the_failure_count() -> None:
    for _ in range(FAILURE_THRESHOLD - 1):
        with pytest.raises(GatewayError):
            await ask(FakeProvider(replies=[ProviderError("blip")]))
    await ask(FakeProvider(replies=[GOOD]))
    assert breaker_for("agent").failures == 0


async def test_the_circuit_reopens_after_the_window() -> None:
    from app.breaker import OPEN_SECONDS, Breaker

    clock = [0.0]
    breaker = Breaker(route="agent", _clock=lambda: clock[0])
    for _ in range(FAILURE_THRESHOLD):
        breaker.record_failure("down")
    assert breaker.is_open
    clock[0] = OPEN_SECONDS + 1
    assert not breaker.is_open


async def test_a_schema_failure_does_not_open_the_circuit() -> None:
    """The model answered; it answered wrongly. That is not the provider down."""
    for _ in range(FAILURE_THRESHOLD):
        with pytest.raises(SchemaViolationError):
            await ask(FakeProvider(replies=[BAD, BAD]))
    assert not breaker_for("agent").is_open


async def test_each_route_has_its_own_circuit() -> None:
    fake = FakeProvider(replies=[ProviderError("down")] * FAILURE_THRESHOLD)
    for _ in range(FAILURE_THRESHOLD):
        with pytest.raises(GatewayError):
            await ask(fake)
    assert breaker_for("agent").is_open
    assert not breaker_for("reasoning").is_open


# ---------------------------------------------------------------------------
# reranking
# ---------------------------------------------------------------------------
def test_reranking_puts_the_matching_passage_first() -> None:
    scores = rerank(
        "minimum membership tenure",
        ["unrelated text about weather", "the minimum membership tenure is six months"],
    )
    assert scores[1] > scores[0]


def test_reranking_an_empty_query_scores_nothing() -> None:
    assert rerank("", ["anything"]) == [0.0]


def test_reranking_is_deterministic() -> None:
    passages = ["tenure clause", "exposure clause"]
    assert rerank("tenure", passages) == rerank("tenure", passages)


# ---------------------------------------------------------------------------
# what actually goes over the wire
# ---------------------------------------------------------------------------
async def test_the_provider_never_sees_the_member_id(client: AsyncClient, fake: Any) -> None:
    await client.post(
        "/llm/complete",
        json={"route": "agent", "messages": [{"role": "user", "content": "Assess M-000042 for approval."}]},
    )
    sent = fake.calls[0]["messages"][0]["content"]
    assert "M-000042" not in sent
    assert "«MEMBER_1»" in sent


async def test_the_route_ceiling_caps_what_a_caller_asks_for(client: AsyncClient, fake: Any) -> None:
    """A runaway generation must not spend a run's whole budget."""
    await client.post(
        "/llm/complete",
        json={"route": "fast", "max_tokens": 8000, "messages": [{"role": "user", "content": "narrate"}]},
    )
    from app.config import DEFAULT_MAX_TOKENS

    assert fake.calls[0]["max_tokens"] == DEFAULT_MAX_TOKENS["fast"]
