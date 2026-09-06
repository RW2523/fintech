"""The core-stub REST façade (docs/08 §9).

Reads are open to the platform. Writes go through `/core/write/*`, require an
`X-Approval-Token` header, and are idempotent on the caller's key so a retried
saga step never activates a facility twice (docs/13 §4).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session
from app.models import (
    Account,
    ActivateRequest,
    Arrangement,
    BulkRequest,
    Bureau,
    Change,
    Deduction,
    Employer,
    Guarantor,
    Member,
    OutageWindow,
    Payment,
    SavingsPoint,
    ScheduleRow,
    SharePoint,
    StatusRequest,
    WriteResult,
)
from app.settings import settings
from cio_common.errors import Forbidden, NotFound, ValidationFailed
from cio_common.ids import new_id

router = APIRouter(prefix="/core", tags=["core"])

# Tables the seed loader may write to. Anything else is refused.
SEEDABLE = {
    "employer",
    "member",
    "account",
    "schedule",
    "payment",
    "deduction",
    "savings",
    "share_capital",
    "guarantor",
    "bureau",
    "outage_window",
    "arrangement",
    "outcome",
    "application_ext",
}


def cols(model: type[BaseModel], prefix: str = "") -> str:
    """The exact column list a response model needs.

    `SELECT *` would leak any column added later into a strict response model,
    so every query names its fields. The names come from the model definition,
    never from a request, and each is checked to be a bare identifier before it
    is interpolated.
    """
    names = list(model.model_fields)
    bad = [n for n in names if not n.isidentifier()]
    if bad:
        raise ValueError(f"{model.__name__} has non-identifier field names: {bad}")
    return ", ".join(f"{prefix}{name}" for name in names)


async def _rows(db: AsyncSession, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in (await db.execute(text(sql), params)).mappings().all()]


async def _one(db: AsyncSession, sql: str, missing: str, **params: Any) -> dict[str, Any]:
    rows = await _rows(db, sql, **params)
    if not rows:
        raise NotFound(missing)
    return rows[0]


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
@router.get("/health", summary="Liveness of the core stub")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "core_stub", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------
@router.get("/members/{member_id}", response_model=Member)
async def get_member(member_id: str) -> Any:
    async with session() as db:
        return await _one(
            db,
            f"SELECT {cols(Member)} FROM core.member WHERE member_id = :m",
            f"member {member_id} not found",
            m=member_id,
        )


@router.get("/members/{member_id}/accounts", response_model=list[Account])
async def get_accounts(member_id: str) -> Any:
    async with session() as db:
        return await _rows(
            db,
            f"SELECT {cols(Account)} FROM core.account WHERE member_id = :m ORDER BY opened_at",
            m=member_id,
        )


@router.get("/accounts/{account_id}/schedule", response_model=list[ScheduleRow])
async def get_schedule(account_id: str) -> Any:
    async with session() as db:
        return await _rows(
            db,
            f"SELECT {cols(ScheduleRow)} FROM core.schedule WHERE account_id = :a ORDER BY seq",
            a=account_id,
        )


@router.get("/accounts/{account_id}/payments", response_model=list[Payment])
async def get_payments(account_id: str) -> Any:
    async with session() as db:
        return await _rows(
            db,
            f"""
            SELECT {cols(Payment, "p.")} FROM core.payment p
            JOIN core.schedule s ON s.schedule_id = p.schedule_id
            WHERE s.account_id = :a ORDER BY p.paid_at
        """,
            a=account_id,
        )


@router.get("/members/{member_id}/deductions", response_model=list[Deduction])
async def get_deductions(member_id: str) -> Any:
    async with session() as db:
        return await _rows(
            db,
            f"SELECT {cols(Deduction)} FROM core.deduction WHERE member_id = :m ORDER BY cycle",
            m=member_id,
        )


@router.get("/members/{member_id}/savings", response_model=list[SavingsPoint])
async def get_savings(member_id: str) -> Any:
    async with session() as db:
        return await _rows(
            db,
            f"SELECT {cols(SavingsPoint)} FROM core.savings WHERE member_id = :m ORDER BY as_of",
            m=member_id,
        )


@router.get("/members/{member_id}/shares", response_model=list[SharePoint])
async def get_shares(member_id: str) -> Any:
    async with session() as db:
        return await _rows(
            db,
            f"SELECT {cols(SharePoint)} FROM core.share_capital WHERE member_id = :m ORDER BY as_of",
            m=member_id,
        )


@router.get("/members/{member_id}/guarantors", response_model=list[Guarantor])
async def get_guarantors(member_id: str) -> Any:
    """Accounts this member guarantees for others."""
    async with session() as db:
        return await _rows(
            db,
            f"SELECT {cols(Guarantor)} FROM core.guarantor WHERE guarantor_member_id = :m ORDER BY since",
            m=member_id,
        )


@router.get("/bureau/{member_id}", response_model=Bureau)
async def get_bureau(member_id: str) -> Any:
    async with session() as db:
        return await _one(
            db,
            f"SELECT {cols(Bureau)} FROM core.bureau WHERE member_id = :m",
            f"no bureau record for {member_id}",
            m=member_id,
        )


@router.get("/employers/{employer_id}", response_model=Employer)
async def get_employer(employer_id: str) -> Any:
    async with session() as db:
        return await _one(
            db,
            f"SELECT {cols(Employer)} FROM core.employer WHERE employer_id = :e",
            f"employer {employer_id} not found",
            e=employer_id,
        )


@router.get("/outages", response_model=list[OutageWindow])
async def get_outages(system: str | None = None) -> Any:
    async with session() as db:
        if system:
            return await _rows(
                db,
                f"SELECT {cols(OutageWindow)} FROM core.outage_window WHERE system = :s ORDER BY from_ts",
                s=system,
            )
        return await _rows(db, f"SELECT {cols(OutageWindow)} FROM core.outage_window ORDER BY from_ts")


@router.get("/arrangements", response_model=list[Arrangement])
async def get_arrangements(member_id: str | None = None, account_id: str | None = None) -> Any:
    async with session() as db:
        if account_id:
            return await _rows(
                db,
                f"SELECT {cols(Arrangement)} FROM core.arrangement WHERE account_id = :a ORDER BY from_date",
                a=account_id,
            )
        if member_id:
            return await _rows(
                db,
                f"""
                SELECT {cols(Arrangement, "ar.")} FROM core.arrangement ar
                JOIN core.account a ON a.account_id = ar.account_id
                WHERE a.member_id = :m ORDER BY ar.from_date
            """,
                m=member_id,
            )
        raise ValidationFailed("member_id or account_id is required")


@router.get("/changes", response_model=list[Change], summary="Change feed for CDC import")
async def get_changes(
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=10_000),
) -> Any:
    async with session() as db:
        return await _rows(
            db,
            f"""
            SELECT {cols(Change)} FROM core.change_feed
            WHERE seq > :since ORDER BY seq LIMIT :limit
        """,
            since=since,
            limit=limit,
        )


# ---------------------------------------------------------------------------
# writes — token required (CLAUDE.md §2.6)
# ---------------------------------------------------------------------------
def _require_token(token: str | None) -> str:
    """The stub records the token; execution-service is what validates it."""
    if not token:
        raise Forbidden("core writes require an X-Approval-Token header")
    return token


async def _replay(db: AsyncSession, key: str) -> WriteResult | None:
    """The stored result of an earlier call with the same idempotency key."""
    row = (
        await db.execute(
            text("SELECT result FROM core.write_log WHERE idempotency_key = :k"),
            {"k": key},
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return WriteResult.model_validate(json.loads(row) if isinstance(row, str) else row)


@router.post("/write/activate", response_model=WriteResult, summary="Activate a financing")
async def activate(
    body: ActivateRequest,
    x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
) -> Any:
    token = _require_token(x_approval_token)

    async with session() as db:
        if previous := await _replay(db, body.idempotency_key):
            return previous.model_copy(update={"replayed": True})

        exists = (
            await db.execute(
                text("SELECT 1 FROM core.member WHERE member_id = :m"),
                {"m": body.member_id},
            )
        ).first()
        if not exists:
            raise NotFound(f"member {body.member_id} not found")

        account_id = new_id("app").replace("app_", "A-")
        opened = date.today()
        await db.execute(
            text("""
            INSERT INTO core.account
              (account_id, member_id, product_code, principal, profit_rate, tenor_months,
               instalment, due_day, opened_at, status)
            VALUES (:account_id, :member_id, :product_code, :principal, :profit_rate,
                    :tenor_months, :instalment, :due_day, :opened_at, 'ACTIVE')
        """),
            {
                "account_id": account_id,
                "member_id": body.member_id,
                "product_code": body.product_code,
                "principal": body.amount,
                "profit_rate": body.profit_rate,
                "tenor_months": body.tenor_months,
                "instalment": body.instalment,
                "due_day": body.due_day,
                "opened_at": opened,
            },
        )

        for seq in range(1, body.tenor_months + 1):
            month = opened.month - 1 + seq
            due = date(opened.year + month // 12, month % 12 + 1, body.due_day)
            await db.execute(
                text("""
                INSERT INTO core.schedule (schedule_id, account_id, seq, due_date, amount_due)
                VALUES (:sid, :aid, :seq, :due, :amount)
            """),
                {
                    "sid": f"S-{account_id}-{seq:03d}",
                    "aid": account_id,
                    "seq": seq,
                    "due": due,
                    "amount": body.instalment,
                },
            )

        result = WriteResult(
            action="activate_financing",
            account_id=account_id,
            status="ACTIVE",
            core_refs={"account_id": account_id, "schedule_rows": body.tenor_months},
        )
        await db.execute(
            text("""
            INSERT INTO core.write_log (action, payload, approval_token, idempotency_key, result)
            VALUES ('activate_financing', CAST(:payload AS jsonb), :token, :key,
                    CAST(:result AS jsonb))
        """),
            {
                "payload": body.model_dump_json(),
                "token": token,
                "key": body.idempotency_key,
                "result": result.model_dump_json(),
            },
        )

        return result


@router.post("/write/status", response_model=WriteResult, summary="Change an account status")
async def set_status(
    body: StatusRequest,
    x_approval_token: str | None = Header(default=None, alias="X-Approval-Token"),
) -> Any:
    token = _require_token(x_approval_token)

    async with session() as db:
        if previous := await _replay(db, body.idempotency_key):
            return previous.model_copy(update={"replayed": True})

        updated = (
            await db.execute(
                text("UPDATE core.account SET status = :s WHERE account_id = :a RETURNING account_id"),
                {"s": body.status, "a": body.account_id},
            )
        ).first()
        if updated is None:
            raise NotFound(f"account {body.account_id} not found")

        result = WriteResult(
            action="set_status",
            account_id=body.account_id,
            status=body.status,
            core_refs={"account_id": body.account_id},
        )
        await db.execute(
            text("""
            INSERT INTO core.write_log (action, payload, approval_token, idempotency_key, result)
            VALUES ('set_status', CAST(:payload AS jsonb), :token, :key, CAST(:result AS jsonb))
        """),
            {
                "payload": body.model_dump_json(),
                "token": token,
                "key": body.idempotency_key,
                "result": result.model_dump_json(),
            },
        )

        return result


# ---------------------------------------------------------------------------
# seeding — synthetic data only (CLAUDE.md §2.10)
# ---------------------------------------------------------------------------
def _refuse_in_production() -> None:
    if settings().cio_env in ("pilot", "prod"):
        raise Forbidden("seed and reset endpoints are disabled outside development")


async def _column_types(db: AsyncSession, table: str) -> dict[str, str]:
    """Actual SQL type per column, so bound values are cast rather than guessed.

    asyncpg will not coerce an ISO date string into a `date` column on its own,
    and the seed loader sends JSON, so every parameter is cast explicitly.
    """
    rows = await db.execute(
        text("""
        SELECT a.attname AS column_name,
               format_type(a.atttypid, a.atttypmod) AS sql_type
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'core' AND c.relname = :table
          AND a.attnum > 0 AND NOT a.attisdropped
    """),
        {"table": table},
    )
    return {r["column_name"]: r["sql_type"] for r in rows.mappings()}


@router.post("/admin/bulk", summary="Insert synthetic rows (seeding only)")
async def bulk(body: BulkRequest) -> dict[str, Any]:
    _refuse_in_production()
    if body.table not in SEEDABLE:
        raise ValidationFailed(f"table {body.table!r} is not seedable", seedable=sorted(SEEDABLE))
    if not body.rows:
        return {"table": body.table, "inserted": 0}

    columns = list(body.rows[0])
    if any(set(row) != set(columns) for row in body.rows):
        raise ValidationFailed("every row must carry the same columns")

    async with session() as db:
        types = await _column_types(db, body.table)
        unknown = [c for c in columns if c not in types]
        if unknown:
            raise ValidationFailed(f"core.{body.table} has no column {unknown[0]!r}", columns=sorted(types))

        # Scalars are bound as text and converted by PostgreSQL: asyncpg wants a
        # real date or Decimal once it knows the target type, and the seed loader
        # only has JSON. Arrays are bound directly, which asyncpg handles.
        arrays = {c for c in columns if types[c].endswith("[]")}
        placeholders = ", ".join(
            f"CAST(:{c} AS {types[c]})" if c in arrays else f"CAST(CAST(:{c} AS text) AS {types[c]})"
            for c in columns
        )
        statement = text(
            f"INSERT INTO core.{body.table} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        )
        for row in body.rows:
            bound = {
                key: value if (key in arrays or value is None) else str(value) for key, value in row.items()
            }
            await db.execute(statement, bound)

    return {"table": body.table, "inserted": len(body.rows)}


@router.post("/admin/reset", summary="Empty the core schema (seeding only)")
async def reset() -> dict[str, str]:
    _refuse_in_production()
    async with session() as db:
        await db.execute(
            text("""
            TRUNCATE core.write_log, core.change_feed, core.outcome, core.arrangement,
                     core.outage_window, core.bureau, core.guarantor, core.share_capital,
                     core.savings, core.deduction, core.payment, core.schedule,
                     core.account, core.application_ext, core.member, core.employer
            RESTART IDENTITY CASCADE
        """)
        )
    return {"status": "reset"}
