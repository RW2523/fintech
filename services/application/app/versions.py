"""Gathering the version stamps a CaseSnapshot must carry (docs/03 §1).

A snapshot names the exact policy pack, model artefacts and document bundle the
case was judged against, so a decision can be reconstructed years later even
after every one of them has moved on.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from cio_common.hashing import canonical_json, sha256

__all__ = ["HttpVersionSource", "SnapshotVersions", "StaticVersionSource", "VersionSource"]

#: service -> the model-version key it fills in (docs/03 §1).
MODEL_SERVICES = {
    "risk": "risk",
    "fraud": "fraud",
    "lmi": "delinquency",
    "document": "document_ai",
    "llm_gateway": "embed",
}


@dataclass(frozen=True, slots=True)
class SnapshotVersions:
    member_snapshot_ver: str
    document_bundle_ver: str
    policy_version: str
    dff_version: str
    autonomy_version: str
    model_versions: dict[str, str]
    evidence_index_ver: str


class VersionSource(Protocol):
    """Where the stamps come from. Swapped for a static one in tests."""

    async def gather(
        self, *, member_id: str, product_code: str, document_ids: list[str]
    ) -> SnapshotVersions: ...


def _bundle_version(document_ids: list[str]) -> str:
    """A hash over the document ids in the case file (docs/03 §1)."""
    return "docs:" + sha256(canonical_json(sorted(document_ids)))[:16]


@dataclass(frozen=True, slots=True)
class StaticVersionSource:
    """Fixed stamps. Used by tests and by the seed loader."""

    policy_version: str = "policy/PF-STD/2026.09.1"
    dff_version: str = "dff/PF-STD/2026.09.1"
    autonomy_version: str = "autonomy/PF-STD/2026.09.1"
    member_snapshot_ver: str = "member:v1"
    evidence_index_ver: str = "evidence:v1"
    model_versions: dict[str, str] | None = None

    async def gather(self, *, member_id: str, product_code: str, document_ids: list[str]) -> SnapshotVersions:
        return SnapshotVersions(
            member_snapshot_ver=self.member_snapshot_ver,
            document_bundle_ver=_bundle_version(document_ids),
            policy_version=self.policy_version.replace("PF-STD", product_code),
            dff_version=self.dff_version.replace("PF-STD", product_code),
            autonomy_version=self.autonomy_version.replace("PF-STD", product_code),
            model_versions=dict(
                self.model_versions or dict.fromkeys(MODEL_SERVICES.values(), "0.0.0-unavailable")
            ),
            evidence_index_ver=self.evidence_index_ver,
        )


class HttpVersionSource:
    """Asks each service for its own version.

    A service that cannot answer is stamped `0.0.0-unavailable` rather than
    failing the freeze: the deterministic path must continue and the case route
    to a human (CLAUDE.md §2.7).
    """

    def __init__(self, base_urls: dict[str, str], *, timeout: float = 5.0) -> None:
        self._base_urls = base_urls
        self._timeout = timeout

    async def _get(self, service: str, path: str) -> dict[str, Any] | None:
        base = self._base_urls.get(service)
        if not base:
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(f"{base}{path}")
                response.raise_for_status()
                body = response.json()
                return body if isinstance(body, dict) else None
        except Exception:
            return None

    async def gather(self, *, member_id: str, product_code: str, document_ids: list[str]) -> SnapshotVersions:
        policy_task = self._get("policy", f"/policy/{product_code}/versions")
        member_task = self._get("core_stub", f"/core/members/{member_id}")
        model_tasks = {key: self._get(service, "/version") for service, key in MODEL_SERVICES.items()}

        policy, member, *model_results = await asyncio.gather(policy_task, member_task, *model_tasks.values())

        active = (policy or {}).get("active", "unknown")
        models = {
            key: str((result or {}).get("version", "0.0.0-unavailable"))
            for key, result in zip(model_tasks, model_results, strict=True)
        }

        return SnapshotVersions(
            member_snapshot_ver="member:" + sha256(canonical_json(member or {}))[:16],
            document_bundle_ver=_bundle_version(document_ids),
            policy_version=f"policy/{product_code}/{active}",
            dff_version=f"dff/{product_code}/{active}",
            autonomy_version=f"autonomy/{product_code}/{active}",
            model_versions=models,
            evidence_index_ver="evidence:v1",
        )
