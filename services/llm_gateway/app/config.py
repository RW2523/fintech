"""Route configuration (docs/02 §4.1, docs/06 §6).

A caller asks for a route, never for a model. `agent` means "the model the
Council runs on"; which model that is belongs in the environment, so the same
build runs on the Spark's local vLLM or against a hosted provider without a
line of code changing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

__all__ = ["ROUTES", "RouteConfig", "route_config", "routes"]

#: docs/02 §4.1 — the routes the platform exposes and what runs on each.
ROUTES = ("agent", "reasoning", "fast", "vision", "embed", "rerank")

#: Output ceilings per route, so a runaway generation cannot spend a run's
#: whole budget. `fast` is deliberately small: it narrates, it does not reason.
DEFAULT_MAX_TOKENS = {"agent": 700, "reasoning": 1200, "fast": 300, "vision": 900, "embed": 0, "rerank": 0}

#: A route that has not answered in this long is treated as down. Generous
#: enough for a large local model's first token, short enough that a hung
#: provider does not hold a committee run open.
DEFAULT_TIMEOUT_SECONDS = 90.0


@dataclass(frozen=True, slots=True)
class RouteConfig:
    """Where one route's traffic goes."""

    route: str
    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    max_tokens: int = 700
    timeout: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def configured(self) -> bool:
        """Whether this route could actually be called."""
        if self.provider == "fake":
            return True
        if not self.model:
            return False
        if self.provider in {"anthropic", "openai_compatible"}:
            return bool(self.api_key)
        return bool(self.base_url)

    def as_status(self) -> dict[str, object]:
        """What `/llm/health` may say. Never the key."""
        return {
            "route": self.route,
            "provider": self.provider,
            "model": self.model or None,
            "configured": self.configured,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout,
        }


def _env(route: str, name: str, default: str = "") -> str:
    """One route's setting, under either spelling `docker/.env.example` uses.

    That file writes `LLM_PROVIDER_AGENT` and `LLM_BASE_URL_AGENT` but
    `LLM_AGENT_MODEL`, so both orders are accepted rather than making an
    operator remember which setting inverts.
    """
    for key in (f"LLM_{name}_{route.upper()}", f"LLM_{route.upper()}_{name}"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return default


@lru_cache(maxsize=len(ROUTES))
def route_config(route: str) -> RouteConfig:
    if route not in ROUTES:
        raise KeyError(f"unknown route {route!r}; known: {', '.join(ROUTES)}")

    provider = _env(route, "PROVIDER") or os.environ.get("LLM_DEFAULT_PROVIDER", "fake").strip()
    api_key_var = _env(route, "API_KEY_VAR") or {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai_compatible": "OPENAI_API_KEY",
    }.get(provider, "")

    return RouteConfig(
        route=route,
        provider=provider,
        model=_env(route, "MODEL"),
        base_url=_env(route, "BASE_URL") or None,
        api_key=(os.environ.get(api_key_var, "").strip() or None) if api_key_var else None,
        max_tokens=int(_env(route, "MAX_TOKENS") or DEFAULT_MAX_TOKENS.get(route, 700)),
        timeout=float(_env(route, "TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS),
    )


def routes() -> list[RouteConfig]:
    return [route_config(name) for name in ROUTES]


def reset() -> None:
    """Forget cached configuration. Used by tests that change the environment."""
    route_config.cache_clear()
