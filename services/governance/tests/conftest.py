"""Fixtures for governance-service."""

from __future__ import annotations

import os
import socket
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ROOT = Path(__file__).resolve().parents[3]

RECORD_ID = "dr_01ARZ3NDEKTSV4RRFFQ69G5FAV"
SNAPSHOT_ID = "snap_01ARZ3NDEKTSV4RRFFQ69G5FAW"
MODEL_RUN_ID = "mr_01ARZ3NDEKTSV4RRFFQ69G5FAX"
EVIDENCE_ID = "ev_01ARZ3NDEKTSV4RRFFQ69G5FAY"
CALC_ID = "calc_01ARZ3NDEKTSV4RRFFQ69G5FAZ"
HUMAN_ID = "hd_01ARZ3NDEKTSV4RRFFQ69G5FB0"
FINDING_ID = "fnd_01ARZ3NDEKTSV4RRFFQ69G5FB1"


@lru_cache(maxsize=1)
def database_url() -> str:
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
def postgres_is_up() -> bool:
    try:
        with socket.create_connection(("localhost", 5432), 2):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def _database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if not postgres_is_up():
        pytest.skip("PostgreSQL not reachable; run `make up`")
    monkeypatch.setenv("DATABASE_URL", database_url())


@pytest.fixture
def decision() -> dict[str, Any]:
    """A DecisionRecord as the decision service stores one."""
    return {
        "decision_record_id": RECORD_ID,
        "snapshot_id": SNAPSHOT_ID,
        "case_id": "case_1",
        "recommendation": "APPROVE_WITH_CONDITIONS",
        "route": "OFFICER_REVIEW",
        "weighted_score": 61.4,
        "hard_gates": [
            {
                "rule_id": "ELG-02",
                "result": "PASS",
                "reason_code": "ELG-02",
                "clause_id": "PF-STD/eligibility/tenure",
                "evidence_refs": [EVIDENCE_ID],
            },
            {
                "rule_id": "CAP-02",
                "result": "FAIL",
                "reason_code": "CAP-02",
                "clause_id": "PF-STD/affordability/dsr",
                "evidence_refs": [],
            },
        ],
        "factor_scores": {
            "CAPACITY": {
                "score": 48,
                "weight": 0.35,
                "weighted": 16.8,
                "decisive": True,
                "calc_id": CALC_ID,
                "evidence_refs": [EVIDENCE_ID],
            },
            "CONDUCT": {"score": 82, "weight": 0.30, "weighted": 24.6, "decisive": False, "calc_id": CALC_ID},
            "INTEGRITY": {
                "score": 90,
                "weight": 0.05,
                "weighted": 4.5,
                "decisive": False,
                "calc_id": CALC_ID,
                "level": "LOW",
            },
        },
        "would_change_outcome": [
            {"condition": "instalment reduced by 12%", "new_recommendation": "APPROVE"},
        ],
        "model_versions": {"risk": "2026.09.1", "model_run_id": MODEL_RUN_ID},
    }


@pytest.fixture
def model_run() -> dict[str, Any]:
    return {
        "model_run_id": MODEL_RUN_ID,
        "drivers": [
            {
                "feature": "savings_slope_180d",
                "direction": "ADVERSE",
                "contribution": 1.42,
                "share": 0.71,
                "reason_code": "COM-04",
            },
            {
                "feature": "employer_sector",
                "direction": "FAVOURABLE",
                "contribution": -0.31,
                "share": 0.16,
                "reason_code": "CND-03",
            },
        ],
        "evidence_refs": [
            {
                "evidence_id": EVIDENCE_ID,
                "type": "ANALYTIC_RESULT",
                "source_system": "app_member.member_event",
                "source_record_id": "fs_01ARZ3NDEKTSV4RRFFQ69G5FB2",
                "locator": {"field_path": "savings_slope_180d"},
                "captured_at": "2026-04-30T00:00:00+00:00",
            },
        ],
    }


@pytest.fixture
def fraud() -> dict[str, Any]:
    return {
        "findings": [{"finding_id": FINDING_ID, "code": "INT-05", "severity": "MEDIUM", "advisory": False}]
    }


@pytest.fixture
def documents() -> dict[str, Any]:
    return {
        "documents": [
            {
                "document_id": "doc_01ARZ3NDEKTSV4RRFFQ69G5FB3",
                "type": "PAYSLIP_LATEST_3",
                "status": "EXTRACTED",
            }
        ]
    }


@pytest.fixture
def human() -> dict[str, Any]:
    return {
        "human_decision_id": HUMAN_ID,
        "decided_by_role": "OFFICER",
        "final_action": "APPROVE_WITH_CONDITIONS",
        "override": True,
        "override_reason_code": "OVR-03",
        "override_note": "known member circumstances confirmed by branch",
        "decided_at": "2026-05-02T09:14:00+00:00",
    }


@pytest.fixture
def bundle(decision, model_run, fraud, documents, human):  # type: ignore[no-untyped-def]
    from app.sources import DecisionBundle

    return DecisionBundle(
        decision=decision,
        model_run=model_run,
        fraud=fraud,
        documents=documents,
        human_decision=human,
        sources={"decision": "decision", "model_run": "risk", "fraud": "fraud", "documents": "document"},
    )


@pytest_asyncio.fixture
async def client(bundle) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    from app.main import app
    from app.routes import set_explain_source
    from app.sources import StaticExplainSource

    source = StaticExplainSource()
    source.add(RECORD_ID, bundle)
    set_explain_source(source)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://governance") as http:
            yield http
    finally:
        set_explain_source(None)
