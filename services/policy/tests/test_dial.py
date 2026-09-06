"""T-051 — the Autonomy Dial, the kill switch and what they change.

The dial is the one setting in the platform that decides whether a person sees
a case at all, so the tests here are as much about who may move it and what a
move is recorded as, as about what it does.

These run against the database, because the whole point of the dial is that it
is state the pack does not carry.
"""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.dial import AmendmentError, amend, check_approvers, effective_setting

ROOT = Path(__file__).resolve().parents[3]
PRODUCT = "PF-STD"

HEADS = [
    {"role": "HEAD_OF_CREDIT", "actor_id": "u-credit"},
    {"role": "HEAD_OF_RISK", "actor_id": "u-risk"},
]


# ---------------------------------------------------------------------------
# the rules of a change, with no database in the way
# ---------------------------------------------------------------------------
def test_two_heads_may_move_the_dial() -> None:
    check_approvers(HEADS)


def test_one_approver_is_not_enough() -> None:
    with pytest.raises(AmendmentError, match="exactly two"):
        check_approvers(HEADS[:1])


def test_one_person_cannot_approve_twice() -> None:
    """The point of two approvers is two people. One holding both roles is
    still one person deciding how much the platform may do without one."""
    both = [
        {"role": "HEAD_OF_CREDIT", "actor_id": "u-same"},
        {"role": "HEAD_OF_RISK", "actor_id": "u-same"},
    ]
    with pytest.raises(AmendmentError, match="cannot approve their own change twice"):
        check_approvers(both)


def test_an_officer_may_not_approve_a_dial_change() -> None:
    with pytest.raises(AmendmentError, match="may not approve"):
        check_approvers([{"role": "CREDIT_OFFICER", "actor_id": "u-1"}, HEADS[1]])


def test_an_unknown_setting_is_refused() -> None:
    with pytest.raises(AmendmentError, match="unknown setting"):
        amend({"setting": "ADVISE"}, {"setting": "FULLY_AUTONOMOUS"})


def test_a_change_may_not_rewrite_the_action_levels() -> None:
    """A setting change is not a route into changing what the platform is
    allowed to do at all."""
    current = {"setting": "ADVISE", "action_levels": {"L3": {"requires": "PER_SETTING"}}}
    amended = amend(current, {"setting": "ASSIST", "action_levels": {"L3": {"requires": "AUTO"}}})
    assert amended["action_levels"] == current["action_levels"]


def test_bands_must_ascend_and_end_open() -> None:
    with pytest.raises(AmendmentError, match="do not ascend"):
        amend({}, {"bands": [{"max_amount": 20000}, {"max_amount": 10000}, {"max_amount": None}]})
    with pytest.raises(AmendmentError, match="open-ended"):
        amend({}, {"bands": [{"max_amount": 10000}, {"max_amount": 20000}]})


def test_conditions_are_merged_not_replaced() -> None:
    """A change that tightened one condition must not silently drop the rest."""
    current = {"autonomous_conditions": {"min_confidence": 0.9, "max_disagreement": 0.25}}
    amended = amend(current, {"conditions": {"min_confidence": 0.95}})
    assert amended["autonomous_conditions"] == {"min_confidence": 0.95, "max_disagreement": 0.25}


def test_the_kill_switch_overrides_the_setting_without_overwriting_it() -> None:
    """Releasing the switch restores what the institution chose, rather than
    whatever ADVISE was written over the top of it."""
    autonomy = {
        "setting": "AUTONOMOUS_WITHIN_LIMITS",
        "kill_switch": {"effect": {"revert_to": "ADVISE"}},
    }
    assert effective_setting(autonomy, kill_switch=True) == "ADVISE"
    assert effective_setting(autonomy, kill_switch=False) == "AUTONOMOUS_WITHIN_LIMITS"
    assert autonomy["setting"] == "AUTONOMOUS_WITHIN_LIMITS"


# ---------------------------------------------------------------------------
# the endpoints, against the database
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def database_url() -> str:
    if url := os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL"):
        return url
    values: dict[str, str] = {}
    env = ROOT / "docker" / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return (
        f"postgresql+asyncpg://{values.get('POSTGRES_USER', 'cio')}:"
        f"{values.get('POSTGRES_PASSWORD', '')}@localhost:"
        f"{values.get('POSTGRES_PORT', '5432')}/{values.get('POSTGRES_DB', 'cio')}"
    )


@lru_cache(maxsize=1)
def postgres_is_up() -> bool:
    try:
        with socket.create_connection(("localhost", 5432), 2):
            return True
    except OSError:
        return False


@pytest_asyncio.fixture
async def dialled(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """A client with the dial and the switch returned to their pack defaults.

    The dial is durable by design, so a test that left it turned up would make
    every later test read a different platform.
    """
    if not postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up`")
    monkeypatch.setenv("DATABASE_URL", database_url())

    from app.db import dispose, engine, session, sessions
    from app.main import app
    from app.settings import settings

    settings.cache_clear()
    engine.cache_clear()
    sessions.cache_clear()

    async def reset() -> None:
        async with session() as db:
            await db.execute(
                text("DELETE FROM app_policy.policy_version WHERE product_code = :p AND kind = 'autonomy'"),
                {"p": PRODUCT},
            )
            await db.execute(
                text("DELETE FROM app_policy.kill_switch WHERE product_code = :p"), {"p": PRODUCT}
            )
            await db.commit()

    await reset()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://policy") as client:
            yield client
    finally:
        await reset()
        await dispose()


async def move(client: AsyncClient, setting: str, **extra: Any) -> Any:
    return await client.post(f"/autonomy/{PRODUCT}", json={"setting": setting, "approvers": HEADS, **extra})


async def test_the_pack_setting_stands_until_the_dial_is_moved(dialled: AsyncClient) -> None:
    body = (await dialled.get(f"/autonomy/{PRODUCT}")).json()
    assert body["setting"] == "ASSIST"
    assert body["autonomy_version"].startswith("autonomy/PF-STD/")
    assert body["kill_switch"]["enabled"] is False


async def test_a_change_is_recorded_with_who_approved_it(dialled: AsyncClient) -> None:
    response = await move(dialled, "AUTONOMOUS_WITHIN_LIMITS", reason="pilot on small amounts")
    assert response.status_code == 200
    body = response.json()
    assert body["setting"] == "AUTONOMOUS_WITHIN_LIMITS"
    assert body["previous_setting"] == "ASSIST"

    read = (await dialled.get(f"/autonomy/{PRODUCT}")).json()
    assert read["setting"] == "AUTONOMOUS_WITHIN_LIMITS"
    assert read["history"][0]["approved_by"] == ["HEAD_OF_CREDIT:u-credit", "HEAD_OF_RISK:u-risk"]


async def test_a_change_supersedes_rather_than_layers(dialled: AsyncClient) -> None:
    await move(dialled, "AUTONOMOUS_WITHIN_LIMITS")
    await move(dialled, "ADVISE")

    read = (await dialled.get(f"/autonomy/{PRODUCT}")).json()
    assert read["setting"] == "ADVISE"
    statuses = [h["status"] for h in read["history"]]
    assert statuses.count("ACTIVE") == 1, "two settings cannot both be in force"


async def test_a_superseded_setting_is_kept(dialled: AsyncClient) -> None:
    """A decision made last week was made under last week's dial, and an
    auditor asking what that was deserves an answer."""
    await move(dialled, "AUTONOMOUS_WITHIN_LIMITS")
    await move(dialled, "ADVISE")

    read = (await dialled.get(f"/autonomy/{PRODUCT}")).json()
    assert [h["setting"] for h in read["history"]] == ["ADVISE", "AUTONOMOUS_WITHIN_LIMITS"]


async def test_a_change_emits_an_event(dialled: AsyncClient) -> None:
    from app.db import session

    await move(dialled, "AUTONOMOUS_WITHIN_LIMITS")
    async with session() as db:
        names = (
            await db.execute(
                text("""
        SELECT name FROM events.outbox WHERE key = :p ORDER BY created_at DESC LIMIT 1
    """),
                {"p": PRODUCT},
            )
        ).scalar_one_or_none()
    assert names == "autonomy.setting_changed"


async def test_a_single_approver_is_refused_by_the_endpoint(dialled: AsyncClient) -> None:
    response = await dialled.post(f"/autonomy/{PRODUCT}", json={"setting": "ADVISE", "approvers": HEADS[:1]})
    assert response.status_code == 422
    assert "two approvers" in response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# the kill switch
# ---------------------------------------------------------------------------
async def test_an_owner_may_stop_the_platform(dialled: AsyncClient) -> None:
    await move(dialled, "AUTONOMOUS_WITHIN_LIMITS")
    response = await dialled.post(
        f"/kill-switch/{PRODUCT}",
        json={"actor_id": "u-risk", "actor_role": "HEAD_OF_RISK", "reason": "model drift"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kill_switch"]["enabled"] is True
    assert body["setting"] == "AUTONOMOUS_WITHIN_LIMITS", "the switch overrides, it does not overwrite"
    assert body["effective_setting"] == "ADVISE"


async def test_an_officer_may_not_stop_the_platform(dialled: AsyncClient) -> None:
    response = await dialled.post(
        f"/kill-switch/{PRODUCT}",
        json={"actor_id": "u-1", "actor_role": "CREDIT_OFFICER", "reason": "worried"},
    )
    assert response.status_code == 403


async def test_releasing_restores_the_chosen_setting(dialled: AsyncClient) -> None:
    await move(dialled, "AUTONOMOUS_WITHIN_LIMITS")
    await dialled.post(
        f"/kill-switch/{PRODUCT}",
        json={"actor_id": "u-risk", "actor_role": "HEAD_OF_RISK", "reason": "model drift"},
    )
    response = await dialled.request(
        "DELETE",
        f"/kill-switch/{PRODUCT}",
        json={"actor_id": "u-risk", "actor_role": "HEAD_OF_RISK", "reason": "drift resolved"},
    )
    assert response.status_code == 200
    assert response.json()["effective_setting"] == "AUTONOMOUS_WITHIN_LIMITS"


async def test_the_switch_is_read_by_the_service_not_taken_from_the_caller(
    dialled: AsyncClient,
) -> None:
    """A caller that forgot to send `kill_switch_active` must not get a
    decision made as though the switch were off. That is the one mistake a kill
    switch may not permit."""
    await move(dialled, "AUTONOMOUS_WITHIN_LIMITS")
    await dialled.post(
        f"/kill-switch/{PRODUCT}",
        json={"actor_id": "u-risk", "actor_role": "HEAD_OF_RISK", "reason": "stop"},
    )

    record = (
        await dialled.post(
            "/policy/synthesize",
            json={
                "product_code": PRODUCT,
                "snapshot_id": "snap_01JQZK7M8N9P0Q1R2S3T4V5W6X",
                "tier": "FAST",
                "requested_amount": "8000",
                "policy_result": {
                    "blockers": [],
                    "rules": [],
                    "evidence_coverage": 1.0,
                    "required_authority": "CREDIT_OFFICER",
                    "policy_version": "policy/PF-STD/2026.09.1",
                },
                # A scored case, so this exercises the router rather than the
                # "nothing was scored" branch above it.
                "factor_scores": [
                    {
                        "family": "CAPACITY",
                        "score": 80,
                        "calc_id": "calc_KILLSWITCH",
                        "tool": "affordability.compute",
                        "inputs_digest": "0" * 64,
                        "evidence_refs": [],
                    }
                ],
                "opinions": [],
                # kill_switch_active deliberately absent
            },
        )
    ).json()

    assert record["route"] == "OFFICER_REVIEW"
    assert "KILL_SWITCH" in record["route_reasons"]


# ---------------------------------------------------------------------------
# severity, which the router has to be able to name
# ---------------------------------------------------------------------------
def test_the_cleanest_case_has_a_severity_the_router_knows() -> None:
    """The fraud service reports NONE for a case with no findings, and a
    router that could not name it raised instead of routing."""
    from app.autonomy import _severity_rank

    assert _severity_rank("NONE") < _severity_rank("LOW") < _severity_rank("CRITICAL")


def test_an_unknown_severity_is_treated_as_the_worst() -> None:
    """Failing closed matters more than failing loudly: a level this router
    cannot reason about must not reach an autonomous approval."""
    from app.autonomy import _severity_rank

    assert _severity_rank("CATASTROPHIC") > _severity_rank("CRITICAL")


def test_a_missing_integrity_factor_is_not_read_as_clean() -> None:
    """A case that did not report an integrity level has not been shown to be
    clean, and reading silence as the cleanest answer is how an unassessed case
    reaches autonomy."""
    from app.factors import FactorScore
    from app.synthesize import _integrity_level

    assert _integrity_level(()) == "LOW"
    scored = (
        FactorScore(
            family="INTEGRITY", score=100, calc_id="c", tool="t", inputs_digest="0" * 64, level="NONE"
        ),
    )
    assert _integrity_level(scored) == "NONE"
