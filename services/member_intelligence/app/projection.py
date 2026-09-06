"""The member profile projection (docs/04 §3, docs/07 §4.1).

A projection is a summary of the timeline, versioned by its content. Agents read
this rather than the raw events, so the version hash is what tells a snapshot
which projection a decision was made against.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cio_common.hashing import canonical_json, sha256

__all__ = ["build_profile", "load_profile", "save_profile"]

_LATE_DAYS = 7


def _months_between(earlier: datetime, later: datetime) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


async def build_profile(db: AsyncSession, member_id: str, *, as_of: datetime | None = None) -> dict[str, Any]:
    """Summarise a member from the core record and their timeline."""
    as_of = as_of or datetime.now(UTC)

    member = (
        (
            await db.execute(
                text("""
        SELECT m.member_id, m.name_token, m.dob, m.joined_at, m.status, m.branch_id,
               m.employer_id, m.salary_monthly, m.identity_verified, m.language,
               e.name AS employer_name, e.sector AS employer_sector
        FROM core.member m LEFT JOIN core.employer e ON e.employer_id = m.employer_id
        WHERE m.member_id = :member_id
    """),
                {"member_id": member_id},
            )
        )
        .mappings()
        .first()
    )
    if member is None:
        return {}

    accounts = (
        (
            await db.execute(
                text("""
        SELECT account_id, product_code, principal, instalment, status, opened_at
        FROM core.account WHERE member_id = :member_id ORDER BY opened_at
    """),
                {"member_id": member_id},
            )
        )
        .mappings()
        .all()
    )

    timing = (
        (
            await db.execute(
                text("""
        SELECT (payload->>'days_late')::int AS days_late, occurred_at
        FROM app_member.member_event
        WHERE member_id = :member_id
          AND event_type IN ('PAYMENT_RECEIVED', 'PAYMENT_LATE', 'PAYMENT_PARTIAL')
        ORDER BY occurred_at
    """),
                {"member_id": member_id},
            )
        )
        .mappings()
        .all()
    )

    deductions = (
        (
            await db.execute(
                text("""
        SELECT event_type, occurred_at FROM app_member.member_event
        WHERE member_id = :member_id
          AND event_type IN ('DEDUCTION_RECEIVED', 'DEDUCTION_MISSED')
        ORDER BY occurred_at
    """),
                {"member_id": member_id},
            )
        )
        .mappings()
        .all()
    )

    savings = (
        (
            await db.execute(
                text("""
        SELECT (payload->>'balance')::numeric AS balance, occurred_at
        FROM app_member.member_event
        WHERE member_id = :member_id AND event_type = 'SAVINGS_BALANCE'
        ORDER BY occurred_at DESC LIMIT 12
    """),
                {"member_id": member_id},
            )
        )
        .mappings()
        .all()
    )

    shares = (
        (
            await db.execute(
                text("""
        SELECT (payload->>'units')::int AS units FROM app_member.member_event
        WHERE member_id = :member_id AND event_type = 'SHARE_CAPITAL'
        ORDER BY occurred_at DESC LIMIT 1
    """),
                {"member_id": member_id},
            )
        )
        .mappings()
        .first()
    )

    lateness = [row["days_late"] for row in timing if row["days_late"] is not None]
    recent = [
        row for row in timing if row["occurred_at"] and _months_between(row["occurred_at"], as_of) <= 24
    ]
    recent_lateness = [r["days_late"] for r in recent if r["days_late"] is not None]
    arrears = [
        r
        for r in timing
        if (r["days_late"] or 0) > _LATE_DAYS and _months_between(r["occurred_at"], as_of) <= 12
    ]

    last_arrears = max((r["occurred_at"] for r in timing if (r["days_late"] or 0) > _LATE_DAYS), default=None)

    joined = member["joined_at"]
    joined_at = datetime.combine(joined, datetime.min.time(), UTC) if joined else as_of

    body: dict[str, Any] = {
        "member_id": member["member_id"],
        "name_token": member["name_token"],
        "status": member["status"],
        "branch": member["branch_id"],
        "employer_id": member["employer_id"],
        "employer_name": member["employer_name"],
        "employer_sector": member["employer_sector"],
        "language": member["language"],
        "identity_verified": member["identity_verified"],
        "joined_at": joined.isoformat() if joined else None,
        "tenure_months": _months_between(joined_at, as_of),
        "salary_monthly": (
            f"{member['salary_monthly']:.2f}" if member["salary_monthly"] is not None else None
        ),
        "accounts": [
            {
                "account_id": a["account_id"],
                "product_code": a["product_code"],
                "principal": f"{a['principal']:.2f}",
                "instalment": f"{a['instalment']:.2f}",
                "status": a["status"],
                "opened_at": a["opened_at"].isoformat(),
            }
            for a in accounts
        ],
        "total_exposure": f"{sum(float(a['principal']) for a in accounts):.2f}",
        "history": {
            "due_events": len(timing),
            "ontime_rate_24m": round(sum(1 for d in recent_lateness if d <= 0) / len(recent_lateness), 4)
            if recent_lateness
            else None,
            "days_to_pay_median": (round(statistics.median(lateness), 2) if lateness else None),
            "arrears_12m": len(arrears),
            "months_since_last_arrears": (_months_between(last_arrears, as_of) if last_arrears else None),
        },
        "deductions": {
            "cycles": len(deductions),
            "missed": sum(1 for d in deductions if d["event_type"] == "DEDUCTION_MISSED"),
        },
        "savings": {
            "latest_balance": (f"{savings[0]['balance']:.2f}" if savings else None),
            "points": len(savings),
        },
        "share_capital_units": shares["units"] if shares else None,
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
    }
    # The version covers everything but the timestamp, so an unchanged member
    # keeps the same version across runs.
    body["version"] = "member:" + sha256(canonical_json({k: v for k, v in body.items() if k != "as_of"}))[:16]
    return body


async def save_profile(db: AsyncSession, profile: dict[str, Any]) -> None:
    """Store the projection. Bind a datetime, not its ISO rendering: asyncpg
    requires one once the column type is known."""
    import json

    as_of = datetime.fromisoformat(str(profile["as_of"]).replace("Z", "+00:00"))

    await db.execute(
        text("""
        INSERT INTO app_member.profile_projection (member_id, version, body, as_of)
        VALUES (:member_id, :version, CAST(:body AS jsonb), :as_of)
        ON CONFLICT (member_id) DO UPDATE SET
          version = EXCLUDED.version, body = EXCLUDED.body,
          as_of = EXCLUDED.as_of, updated_at = now()
    """),
        {
            "member_id": profile["member_id"],
            "version": profile["version"],
            "body": json.dumps(profile),
            "as_of": as_of,
        },
    )


async def load_profile(db: AsyncSession, member_id: str) -> dict[str, Any] | None:
    import json

    row = (
        await db.execute(
            text("""
        SELECT body FROM app_member.profile_projection WHERE member_id = :member_id
    """),
            {"member_id": member_id},
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return json.loads(row) if isinstance(row, str) else row
