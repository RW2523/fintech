"""Loading a generated population into the core stub (docs/08 §9).

Rows go in through `/core/admin/bulk`, which is allow-listed and refused outside
development. The order matters: foreign keys are real.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from synthetic.writer import DEFAULT_OUT, read_table

__all__ = ["LOAD_ORDER", "load_population", "reset_core"]

#: Referential order. Employers before members, members before accounts.
LOAD_ORDER = (
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
)

_BATCH = 2000


def _base_url() -> str:
    return os.environ.get("CORE_STUB_URL", "http://localhost:8000/api/core_stub")


async def reset_core(client: httpx.AsyncClient, base_url: str | None = None) -> None:
    response = await client.post(f"{base_url or _base_url()}/core/admin/reset")
    response.raise_for_status()


async def load_population(
    out: Path = DEFAULT_OUT,
    *,
    base_url: str | None = None,
    reset: bool = True,
    token: str | None = None,
) -> dict[str, int]:
    """Load every table, in order, in batches. Returns rows loaded per table."""
    url = base_url or _base_url()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    loaded: dict[str, int] = {}

    async with httpx.AsyncClient(timeout=120.0, headers=headers) as client:
        if reset:
            await reset_core(client, url)

        for table in LOAD_ORDER:
            rows: list[dict[str, Any]] = read_table(table, out)
            if not rows:
                loaded[table] = 0
                continue
            for start in range(0, len(rows), _BATCH):
                batch = rows[start : start + _BATCH]
                response = await client.post(f"{url}/core/admin/bulk", json={"table": table, "rows": batch})
                response.raise_for_status()
            loaded[table] = len(rows)

    return loaded
