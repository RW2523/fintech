"""llm-gateway endpoints (docs/06 §6, docs/08 §5)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app import gateway
from app.breaker import CircuitOpenError, breaker_for
from app.budget import Budget, BudgetExceededError
from app.config import ROUTES, route_config
from app.config import routes as route_configs
from app.gateway import GatewayError, SchemaViolationError
from app.providers import Provider, ProviderError
from cio_common.errors import LlmUnavailable, ValidationFailed

router = APIRouter(prefix="/llm", tags=["llm"])

#: Swapped for a fake in tests, and by the warmup drill.
_provider: Provider | None = None


def set_provider(provider: Provider | None) -> None:
    global _provider
    _provider = provider


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class BudgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens: int = Field(default=0, ge=0)


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str = Field(default="agent")
    messages: list[Message] = Field(min_length=1)
    json_schema: dict[str, Any] | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    budget: BudgetIn | None = None
    request_id: str = ""
    run_id: str = ""
    #: Values the caller knows are personal. Names and addresses have no shape
    #: to match on, so the gateway masks what it is told rather than guessing.
    mask_values: dict[str, list[str]] | None = None


class VisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str
    images: list[dict[str, str]] = Field(min_length=1, max_length=8)
    json_schema: dict[str, Any] | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    run_id: str = ""
    budget: BudgetIn | None = None


class EmbedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texts: list[str] = Field(min_length=1, max_length=256)
    mask_values: dict[str, list[str]] | None = None


class RerankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    passages: list[str] = Field(min_length=1, max_length=200)


def _check_route(name: str) -> None:
    if name not in ROUTES:
        raise ValidationFailed(f"unknown route {name!r}", routes=list(ROUTES))


async def _complete(**kwargs: Any) -> dict[str, Any]:
    """Run a completion, turning the gateway's failures into the envelope."""
    try:
        result = await gateway.complete(provider=_provider, **kwargs)
    except CircuitOpenError as exc:
        raise LlmUnavailable(
            str(exc), route=exc.route, retry_after_seconds=round(exc.retry_after, 1)
        ) from exc
    except BudgetExceededError as exc:
        raise ValidationFailed(str(exc), run_id=exc.run_id, spent=exc.spent, budget=exc.budget) from exc
    except SchemaViolationError as exc:
        # The model answered, wrongly, twice. That is not the provider being
        # down, so it is a 422: the caller degrades rather than retries.
        raise ValidationFailed(str(exc), errors=exc.errors[:5], attempts=exc.attempts) from exc
    except (GatewayError, ProviderError) as exc:
        raise LlmUnavailable(str(exc)) from exc
    return result.as_contract()


@router.post("/complete", summary="Ask a route for an answer")
async def complete(body: CompleteRequest) -> dict[str, Any]:
    _check_route(body.route)
    return await _complete(
        route=body.route,
        messages=[m.model_dump() for m in body.messages],
        json_schema=body.json_schema,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
        budget=Budget(tokens=body.budget.tokens if body.budget else 0),
        run_id=body.run_id or body.request_id,
        known_values=body.mask_values,
    )


@router.post("/vision", summary="Ask the vision route about images")
async def vision(body: VisionRequest) -> dict[str, Any]:
    """Images travel as content parts; the prompt is masked like any other."""
    parts: list[dict[str, Any]] = [{"type": "text", "text": body.prompt}]
    for image in body.images:
        if "b64" not in image and "url" not in image:
            raise ValidationFailed("each image needs b64 or url")
        parts.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": image.get("url") or f"data:{image.get('mime', 'image/png')};base64,{image['b64']}"
                },
            }
        )
    return await _complete(
        route="vision",
        messages=[{"role": "user", "content": parts}],
        json_schema=body.json_schema,
        max_tokens=body.max_tokens,
        budget=Budget(tokens=body.budget.tokens if body.budget else 0),
        run_id=body.run_id,
    )


@router.post("/embed", summary="Vectors for a list of texts")
async def embed(body: EmbedRequest) -> dict[str, Any]:
    try:
        return await gateway.embed(texts=body.texts, provider=_provider, known_values=body.mask_values)
    except CircuitOpenError as exc:
        raise LlmUnavailable(str(exc), route="embed") from exc
    except (GatewayError, ProviderError) as exc:
        raise LlmUnavailable(str(exc)) from exc


@router.post("/rerank", summary="Score passages against a query")
async def rerank(body: RerankRequest) -> dict[str, Any]:
    scores = gateway.rerank(body.query, body.passages)
    return {
        "scores": scores,
        "model": "lexical-overlap",
        "note": "word overlap, not a cross-encoder; see docs/02 §4.1",
    }


@router.get("/health", summary="Per-route provider status")
async def health() -> dict[str, Any]:
    entries = []
    for config in route_configs():
        entry = config.as_status()
        entry["breaker"] = breaker_for(config.route).as_status()
        entry["available"] = bool(config.configured and not breaker_for(config.route).is_open)
        entries.append(entry)
    return {
        "routes": entries,
        "available": sum(1 for e in entries if e["available"]),
        "total": len(entries),
        "debug_prompts": os.environ.get("CIO_DEBUG_PROMPTS") == "1",
    }


@router.post("/warmup", summary="Send one small call down every route")
async def warmup() -> dict[str, Any]:
    """Load the models before the demo asks them anything.

    A route that fails here is reported rather than raised: warmup tells an
    operator what is ready, and a half-ready gateway is still worth knowing
    about.
    """
    results: list[dict[str, Any]] = []
    for name in ROUTES:
        config = route_config(name)
        if not config.configured:
            results.append({"route": name, "ready": False, "detail": "not configured"})
            continue
        try:
            if name == "embed":
                await gateway.embed(texts=["warmup"], provider=_provider)
            elif name == "rerank":
                gateway.rerank("warmup", ["warmup"])
            else:
                await gateway.complete(
                    route=name,
                    messages=[{"role": "user", "content": "Reply OK."}],
                    max_tokens=8,
                    provider=_provider,
                )
            results.append({"route": name, "ready": True})
        except Exception as exc:
            results.append({"route": name, "ready": False, "detail": str(exc)})
    return {"routes": results, "ready": sum(1 for r in results if r["ready"]), "total": len(results)}
