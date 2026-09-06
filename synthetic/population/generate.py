"""The synthetic population generator (docs/10 §1-§4).

Everything is drawn from one seeded generator in a fixed order, so a run with
the same seed reproduces byte for byte. That matters: the demo, the golden
cases and the model training all rest on the same population.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np

from synthetic.config import (
    ARCHETYPE_MIX,
    BRANCHES,
    GRADE_BY_ARCHETYPE,
    LANGUAGES,
    PRODUCTS,
    SAVINGS_HABIT,
    SECTOR_BASE_SALARY,
    SECTORS,
    Settings,
)
from synthetic.population.behaviour import (
    DRIFT_DEDUCTION_FROM,
    DRIFT_DEDUCTION_MISS_RATE,
    DRIFT_SAVINGS_PAUSE_FROM,
    LATE_THRESHOLD_DAYS,
    MISSED_THRESHOLD_DAYS,
    days_to_pay_series,
)
from synthetic.population.calendar import (
    add_days,
    cycle_label,
    due_date_for,
    month_end,
    month_start,
)
from synthetic.population.names import employer_name, person_name

__all__ = ["Population", "generate"]

_PROFIT_RATE = {"PF-STD": 0.065, "PF-SHARIAH": 0.070}
_TEMPLATES = tuple(f"tpl-{i:02d}" for i in range(1, 13))


def _money(value: float) -> str:
    """Money crosses every boundary as a two-decimal string (CLAUDE.md §7)."""
    return f"{value:.2f}"


def _pick(rng: np.random.Generator, weighted: tuple[tuple[Any, float], ...]) -> Any:
    values = [v for v, _ in weighted]
    weights = np.array([w for _, w in weighted], dtype=float)
    return values[int(rng.choice(len(values), p=weights / weights.sum()))]


def _instalment(principal: float, tenor: int, rate: float) -> float:
    return round(principal * (1 + rate * tenor / 12) / tenor, 2)


@dataclass
class Population:
    """Every table the core stub needs, plus the manifest that describes them."""

    employers: list[dict[str, Any]] = field(default_factory=list)
    members: list[dict[str, Any]] = field(default_factory=list)
    accounts: list[dict[str, Any]] = field(default_factory=list)
    schedules: list[dict[str, Any]] = field(default_factory=list)
    payments: list[dict[str, Any]] = field(default_factory=list)
    deductions: list[dict[str, Any]] = field(default_factory=list)
    savings: list[dict[str, Any]] = field(default_factory=list)
    share_capital: list[dict[str, Any]] = field(default_factory=list)
    guarantors: list[dict[str, Any]] = field(default_factory=list)
    bureau: list[dict[str, Any]] = field(default_factory=list)
    outage_windows: list[dict[str, Any]] = field(default_factory=list)
    arrangements: list[dict[str, Any]] = field(default_factory=list)
    interactions: list[dict[str, Any]] = field(default_factory=list)

    #: member_id -> archetype and the derived facts tests and scenarios need.
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)

    def tables(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "employer": self.employers,
            "member": self.members,
            "account": self.accounts,
            "schedule": self.schedules,
            "payment": self.payments,
            "deduction": self.deductions,
            "savings": self.savings,
            "share_capital": self.share_capital,
            "guarantor": self.guarantors,
            "bureau": self.bureau,
            "outage_window": self.outage_windows,
            "arrangement": self.arrangements,
        }

    def counts(self) -> dict[str, int]:
        return {name: len(rows) for name, rows in self.tables().items()}


# ---------------------------------------------------------------------------
# employers
# ---------------------------------------------------------------------------
def _employers(rng: np.random.Generator, settings: Settings) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # Two employers lose a whole deduction cycle in months 14 and 15 (S9).
    interrupted = set(
        rng.choice(settings.employers, size=settings.employers_with_interruption, replace=False).tolist()
    )

    for index in range(settings.employers):
        sector = SECTORS[index % len(SECTORS)]
        rows.append(
            {
                "employer_id": f"E-{index + 1:03d}",
                "name": employer_name(rng, index + 1),
                "sector": sector,
                "template_id": _TEMPLATES[index % len(_TEMPLATES)],
                "deduction_day": int(rng.integers(25, 29)),
                # not a core column; carried for the generator and dropped on load
                "_size": max(5, int(rng.lognormal(math.log(60), 0.7))),
                "_interrupted": index in interrupted,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# members
# ---------------------------------------------------------------------------
def _members(
    rng: np.random.Generator, settings: Settings, employers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    sizes = np.array([e["_size"] for e in employers], dtype=float)
    weights = sizes / sizes.sum()
    window_end = month_end(settings.history_start, settings.months)

    rows: list[dict[str, Any]] = []
    for index in range(settings.members):
        employer = employers[int(rng.choice(len(employers), p=weights))]

        # joined_at: up to 25 years ago, skewed towards recent (docs/10 §2)
        years_ago = float(rng.beta(2, 3)) * 25
        joined_at = window_end - timedelta(days=int(years_ago * 365.25))
        age_at_join = int(rng.integers(22, 61))
        dob = joined_at - timedelta(days=int(age_at_join * 365.25))

        base = SECTOR_BASE_SALARY[employer["sector"]]
        salary = float(
            np.clip(
                base * rng.lognormal(0.0, settings.salary.sigma), settings.salary.low, settings.salary.high
            )
        )

        archetype = _pick(rng, tuple(ARCHETYPE_MIX.items()))
        grade = _pick(rng, GRADE_BY_ARCHETYPE[archetype])

        rows.append(
            {
                "member_id": f"M-{index + 1:06d}",
                "name_token": person_name(rng),
                "dob": dob.isoformat(),
                "joined_at": joined_at.isoformat(),
                "status": "ACTIVE",
                "branch_id": str(rng.choice(BRANCHES)),
                "employer_id": employer["employer_id"],
                "salary_monthly": _money(salary),
                "identity_verified": bool(rng.random() < settings.identity_verified_rate),
                "contact_updated_at": None,
                "language": _pick(rng, LANGUAGES),
                "_archetype": archetype,
                "_grade": grade,
                "_sector": employer["sector"],
                "_salary": salary,
                "_tenure_years": round(years_ago, 2),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# accounts and their schedules
# ---------------------------------------------------------------------------
def _accounts(
    rng: np.random.Generator, settings: Settings, members: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accounts: list[dict[str, Any]] = []
    schedules: list[dict[str, Any]] = []
    counter = 0

    for member in members:
        how_many = int(_pick(rng, settings.accounts_mix))
        for _ in range(how_many):
            counter += 1
            product = _pick(rng, PRODUCTS)
            rate = _PROFIT_RATE[product]
            principal = round(member["_salary"] * float(rng.uniform(0.5, 8.0)), 2)
            tenor = int(rng.integers(12, 85))
            due_day = int(rng.integers(1, 29))
            opened_month = int(rng.integers(1, settings.months + 1))
            opened_at = month_start(settings.history_start, opened_month)

            account_id = f"A-{counter:06d}"
            instalment = _instalment(principal, tenor, rate)
            accounts.append(
                {
                    "account_id": account_id,
                    "member_id": member["member_id"],
                    "product_code": product,
                    "principal": _money(principal),
                    "profit_rate": f"{rate:.4f}",
                    "tenor_months": tenor,
                    "instalment": _money(instalment),
                    "due_day": due_day,
                    "opened_at": opened_at.isoformat(),
                    "status": "ACTIVE",
                    "restructured_at": None,
                    "_opened_month": opened_month,
                    "_instalment": instalment,
                    "_archetype": member["_archetype"],
                }
            )

            # due events run from the month after opening to the window's end
            for seq, month in enumerate(range(opened_month + 1, settings.months + 1), 1):
                schedules.append(
                    {
                        "schedule_id": f"S-{account_id}-{seq:03d}",
                        "account_id": account_id,
                        "seq": seq,
                        "due_date": due_date_for(settings.history_start, month, due_day).isoformat(),
                        "amount_due": _money(instalment),
                        "_month": month,
                    }
                )

    return accounts, schedules


# ---------------------------------------------------------------------------
# payments
# ---------------------------------------------------------------------------
def _payments(
    rng: np.random.Generator,
    settings: Settings,
    accounts: list[dict[str, Any]],
    schedules: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    by_account: dict[str, list[dict[str, Any]]] = {}
    for row in schedules:
        by_account.setdefault(row["account_id"], []).append(row)

    payments: list[dict[str, Any]] = []
    account_facts: dict[str, dict[str, Any]] = {}
    counter = 0

    for account in accounts:
        rows = by_account.get(account["account_id"], [])
        if not rows:
            account_facts[account["account_id"]] = {
                "first_late_month": None,
                "late30_months": 0,
                "due_events": 0,
            }
            continue

        months = [r["_month"] for r in rows]
        series = days_to_pay_series(account["_archetype"], months, rng)

        late30 = 0
        first_late: int | None = None
        for row, days, missed in zip(rows, series.days_to_pay, series.missed, strict=True):
            due = date.fromisoformat(row["due_date"])
            if days > LATE_THRESHOLD_DAYS and first_late is None:
                first_late = row["_month"]
            if days > MISSED_THRESHOLD_DAYS:
                late30 += 1

            # a missed cycle is only paid if it is recovered inside the window
            if missed and days > 90:
                continue

            counter += 1
            paid_at = datetime.combine(add_days(due, days), datetime.min.time(), UTC)
            partial = not missed and rng.random() < 0.03
            amount = (
                round(account["_instalment"] * float(rng.uniform(0.6, 0.9)), 2)
                if partial
                else account["_instalment"]
            )
            payments.append(
                {
                    "payment_id": f"P-{counter:07d}",
                    "schedule_id": row["schedule_id"],
                    "paid_at": paid_at.isoformat().replace("+00:00", "Z"),
                    "amount_paid": _money(amount),
                    "channel": "DEDUCTION" if not missed else "COUNTER",
                    "reversed": False,
                }
            )

        account_facts[account["account_id"]] = {
            "first_late_month": first_late,
            "late30_months": late30,
            "due_events": len(rows),
            "shock_month": series.shock_month,
            "recovers": series.recovers,
        }

    return payments, account_facts


# ---------------------------------------------------------------------------
# deductions, savings, shares
# ---------------------------------------------------------------------------
def _deductions(
    rng: np.random.Generator,
    settings: Settings,
    members: list[dict[str, Any]],
    employers: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counter = 0

    # An employer-wide outage misses the same cycle for all of its members.
    outage_cycles: dict[str, set[int]] = {}
    for employer in employers.values():
        missed = {
            month for month in range(1, settings.months + 1) if rng.random() < settings.employer_outage_rate
        }
        if employer["_interrupted"]:
            missed |= set(settings.employer_interruption_months)
        outage_cycles[employer["employer_id"]] = missed

    for member in members:
        employer_id = member["employer_id"]
        expected = round(member["_salary"] * 0.25, 2)
        archetype = member["_archetype"]

        for month in range(1, settings.months + 1):
            counter += 1
            employer_missed = month in outage_cycles[employer_id]
            drift_missed = (
                archetype == "SLOW_DRIFT"
                and month >= DRIFT_DEDUCTION_FROM
                and rng.random() < DRIFT_DEDUCTION_MISS_RATE
            )
            received = not (employer_missed or drift_missed)
            when = month_end(settings.history_start, month)

            rows.append(
                {
                    "deduction_id": f"D-{counter:07d}",
                    "member_id": member["member_id"],
                    "employer_id": employer_id,
                    "cycle": cycle_label(settings.history_start, month),
                    "expected_amount": _money(expected),
                    "received_amount": _money(expected) if received else None,
                    "received_at": datetime.combine(when, datetime.min.time(), UTC)
                    .isoformat()
                    .replace("+00:00", "Z")
                    if received
                    else None,
                }
            )
    return rows


def _savings_and_shares(
    rng: np.random.Generator, settings: Settings, members: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    savings: list[dict[str, Any]] = []
    shares: list[dict[str, Any]] = []
    paused_months: dict[str, int] = {}

    for member in members:
        archetype = member["_archetype"]
        salary = member["_salary"]
        balance = salary * float(rng.uniform(0.3, 4.0))
        habit = SAVINGS_HABIT[archetype]
        paused = 0

        # A pause means a member who was saving stopped: that is the signal LMI
        # looks for. A CHRONIC member who never saved, or a SHOCK member who
        # dips into savings, is not the same thing and is not counted here.
        shock_stops_saving = archetype == "SHOCK" and rng.random() < 0.40

        for month in range(1, settings.months + 1):
            drift_pause = archetype == "SLOW_DRIFT" and month >= DRIFT_SAVINGS_PAUSE_FROM
            shock_pause = shock_stops_saving and month >= 12
            withdrawing = archetype in ("SHOCK", "CHRONIC") and rng.random() < 0.25

            if drift_pause or shock_pause:
                paused += 1
            else:
                balance += salary * habit
            if withdrawing:
                balance = max(0.0, balance - salary * float(rng.uniform(0.05, 0.3)))

            savings.append(
                {
                    "member_id": member["member_id"],
                    "as_of": month_end(settings.history_start, month).isoformat(),
                    "balance": _money(balance),
                }
            )

        paused_months[member["member_id"]] = paused

        has_minimum = rng.random() < settings.share_capital_coverage
        units = (
            settings.min_share_units + int(member["_tenure_years"] * float(rng.uniform(1, 6)))
            if has_minimum
            else int(rng.integers(10, settings.min_share_units))
        )
        for quarter in range(1, settings.months + 1, 3):
            shares.append(
                {
                    "member_id": member["member_id"],
                    "as_of": month_end(settings.history_start, quarter).isoformat(),
                    "units": units,
                    "value": _money(units * 10.0),
                }
            )

    return savings, shares, paused_months


# ---------------------------------------------------------------------------
# guarantors, bureau, outages, arrangements
# ---------------------------------------------------------------------------
def _guarantors(
    rng: np.random.Generator,
    settings: Settings,
    members: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_employer: dict[str, list[str]] = {}
    for member in members:
        by_employer.setdefault(member["employer_id"], []).append(member["member_id"])
    member_employer = {m["member_id"]: m["employer_id"] for m in members}

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for account in accounts:
        if rng.random() >= settings.guarantor_share:
            continue
        pool = [m for m in by_employer[member_employer[account["member_id"]]] if m != account["member_id"]]
        if not pool:
            continue
        for _ in range(int(rng.integers(1, 3))):
            guarantor = str(rng.choice(pool))
            key = (account["account_id"], guarantor)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "account_id": account["account_id"],
                    "guarantor_member_id": guarantor,
                    "since": account["opened_at"],
                }
            )
    return rows


def _bureau(
    rng: np.random.Generator, settings: Settings, members: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    as_of = month_end(settings.history_start, settings.months).isoformat()
    return [
        {
            "member_id": member["member_id"],
            "grade": member["_grade"],
            "adverse_flags": ["ARREARS"] if member["_grade"] in ("D", "E") and rng.random() < 0.5 else [],
            "as_of": as_of,
        }
        for member in members
    ]


def _outages(rng: np.random.Generator, settings: Settings) -> list[dict[str, Any]]:
    """Posting outages that delay receipts without anyone paying late (S9)."""
    rows: list[dict[str, Any]] = []
    for index in range(settings.system_outages):
        month = int(rng.integers(16, settings.months + 1))
        start = month_start(settings.history_start, month) + timedelta(days=int(rng.integers(5, 20)))
        rows.append(
            {
                "system": "PAYMENT_POSTING" if index == 0 else "DEDUCTION_FEED",
                "from_ts": datetime.combine(start, datetime.min.time(), UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "to_ts": datetime.combine(
                    start + timedelta(days=int(rng.integers(2, 4))), datetime.min.time(), UTC
                )
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )
    return rows


def _arrangements(
    rng: np.random.Generator,
    settings: Settings,
    accounts: list[dict[str, Any]],
    facts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """CHRONIC members pick up one or two restructures (docs/10 §4)."""
    rows: list[dict[str, Any]] = []
    counter = 0
    for account in accounts:
        if account["_archetype"] != "CHRONIC":
            continue
        earliest = account["_opened_month"] + 1
        if earliest > settings.months:
            continue  # opened too late in the window to have been restructured
        for _ in range(int(rng.integers(1, 3))):
            counter += 1
            month = int(rng.integers(earliest, settings.months + 1))
            start = month_start(settings.history_start, month)
            rows.append(
                {
                    "arrangement_id": f"AR-{counter:06d}",
                    "account_id": account["account_id"],
                    "type": str(rng.choice(["DEFERMENT_1M", "RESCHEDULE_EXTEND_12M"])),
                    "from_date": start.isoformat(),
                    "to_date": (start + timedelta(days=90)).isoformat(),
                }
            )
    return rows


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def generate(settings: Settings | None = None) -> Population:
    """Build the whole population. Deterministic for a given seed."""
    settings = settings or Settings()
    rng = np.random.default_rng(settings.seed)

    employers = _employers(rng, settings)
    members = _members(rng, settings, employers)
    accounts, schedules = _accounts(rng, settings, members)
    payments, account_facts = _payments(rng, settings, accounts, schedules)
    deductions = _deductions(rng, settings, members, {e["employer_id"]: e for e in employers})
    savings, shares, paused = _savings_and_shares(rng, settings, members)
    guarantors = _guarantors(rng, settings, members, accounts)
    bureau = _bureau(rng, settings, members)
    outages = _outages(rng, settings)
    arrangements = _arrangements(rng, settings, accounts, account_facts)

    accounts_by_member: dict[str, list[str]] = {}
    opened_month_by_account: dict[str, int] = {}
    for account in accounts:
        accounts_by_member.setdefault(account["member_id"], []).append(account["account_id"])
        opened_month_by_account[account["account_id"]] = account["_opened_month"]

    profiles = {
        member["member_id"]: {
            "archetype": member["_archetype"],
            "grade": member["_grade"],
            "sector": member["_sector"],
            "tenure_years": member["_tenure_years"],
            "savings_paused_months": paused.get(member["member_id"], 0),
            "accounts": accounts_by_member.get(member["member_id"], []),
            "first_late_month": min(
                (
                    account_facts[a]["first_late_month"]
                    for a in accounts_by_member.get(member["member_id"], [])
                    if account_facts[a]["first_late_month"] is not None
                ),
                default=None,
            ),
            # The S8 pattern needs fourteen clean months before the drift, so
            # the sanity range is measured over members whose history is long
            # enough to show it.
            "earliest_account_month": min(
                (opened_month_by_account[a] for a in accounts_by_member.get(member["member_id"], [])),
                default=None,
            ),
        }
        for member in members
    }

    return Population(
        employers=employers,
        members=members,
        accounts=accounts,
        schedules=schedules,
        payments=payments,
        deductions=deductions,
        savings=savings,
        share_capital=shares,
        guarantors=guarantors,
        bureau=bureau,
        outage_windows=outages,
        arrangements=arrangements,
        profiles=profiles,
    )
