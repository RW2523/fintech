"""The reads the fraud service needs from the register (docs/07 §3).

A guarantee ring is invisible from one member's own record, and a velocity
check is a question about a workplace rather than a person. The core stub owns
both the guarantees and the members' employers, so it answers rather than
handing the tables over.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.routes import MAX_GUARANTEE_HOPS, MAX_MEMBERS_PER_QUERY, MAX_VELOCITY_WINDOW_DAYS


async def _seed_ring(client: AsyncClient, size: int = 5) -> list[str]:
    """A closed loop of guarantees: each member guarantees the next one's account."""
    members = [f"M-9000{index:02d}" for index in range(size)]
    created = await client.post(
        "/core/admin/bulk",
        json={
            "table": "employer",
            "rows": [
                {
                    "employer_id": "E-900",
                    "name": "Ring Transit",
                    "sector": "TRANSPORT",
                    "template_id": "tpl-01",
                    "deduction_day": 26,
                }
            ],
        },
    )
    assert created.status_code == 200, created.text
    added = await client.post(
        "/core/admin/bulk",
        json={
            "table": "member",
            "rows": [
                {
                    "member_id": m,
                    "name_token": f"Member {i}",
                    "dob": "1990-01-01",
                    "joined_at": "2020-01-01",
                    "status": "ACTIVE",
                    "branch_id": "BR-90",
                    "employer_id": "E-900",
                    "salary_monthly": "3000.00",
                    "identity_verified": True,
                    "contact_updated_at": None,
                    "language": "en",
                }
                for i, m in enumerate(members)
            ],
        },
    )
    assert added.status_code == 200, added.text
    await client.post(
        "/core/admin/bulk",
        json={
            "table": "account",
            "rows": [
                {
                    "account_id": f"A-9000{i:02d}",
                    "member_id": m,
                    "product_code": "PF-STD",
                    "principal": "10000.00",
                    "profit_rate": "0.1200",
                    "tenor_months": 24,
                    "instalment": "500.00",
                    "due_day": 5,
                    "opened_at": "2025-01-01",
                    "status": "ACTIVE",
                    "restructured_at": None,
                }
                for i, m in enumerate(members)
            ],
        },
    )
    await client.post(
        "/core/admin/bulk",
        json={
            "table": "guarantor",
            "rows": [
                {
                    "account_id": f"A-9000{(i + 1) % len(members):02d}",
                    "guarantor_member_id": m,
                    "since": "2025-01-01",
                }
                for i, m in enumerate(members)
            ],
        },
    )
    return members


# ---------------------------------------------------------------------------
# guarantee neighbourhood
# ---------------------------------------------------------------------------
async def test_a_ring_closes_when_the_walk_is_deep_enough(client: AsyncClient) -> None:
    members = await _seed_ring(client, size=5)
    body = (await client.get(f"/core/guarantees/{members[0]}/neighbourhood?hops=4")).json()
    assert set(members) <= set(body["members"])
    assert len(body["edges"]) == len(members)


async def test_one_hop_shows_only_the_immediate_neighbours(client: AsyncClient) -> None:
    members = await _seed_ring(client, size=5)
    body = (await client.get(f"/core/guarantees/{members[0]}/neighbourhood?hops=1")).json()
    assert len(body["members"]) < len(members)


async def test_each_edge_names_both_sides(client: AsyncClient) -> None:
    """`core.guarantor` records a guarantee against an account; what an analyst
    reads is who stands behind whom."""
    members = await _seed_ring(client, size=4)
    body = (await client.get(f"/core/guarantees/{members[0]}/neighbourhood?hops=4")).json()
    for edge in body["edges"]:
        assert edge["guarantor_member_id"] in members
        assert edge["borrower_member_id"] in members
        assert edge["guarantor_member_id"] != edge["borrower_member_id"]


async def test_a_member_with_no_guarantees_has_an_empty_neighbourhood(
    client: AsyncClient, seeded: Any
) -> None:
    body = (await client.get("/core/guarantees/M-999999/neighbourhood")).json()
    assert body["edges"] == []
    assert body["members"] == ["M-999999"]


async def test_the_walk_is_bounded(client: AsyncClient) -> None:
    """A wider walk is a portfolio question and would pull in the population."""
    response = await client.get(f"/core/guarantees/M-000001/neighbourhood?hops={MAX_GUARANTEE_HOPS + 1}")
    assert response.status_code == 422
    assert (await client.get("/core/guarantees/M-000001/neighbourhood?hops=0")).status_code == 422


# ---------------------------------------------------------------------------
# applications
# ---------------------------------------------------------------------------
async def _seed_applications(client: AsyncClient, members: list[str]) -> None:
    await client.post(
        "/core/admin/bulk",
        json={
            "table": "application_ext",
            "rows": [
                {
                    "application_id": f"APP-9{index:04d}",
                    "member_id": member,
                    "product_code": "PF-STD",
                    "amount": "8000.00",
                    "tenor_months": 36,
                    "status": "SUBMITTED",
                    "created_at": f"2026-03-0{index + 1}",
                }
                for index, member in enumerate(members)
            ],
        },
    )


async def test_a_burst_from_one_employer_is_visible(client: AsyncClient) -> None:
    members = await _seed_ring(client, size=4)
    await _seed_applications(client, members)
    body = (
        await client.get(
            "/core/applications/velocity",
            params={"employer_id": "E-900", "date_from": "2026-03-01", "date_to": "2026-03-07"},
        )
    ).json()
    assert body["count"] == len(members)
    assert {a["member_id"] for a in body["applications"]} == set(members)


async def test_velocity_can_be_narrowed_to_a_branch(client: AsyncClient) -> None:
    members = await _seed_ring(client, size=3)
    await _seed_applications(client, members)
    same = (
        await client.get(
            "/core/applications/velocity",
            params={
                "employer_id": "E-900",
                "branch_id": "BR-90",
                "date_from": "2026-03-01",
                "date_to": "2026-03-07",
            },
        )
    ).json()
    other = (
        await client.get(
            "/core/applications/velocity",
            params={
                "employer_id": "E-900",
                "branch_id": "BR-01",
                "date_from": "2026-03-01",
                "date_to": "2026-03-07",
            },
        )
    ).json()
    assert same["count"] == len(members)
    assert other["count"] == 0


async def test_a_backwards_window_is_refused(client: AsyncClient) -> None:
    response = await client.get(
        "/core/applications/velocity",
        params={"employer_id": "E-900", "date_from": "2026-03-07", "date_to": "2026-03-01"},
    )
    assert response.status_code == 422


async def test_a_window_wider_than_the_limit_is_refused(client: AsyncClient) -> None:
    """A velocity rule asks about days. A wider window scans the register."""
    response = await client.get(
        "/core/applications/velocity",
        params={"employer_id": "E-900", "date_from": "2020-01-01", "date_to": "2026-03-01"},
    )
    assert response.status_code == 422
    assert str(MAX_VELOCITY_WINDOW_DAYS) in response.json()["error"]["message"]


async def test_applications_can_be_fetched_for_several_members(client: AsyncClient) -> None:
    members = await _seed_ring(client, size=4)
    await _seed_applications(client, members)
    body = (await client.get("/core/applications/by-members", params={"members": ",".join(members)})).json()
    assert body["count"] == len(members)
    assert {a["member_id"] for a in body["applications"]} == set(members)


async def test_applications_can_be_limited_to_a_recent_window(client: AsyncClient) -> None:
    members = await _seed_ring(client, size=3)
    await _seed_applications(client, members)
    body = (
        await client.get(
            "/core/applications/by-members", params={"members": ",".join(members), "since": "2026-03-03"}
        )
    ).json()
    assert body["count"] < len(members)


async def test_asking_about_nobody_is_refused(client: AsyncClient) -> None:
    assert (await client.get("/core/applications/by-members", params={"members": " , "})).status_code == 422


async def test_asking_about_too_many_members_is_refused(client: AsyncClient) -> None:
    members = ",".join(f"M-{i:06d}" for i in range(MAX_MEMBERS_PER_QUERY + 1))
    response = await client.get("/core/applications/by-members", params={"members": members})
    assert response.status_code == 422
