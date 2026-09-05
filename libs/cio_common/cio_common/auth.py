"""Demo identity, roles and attribute scoping (docs/13 §1).

HS256 JWTs issued by the platform itself. Authority is never trusted from the
UI: `decision-service` re-checks the authority matrix at decision time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from cio_common.errors import Forbidden
from cio_common.settings import get_settings

__all__ = ["ROLES", "Principal", "decode_token", "issue_token", "require_role", "scope_for"]

#: Role -> the authority role it carries in policy.yaml `authority.bands`.
ROLES: dict[str, str | None] = {
    "officer": "CREDIT_OFFICER",
    "senior_officer": "SENIOR_OFFICER",
    "committee": "CREDIT_COMMITTEE",
    "collections": "COLLECTIONS",
    "manager": "MANAGER",
    "compliance": "COMPLIANCE",
    "head_of_credit": "HEAD_OF_CREDIT",
    "head_of_risk": "HEAD_OF_RISK",
    "member": "MEMBER",
    "system": "SYSTEM",
}

_ALGORITHM = "HS256"


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making a request."""

    sub: str
    role: str
    branch: str | None = None
    member_id: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def authority_role(self) -> str | None:
        return ROLES.get(self.role)

    def has_role(self, *roles: str) -> bool:
        return self.role in roles


@dataclass(frozen=True, slots=True)
class Scope:
    """What a principal may see (ABAC, docs/08 §3)."""

    branches: tuple[str, ...] = ()
    member_ids: tuple[str, ...] = ()
    assigned_cases: tuple[str, ...] = ()
    unrestricted: bool = False

    def allows_member(self, member_id: str) -> bool:
        return self.unrestricted or member_id in self.member_ids

    def allows_branch(self, branch: str) -> bool:
        return self.unrestricted or branch in self.branches


def issue_token(
    sub: str,
    role: str,
    *,
    branch: str | None = None,
    member_id: str | None = None,
    ttl_seconds: int | None = None,
    **claims: Any,
) -> str:
    """Mint a signed token. `/api/auth/dev-token` is the only public caller."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; known: {', '.join(sorted(ROLES))}")
    settings = get_settings()
    now = datetime.now(UTC)
    body: dict[str, Any] = {
        "sub": sub,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds or settings.jwt_ttl_seconds)).timestamp()),
        **claims,
    }
    if branch:
        body["branch"] = branch
    if member_id:
        body["member_id"] = member_id
    return jwt.encode(body, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> Principal:
    """Verify and unpack a token. Raises :class:`Forbidden` when it is not usable."""
    try:
        body = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Forbidden("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise Forbidden("invalid token") from exc

    role = body.get("role")
    if role not in ROLES:
        raise Forbidden(f"unknown role {role!r}")

    reserved = {"sub", "role", "branch", "member_id", "iat", "exp"}
    return Principal(
        sub=body["sub"],
        role=role,
        branch=body.get("branch"),
        member_id=body.get("member_id"),
        claims={k: v for k, v in body.items() if k not in reserved},
    )


def require_role(*roles: str):  # type: ignore[no-untyped-def]
    """FastAPI dependency: the caller must hold one of ``roles``.

    Usage::

        @app.get("/cases", dependencies=[Depends(require_role("officer", "compliance"))])
    """
    unknown = set(roles) - set(ROLES)
    if unknown:
        raise ValueError(f"unknown roles: {sorted(unknown)}")

    from fastapi import Header  # imported lazily so the lib stays framework-agnostic

    async def dependency(authorization: str = Header(default="")) -> Principal:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise Forbidden("missing bearer token")
        principal = decode_token(token)
        if roles and not principal.has_role(*roles):
            raise Forbidden(
                f"role {principal.role!r} may not perform this action",
                required=sorted(roles),
            )
        return principal

    return dependency


def scope_for(principal: Principal, *, assigned_cases: tuple[str, ...] = ()) -> Scope:
    """What this principal may read (docs/08 §3).

    Officers see their own branch and assigned cases. Members see only
    themselves. Compliance, managers and the platform itself see everything.
    """
    if principal.role in ("compliance", "manager", "system", "committee", "head_of_credit", "head_of_risk"):
        return Scope(unrestricted=True)

    if principal.role == "member":
        if not principal.member_id:
            raise Forbidden("member token carries no member_id")
        return Scope(member_ids=(principal.member_id,))

    return Scope(
        branches=(principal.branch,) if principal.branch else (),
        assigned_cases=assigned_cases,
    )
