"""Signing in when the platform is reachable from outside its bench (docs/13 §1).

The demo signs in with a role picker, and `/api/auth/dev-token` mints a
`head_of_credit` token for whoever asks with no password. That is right on a
machine nobody else can reach and an open admin panel anywhere else: the token
it hands out can pull the kill switch, adopt a policy version and approve a
financing.

These tests are about the switch between the two and the properties of the one
that replaces it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

from cio_common.users import UserStoreError, hash_password, load_accounts, verify

PASSWORD = "a-long-enough-password"


@pytest.fixture
def store(tmp_path: Path) -> Path:
    path = tmp_path / "users.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "accounts": [
                    {
                        "email": "Alice@Example.com",
                        "role": "officer",
                        "password_hash": hash_password(PASSWORD),
                        "name": "Alice",
                    },
                    {
                        "email": "m@example.com",
                        "role": "member",
                        "password_hash": hash_password(PASSWORD),
                        "member_id": "M-000042",
                    },
                ]
            }
        )
    )
    load_accounts.cache_clear()
    yield path
    load_accounts.cache_clear()


@pytest.fixture
def limiter(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """The rate limiter counting in memory instead of in Redis.

    The limiter's rule is what is under test, not whether a Redis is running:
    with none reachable it allows everything and logs that it is not working,
    which would make these tests pass for the wrong reason.
    """
    from app import limits

    counts: dict[str, int] = {}

    class Counter:
        async def incr(self, key: str) -> int:
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        async def expire(self, key: str, seconds: int) -> None:
            return None

        async def get(self, key: str) -> int | None:
            return counts.get(key)

    monkeypatch.setattr(limits, "_client", Counter())
    yield counts
    monkeypatch.setattr(limits, "_client", None)


@pytest.fixture
def hardened(store: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A gateway configured the way a published one would be."""
    from app.settings import settings

    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("USER_STORE", str(store))
    monkeypatch.setenv("JWT_SECRET", "j" * 48)
    monkeypatch.setenv("TOKEN_SECRET", "t" * 48)
    monkeypatch.setenv("INTERNAL_KEY", "i" * 48)
    settings.cache_clear()
    from cio_common.settings import get_settings

    get_settings.cache_clear()
    yield
    settings.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway")


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------
def test_a_password_is_never_stored(store: Path) -> None:
    """The file holds a hash and the plaintext is nowhere in it."""
    assert PASSWORD not in store.read_text()
    assert "$argon2" in store.read_text()


def test_an_account_is_found_however_the_address_was_typed(store: Path) -> None:
    accounts = load_accounts(str(store))
    assert "alice@example.com" in accounts
    assert accounts["alice@example.com"].role == "officer"


def test_the_right_password_verifies_and_a_wrong_one_does_not(store: Path) -> None:
    account = load_accounts(str(store))["alice@example.com"]
    assert verify(account, PASSWORD)
    assert not verify(account, PASSWORD.upper())
    assert not verify(account, "")


def test_a_short_password_is_refused() -> None:
    """Not a policy, a floor. A four-character password on a public URL is the
    whole hardening exercise undone."""
    with pytest.raises(UserStoreError):
        hash_password("short")


def test_a_member_account_without_a_member_id_is_refused(tmp_path: Path) -> None:
    """It would sign in and see nothing: every member-facing tool reads the
    member from the token."""
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump({"accounts": [{"email": "m@example.com", "role": "member", "password_hash": "x"}]})
    )
    load_accounts.cache_clear()
    with pytest.raises(UserStoreError, match="member_id"):
        load_accounts(str(path))
    load_accounts.cache_clear()


def test_a_missing_store_is_not_a_reason_to_let_anybody_in(tmp_path: Path) -> None:
    load_accounts.cache_clear()
    with pytest.raises(UserStoreError):
        load_accounts(str(tmp_path / "nothing.yaml"))
    load_accounts.cache_clear()


# ---------------------------------------------------------------------------
# the switch
# ---------------------------------------------------------------------------
def test_the_mode_follows_the_environment() -> None:
    from cio_common.settings import Settings

    real: dict[str, Any] = {"jwt_secret": "j" * 48, "token_secret": "t" * 48, "internal_key": "i" * 48}
    assert not Settings(cio_env="demo", **real).passwords_required
    assert Settings(cio_env="pilot", **real).passwords_required
    assert Settings(cio_env="prod", **real).passwords_required
    # And a bench that has been exposed can ask for it without pretending to
    # be a pilot.
    assert Settings(cio_env="demo", auth_mode="password", **real).passwords_required


def test_placeholder_secrets_are_refused_outside_the_bench() -> None:
    """The example env ships them, so they are in git and in every copy of this
    repository. Signing tokens with one on a reachable deployment means anybody
    can mint a head_of_credit token."""
    from cio_common.settings import Settings

    with pytest.raises(ValueError, match="development default"):
        Settings(cio_env="pilot")


# ---------------------------------------------------------------------------
# the endpoint
# ---------------------------------------------------------------------------
async def test_signing_in_returns_a_token_carrying_the_account(hardened: None, client: AsyncClient) -> None:
    async with client as http:
        response = await http.post(
            "/api/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "officer"
    assert body["name"] == "Alice"

    from cio_common.auth import decode_token

    principal = decode_token(body["access_token"])
    assert principal.sub == "alice@example.com"
    assert principal.role == "officer"


async def test_a_member_signs_in_carrying_their_member_id(hardened: None, client: AsyncClient) -> None:
    """Identity that can be typed is not identity: the assistant reads the
    member from the token and nothing else."""
    async with client as http:
        response = await http.post("/api/auth/login", json={"email": "m@example.com", "password": PASSWORD})
    assert response.json()["member_id"] == "M-000042"


async def test_a_wrong_password_and_an_unknown_address_fail_the_same_way(
    hardened: None, client: AsyncClient
) -> None:
    """Telling them apart is how somebody finds out who has an account here."""
    async with client as http:
        wrong = await http.post(
            "/api/auth/login", json={"email": "alice@example.com", "password": "not-the-password"}
        )
        unknown = await http.post(
            "/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
        )

    assert wrong.status_code == unknown.status_code == 403
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


async def test_dev_tokens_are_refused_once_a_password_is_required(
    hardened: None, client: AsyncClient
) -> None:
    """The endpoint that mints a head_of_credit token for whoever asks."""
    async with client as http:
        response = await http.post("/api/auth/dev-token", json={"role": "head_of_credit"})
    assert response.status_code == 403


async def test_the_workbench_is_told_which_screen_to_show(hardened: None, client: AsyncClient) -> None:
    async with client as http:
        response = await http.get("/api/auth/mode")
    assert response.json()["mode"] == "password"


async def test_signing_in_repeatedly_is_not_treated_as_an_attack(
    hardened: None, client: AsyncClient, limiter: dict[str, int]
) -> None:
    """The strict limit counts failures, not people.

    Found by the browser suite, which signs in for every test and locked itself
    out of the platform it was testing. A limit standing in for a lockout is
    there to stop guessing; somebody who signs in, signs out and signs in again
    has guessed nothing.
    """
    async with client as http:
        for _ in range(8):  # more than login_attempts_per_minute
            response = await http.post(
                "/api/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
            )
            assert response.status_code == 200, response.text


async def test_wrong_passwords_run_out(hardened: None, client: AsyncClient, limiter: dict[str, int]) -> None:
    """And the guessing this is here to stop does run out."""
    async with client as http:
        seen = []
        for _ in range(8):
            response = await http.post(
                "/api/auth/login", json={"email": "alice@example.com", "password": "wrong"}
            )
            seen.append(response.status_code)

        assert 429 in seen, seen
        assert seen.index(429) <= 6, f"guessing should stop within a few tries: {seen}"

        # The account is not locked, only that address's guessing budget is
        # spent: clear it and the right password works. A store that locked the
        # account would be a way to lock somebody out of their own platform.
        limiter.clear()
        ok = await http.post("/api/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    assert ok.status_code == 200


async def test_signing_in_as_a_role_finds_the_account(hardened: None, client: AsyncClient) -> None:
    """The demo's way in: pick a role, give the password.

    A demonstration is a sequence of "and here is what the manager sees", and
    a screen that made somebody recall an email address for each of those was
    asking them to prove something the password already proved.
    """
    async with client as http:
        response = await http.post("/api/auth/login", json={"role": "officer", "password": PASSWORD})

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "officer"


async def test_a_role_two_accounts_share_is_refused(
    hardened: None, client: AsyncClient, store: Path
) -> None:
    """Ambiguous means no.

    Resolving "sign in as the officer" by taking the first match is how
    somebody ends up signed in as a colleague.
    """
    accounts = yaml.safe_load(store.read_text())
    accounts["accounts"].append(
        {
            "email": "bob@example.com",
            "role": "officer",
            "password_hash": hash_password(PASSWORD),
        }
    )
    store.write_text(yaml.safe_dump(accounts))
    load_accounts.cache_clear()

    async with client as http:
        response = await http.post("/api/auth/login", json={"role": "officer", "password": PASSWORD})

    assert response.status_code == 403


async def test_the_sign_in_screen_is_offered_roles_and_never_addresses(
    hardened: None, client: AsyncClient
) -> None:
    """The picker needs to know what to offer, and nothing more.

    `/api/auth/login` deliberately will not say whether an account exists, so
    publishing the addresses from the endpoint next to it would give away from
    one hand what the other is protecting.
    """
    async with client as http:
        body = (await http.get("/api/auth/mode")).json()

    roles = body["roles"]
    assert {entry["role"] for entry in roles} == {"officer", "member"}
    assert "example.com" not in json.dumps(body)
