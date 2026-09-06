"""Fixtures for llm-gateway.

Every gateway behaviour worth testing happens without a model, so the fake
provider is the default and no test reaches the network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

OPINION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["stance", "confidence"],
    "additionalProperties": False,
    "properties": {
        "stance": {"enum": ["SUPPORT", "SUPPORT_WITH_CONDITIONS", "OPPOSE"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    """Breakers and budgets are process-wide, so each test starts fresh."""
    from app.breaker import reset_breakers
    from app.budget import usage_ledger
    from app.config import reset

    reset_breakers()
    usage_ledger().clear()
    reset()
    yield
    reset_breakers()
    usage_ledger().clear()
    reset()


@pytest.fixture
def fake() -> Any:
    from app.providers import FakeProvider

    return FakeProvider(default='{"stance": "SUPPORT", "confidence": 0.8}')


@pytest_asyncio.fixture
async def client(fake: Any) -> AsyncIterator[AsyncClient]:
    from app.main import app
    from app.routes import set_provider

    set_provider(fake)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://llm") as http:
            yield http
    finally:
        set_provider(None)
