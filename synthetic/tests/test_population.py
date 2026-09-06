"""T-020 — the synthetic population is reproducible and realistic (docs/10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synthetic.config import ARCHETYPE_MIX, DEMO_AS_OF_MONTH, Settings
from synthetic.population.behaviour import LATE_THRESHOLD_DAYS, days_to_pay_series
from synthetic.population.calendar import cycle_label, due_date_for, month_end, month_start
from synthetic.population.generate import Population, generate
from synthetic.stats import RANGES, summarise
from synthetic.writer import write_population

SMALL = Settings(members=400, months=24, employers=40, seed=42)


@pytest.fixture(scope="module")
def population() -> Population:
    return generate(SMALL)


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------
def test_the_same_seed_produces_identical_files(tmp_path: Path) -> None:
    """T-020 acceptance: a re-run with the same seed is byte-identical."""
    first = write_population(generate(SMALL), tmp_path / "a", seed=SMALL.seed, months=SMALL.months)
    second = write_population(generate(SMALL), tmp_path / "b", seed=SMALL.seed, months=SMALL.months)

    assert first.digest == second.digest
    for name in first.tables:
        a = (tmp_path / "a" / f"{name}.jsonl").read_bytes()
        b = (tmp_path / "b" / f"{name}.jsonl").read_bytes()
        assert a == b, f"{name}.jsonl differs between runs"


def test_a_different_seed_produces_a_different_population(tmp_path: Path) -> None:
    first = write_population(generate(SMALL), tmp_path / "a", seed=SMALL.seed, months=SMALL.months)
    other = Settings(members=400, months=24, employers=40, seed=43)
    second = write_population(generate(other), tmp_path / "b", seed=other.seed, months=other.months)
    assert first.digest != second.digest


def test_the_manifest_records_every_table(tmp_path: Path) -> None:
    manifest = write_population(generate(SMALL), tmp_path, seed=SMALL.seed, months=SMALL.months)
    body = json.loads((tmp_path / "manifest.json").read_text())
    assert body["seed"] == 42
    assert body["members"] == 400
    assert set(manifest.tables) >= {
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
        "profile",
    }


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------
def test_the_population_has_every_table(population: Population) -> None:
    counts = population.counts()
    assert counts["member"] == 400
    assert counts["employer"] == 40
    for table in (
        "account",
        "schedule",
        "payment",
        "deduction",
        "savings",
        "share_capital",
        "guarantor",
        "bureau",
        "outage_window",
    ):
        assert counts[table] > 0, f"{table} is empty"


def test_the_archetype_mix_matches_the_specification(population: Population) -> None:
    from collections import Counter

    mix = Counter(p["archetype"] for p in population.profiles.values())
    for archetype, target in ARCHETYPE_MIX.items():
        share = mix[archetype] / len(population.profiles)
        assert abs(share - target) < 0.06, f"{archetype} at {share:.2%}, target {target:.0%}"


def test_money_is_always_a_two_decimal_string(population: Population) -> None:
    """CLAUDE.md §7 — no floats cross a boundary."""
    checks = [
        ("member", "salary_monthly"),
        ("account", "principal"),
        ("account", "instalment"),
        ("schedule", "amount_due"),
        ("payment", "amount_paid"),
        ("deduction", "expected_amount"),
        ("savings", "balance"),
        ("share_capital", "value"),
    ]
    for table, column in checks:
        for row in population.tables()[table][:200]:
            value = row[column]
            if value is None:
                continue
            assert isinstance(value, str), f"{table}.{column} is not a string"
            assert value.count(".") == 1 and len(value.split(".")[1]) == 2, (
                f"{table}.{column} = {value!r} is not two-decimal"
            )


def test_ids_are_stable_and_unique(population: Population) -> None:
    for table, key in (
        ("member", "member_id"),
        ("account", "account_id"),
        ("schedule", "schedule_id"),
        ("payment", "payment_id"),
        ("employer", "employer_id"),
    ):
        ids = [row[key] for row in population.tables()[table]]
        assert len(ids) == len(set(ids)), f"{table} has duplicate {key}"


def test_every_account_belongs_to_a_member(population: Population) -> None:
    members = {m["member_id"] for m in population.members}
    assert all(a["member_id"] in members for a in population.accounts)


def test_every_payment_belongs_to_a_schedule(population: Population) -> None:
    schedules = {s["schedule_id"] for s in population.schedules}
    assert all(p["schedule_id"] in schedules for p in population.payments)


def test_a_guarantor_never_guarantees_their_own_account(population: Population) -> None:
    owner = {a["account_id"]: a["member_id"] for a in population.accounts}
    for row in population.guarantors:
        assert row["guarantor_member_id"] != owner[row["account_id"]]


def test_the_instalment_follows_the_product_formula(population: Population) -> None:
    for account in population.accounts[:200]:
        principal = float(account["principal"])
        rate = float(account["profit_rate"])
        tenor = account["tenor_months"]
        expected = round(principal * (1 + rate * tenor / 12) / tenor, 2)
        assert abs(float(account["instalment"]) - expected) < 0.01


def test_two_employers_lose_a_whole_deduction_cycle(population: Population) -> None:
    """docs/10 §1 — the employer interruption scenario S9 depends on."""
    interrupted = [e for e in population.employers if e["_interrupted"]]
    assert len(interrupted) == 2

    ids = {e["employer_id"] for e in interrupted}
    cycles = {cycle_label(SMALL.history_start, m) for m in (14, 15)}
    for row in population.deductions:
        if row["employer_id"] in ids and row["cycle"] in cycles:
            assert row["received_amount"] is None


def test_posting_outages_exist_for_the_recovery_scenario(population: Population) -> None:
    assert len(population.outage_windows) == 2
    for row in population.outage_windows:
        assert row["from_ts"] < row["to_ts"]


# ---------------------------------------------------------------------------
# the sanity ranges
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_the_full_population_lands_inside_every_sanity_range(tmp_path: Path) -> None:
    """T-020 acceptance: docs/10 §4.6 is the contract for the population."""
    settings = Settings(members=5000, months=24, seed=42)
    write_population(generate(settings), tmp_path, seed=settings.seed, months=settings.months)

    summary = summarise(tmp_path)
    failures = {name: check for name, check in summary["checks"].items() if not check["ok"]}
    assert not failures, f"outside the documented ranges: {failures}"


@pytest.mark.slow
@pytest.mark.parametrize("seed", [7, 2026])
def test_other_seeds_also_land_inside_the_ranges(tmp_path: Path, seed: int) -> None:
    """The ranges must hold for any seed, not just the demo one."""
    settings = Settings(members=5000, months=24, seed=seed)
    write_population(generate(settings), tmp_path, seed=settings.seed, months=settings.months)
    summary = summarise(tmp_path)
    assert summary["within_ranges"], summary["checks"]


def test_every_documented_range_is_measured() -> None:
    assert set(RANGES) == {
        "delinquency_30d_rate",
        "steady_late30_rate",
        "chronic_late30_rate",
        "slow_drift_first_late_in_window",
        "deduction_missed_rate",
        "savings_paused_share",
    }


# ---------------------------------------------------------------------------
# archetype dynamics
# ---------------------------------------------------------------------------
def test_a_drifting_member_is_clean_before_the_drift() -> None:
    """Scenario S8 rests on fourteen genuinely clean months."""
    import numpy as np

    rng = np.random.default_rng(11)
    clean = 0
    for _ in range(300):
        series = days_to_pay_series("SLOW_DRIFT", list(range(1, 25)), rng)
        if all(d <= LATE_THRESHOLD_DAYS for d in series.days_to_pay[:14]):
            clean += 1
    assert clean / 300 > 0.95


def test_a_seasonal_member_slips_only_in_festive_months() -> None:
    import numpy as np

    rng = np.random.default_rng(11)
    festive: list[float] = []
    ordinary: list[float] = []
    for _ in range(200):
        series = days_to_pay_series("SEASONAL", list(range(1, 25)), rng)
        for month, days in zip(range(1, 25), series.days_to_pay, strict=True):
            (festive if month in (11, 12, 23, 24) else ordinary).append(days)
    assert sum(festive) / len(festive) > sum(ordinary) / len(ordinary) + 2


def test_an_improving_member_gets_better_over_time() -> None:
    import numpy as np

    rng = np.random.default_rng(11)
    early: list[float] = []
    late: list[float] = []
    for _ in range(200):
        series = days_to_pay_series("IMPROVING", list(range(1, 25)), rng)
        early.extend(series.days_to_pay[:4])
        late.extend(series.days_to_pay[-4:])
    assert sum(early) / len(early) > sum(late) / len(late) + 3


# ---------------------------------------------------------------------------
# the calendar
# ---------------------------------------------------------------------------
def test_the_demo_as_of_month_leaves_future_months_for_backtests() -> None:
    """docs/10 §8 — today is month 20 of 24."""
    assert DEMO_AS_OF_MONTH == 20
    assert month_end(SMALL.history_start, DEMO_AS_OF_MONTH) < month_end(SMALL.history_start, SMALL.months)


def test_a_due_day_past_the_month_end_is_clamped() -> None:
    february = due_date_for(SMALL.history_start, 6, 31)
    assert february.day == 28
    assert month_start(SMALL.history_start, 6).month == february.month
