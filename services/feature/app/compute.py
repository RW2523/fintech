"""Computing an origination feature snapshot (docs/07 §2.1).

Features come from the member timeline and the core record, always as of a
stated moment. The same inputs must produce the same values: a model run cites
a snapshot, and a snapshot that drifted would make the decision unreconstructable.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.registry import FEATURES, REGISTRY_VERSION, PermittedUse
from cio_common.hashing import canonical_json, sha256

__all__ = ["SEVERITY_ORDINAL", "ComputedSnapshot", "compute_features"]

#: findings_max_severity as a number a model can use.
SEVERITY_ORDINAL = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

_LATE_DAYS = 7
#: Grade exposure limits, mirroring policy_packs PF-STD (docs/05 §2).
_GRADE_LIMIT = {"A": 200000.0, "B": 150000.0, "C": 100000.0, "D": 60000.0, "E": 30000.0}
_MIN_SHARE_UNITS = 100


@dataclass
class ComputedSnapshot:
    member_id: str
    account_id: str | None
    as_of: datetime
    values: dict[str, float | None] = field(default_factory=dict)
    text_values: dict[str, str | None] = field(default_factory=dict)
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def inputs_digest(self) -> str:
        """Covers the values, so an identical snapshot is recognisable as one."""
        return sha256(
            canonical_json(
                {
                    "member_id": self.member_id,
                    "as_of": self.as_of.isoformat(),
                    "values": self.values,
                    "text_values": self.text_values,
                }
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {**self.values, **self.text_values}


def _months_between(earlier: datetime, later: datetime) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _slope(points: list[tuple[float, float]]) -> float | None:
    """Ordinary least squares slope, or None when there is nothing to fit."""
    if len(points) < 3:
        return None
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator


async def compute_features(
    db: AsyncSession,
    member_id: str,
    *,
    as_of: datetime | None = None,
    account_id: str | None = None,
    proposed_instalment: float | None = None,
    purpose: PermittedUse = PermittedUse.UNDERWRITING,
) -> ComputedSnapshot:
    """Every declared feature this purpose may see, as of ``as_of``."""
    as_of = as_of or datetime.now(UTC)
    snapshot = ComputedSnapshot(member_id=member_id, account_id=account_id, as_of=as_of)

    member = (
        (
            await db.execute(
                text("""
        SELECT m.member_id, m.joined_at, m.contact_updated_at, m.salary_monthly,
               e.sector AS employer_sector, b.grade
        FROM core.member m
        LEFT JOIN core.employer e ON e.employer_id = m.employer_id
        LEFT JOIN core.bureau b ON b.member_id = m.member_id
        WHERE m.member_id = :member_id
    """),
                {"member_id": member_id},
            )
        )
        .mappings()
        .first()
    )
    if member is None:
        raise KeyError(f"no member {member_id!r}")

    accounts = (
        (
            await db.execute(
                text("""
        SELECT account_id, principal, instalment, opened_at, status
        FROM core.account WHERE member_id = :member_id AND opened_at <= :as_of
    """),
                {"member_id": member_id, "as_of": as_of.date()},
            )
        )
        .mappings()
        .all()
    )

    payments = (
        (
            await db.execute(
                text("""
        SELECT occurred_at, (payload->>'days_late')::int AS days_late
        FROM app_member.member_event
        WHERE member_id = :member_id AND occurred_at <= :as_of
          AND event_type IN ('PAYMENT_RECEIVED', 'PAYMENT_LATE', 'PAYMENT_PARTIAL')
        ORDER BY occurred_at
    """),
                {"member_id": member_id, "as_of": as_of},
            )
        )
        .mappings()
        .all()
    )

    savings = (
        (
            await db.execute(
                text("""
        SELECT occurred_at, (payload->>'balance')::numeric AS balance
        FROM app_member.member_event
        WHERE member_id = :member_id AND event_type = 'SAVINGS_BALANCE'
          AND occurred_at <= :as_of
        ORDER BY occurred_at
    """),
                {"member_id": member_id, "as_of": as_of},
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
          AND occurred_at <= :as_of
        ORDER BY occurred_at DESC LIMIT 1
    """),
                {"member_id": member_id, "as_of": as_of},
            )
        )
        .mappings()
        .first()
    )

    deductions = (
        (
            await db.execute(
                text("""
        SELECT occurred_at, event_type,
               (payload->>'net_salary')::numeric AS net_salary
        FROM app_member.member_event
        WHERE member_id = :member_id AND occurred_at <= :as_of
          AND event_type IN ('DEDUCTION_RECEIVED', 'DEDUCTION_MISSED')
        ORDER BY occurred_at
    """),
                {"member_id": member_id, "as_of": as_of},
            )
        )
        .mappings()
        .all()
    )

    arrangements = (
        (
            await db.execute(
                text("""
        SELECT ar.from_date FROM core.arrangement ar
        JOIN core.account a ON a.account_id = ar.account_id
        WHERE a.member_id = :member_id AND ar.from_date <= :as_of
    """),
                {"member_id": member_id, "as_of": as_of.date()},
            )
        )
        .mappings()
        .all()
    )

    def record(name: str, value: float | None, source: str, **detail: Any) -> None:
        snapshot.values[name] = value
        snapshot.provenance[name] = {"source": source, **detail}

    def record_text(name: str, value: str | None, source: str) -> None:
        snapshot.text_values[name] = value
        snapshot.provenance[name] = {"source": source}

    # --- conduct ------------------------------------------------------------
    recent = [p for p in payments if p["occurred_at"] >= as_of - timedelta(days=730)]
    on_time = [p for p in recent if (p["days_late"] or 0) <= 0]
    record(
        "ontime_rate_24m",
        round(len(on_time) / len(recent), 4) if recent else None,
        "app_member.member_event",
        due_events=len(recent),
    )

    year = [p for p in payments if p["occurred_at"] >= as_of - timedelta(days=365)]
    arrears = [p for p in year if (p["days_late"] or 0) > _LATE_DAYS]
    record("arrears_events_12m", float(len(arrears)), "app_member.member_event")

    last_arrears = max(
        (p["occurred_at"] for p in payments if (p["days_late"] or 0) > _LATE_DAYS), default=None
    )
    record(
        "months_since_last_arrears",
        float(_months_between(last_arrears, as_of)) if last_arrears else None,
        "app_member.member_event",
    )

    three_years = [a for a in arrangements if a["from_date"] >= (as_of - timedelta(days=1095)).date()]
    record("restructures_36m", float(len(three_years)), "core.arrangement")

    open_accounts = [a for a in accounts if a["status"] == "ACTIVE"]
    record("facilities_open", float(len(open_accounts)), "core.account")
    record(
        "facilities_new_6m",
        float(sum(1 for a in accounts if a["opened_at"] >= (as_of - timedelta(days=183)).date())),
        "core.account",
    )

    exposure = sum(float(a["principal"]) for a in open_accounts)
    limit = _GRADE_LIMIT.get(str(member["grade"] or ""))
    record(
        "utilisation",
        round(exposure / limit, 4) if limit else None,
        "core.account",
        exposure=round(exposure, 2),
        limit=limit,
    )

    # --- commitment ---------------------------------------------------------
    joined = member["joined_at"]
    joined_at = datetime.combine(joined, datetime.min.time(), UTC) if joined else as_of
    record("tenure_months", float(_months_between(joined_at, as_of)), "core.member")

    record(
        "savings_balance",
        round(float(savings[-1]["balance"]), 2) if savings else None,
        "app_member.member_event",
        points=len(savings),
    )

    window = [s for s in savings if s["occurred_at"] >= as_of - timedelta(days=180)]
    slope = _slope([((s["occurred_at"] - as_of).days, float(s["balance"])) for s in window])
    record(
        "savings_slope_180d",
        round(slope, 4) if slope is not None else None,
        "app_member.member_event",
        points=len(window),
    )

    paused = 0
    for earlier, later in itertools.pairwise(savings):
        if float(later["balance"]) <= float(earlier["balance"]):
            paused += 1
    record("savings_paused_months", float(paused), "app_member.member_event")

    units = shares["units"] if shares else None
    record("share_capital_units", float(units) if units is not None else None, "app_member.member_event")
    record(
        "share_capital_ratio",
        round(units / _MIN_SHARE_UNITS, 4) if units is not None else None,
        "app_member.member_event",
        minimum=_MIN_SHARE_UNITS,
    )

    # --- capacity -----------------------------------------------------------
    reported = [float(d["net_salary"]) for d in deductions if d["net_salary"] is not None][-3:]
    income = round(statistics.median(reported), 2) if reported else None
    record("income_verified_monthly", income, "core.deduction", cycles=len(reported))

    variance = None
    if len(reported) >= 2:
        largest = max(reported)
        variance = round((largest - min(reported)) / largest, 4) if largest else 0.0
    record("income_source_variance", variance, "core.deduction")

    commitments = round(sum(float(a["instalment"]) for a in open_accounts), 2)
    record("commitments_monthly", commitments, "core.account")

    dsr = None
    if income and income > 0:
        dsr = round((commitments + (proposed_instalment or 0.0)) / income, 4)
    record("dsr_proposed", dsr, "policy.affordability", proposed_instalment=proposed_instalment)

    # --- conditions ---------------------------------------------------------
    record_text("employer_sector", member["employer_sector"], "core.employer")
    received = [d for d in deductions if d["event_type"] == "DEDUCTION_RECEIVED"]
    record("employer_tenure_months", float(len(received)), "app_member.member_event")

    # --- integrity ----------------------------------------------------------
    applications = (
        await db.execute(
            text("""
        SELECT count(*) FROM core.application_ext
        WHERE member_id = :member_id AND created_at >= :since AND created_at <= :as_of
    """),
            {"member_id": member_id, "since": as_of - timedelta(days=365), "as_of": as_of},
        )
    ).scalar_one()
    record("application_count_12m", float(applications), "core.application_ext")

    changed = member["contact_updated_at"]
    record("contact_change_days", float((as_of - changed).days) if changed else None, "core.member")

    documents = (
        (
            await db.execute(
                text("""
        SELECT min(e.conf) AS min_conf
        FROM app_document.extraction e
        JOIN app_document.document d ON d.document_id = e.document_id
        WHERE d.member_id = :member_id AND e.superseded_by IS NULL
          AND e.value IS NOT NULL
    """),
                {"member_id": member_id},
            )
        )
        .mappings()
        .first()
    )
    record(
        "doc_min_conf",
        round(float(documents["min_conf"]), 4) if documents and documents["min_conf"] is not None else None,
        "app_document.extraction",
    )

    findings = (
        (
            await db.execute(
                text("""
        SELECT severity FROM app_document.finding f
        JOIN app_document.document d ON d.document_id = f.document_id
        WHERE d.member_id = :member_id AND f.status = 'OPEN'
    """),
                {"member_id": member_id},
            )
        )
        .scalars()
        .all()
    )
    worst = max((SEVERITY_ORDINAL.get(str(s), 0) for s in findings), default=0)
    record("findings_max_severity", float(worst), "app_document.finding", open_findings=len(findings))

    # --- purpose filter -----------------------------------------------------
    allowed = {d.name for d in FEATURES if d.permits(purpose)}
    snapshot.values = {k: v for k, v in snapshot.values.items() if k in allowed}
    snapshot.text_values = {k: v for k, v in snapshot.text_values.items() if k in allowed}
    snapshot.provenance = {k: v for k, v in snapshot.provenance.items() if k in allowed}
    snapshot.provenance["_registry"] = {"version": REGISTRY_VERSION, "purpose": str(purpose)}
    return snapshot
