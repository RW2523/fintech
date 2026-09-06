"""T-025 — profile projection, timeline paging and the import (docs/08 §3).

These run against the loaded synthetic population, which is what makes the
projection comparable with the core stub.
"""

from __future__ import annotations

import random
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------
async def test_importing_a_member_builds_their_timeline(client: AsyncClient, seeded_member: str) -> None:
    body = (await client.get(f"/members/{seeded_member}/stats")).json()
    assert body["total"] > 0
    assert "PAYMENT_DUE" in body["by_type"]


async def test_a_replayed_import_does_not_duplicate_events(client: AsyncClient, seeded_member: str) -> None:
    """Deterministic event ids are what make the change feed safe to replay."""
    before = (await client.get(f"/members/{seeded_member}/stats")).json()["total"]
    await client.post("/members/import", json={"member_ids": [seeded_member], "rebuild_profiles": False})
    after = (await client.get(f"/members/{seeded_member}/stats")).json()["total"]
    assert after == before


async def test_the_import_reports_what_it_wrote(client: AsyncClient, seeded_member: str) -> None:
    body = (await client.post("/members/import", json={"member_ids": [seeded_member]})).json()
    assert body["members"] == 1
    assert body["events"] > 0
    assert set(body["by_type"]) <= {
        "PAYMENT_DUE",
        "PAYMENT_RECEIVED",
        "PAYMENT_LATE",
        "PAYMENT_PARTIAL",
        "DEDUCTION_RECEIVED",
        "DEDUCTION_MISSED",
        "SAVINGS_BALANCE",
        "SHARE_CAPITAL",
        "ARRANGEMENT_ACTIVE",
    }


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------
async def test_the_profile_matches_the_core_record(client: AsyncClient, seeded_member: str, db: Any) -> None:
    profile = (await client.get(f"/members/{seeded_member}/profile")).json()
    core = (
        (
            await db.execute(
                text("""
        SELECT m.status, m.branch_id, m.employer_id, m.language, m.identity_verified,
               e.name AS employer_name
        FROM core.member m LEFT JOIN core.employer e ON e.employer_id = m.employer_id
        WHERE m.member_id = :m
    """),
                {"m": seeded_member},
            )
        )
        .mappings()
        .one()
    )

    assert profile["status"] == core["status"]
    assert profile["branch"] == core["branch_id"]
    assert profile["employer_id"] == core["employer_id"]
    assert profile["employer_name"] == core["employer_name"]
    assert profile["language"] == core["language"]
    assert profile["identity_verified"] == core["identity_verified"]


async def test_the_profile_lists_every_account_and_its_exposure(
    client: AsyncClient, seeded_member: str, db: Any
) -> None:
    profile = (await client.get(f"/members/{seeded_member}/profile")).json()
    rows = (
        (
            await db.execute(
                text("""
        SELECT account_id, principal FROM core.account WHERE member_id = :m
    """),
                {"m": seeded_member},
            )
        )
        .mappings()
        .all()
    )

    assert {a["account_id"] for a in profile["accounts"]} == {r["account_id"] for r in rows}
    assert float(profile["total_exposure"]) == pytest.approx(
        sum(float(r["principal"]) for r in rows), abs=0.01
    )


async def test_the_projection_version_is_content_addressed(client: AsyncClient, seeded_member: str) -> None:
    """A snapshot names the projection version a decision was made against."""
    first = (await client.get(f"/members/{seeded_member}/profile?refresh=true")).json()
    second = (await client.get(f"/members/{seeded_member}/profile?refresh=true")).json()
    assert first["version"] == second["version"]
    assert first["version"].startswith("member:")


async def test_the_profile_summarises_payment_history(client: AsyncClient, seeded_member: str) -> None:
    history = (await client.get(f"/members/{seeded_member}/profile")).json()["history"]
    assert history["due_events"] > 0
    if history["ontime_rate_24m"] is not None:
        assert 0.0 <= history["ontime_rate_24m"] <= 1.0
    assert history["arrears_12m"] >= 0


async def test_an_unknown_member_is_not_found(client: AsyncClient) -> None:
    response = await client.get("/members/M-999999/profile")
    assert response.status_code == 404


@pytest.mark.slow
async def test_the_projection_matches_core_for_a_hundred_members(client: AsyncClient, db: Any) -> None:
    """T-025 acceptance: the projection agrees with the core stub."""
    from app.importer import import_members

    rows = (
        (
            await db.execute(
                text("""
        SELECT member_id FROM core.member ORDER BY member_id
    """)
            )
        )
        .scalars()
        .all()
    )
    if len(rows) < 100:
        pytest.skip("no synthetic population loaded")

    sample = random.Random(42).sample(list(rows), 100)
    await import_members(db, sample)

    core = {
        r["member_id"]: r
        for r in (
            await db.execute(
                text("""
        SELECT m.member_id, m.status, m.branch_id, m.employer_id, m.language,
               COALESCE(SUM(a.principal), 0) AS exposure
        FROM core.member m LEFT JOIN core.account a ON a.member_id = m.member_id
        WHERE m.member_id = ANY(:members)
        GROUP BY m.member_id, m.status, m.branch_id, m.employer_id, m.language
    """),
                {"members": sample},
            )
        )
        .mappings()
        .all()
    }

    mismatches: list[str] = []
    for member_id in sample:
        profile = (await client.get(f"/members/{member_id}/profile?refresh=true")).json()
        expected = core[member_id]
        if (
            profile["status"] != expected["status"]
            or profile["branch"] != expected["branch_id"]
            or profile["employer_id"] != expected["employer_id"]
            or abs(float(profile["total_exposure"]) - float(expected["exposure"])) > 0.01
        ):
            mismatches.append(member_id)

    assert not mismatches, f"{len(mismatches)} projections disagree with core"


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------
async def test_the_timeline_returns_events_in_order(client: AsyncClient, seeded_member: str) -> None:
    body = (await client.get(f"/members/{seeded_member}/timeline?limit=50")).json()
    times = [e["occurred_at"] for e in body["events"]]
    assert times == sorted(times)


async def test_the_timeline_pages_without_gaps_or_repeats(client: AsyncClient, seeded_member: str) -> None:
    """T-025 acceptance: the timeline is paginated."""
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(50):
        url = f"/members/{seeded_member}/timeline?limit=25"
        if cursor:
            url += f"&cursor={cursor}"
        body = (await client.get(url)).json()
        seen.extend(e["event_id"] for e in body["events"])
        cursor = body["next_cursor"]
        if not cursor:
            break

    total = (await client.get(f"/members/{seeded_member}/stats")).json()["total"]
    assert len(seen) == total, "paging lost or repeated events"
    assert len(set(seen)) == len(seen), "an event was returned twice"


async def test_the_timeline_filters_by_type(client: AsyncClient, seeded_member: str) -> None:
    body = (await client.get(f"/members/{seeded_member}/timeline?types=PAYMENT_DUE&limit=20")).json()
    assert body["events"]
    assert {e["event_type"] for e in body["events"]} == {"PAYMENT_DUE"}


async def test_the_timeline_filters_by_date(client: AsyncClient, seeded_member: str) -> None:
    body = (await client.get(f"/members/{seeded_member}/timeline?from=2026-01-01T00:00:00Z&limit=50")).json()
    assert all(e["occurred_at"] >= "2026-01-01" for e in body["events"])


async def test_an_unknown_event_type_is_refused(client: AsyncClient, seeded_member: str) -> None:
    response = await client.get(f"/members/{seeded_member}/timeline?types=GOSSIP")
    assert response.status_code == 422
    assert "GOSSIP" in response.json()["error"]["message"]


async def test_a_malformed_cursor_is_refused(client: AsyncClient, seeded_member: str) -> None:
    response = await client.get(f"/members/{seeded_member}/timeline?cursor=nonsense")
    assert response.status_code == 422


async def test_every_event_carries_provenance_and_permitted_uses(
    client: AsyncClient, seeded_member: str
) -> None:
    body = (await client.get(f"/members/{seeded_member}/timeline?limit=30")).json()
    for event in body["events"]:
        assert event["source_system"] and event["source_record_id"]
        assert event["permitted_uses"]
        assert event["data_quality"]["validation"] in ("PASS", "WARN", "FAIL")


async def test_events_validate_against_the_contract(client: AsyncClient, seeded_member: str) -> None:
    import cio_contracts

    body = (await client.get(f"/members/{seeded_member}/timeline?limit=10")).json()
    for event in body["events"]:
        cio_contracts.validate(
            {**event, "schema": "member_event/1.0", "ingested_at": event["occurred_at"], "evidence_refs": []},
            "MemberEvent",
        )


# ---------------------------------------------------------------------------
# derived views the tools read
# ---------------------------------------------------------------------------
async def test_the_history_view_matches_the_tool_contract(client: AsyncClient, seeded_member: str) -> None:
    """docs/06 §4 — what history.get must return."""
    body = (await client.get(f"/members/{seeded_member}/history")).json()
    for field in (
        "ontime_rate_24m",
        "arrears_12m",
        "months_since_last_arrears",
        "restructures_36m",
        "facilities",
    ):
        assert field in body


async def test_the_savings_series_is_chronological(client: AsyncClient, seeded_member: str) -> None:
    body = (await client.get(f"/members/{seeded_member}/savings?months=12")).json()
    dates = [p["as_of"] for p in body["series"]]
    assert dates == sorted(dates)
    assert len(dates) <= 12


async def test_the_shares_series_is_returned(client: AsyncClient, seeded_member: str) -> None:
    body = (await client.get(f"/members/{seeded_member}/shares")).json()
    assert all("units" in p and "value" in p for p in body["series"])
