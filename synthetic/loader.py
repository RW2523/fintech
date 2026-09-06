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
    "outcome",
    "application_ext",
)

#: Applications are written by the document programme, not the population one,
#: so they live under `documents/`. They belong in the register all the same:
#: without them `application_count_12m` is always zero and a velocity rule has
#: nothing to count.
ALTERNATE_SOURCE = {"application_ext": ("documents", "applications")}

_BATCH = 2000


def _base_url() -> str:
    return os.environ.get("CORE_STUB_URL", "http://localhost:8000/api/core_stub")


async def reset_core(client: httpx.AsyncClient, base_url: str | None = None) -> None:
    response = await client.post(f"{base_url or _base_url()}/core/admin/reset")
    response.raise_for_status()


def _read(table: str, out: Path) -> list[dict[str, Any]]:
    """The rows for a table, wherever the generator wrote them."""
    if table not in ALTERNATE_SOURCE:
        return read_table(table, out)
    directory, name = ALTERNATE_SOURCE[table]
    rows = read_table(name, out / directory)
    return [_application_row(row) for row in rows]


def _application_row(row: dict[str, Any]) -> dict[str, Any]:
    """An application as the register holds it.

    The generator's row carries the purpose and the planting marker, which the
    register has no column for; keeping them would fail the insert rather than
    be quietly dropped.
    """
    return {
        "application_id": row["application_id"],
        "member_id": row["member_id"],
        "product_code": row["product_code"],
        "amount": row["amount"],
        "tenor_months": row["tenor_months"],
        "status": row.get("status", "SUBMITTED"),
        "created_at": row["created_at"],
    }


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
            rows: list[dict[str, Any]] = _read(table, out)
            if not rows:
                loaded[table] = 0
                continue
            for start in range(0, len(rows), _BATCH):
                batch = rows[start : start + _BATCH]
                response = await client.post(f"{url}/core/admin/bulk", json={"table": table, "rows": batch})
                response.raise_for_status()
            loaded[table] = len(rows)

    return loaded
