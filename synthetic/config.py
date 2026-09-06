"""The distributions behind the synthetic programme (docs/10).

Everything here is a knob the generator reads. The sanity ranges in §4.6 are the
contract; the constants below are calibrated to land inside them and are checked
by `synthetic/tests/test_sanity.py`.

No real people, no real names: members carry generated tokens and a neutral
currency (CLAUDE.md §2.10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

__all__ = [
    "ARCHETYPES",
    "ARCHETYPE_MIX",
    "BRANCHES",
    "DEMO_AS_OF_MONTH",
    "GRADE_BY_ARCHETYPE",
    "HISTORY_MONTHS",
    "HISTORY_START",
    "LANGUAGES",
    "PRODUCTS",
    "SECTORS",
    "SECTOR_BASE_SALARY",
    "SalaryBand",
    "Settings",
]

# --- the 24-month window --------------------------------------------------
#: Month 1 begins here; month 24 ends the day before HISTORY_START + 24 months.
HISTORY_START = date(2024, 9, 1)
HISTORY_MONTHS = 24
#: docs/10 §8 — the demo runs as if it were the end of month 20, so later
#: months exist for backtests but are hidden from the live views.
DEMO_AS_OF_MONTH = 20

SECTORS = (
    "PUBLIC_ADMIN",
    "EDUCATION",
    "HEALTH",
    "UTILITIES",
    "MANUFACTURING",
    "RETAIL",
    "TRANSPORT",
    "AGRICULTURE",
)

#: Sector -> the middle of its salary band, in LCU per month.
SECTOR_BASE_SALARY = {
    "PUBLIC_ADMIN": 4200,
    "EDUCATION": 3800,
    "HEALTH": 4600,
    "UTILITIES": 5200,
    "MANUFACTURING": 3600,
    "RETAIL": 2600,
    "TRANSPORT": 3100,
    "AGRICULTURE": 2300,
}

BRANCHES = ("B-01", "B-02", "B-03", "B-04", "B-05")
LANGUAGES = (("en", 0.60), ("lang_b", 0.25), ("lang_c", 0.15))
PRODUCTS = (("PF-STD", 0.80), ("PF-SHARIAH", 0.20))

ARCHETYPES = ("STEADY", "SEASONAL", "IMPROVING", "SLOW_DRIFT", "SHOCK", "CHRONIC")
ARCHETYPE_MIX = {
    "STEADY": 0.55,
    "SEASONAL": 0.12,
    "IMPROVING": 0.08,
    "SLOW_DRIFT": 0.12,
    "SHOCK": 0.08,
    "CHRONIC": 0.05,
}

#: docs/10 §2 — grade follows the archetype, with noise.
GRADE_BY_ARCHETYPE = {
    "STEADY": (("A", 0.55), ("B", 0.40), ("C", 0.05)),
    "SEASONAL": (("B", 0.75), ("A", 0.15), ("C", 0.10)),
    "IMPROVING": (("B", 0.50), ("C", 0.45), ("D", 0.05)),
    "SLOW_DRIFT": (("B", 0.50), ("C", 0.45), ("D", 0.05)),
    "SHOCK": (("B", 0.55), ("C", 0.30), ("D", 0.15)),
    "CHRONIC": (("D", 0.60), ("E", 0.40)),
}

#: Monthly savings deposit, as a share of salary, by archetype.
SAVINGS_HABIT = {
    "STEADY": 0.05,
    "SEASONAL": 0.05,
    "IMPROVING": 0.02,
    "SLOW_DRIFT": 0.05,
    "SHOCK": 0.02,
    "CHRONIC": 0.0,
}


@dataclass(frozen=True, slots=True)
class SalaryBand:
    low: int = 1800
    high: int = 18000
    sigma: float = 0.35


@dataclass(frozen=True, slots=True)
class Settings:
    """One generation run."""

    seed: int = 42
    members: int = 5000
    months: int = HISTORY_MONTHS
    employers: int = 120
    history_start: date = HISTORY_START
    salary: SalaryBand = field(default_factory=SalaryBand)

    #: docs/10 §1 — chance an employer misses a whole deduction cycle.
    employer_outage_rate: float = 0.01
    #: Two employers lose months 14 and 15 entirely, for scenario S9.
    employer_interruption_months: tuple[int, int] = (14, 15)
    employers_with_interruption: int = 2

    #: docs/10 §3 — accounts per member.
    accounts_mix: tuple[tuple[int, float], ...] = ((1, 0.60), (2, 0.30), (3, 0.10))
    guarantor_share: float = 0.30
    min_share_units: int = 100
    share_capital_coverage: float = 0.85
    identity_verified_rate: float = 0.97

    #: Two posting outages of two to three days, for scenario S9.
    system_outages: int = 2
