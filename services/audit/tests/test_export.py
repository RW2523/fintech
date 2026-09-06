"""T-053 — the daily write-once export (docs/04 §6).

The append-only trigger stops a stray query. It does not stop somebody with the
database password, and an audit trail is worth having precisely because it
holds against the people who run the system. These tests are about what the
export claims and what it does when the storage cannot back the claim up.
"""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient

from app.worm import object_key, serialise
from tests.conftest import entry


class FakeWorm:
    """A write-once bucket that can be told it has no lock."""

    def __init__(self, *, locked: bool = True, refuse: bool = False) -> None:
        self.locked = locked
        self.refuse = refuse
        self.written: list[tuple[str, bytes, bool]] = []

    def ensure_bucket(self) -> bool:
        return self.locked

    def put(self, key: str, body: bytes, *, locked: bool = True) -> None:
        if self.refuse:
            raise RuntimeError("the bucket refused the write")
        self.written.append((key, body, locked))


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> FakeWorm:
    from app import routes

    store = FakeWorm()
    monkeypatch.setattr(routes, "worm", lambda: store)
    return store


async def test_a_day_is_written_once_and_recorded(client: AsyncClient, bucket: FakeWorm) -> None:
    await client.post("/audit", json=entry())
    today = date.today().isoformat()

    body = (await client.post("/audit/export", json={"day": today})).json()
    assert body["entries"] == 1
    assert body["object_key"] == object_key(date.fromisoformat(today))
    assert body["locked"] is True
    assert body["warning"] is None

    listed = (await client.get("/audit/exports")).json()
    assert listed["count"] == 1
    assert listed["exports"][0]["sha256"] == body["sha256"]


async def test_the_export_carries_the_chain_head(client: AsyncClient, bucket: FakeWorm) -> None:
    """A later export whose entries do not follow the previous head means rows
    were removed in between, and the gap is visible without reading one."""
    first = await client.post("/audit", json=entry())
    today = date.today().isoformat()

    body = (await client.post("/audit/export", json={"day": today})).json()
    assert body["head_hash"] == first.json()["hash"]


async def test_the_bytes_are_one_entry_per_line(client: AsyncClient, bucket: FakeWorm) -> None:
    """Newline-delimited so a reader can verify the chain by streaming, without
    holding a day of entries in memory."""
    await client.post("/audit", json=entry())
    await client.post("/audit", json=entry(action="token.issued"))
    await client.post("/audit/export", json={"day": date.today().isoformat()})

    _, written, _ = bucket.written[0]
    assert written.count(b"\n") == 2


async def test_a_bucket_without_object_lock_says_so(client: AsyncClient, bucket: FakeWorm) -> None:
    """An export the storage did not lock is a backup, not a WORM archive, and
    the difference matters to whoever has to rely on it."""
    bucket.locked = False
    await client.post("/audit", json=entry())

    body = (await client.post("/audit/export", json={"day": date.today().isoformat()})).json()
    assert body["locked"] is False
    assert "not WORM" in body["warning"]
    # The copy is still written: losing the day as well would be worse.
    assert bucket.written and bucket.written[0][2] is False


async def test_a_bucket_that_refuses_fails_the_export(client: AsyncClient, bucket: FakeWorm) -> None:
    """Reported rather than recorded as done: an export row for an object that
    is not there would be a false statement about where the archive is."""
    bucket.refuse = True
    await client.post("/audit", json=entry())

    response = await client.post("/audit/export", json={"day": date.today().isoformat()})
    assert response.status_code >= 400
    listed = (await client.get("/audit/exports")).json()
    assert listed["count"] == 0


async def test_re_exporting_a_day_replaces_its_row(client: AsyncClient, bucket: FakeWorm) -> None:
    """A day is one export. Two rows for the same day would leave a reader
    unsure which object holds it."""
    await client.post("/audit", json=entry())
    today = date.today().isoformat()
    await client.post("/audit/export", json={"day": today})
    await client.post("/audit", json=entry(action="token.issued"))
    second = (await client.post("/audit/export", json={"day": today})).json()

    listed = (await client.get("/audit/exports")).json()
    assert listed["count"] == 1
    assert listed["exports"][0]["entries"] == second["entries"] == 2


def test_the_key_is_partitioned_by_day() -> None:
    """So a retention policy can act on a prefix."""
    assert object_key(date(2026, 9, 6)) == "2026/09/06/audit-20260906.ndjson"


def test_serialising_nothing_is_empty_not_broken() -> None:
    assert serialise([]) == b""


async def test_a_day_with_no_entries_exports_an_empty_object(client: AsyncClient, bucket: FakeWorm) -> None:
    """A day nobody used still gets an object, so a missing one means a missing
    export rather than a quiet day."""
    body = (await client.post("/audit/export", json={"day": "2020-01-01"})).json()
    assert body["entries"] == 0
    assert bucket.written[0][1] == b""
