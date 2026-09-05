"""Upstream map for the gateway (docs/01 §4).

`/api/<service>/<path>` reaches `http://<service>:<port>/<path>` on the compose
network. The gateway is the only public surface; services are not published.
"""

from __future__ import annotations

import os

__all__ = ["PUBLIC_PREFIXES", "UPSTREAMS", "upstream_for"]

UPSTREAMS: dict[str, int] = {
    "application": 8001,
    "document": 8002,
    "member_intelligence": 8003,
    "policy": 8004,
    "feature": 8005,
    "risk": 8006,
    "fraud": 8007,
    "lmi": 8008,
    "committee": 8009,
    "core_stub": 8010,
    "agent_runtime": 8011,
    "decision": 8012,
    "execution": 8013,
    "notification": 8014,
    "audit": 8015,
    "governance": 8016,
    "llm_gateway": 8020,
}

#: Paths reachable without a token. Everything else needs a bearer token.
PUBLIC_PREFIXES: tuple[str, ...] = (
    "/health",
    "/version",
    "/api/auth/dev-token",
    "/api/openapi.json",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def upstream_for(service: str) -> str | None:
    """Base URL for a service, or None when the name is not routable."""
    port = UPSTREAMS.get(service)
    if port is None:
        return None
    host = os.environ.get(f"UPSTREAM_HOST_{service.upper()}", service)
    return f"http://{host}:{port}"
