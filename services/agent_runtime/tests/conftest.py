"""Fixtures for agent-runtime. Every test runs against a fake gateway."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

RUN_ID = "run_01ARZ3NDEKTSV4RRFFQ69G5FAX"
SNAPSHOT_ID = "snap_01ARZ3NDEKTSV4RRFFQ69G5FAV"
EVIDENCE_ID = "ev_01ARZ3NDEKTSV4RRFFQ69G5FAW"


@pytest.fixture
def snapshot() -> dict[str, Any]:
    return {
        "snapshot_id": SNAPSHOT_ID,
        "product_code": "PF-STD",
        "amount": "12000.00",
        "tenor_months": 36,
        "purpose": "HOME_IMPROVEMENT",
        "policy_version": "policy/PF-STD/2026.09.1",
        "member": {
            "member_ref": "«MEMBER_1»",
            "tenure_months": 96,
            "branch_id": "BR-01",
            "employer_sector": "PUBLIC_ADMIN",
        },
        "documents": [
            {"document_id": "doc_1", "type": "IDENTITY", "status": "EXTRACTED", "confidence": 0.94}
        ],
    }


@pytest.fixture
def tool_results() -> list[dict[str, Any]]:
    return [
        {
            "tool": "documents.list",
            "result": {"count": 4, "required_complete": True, "min_confidence": 0.94},
            "evidence_refs": [{"evidence_id": EVIDENCE_ID}],
        }
    ]


@pytest.fixture
def good_opinion() -> dict[str, Any]:
    return {
        "stance": "SUPPORT",
        "confidence": 0.8,
        "reason_codes": ["DOC-01"],
        "claims": [
            {
                "text": "All 4 required documents are present at confidence 0.94.",
                "evidence_refs": [EVIDENCE_ID],
            }
        ],
        "contradictions": [],
        "unresolved": [],
        "proposed_actions": [],
    }


@pytest.fixture
def fake(good_opinion: dict[str, Any]) -> Any:
    from app.gateway_client import FakeGatewayClient

    return FakeGatewayClient(default=good_opinion)


@pytest_asyncio.fixture
async def client(fake: Any) -> AsyncIterator[AsyncClient]:
    from app.main import app
    from app.routes import set_gateway_client

    set_gateway_client(fake)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://agent_runtime") as http:
            yield http
    finally:
        set_gateway_client(None)
