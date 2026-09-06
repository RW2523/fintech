"""gateway: the platform's single public surface (docs/01 §4, docs/13 §1).

Checks the bearer token, stamps a trace id, and proxies `/api/<service>/…` to
the service that owns it. Internal services are not published to the host.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response

from app.routing import PUBLIC_PREFIXES, UPSTREAMS, upstream_for
from app.settings import settings
from cio_common.auth import ROLES, decode_token, issue_token
from cio_common.errors import Forbidden, NotFound, ValidationFailed
from cio_common.otel import inject_context
from cio_common.service import TRACE_HEADER, create_app

router = APIRouter()

#: Hop-by-hop headers that must not be forwarded, plus the ones the gateway
#: sets itself. Incoming header names are lowercase, so they are dropped here
#: rather than colliding with the canonical spelling we add below.
_STRIP = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "proxy-authorization",
    TRACE_HEADER.lower(),
    "x-principal-sub",
    "x-principal-role",
    "x-principal-branch",
    "x-principal-member",
    "x-internal-key",
}

#: Upstreams whose work is a batch rather than a query. A model call and a
#: five-thousand-member import are slow for different reasons and need the same
#: deadline: the import builds 453,000 timeline events and takes ninety
#: seconds, which the reader's timeout cut off at sixty with a 500 that said
#: nothing about what had happened.
_SLOW_SERVICES = frozenset({"llm_gateway", "agent_runtime", "committee", "member_intelligence", "lmi"})

_client: httpx.AsyncClient | None = None


async def _startup(_app: object) -> None:
    global _client
    _client = httpx.AsyncClient(timeout=settings().upstream_timeout_seconds)


async def _shutdown(_app: object) -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


@router.get("/api/services", tags=["meta"], summary="Routable services and their ports")
async def services() -> dict[str, int]:
    return dict(sorted(UPSTREAMS.items()))


@router.post("/api/auth/dev-token", tags=["auth"], summary="Mint a demo token")
async def dev_token(body: dict[str, Any]) -> dict[str, Any]:
    """Demo only. Disabled unless CIO_ENV is a development environment."""
    config = settings()
    if config.cio_env in ("pilot", "prod"):
        raise Forbidden("dev tokens are disabled outside development")

    role = body.get("role")
    if role not in ROLES:
        raise ValidationFailed(f"unknown role {role!r}", roles=sorted(ROLES))

    token = issue_token(
        body.get("sub") or f"{role}-demo",
        role,
        branch=body.get("branch"),
        member_id=body.get("member_id"),
    )
    return {"access_token": token, "token_type": "bearer", "role": role}


@router.api_route(
    "/api/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    tags=["proxy"],
    summary="Proxy to a domain service",
)
async def proxy(service: str, path: str, request: Request) -> Response:
    base = upstream_for(service)
    if base is None:
        raise NotFound(f"no service named {service!r}", services=sorted(UPSTREAMS))

    principal = _authenticate(request)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    headers[TRACE_HEADER] = request.headers.get(TRACE_HEADER) or uuid.uuid4().hex
    if principal is not None:
        headers["X-Principal-Sub"] = principal.sub
        headers["X-Principal-Role"] = principal.role
        if principal.branch:
            headers["X-Principal-Branch"] = principal.branch
        if principal.member_id:
            headers["X-Principal-Member"] = principal.member_id
    headers["X-Internal-Key"] = settings().internal_key
    # The trace the gateway is in, passed to whoever it calls. A `traceparent`
    # the caller sent is already in `headers`; this fills it in when there was
    # none, which is every request from the workbench.
    inject_context(headers)

    assert _client is not None, "gateway client not started"
    # Services that run a model get the longer deadline; everything else keeps
    # the reader's, so one slow upstream cannot hold a connection open.
    timeout = (
        settings().llm_timeout_seconds if service in _SLOW_SERVICES else settings().upstream_timeout_seconds
    )
    upstream = await _client.request(
        request.method,
        f"{base}/{path}",
        params=request.query_params,
        content=await request.body(),
        headers=headers,
        timeout=timeout,
    )

    passthrough = {k: v for k, v in upstream.headers.items() if k.lower() not in _STRIP}
    passthrough[TRACE_HEADER] = headers[TRACE_HEADER]
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=passthrough,
        media_type=upstream.headers.get("content-type"),
    )


def _authenticate(request: Request) -> Any:
    """Every proxied call needs a bearer token except the public prefixes."""
    if any(request.url.path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        return None
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Forbidden("missing bearer token")
    return decode_token(token)


app = create_app(
    "gateway",
    version="0.1.0",
    routers=[router],
    settings=settings(),
    on_startup=_startup,
    on_shutdown=_shutdown,
)
