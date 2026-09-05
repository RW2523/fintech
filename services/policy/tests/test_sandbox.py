"""T-013 — Policy Sandbox replay (docs/05 §7, demo scenario S6).

The Board changes a weight and sees what it would have done to the last twelve
months before adopting it. Replay must be deterministic and call no model.
"""

from __future__ import annotations

import os
import socket
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.factors import FactorScore
from app.packs import PolicyPack
from app.sandbox import ReplayCase, apply_candidate, replay, summarise
from tests.conftest import clean_inputs

ROOT = Path(__file__).resolve().parents[3]
CALC = "calc_01JQZK7M8N9P0Q1R2S3T4V5W6X"
GRADES = ["A", "B", "C", "D", "E"]
BRANCHES = ["B-01", "B-02", "B-03"]
SECTORS = ["PUBLIC_ADMIN", "EDUCATION", "RETAIL"]


def factor(family: str, score: int) -> FactorScore:
    return FactorScore(
        family=family,
        score=score,
        calc_id=CALC,
        tool="test",
        inputs_digest="0" * 64,
        level="LOW" if family == "INTEGRITY" else None,
    )


def make_case(index: int) -> ReplayCase:
    """A spread of cases so a weight change moves some but not all of them."""
    grade = GRADES[index % 5]
    amount = Decimal(str(4000 + (index % 12) * 1500))
    inputs = clean_inputs(
        member_grade=grade,
        requested_amount=amount,
        income_verified_monthly=Decimal(str(3000 + (index % 7) * 900)),
        commitments_monthly=Decimal(str(200 + (index % 5) * 250)),
        member_tenure_months=24 + (index % 9) * 18,
    )
    scores = {
        "CAPACITY": 60 + (index * 7) % 40,
        "CONDUCT": 50 + (index * 11) % 45,
        "COMMITMENT": 40 + (index * 13) % 55,
        "CONDITIONS": 80 + (index * 3) % 20,
        "INTEGRITY": 90 + (index * 5) % 11,
    }
    return ReplayCase(
        snapshot_id=f"snap_{index:026d}".replace("_0", "_A", 1)
        if False
        else f"snap_01JQZK7M8N9P0Q1R2S3T{index:05d}",
        case_id=f"case_01JQZK7M8N9P0Q1R2S3T{index:05d}",
        product_code="PF-STD",
        inputs=inputs,
        factor_scores=tuple(factor(f, s) for f, s in scores.items()),
        opinions=(),
        baseline={},
        segment={
            "grade": grade,
            "branch": BRANCHES[index % 3],
            "employer_sector": SECTORS[index % 3],
            "amount_band": "small" if amount <= 10000 else "large",
        },
        pd_12m=0.01 + (index % 9) * 0.004,
    )


# ---------------------------------------------------------------------------
# candidate application
# ---------------------------------------------------------------------------
def test_a_candidate_patch_does_not_touch_the_live_pack(std_pack: PolicyPack) -> None:
    before = std_pack.dff["weights"]["COMMITMENT"]
    candidate = apply_candidate(std_pack, {"dff": {"weights": {"COMMITMENT": 0.30, "CONDUCT": 0.20}}})
    assert candidate.dff["weights"]["COMMITMENT"] == 0.30
    assert std_pack.dff["weights"]["COMMITMENT"] == before, "the live pack must not change"


def test_a_patch_merges_rather_than_replaces(std_pack: PolicyPack) -> None:
    candidate = apply_candidate(std_pack, {"dff": {"thresholds": {"approve": 65}}})
    assert candidate.dff["thresholds"]["approve"] == 65
    assert candidate.dff["thresholds"]["decline"] == 45, "untouched keys survive"
    assert candidate.dff["weights"] == std_pack.dff["weights"]


def test_an_unknown_top_level_key_is_ignored(std_pack: PolicyPack) -> None:
    candidate = apply_candidate(std_pack, {"wizardry": {"x": 1}})
    assert candidate.dff == std_pack.dff


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------
def test_replaying_with_no_change_moves_nothing(std_pack: PolicyPack) -> None:
    cases = [make_case(i) for i in range(30)]
    report = replay(std_pack, cases, {})
    assert report.diffs == []
    assert report.baseline == report.candidate


def test_the_s6_weight_change_moves_cases(std_pack: PolicyPack) -> None:
    """Demo scenario S6: COMMITMENT 0.20 -> 0.30, CONDUCT 0.30 -> 0.20."""
    cases = [make_case(i) for i in range(50)]
    report = replay(std_pack, cases, {"dff": {"weights": {"COMMITMENT": 0.30, "CONDUCT": 0.20}}})

    assert report.cases_replayed == 50
    assert report.diffs, "a weight change of this size must move some cases"
    for diff in report.diffs:
        assert (
            diff["before"]["weighted_score"] != diff["after"]["weighted_score"]
            or diff["before"]["recommendation"] != diff["after"]["recommendation"]
        )


def test_the_report_compares_baseline_with_candidate(std_pack: PolicyPack) -> None:
    cases = [make_case(i) for i in range(40)]
    report = replay(std_pack, cases, {"dff": {"thresholds": {"approve": 60}}})

    for side in (report.baseline, report.candidate):
        assert side["cases"] == 40
        for key in (
            "approval_rate",
            "decline_rate",
            "review_rate",
            "autonomous_share",
            "approved_exposure",
            "projected_delinquency_12m",
        ):
            assert key in side

    assert report.candidate["approval_rate"] > report.baseline["approval_rate"], (
        "a lower approve threshold must approve more"
    )


def test_lowering_the_threshold_raises_approved_exposure(std_pack: PolicyPack) -> None:
    cases = [make_case(i) for i in range(40)]
    report = replay(std_pack, cases, {"dff": {"thresholds": {"approve": 55}}})
    assert float(report.candidate["approved_exposure"]) >= float(report.baseline["approved_exposure"])


def test_projected_delinquency_follows_approved_exposure(std_pack: PolicyPack) -> None:
    """Sum of pd_12m over approved amounts (docs/05 §7)."""
    cases = [make_case(i) for i in range(40)]
    report = replay(std_pack, cases, {"dff": {"thresholds": {"approve": 55}}})
    assert report.candidate["projected_delinquency_12m"] >= report.baseline["projected_delinquency_12m"]


def test_segments_are_reported_on_both_sides(std_pack: PolicyPack) -> None:
    cases = [make_case(i) for i in range(45)]
    report = replay(std_pack, cases, {"dff": {"weights": {"COMMITMENT": 0.30, "CONDUCT": 0.20}}})
    for side in ("baseline", "candidate"):
        assert set(report.segments[side]) == {"grade", "branch", "employer_sector", "amount_band"}
        assert set(report.segments[side]["grade"]) <= set(GRADES)
        assert set(report.segments[side]["branch"]) <= set(BRANCHES)


def test_a_diff_names_the_decisive_family_on_both_sides(std_pack: PolicyPack) -> None:
    cases = [make_case(i) for i in range(50)]
    report = replay(std_pack, cases, {"dff": {"weights": {"COMMITMENT": 0.40, "CONDUCT": 0.10}}})
    assert report.diffs
    for diff in report.diffs:
        assert "decisive" in diff["before"]
        assert "decisive" in diff["after"]
        assert isinstance(diff["decisive_change"], bool)


def test_replay_is_deterministic(std_pack: PolicyPack) -> None:
    cases = [make_case(i) for i in range(30)]
    candidate = {"dff": {"weights": {"COMMITMENT": 0.30, "CONDUCT": 0.20}}}
    first = replay(std_pack, cases, candidate)
    second = replay(std_pack, cases, candidate)
    assert first.baseline == second.baseline
    assert first.candidate == second.candidate
    assert [d["snapshot_id"] for d in first.diffs] == [d["snapshot_id"] for d in second.diffs]


def test_fifty_cases_replay_well_inside_the_budget(std_pack: PolicyPack) -> None:
    """T-013 acceptance: 50 snapshots in under 10 seconds."""
    cases = [make_case(i) for i in range(50)]
    started = time.perf_counter()
    report = replay(std_pack, cases, {"dff": {"weights": {"COMMITMENT": 0.30, "CONDUCT": 0.20}}})
    elapsed = time.perf_counter() - started
    assert report.cases_replayed == 50
    assert elapsed < 10.0, f"replay took {elapsed:.2f}s"


def test_summarise_of_no_cases_is_empty_not_an_error() -> None:
    assert summarise([])["cases"] == 0


def test_diffs_are_capped(std_pack: PolicyPack) -> None:
    cases = [make_case(i) for i in range(60)]
    report = replay(std_pack, cases, {"dff": {"weights": {"COMMITMENT": 0.40, "CONDUCT": 0.10}}}, max_diffs=5)
    assert len(report.diffs) <= 5
    assert report.cases_replayed == 60


# ---------------------------------------------------------------------------
# through the API, against the database
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _database_url() -> str:
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
def _postgres_is_up() -> bool:
    try:
        with socket.create_connection(("localhost", 5432), 2):
            return True
    except OSError:
        return False


@pytest_asyncio.fixture
async def seeded_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    if not _postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up` to include sandbox API tests")
    monkeypatch.setenv("DATABASE_URL", _database_url())

    from app.db import dispose, engine, session, sessions
    from app.repository import save_replay_case
    from app.settings import settings

    settings.cache_clear()
    engine.cache_clear()
    sessions.cache_clear()

    async with session() as db:
        await db.execute(text("TRUNCATE app_policy.replay_case, app_policy.sandbox_run"))
        for i in range(50):
            case = make_case(i)
            await save_replay_case(
                db,
                snapshot_id=case.snapshot_id,
                case_id=case.case_id,
                product_code="PF-STD",
                policy_version="policy/PF-STD/2026.09.1",
                inputs={
                    "member_status": case.inputs.member_status,
                    "member_tenure_months": case.inputs.member_tenure_months,
                    "member_age": case.inputs.member_age,
                    "member_total_exposure": str(case.inputs.member_total_exposure),
                    "member_grade": case.inputs.member_grade,
                    "requested_amount": str(case.inputs.requested_amount),
                    "requested_tenor": case.inputs.requested_tenor,
                    "requested_purpose": case.inputs.requested_purpose,
                    "documents_present": list(case.inputs.documents_present),
                    "document_confidence": case.inputs.document_confidence,
                    "documents_min_critical_confidence": case.inputs.documents_min_critical_confidence,
                    "income_verified_monthly": str(case.inputs.income_verified_monthly),
                    "commitments_monthly": str(case.inputs.commitments_monthly),
                },
                factor_scores=[f.as_contract() for f in case.factor_scores],
                baseline={},
                segment=case.segment,
                pd_12m=case.pd_12m,
                decided_at=datetime.now(UTC) - timedelta(days=i),
            )

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://policy") as http:
        yield http
    await dispose()


async def test_the_api_replays_fifty_stored_cases(seeded_client: AsyncClient) -> None:
    started = time.perf_counter()
    response = await seeded_client.post(
        "/policy/sandbox/replay",
        json={
            "product_code": "PF-STD",
            "candidate": {"dff": {"weights": {"COMMITMENT": 0.30, "CONDUCT": 0.20}}},
        },
    )
    elapsed = time.perf_counter() - started

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cases_replayed"] == 50
    assert body["sandbox_id"].startswith("sbx_")
    assert body["baseline"]["cases"] == 50
    assert elapsed < 10.0, f"API replay took {elapsed:.2f}s"


async def test_the_run_is_recorded_for_audit(seeded_client: AsyncClient) -> None:
    from app.db import session

    await seeded_client.post(
        "/policy/sandbox/replay",
        json={"product_code": "PF-STD", "candidate": {"dff": {"thresholds": {"approve": 60}}}},
    )

    async with session() as db:
        count = (await db.execute(text("SELECT count(*) FROM app_policy.sandbox_run"))).scalar_one()
    assert count >= 1


async def test_an_empty_range_is_reported_rather_than_returning_nothing(seeded_client: AsyncClient) -> None:
    response = await seeded_client.post(
        "/policy/sandbox/replay",
        json={"product_code": "PF-STD", "range": {"snapshot_ids": ["snap_01JQZK7M8N9P0Q1R2S3T99999"]}},
    )
    assert response.status_code == 404
