"""T-005 — tokens, roles and attribute scoping (docs/13 §1)."""

from __future__ import annotations

import time

import pytest

from cio_common.auth import ROLES, decode_token, issue_token, require_role, scope_for
from cio_common.errors import Forbidden


def test_round_trip_preserves_identity_and_claims() -> None:
    token = issue_token("u-1", "officer", branch="B-01", desk="north")
    p = decode_token(token)
    assert (p.sub, p.role, p.branch) == ("u-1", "officer", "B-01")
    assert p.claims["desk"] == "north"


@pytest.mark.parametrize("role,authority", sorted(ROLES.items()))
def test_every_role_maps_to_its_authority_role(role: str, authority: str) -> None:
    assert decode_token(issue_token("u", role)).authority_role == authority


def test_unknown_roles_cannot_be_issued() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        issue_token("u", "wizard")


def test_expired_tokens_are_refused() -> None:
    token = issue_token("u-1", "officer", ttl_seconds=1)
    time.sleep(1.1)
    with pytest.raises(Forbidden, match="expired"):
        decode_token(token)


def test_tampered_tokens_are_refused() -> None:
    token = issue_token("u-1", "officer")
    head, payload, signature = token.split(".")
    with pytest.raises(Forbidden, match="invalid token"):
        decode_token(f"{head}.{payload}.{signature[:-4]}AAAA")


def test_a_token_signed_with_another_key_is_refused() -> None:
    import jwt as pyjwt

    forged = pyjwt.encode(
        {"sub": "u-1", "role": "head_of_credit", "exp": 2**31}, "another" * 8, algorithm="HS256"
    )
    with pytest.raises(Forbidden, match="invalid token"):
        decode_token(forged)


def test_officer_scope_is_limited_to_their_branch() -> None:
    scope = scope_for(decode_token(issue_token("u-1", "officer", branch="B-01")))
    assert not scope.unrestricted
    assert scope.allows_branch("B-01")
    assert not scope.allows_branch("B-02")


def test_member_scope_is_limited_to_themselves() -> None:
    scope = scope_for(decode_token(issue_token("m-1", "member", member_id="M-000042")))
    assert scope.allows_member("M-000042")
    assert not scope.allows_member("M-000043")


def test_a_member_token_without_a_member_id_is_refused() -> None:
    with pytest.raises(Forbidden, match="member_id"):
        scope_for(decode_token(issue_token("m-1", "member")))


@pytest.mark.parametrize("role", ["compliance", "manager", "committee", "head_of_credit"])
def test_oversight_roles_see_everything(role: str) -> None:
    assert scope_for(decode_token(issue_token("u", role))).unrestricted


async def test_require_role_admits_the_right_role() -> None:
    dependency = require_role("officer", "senior_officer")
    principal = await dependency(authorization=f"Bearer {issue_token('u-1', 'officer')}")
    assert principal.role == "officer"


async def test_require_role_rejects_the_wrong_role() -> None:
    dependency = require_role("head_of_credit")
    with pytest.raises(Forbidden, match="may not perform"):
        await dependency(authorization=f"Bearer {issue_token('u-1', 'officer')}")


@pytest.mark.parametrize("header", ["", "Bearer", "Basic abc", "token abc"])
async def test_require_role_rejects_a_missing_or_malformed_header(header: str) -> None:
    with pytest.raises(Forbidden, match="missing bearer token"):
        await require_role("officer")(authorization=header)


def test_require_role_refuses_to_be_built_with_an_unknown_role() -> None:
    with pytest.raises(ValueError, match="unknown roles"):
        require_role("officer", "wizard")
