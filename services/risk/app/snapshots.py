"""Reading the feature snapshot a score is computed from (docs/07 §2.3).

The snapshot belongs to the feature service, so the risk service asks for it
rather than reading another schema's tables. Scoring a snapshot id rather than
a bag of numbers is what makes a decision reconstructable: the inputs were
frozen and stored before anyone looked at them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from cio_common.errors import CioError, NotFound

__all__ = ["FeatureSnapshot", "HttpSnapshotSource", "SnapshotSource", "StaticSnapshotSource"]


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    """One frozen set of feature values, as the feature service stored it."""

    snapshot_id: str
    member_id: str
    as_of: str
    registry_version: str
    inputs_digest: str
    values: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)
    account_id: str | None = None

    @classmethod
    def from_body(cls, body: dict[str, Any]) -> FeatureSnapshot:
        features = body.get("features") or {}
        return cls(
            snapshot_id=str(body["snapshot_id"]),
            member_id=str(body["member_id"]),
            as_of=str(body.get("as_of", "")),
            registry_version=str(body.get("registry_version", "")),
            inputs_digest=str(body.get("inputs_digest", "")),
            values=dict(features),
            provenance=dict(body.get("provenance") or {}),
            account_id=body.get("account_id"),
        )


class SnapshotSource(Protocol):
    """Where a snapshot comes from. Swapped for a static one in tests."""

    async def fetch(self, snapshot_id: str) -> FeatureSnapshot: ...


class HttpSnapshotSource:
    """Asks the feature service."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 5.0) -> None:
        self._base = (base_url or os.environ.get("FEATURE_URL", "http://feature:8005")).rstrip("/")
        self._timeout = timeout

    async def fetch(self, snapshot_id: str) -> FeatureSnapshot:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{self._base}/features/{snapshot_id}")
        except httpx.HTTPError as exc:
            # Fail safe, not open: without the inputs there is no score, and
            # inventing one would be worse than routing the case to a person.
            # Fails as INTERNAL rather than a new error code: docs/08 fixes the
            # vocabulary at eight codes and adding one needs an ADR.
            raise CioError("feature service unreachable", snapshot_id=snapshot_id) from exc
        if response.status_code == 404:
            raise NotFound(f"no feature snapshot {snapshot_id}")
        response.raise_for_status()
        return FeatureSnapshot.from_body(response.json())


@dataclass
class StaticSnapshotSource:
    """A fixed set of snapshots, for tests and the seed loader."""

    snapshots: dict[str, FeatureSnapshot] = field(default_factory=dict)

    def add(self, snapshot: FeatureSnapshot) -> FeatureSnapshot:
        self.snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    async def fetch(self, snapshot_id: str) -> FeatureSnapshot:
        try:
            return self.snapshots[snapshot_id]
        except KeyError as exc:
            raise NotFound(f"no feature snapshot {snapshot_id}") from exc
