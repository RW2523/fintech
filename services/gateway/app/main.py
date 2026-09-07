"""gateway: the platform's single public surface (docs/01 §4, docs/13 §1).

Checks the bearer token, stamps a trace id, and proxies `/api/<service>/…` to
the service that owns it. Internal services are not published to the host.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.limits import check as _check
from app.limits import close as close_limiter
from app.limits import spend as _spend
from app.routing import PUBLIC_PREFIXES, UPSTREAMS, upstream_for
from app.settings import settings
from cio_common.auth import ROLES, decode_token, issue_token
from cio_common.errors import Forbidden, NotFound, ValidationFailed
from cio_common.otel import inject_context
from cio_common.service import TRACE_HEADER, create_app
from cio_common.users import Account, UserStoreError, hash_password, load_accounts, verify

log = logging.getLogger(__name__)

#: Verified against when the address does not exist, so a missing account takes
#: the same time as a wrong password. Without it the response time says whether
#: somebody has an account here, which is the first thing to probe for.
_ABSENT = Account(
    email="absent@invalid",
    role="officer",
    password_hash=hash_password("a-password-nobody-has-ever-used"),
)


def _caller(request: Request) -> str:
    """Who to count this request against.

    The first hop in `X-Forwarded-For` when a proxy set one, because behind a
    tunnel every request arrives from the tunnel's own address and counting
    those together would rate-limit the world as one caller.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
    await close_limiter()


@router.get("/api/auth/mode", tags=["auth"], summary="How to sign in here")
async def auth_mode() -> dict[str, Any]:
    """What the workbench should put on its sign-in screen.

    Asked rather than assumed, so one build serves both: a bench where a role
    picker is the point, and a deployment where an account is required. A web
    app that decided this for itself would be a second place to get it wrong.
    """
    config = settings()
    roles: list[dict[str, str]] = []
    if config.passwords_required:
        # Which roles somebody could sign in as, so the screen can offer them
        # to pick from rather than asking them to remember an address. Roles,
        # never addresses: `/api/auth/login` deliberately will not say whether
        # an account exists, and publishing the list of emails here would give
        # that away from the next endpoint along.
        try:
            for account in load_accounts(config.user_store).values():
                roles.append({"role": account.role, "name": account.name or account.role})
        except UserStoreError:  # pragma: no cover - reported by /login instead
            roles = []
    return {
        "mode": "password" if config.passwords_required else "dev",
        "environment": config.cio_env,
        "roles": sorted(roles, key=lambda entry: entry["role"]),
    }


@router.get("/api/services", tags=["meta"], summary="Routable services and their ports")
async def services() -> dict[str, int]:
    return dict(sorted(UPSTREAMS.items()))


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Usually an email address, and not validated as one. `EmailStr` refuses
    #: special-use domains, so an operator creating `officer@bank.local` or
    #: `admin@cio.internal` is told their own address is invalid. What this
    #: needs is an identifier that matches the store, and a name nobody has an
    #: account under fails at the next line anyway.
    email: str | None = Field(default=None, min_length=3, max_length=254)
    #: An alternative to `email`, for the demo's role picker: sign in as
    #: whichever account holds this role. Only ever resolves when exactly one
    #: account does, so it cannot become a way to guess at a shared inbox.
    role: str | None = Field(default=None, min_length=2, max_length=40)
    password: str = Field(min_length=1, max_length=256)


@router.post("/api/auth/login", tags=["auth"], summary="Sign in")
async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    """docs/13 §1 — an account and a password.

    One failure message for a bad address and a bad password alike. Telling
    them apart turns this endpoint into a way to find out who has an account
    here, which on a public URL is the first thing somebody will do.

    Two limits, because they guard two different things. The strict one counts
    *failures*: it is standing in for a lockout, and what a lockout stops is
    guessing. Counting successes there too would have meant a person who signs
    in, signs out and signs in again five times is locked out of their own
    platform for a minute, and it made the browser suite — which signs in for
    every test — unrunnable, which is how the distinction was found.

    The loose one counts every request, because verifying a password is
    deliberately expensive and an endpoint that will run Argon2 as often as it
    is asked is a way to spend this machine's CPU from outside it.
    """
    caller = _caller(request)
    config = settings()
    await _spend(f"login:{caller}", config.login_requests_per_minute)
    await _check(f"loginfail:{caller}", config.login_attempts_per_minute)

    try:
        accounts = load_accounts(config.user_store)
    except UserStoreError as exc:
        # A store that cannot be read is never a reason to let somebody in.
        log.error("the user store could not be read: %s", exc)
        raise Forbidden("sign-in is unavailable") from exc

    if body.email:
        account = accounts.get(body.email.strip().lower())
    elif body.role:
        # The role picker. One account or none: two accounts sharing a role
        # would make "sign in as the manager" ambiguous, and resolving it by
        # picking the first is how somebody ends up signed in as a colleague.
        matches = [a for a in accounts.values() if a.role == body.role.strip().lower()]
        account = matches[0] if len(matches) == 1 else None
    else:
        raise ValidationFailed("give an email or a role to sign in as")
    # Verified even when the account is missing, against a throwaway hash, so
    # the time this takes does not say whether the address exists.
    if account is None or not verify(account, body.password):
        if account is None:
            verify(_ABSENT, body.password)
        await _spend(f"loginfail:{caller}", config.login_attempts_per_minute)
        raise Forbidden("that email and password do not match an account")

    token = issue_token(
        account.email,
        account.role,
        branch=account.branch,
        member_id=account.member_id,
        name=account.name or None,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": account.role,
        "name": account.name,
        "member_id": account.member_id,
    }


@router.post("/api/auth/dev-token", tags=["auth"], summary="Mint a demo token")
async def dev_token(body: dict[str, Any]) -> dict[str, Any]:
    """Demo only, and refused the moment a password is required.

    This mints a `head_of_credit` token for whoever asks, with no password. On
    a bench that is the point; anywhere reachable from outside it, it is an
    open admin panel with a kill switch behind it.
    """
    config = settings()
    if config.passwords_required:
        raise Forbidden("dev tokens are disabled here; sign in at /api/auth/login")

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

    # Counted per signed-in principal, falling back to the address for the
    # public prefixes. Per principal rather than per address because behind a
    # tunnel or an office NAT every caller shares one address, and a limit that
    # counts them together is a limit one person can spend for everybody.
    await _spend(
        f"api:{principal.sub if principal else _caller(request)}",
        settings().requests_per_minute,
    )

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
