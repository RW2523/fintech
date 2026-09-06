"""The gateway itself: mask, call, validate, unmask, account (docs/06 §6).

Everything a caller relies on happens here in a fixed order, and the order is
the point. Personal data is replaced before the request leaves the platform.
The answer is validated against the schema the caller asked for, and a failure
gets one corrective turn rather than being passed on as though it were valid.
Only then is the answer unmasked, and only what the run actually spent is
recorded against its budget.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from app import pii
from app.breaker import breaker_for
from app.budget import Budget, usage_ledger
from app.config import RouteConfig, route_config
from app.providers import Completion, Provider, ProviderError, provider_for

__all__ = ["GatewayError", "Result", "SchemaViolationError", "complete", "embed"]

#: A model asked for JSON sometimes wraps it in a fence or a sentence. Pulling
#: the object out is not leniency about the schema, only about the packaging.
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_BARE_OBJECT = re.compile(r"\{.*\}", re.S)

#: docs/06 §6 — one corrective turn, then the caller is told it failed. A
#: second retry mostly buys a second wrong answer and a doubled latency.
CORRECTIVE_TURNS = 1


class GatewayError(RuntimeError):
    """The gateway could not produce an answer."""


class SchemaViolationError(GatewayError):
    """The model's answer did not satisfy the schema, twice."""

    def __init__(self, errors: list[str], attempts: int) -> None:
        super().__init__(
            f"the model's answer failed its schema after {attempts} attempts: {'; '.join(errors[:3])}"
        )
        self.errors = errors
        self.attempts = attempts


@dataclass
class Result:
    """One completed call, in the shape docs/06 §6 promises."""

    content: str
    model: str
    provider: str
    route: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    masked_fields: int
    attempts: int = 1
    json_value: Any | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    mask_summary: dict[str, Any] = field(default_factory=dict)
    run_usage: dict[str, int] = field(default_factory=dict)

    def as_contract(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "content": self.content,
            "model": self.model,
            "provider": self.provider,
            "route": self.route,
            "usage": {
                "in": self.tokens_in,
                "out": self.tokens_out,
                "total": self.tokens_in + self.tokens_out,
            },
            "latency_ms": round(self.latency_ms, 2),
            "masked_fields": self.masked_fields,
            "attempts": self.attempts,
            "mask": self.mask_summary,
            "run_usage": self.run_usage,
        }
        if self.json_value is not None:
            body["json"] = self.json_value
        if self.tool_calls:
            body["tool_calls"] = self.tool_calls
        return body


def _extract_json(text: str) -> Any:
    """The JSON object in a model's answer, however it was wrapped."""
    stripped = text.strip()
    for candidate in (stripped, *(m.group(1) for m in _FENCED.finditer(stripped))):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    match = _BARE_OBJECT.search(stripped)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _validate(value: Any, schema: dict[str, Any]) -> list[str]:
    if value is None:
        return ["the answer was not JSON"]
    validator = Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
    ]


def _corrective_turn(errors: list[str]) -> dict[str, str]:
    """The one follow-up the gateway is allowed.

    It names what was wrong rather than repeating the schema: a model that has
    just seen the schema and missed it is not helped by seeing it again.
    """
    return {
        "role": "user",
        "content": (
            "Your previous answer did not satisfy the schema:\n- "
            + "\n- ".join(errors[:5])
            + "\nReply with the corrected JSON object only."
        ),
    }


async def complete(
    *,
    route: str,
    messages: list[dict[str, Any]],
    json_schema: dict[str, Any] | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.0,
    budget: Budget | None = None,
    run_id: str = "",
    known_values: dict[str, list[str]] | None = None,
    provider: Provider | None = None,
    config: RouteConfig | None = None,
) -> Result:
    """Ask a route for an answer, with everything the platform requires around it."""
    config = config or route_config(route)
    breaker = breaker_for(route)
    breaker.check()

    ledger = usage_ledger()
    ceiling = min(max_tokens or config.max_tokens, config.max_tokens)
    # Checked before the call, against what it could cost at worst. Refusing
    # after the tokens are spent would not be a budget.
    ledger.check(run_id, budget or Budget(), ceiling)

    masked_messages, mapping = pii.mask_payload(messages, known=known_values or {})
    adapter = provider or provider_for(config)

    started = time.perf_counter()
    conversation = list(masked_messages)
    errors: list[str] = []
    completion: Completion | None = None
    value: Any | None = None
    spent_in = spent_out = 0

    for attempt in range(1, CORRECTIVE_TURNS + 2):
        try:
            completion = await adapter.complete(
                model=config.model,
                messages=conversation,
                max_tokens=ceiling,
                temperature=temperature,
                json_schema=json_schema,
                timeout=config.timeout,
            )
        except ProviderError as exc:
            breaker.record_failure(str(exc))
            raise GatewayError(str(exc)) from exc
        spent_in += completion.tokens_in
        spent_out += completion.tokens_out

        if json_schema is None:
            errors = []
            break
        value = _extract_json(completion.content)
        errors = _validate(value, json_schema)
        if not errors:
            break
        if attempt <= CORRECTIVE_TURNS:
            conversation = [
                *conversation,
                {"role": "assistant", "content": completion.content},
                _corrective_turn(errors),
            ]

    breaker.record_success()
    assert completion is not None
    # Recorded whether or not the schema held: the tokens were spent either
    # way, and a run that fails its schema twice must not get them back.
    ledger.record(run_id, spent_in + spent_out)
    run_usage = ledger.report(run_id)

    if errors:
        # A schema failure is not a provider failure: the model answered, it
        # answered wrongly. The breaker stays closed and the caller decides
        # whether to degrade.
        raise SchemaViolationError(errors, attempt)

    content = pii.unmask_text(completion.content, mapping)
    return Result(
        content=content,
        model=completion.model,
        provider=completion.provider,
        route=route,
        tokens_in=spent_in,
        tokens_out=spent_out,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        masked_fields=mapping.masked,
        attempts=attempt,
        json_value=pii.unmask_payload(value, mapping) if value is not None else None,
        tool_calls=[pii.unmask_payload(c, mapping) for c in completion.tool_calls],
        mask_summary=mapping.as_dict(),
        run_usage=run_usage,
    )


async def embed(
    *,
    texts: list[str],
    provider: Provider | None = None,
    config: RouteConfig | None = None,
    known_values: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Vectors for a list of texts, masked the same way a prompt would be."""
    config = config or route_config("embed")
    breaker = breaker_for("embed")
    breaker.check()

    masked, mapping = pii.mask_payload(texts, known=known_values or {})
    adapter = provider or provider_for(config)
    started = time.perf_counter()
    try:
        vectors = await adapter.embed(model=config.model, texts=masked, timeout=config.timeout)
    except ProviderError as exc:
        breaker.record_failure(str(exc))
        raise GatewayError(str(exc)) from exc
    breaker.record_success()
    return {
        "vectors": vectors,
        "model": config.model or "fake-embed",
        "provider": config.provider,
        "masked_fields": mapping.masked,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
    }


def rerank(query: str, passages: list[str]) -> list[float]:
    """Lexical overlap between the query and each passage.

    Deterministic and local. docs/02 §4.1 puts a cross-encoder on this route;
    until the model is loaded this keeps retrieval ordered by something rather
    than by nothing, and it is honest about what it is: word overlap, not
    relevance.
    """
    wanted = {w for w in re.findall(r"\w+", query.casefold()) if len(w) > 2}
    if not wanted:
        return [0.0] * len(passages)
    scores: list[float] = []
    for passage in passages:
        words = {w for w in re.findall(r"\w+", passage.casefold()) if len(w) > 2}
        scores.append(round(len(wanted & words) / len(wanted), 4) if words else 0.0)
    return scores
