"""Provider adapters (docs/06 §6, docs/02 §4).

Four ways to reach a model and one that pretends to. They differ in wire
format and in how they take a JSON schema; everything above this module works
in terms of `Completion`, so the rest of the platform never learns which
provider answered.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

__all__ = ["Completion", "Provider", "ProviderError", "provider_for"]


class ProviderError(RuntimeError):
    """The provider could not be reached, or refused."""


@dataclass(frozen=True, slots=True)
class Completion:
    """One answer, in the shape the gateway promises its callers."""

    content: str
    model: str
    provider: str
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_finish_reason: str | None = None


class Provider(ABC):
    """What every adapter must be able to do."""

    name: str = "provider"

    @abstractmethod
    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        json_schema: dict[str, Any] | None,
        timeout: float,
    ) -> Completion: ...

    async def embed(self, *, model: str, texts: list[str], timeout: float) -> list[list[float]]:
        raise ProviderError(f"{self.name} cannot embed")

    async def close(self) -> None:
        return None


def _json_instruction(schema: dict[str, Any]) -> str:
    """The fallback for providers with no structured-output mode.

    Asking in words is weaker than the provider enforcing it, which is why the
    gateway validates the answer either way and takes one corrective turn.
    """
    return (
        "Answer with a single JSON object and nothing else. No prose, no "
        "code fence. It must satisfy this JSON Schema:\n" + json.dumps(schema, separators=(",", ":"))
    )


class OpenAICompatible(Provider):
    """vLLM, Ollama's OpenAI endpoint, and hosted OpenAI-compatible APIs."""

    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        name: str | None = None,
        supports_schema: bool = True,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._supports_schema = supports_schema
        if name:
            self.name = name

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        return headers

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        json_schema: dict[str, Any] | None,
        timeout: float,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_schema:
            if self._supports_schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "opinion", "schema": json_schema, "strict": True},
                }
            else:
                body["messages"] = [*messages, {"role": "system", "content": _json_instruction(json_schema)}]
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self._base}/chat/completions", json=body, headers=self._headers()
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: {exc}") from exc

        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = payload.get("usage") or {}
        return Completion(
            content=str(message.get("content") or ""),
            model=str(payload.get("model") or model),
            provider=self.name,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            tool_calls=list(message.get("tool_calls") or []),
            raw_finish_reason=choice.get("finish_reason"),
        )

    async def embed(self, *, model: str, texts: list[str], timeout: float) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self._base}/embeddings", json={"model": model, "input": texts}, headers=self._headers()
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: {exc}") from exc
        return [list(item["embedding"]) for item in payload.get("data", [])]


class Anthropic(Provider):
    """The Anthropic messages API.

    Structured output goes through tool use: the schema becomes a single tool
    the model must call, which is stricter than asking in prose.
    """

    name = "anthropic"
    VERSION = "2023-06-01"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._key = api_key
        self._base = (base_url or "https://api.anthropic.com").rstrip("/")

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        json_schema: dict[str, Any] | None,
        timeout: float,
    ) -> Completion:
        system = " ".join(m["content"] for m in messages if m.get("role") == "system")
        turns = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") in {"user", "assistant"}
        ]
        body: dict[str, Any] = {
            "model": model,
            "messages": turns,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            body["system"] = system
        if json_schema:
            body["tools"] = [
                {"name": "respond", "description": "Return the answer.", "input_schema": json_schema}
            ]
            body["tool_choice"] = {"type": "tool", "name": "respond"}

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self._base}/v1/messages",
                    json=body,
                    headers={
                        "content-type": "application/json",
                        "x-api-key": self._key,
                        "anthropic-version": self.VERSION,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: {exc}") from exc

        text_parts: list[str] = []
        for block in payload.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                text_parts.append(json.dumps(block.get("input") or {}))
        usage = payload.get("usage") or {}
        return Completion(
            content="".join(text_parts),
            model=str(payload.get("model") or model),
            provider=self.name,
            tokens_in=int(usage.get("input_tokens") or 0),
            tokens_out=int(usage.get("output_tokens") or 0),
            raw_finish_reason=payload.get("stop_reason"),
        )


@dataclass
class FakeProvider(Provider):
    """A provider that answers from a script.

    Every gateway behaviour worth testing -- schema retry, budget refusal, the
    circuit breaker, masking -- has to be exercised without a model, so the
    fake is a first-class adapter rather than a mock built in the tests.
    """

    name: str = "fake"
    replies: list[str | Exception] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    default: str = '{"ok": true}'
    tokens_in: int = 40
    tokens_out: int = 20

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        json_schema: dict[str, Any] | None,
        timeout: float,
    ) -> Completion:
        self.calls.append(
            {"model": model, "messages": messages, "max_tokens": max_tokens, "json_schema": json_schema}
        )
        reply: str | Exception = self.replies.pop(0) if self.replies else self.default
        if isinstance(reply, Exception):
            raise ProviderError(str(reply)) from reply
        return Completion(
            content=reply,
            model=model or "fake-1",
            provider=self.name,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
        )

    async def embed(self, *, model: str, texts: list[str], timeout: float) -> list[list[float]]:
        self.calls.append({"model": model, "texts": texts})
        # Deterministic and cheap: the same text always gets the same vector,
        # which is what a retrieval test needs from a stand-in.
        return [[((hash(text) >> shift) % 1000) / 1000.0 for shift in range(8)] for text in texts]


def provider_for(config: Any) -> Provider:
    """The adapter for a route's configuration."""
    name = config.provider
    if name == "fake":
        return FakeProvider()
    if name == "anthropic":
        if not config.api_key:
            raise ProviderError(f"route {config.route} has no Anthropic API key")
        return Anthropic(config.api_key, config.base_url)
    if name in {"vllm", "ollama", "openai_compatible"}:
        base = config.base_url or os.environ.get("LLM_BASE_URL", "")
        if not base:
            raise ProviderError(f"route {config.route} has no base URL")
        # Ollama's OpenAI-compatible endpoint ignores `response_format`, so the
        # schema is asked for in the prompt and validated here instead.
        return OpenAICompatible(base, config.api_key, name=name, supports_schema=name != "ollama")
    raise ProviderError(f"unknown provider {name!r} for route {config.route}")
