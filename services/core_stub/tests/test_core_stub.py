"""T-007 — the core-stub façade, its change feed and its guarded writes."""

from __future__ import annotations

from httpx import AsyncClient


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
async def test_health_answers(client: AsyncClient) -> None:
    response = await client.get("/core/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_responses_carry_a_trace_header(client: AsyncClient) -> None:
    """docs/08 preamble — every response includes X-Trace-Id."""
    assert "X-Trace-Id" in (await client.get("/core/health")).headers


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------
async def test_member_is_returned_with_money_as_a_string(seeded: AsyncClient) -> None:
    body = (await seeded.get("/core/members/M-000042")).json()
    assert body["member_id"] == "M-000042"
    assert body["salary_monthly"] == "4200.00", "money must serialise as a decimal string"
    assert body["identity_verified"] is True


async def test_a_missing_member_is_a_documented_not_found(client: AsyncClient) -> None:
    response = await client.get("/core/members/M-999999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_accounts_schedule_and_payments_join_up(seeded: AsyncClient) -> None:
    accounts = (await seeded.get("/core/members/M-000042/accounts")).json()
    assert [a["account_id"] for a in accounts] == ["A-1001"]
    assert accounts[0]["instalment"] == "1130.00"

    schedule = (await seeded.get("/core/accounts/A-1001/schedule")).json()
    assert [row["seq"] for row in schedule] == [1, 2]

    payments = (await seeded.get("/core/accounts/A-1001/payments")).json()
    assert [p["payment_id"] for p in payments] == ["P-1"]
    assert payments[0]["reversed"] is False


async def test_deductions_savings_shares_and_bureau_are_exposed(seeded: AsyncClient) -> None:
    assert (await seeded.get("/core/members/M-000042/deductions")).json()[0]["cycle"] == "2025-02"
    assert (await seeded.get("/core/members/M-000042/savings")).json()[0]["balance"] == "8400.00"
    assert (await seeded.get("/core/members/M-000042/shares")).json()[0]["units"] == 150
    assert (await seeded.get("/core/bureau/M-000042")).json()["grade"] == "B"


async def test_employer_lookup(seeded: AsyncClient) -> None:
    body = (await seeded.get("/core/employers/E-001")).json()
    assert body["sector"] == "UTILITIES"
    assert body["deduction_day"] == 26


async def test_arrangements_require_a_selector(client: AsyncClient) -> None:
    response = await client.get("/core/arrangements")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION"


# ---------------------------------------------------------------------------
# change feed
# ---------------------------------------------------------------------------
async def test_the_change_feed_records_inserts(seeded: AsyncClient) -> None:
    """member-intelligence imports from here (docs/08 §9)."""
    changes = (await seeded.get("/core/changes?since=0")).json()
    tables = {c["table_name"] for c in changes}
    assert {"member", "account", "payment"} <= tables
    assert all(c["op"] in ("INSERT", "UPDATE") for c in changes)


async def test_the_change_feed_is_a_resumable_cursor(seeded: AsyncClient) -> None:
    first = (await seeded.get("/core/changes?since=0")).json()
    cursor = first[0]["seq"]
    rest = (await seeded.get(f"/core/changes?since={cursor}")).json()
    assert all(c["seq"] > cursor for c in rest)
    assert len(rest) == len(first) - 1


# ---------------------------------------------------------------------------
# writes — the guarantees execution-service depends on
# ---------------------------------------------------------------------------
ACTIVATE = {
    "member_id": "M-000042",
    "product_code": "PF-STD",
    "amount": "18000.00",
    "tenor_months": 24,
    "instalment": "825.00",
    "profit_rate": "0.0650",
    "due_day": 10,
    "idempotency_key": "idem-activate-0001",
}


async def test_a_write_without_an_approval_token_is_refused(seeded: AsyncClient) -> None:
    """CLAUDE.md §2.6 — nothing reaches the core without a token."""
    response = await seeded.post("/core/write/activate", json=ACTIVATE)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_activation_creates_the_account_and_its_schedule(seeded: AsyncClient) -> None:
    response = await seeded.post(
        "/core/write/activate", json=ACTIVATE, headers={"X-Approval-Token": "tok_demo"}
    )
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["replayed"] is False
    assert body["core_refs"]["schedule_rows"] == 24

    schedule = (await seeded.get(f"/core/accounts/{body['account_id']}/schedule")).json()
    assert len(schedule) == 24
    assert schedule[0]["amount_due"] == "825.00"


async def test_a_replayed_activation_is_a_no_op(seeded: AsyncClient) -> None:
    """docs/13 §4 — duplicate execute must not activate twice."""
    headers = {"X-Approval-Token": "tok_demo"}
    first = (await seeded.post("/core/write/activate", json=ACTIVATE, headers=headers)).json()
    second = (await seeded.post("/core/write/activate", json=ACTIVATE, headers=headers)).json()

    assert second["replayed"] is True
    assert second["account_id"] == first["account_id"]

    accounts = (await seeded.get("/core/members/M-000042/accounts")).json()
    new_accounts = [a for a in accounts if a["account_id"] == first["account_id"]]
    assert len(new_accounts) == 1, "the second call must not create a second account"


async def test_activation_for_an_unknown_member_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/core/write/activate",
        json={**ACTIVATE, "member_id": "M-999999"},
        headers={"X-Approval-Token": "tok_demo"},
    )
    assert response.status_code == 404


async def test_status_change_requires_a_token_and_is_idempotent(seeded: AsyncClient) -> None:
    headers = {"X-Approval-Token": "tok_demo"}
    body = {"account_id": "A-1001", "status": "CLOSED", "idempotency_key": "idem-status-0001"}

    assert (await seeded.post("/core/write/status", json=body)).status_code == 403

    first = (await seeded.post("/core/write/status", json=body, headers=headers)).json()
    second = (await seeded.post("/core/write/status", json=body, headers=headers)).json()
    assert first["status"] == "CLOSED"
    assert second["replayed"] is True

    accounts = (await seeded.get("/core/members/M-000042/accounts")).json()
    assert accounts[0]["status"] == "CLOSED"


async def test_every_write_is_logged_with_its_token(seeded: AsyncClient) -> None:
    from sqlalchemy import text

    from app.db import session

    await seeded.post("/core/write/activate", json=ACTIVATE, headers={"X-Approval-Token": "tok_audit_me"})
    async with session() as db:
        row = (
            (
                await db.execute(
                    text("SELECT action, approval_token FROM core.write_log ORDER BY id DESC LIMIT 1")
                )
            )
            .mappings()
            .one()
        )
    assert row["action"] == "activate_financing"
    assert row["approval_token"] == "tok_audit_me"


async def test_a_rejected_write_body_never_reaches_the_database(seeded: AsyncClient) -> None:
    response = await seeded.post(
        "/core/write/activate", json={**ACTIVATE, "amount": "-5.00"}, headers={"X-Approval-Token": "tok_demo"}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# seeding
# ---------------------------------------------------------------------------
async def test_bulk_insert_accepts_allow_listed_tables(client: AsyncClient) -> None:
    response = await client.post(
        "/core/admin/bulk",
        json={
            "table": "employer",
            "rows": [
                {
                    "employer_id": "E-900",
                    "name": "Seed Co",
                    "sector": "RETAIL",
                    "template_id": "t1",
                    "deduction_day": 25,
                },
                {
                    "employer_id": "E-901",
                    "name": "Seed Two",
                    "sector": "HEALTH",
                    "template_id": "t2",
                    "deduction_day": 26,
                },
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["inserted"] == 2
    assert (await client.get("/core/employers/E-900")).json()["name"] == "Seed Co"


async def test_bulk_insert_refuses_tables_outside_the_allow_list(client: AsyncClient) -> None:
    """The table name is interpolated into SQL, so the allow-list is the guard."""
    response = await client.post(
        "/core/admin/bulk", json={"table": "write_log; DROP TABLE core.member", "rows": [{"a": 1}]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION"


async def test_bulk_insert_requires_uniform_rows(client: AsyncClient) -> None:
    response = await client.post(
        "/core/admin/bulk",
        json={
            "table": "employer",
            "rows": [{"employer_id": "E-1", "name": "A", "sector": "RETAIL"}, {"employer_id": "E-2"}],
        },
    )
    assert response.status_code == 422


async def test_reset_empties_the_schema(seeded: AsyncClient) -> None:
    assert (await seeded.post("/core/admin/reset")).json()["status"] == "reset"
    assert (await seeded.get("/core/members/M-000042")).status_code == 404
