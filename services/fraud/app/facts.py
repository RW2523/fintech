"""Gathering what the rules need about a case (docs/07 §3).

Each fact belongs to a service that owns it: the core stub knows who a member
works for and who guarantees whom, the document service knows what it found in
the file, the feature service knows the snapshot. The fraud service reads
broadly and writes narrowly, so it asks rather than reaching into schemas that
are not its own.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

import httpx

from app.graph import EntityGraph
from app.rules import VELOCITY_DAYS, CaseFacts
from cio_common.errors import CioError, NotFound

__all__ = ["CaseBundle", "FactSource", "HttpFactSource", "StaticFactSource"]

#: How far the guarantee walk goes. Measured on this population, three hops
#: closes a seven-member ring and two does not; four adds edges without
#: finding anything three missed.
GUARANTEE_HOPS = 3

#: How far back to look for other applications in the cycle. docs/07 §3 grades
#: a cycle HIGH on applications within ninety days.
CYCLE_LOOKBACK_DAYS = 90


@dataclass
class CaseBundle:
    """The facts and the graph built from them."""

    facts: CaseFacts
    graph: EntityGraph
    sources: dict[str, str] = field(default_factory=dict)


class FactSource(Protocol):
    async def gather(self, *, case_id: str, member_id: str, snapshot_id: str | None = None) -> CaseBundle: ...


@dataclass
class StaticFactSource:
    """Facts handed in directly. Used by tests and the evaluation harness."""

    bundles: dict[str, CaseBundle] = field(default_factory=dict)

    def add(self, case_id: str, bundle: CaseBundle) -> CaseBundle:
        self.bundles[case_id] = bundle
        return bundle

    async def gather(self, *, case_id: str, member_id: str, snapshot_id: str | None = None) -> CaseBundle:
        try:
            return self.bundles[case_id]
        except KeyError as exc:
            raise NotFound(f"no facts for case {case_id}") from exc


class HttpFactSource:
    """Asks the services that own each fact."""

    def __init__(
        self,
        *,
        core_url: str | None = None,
        document_url: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._core = (core_url or os.environ.get("CORE_STUB_URL", "http://core_stub:8010")).rstrip("/")
        self._document = (document_url or os.environ.get("DOCUMENT_URL", "http://document:8002")).rstrip("/")
        self._timeout = timeout

    async def _get(self, client: httpx.AsyncClient, url: str, **params: Any) -> Any | None:
        try:
            response = await client.get(url, params={k: v for k, v in params.items() if v is not None})
        except httpx.HTTPError as exc:
            # Fail safe: a fact that cannot be fetched is absent, and an absent
            # fact must never be read as an all-clear. The caller records which
            # sources answered so an assessment built on a partial picture is
            # visibly partial.
            raise CioError(f"{url} unreachable") from exc
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def gather(self, *, case_id: str, member_id: str, snapshot_id: str | None = None) -> CaseBundle:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            member, neighbourhood, documents, mine = await asyncio.gather(
                self._get(client, f"{self._core}/core/members/{member_id}"),
                self._get(
                    client, f"{self._core}/core/guarantees/{member_id}/neighbourhood", hops=GUARANTEE_HOPS
                ),
                self._get(client, f"{self._document}/cases/{case_id}/findings"),
                self._get(client, f"{self._core}/core/applications/by-members", members=member_id),
                return_exceptions=False,
            )
            if member is None:
                raise NotFound(f"no member {member_id}")

            applied_at = _latest_application((mine or {}).get("applications"))
            employer_id = member.get("employer_id")
            velocity_window = None
            if applied_at and employer_id:
                velocity_window = await self._get(
                    client,
                    f"{self._core}/core/applications/velocity",
                    employer_id=employer_id,
                    branch_id=member.get("branch_id"),
                    date_from=(applied_at - timedelta(days=VELOCITY_DAYS)).isoformat(),
                    date_to=applied_at.isoformat(),
                )

            members_in_reach = list((neighbourhood or {}).get("members") or [])
            cycle_applications = await self._applications_for(client, members_in_reach, before=applied_at)

        graph = EntityGraph()
        graph.add_member(member_id)
        if employer_id:
            graph.add_employment(member_id, str(employer_id))
        for edge in (neighbourhood or {}).get("edges", []):
            graph.add_guarantee(
                str(edge["guarantor_member_id"]),
                str(edge["borrower_member_id"]),
                account_id=edge.get("account_id"),
            )

        findings = tuple((documents or {}).get("findings") or [])
        facts = CaseFacts(
            case_id=case_id,
            member_id=member_id,
            applied_at=applied_at,
            employer_id=str(employer_id) if employer_id else None,
            branch_id=member.get("branch_id"),
            contact_updated_at=_as_date(member.get("contact_updated_at")),
            document_findings=findings,
            recent_applications=tuple(
                _velocity_row(row) for row in (velocity_window or {}).get("applications", [])
            ),
            applications_by_member=cycle_applications,
            duplicate_identities=_duplicates(findings),
        )
        return CaseBundle(
            facts=facts,
            graph=graph,
            sources={
                "member": "core_stub",
                "guarantees": "core_stub" if neighbourhood else "unavailable",
                "documents": "document" if documents else "unavailable",
            },
        )

    async def _applications_for(
        self, client: httpx.AsyncClient, members: list[str], *, before: date | None
    ) -> dict[str, date]:
        """When each member in the neighbourhood last applied."""
        if not members:
            return {}
        found = await self._get(
            client, f"{self._core}/core/applications/by-members", members=",".join(sorted(members))
        )
        out: dict[str, date] = {}
        cutoff = (before or date.today()) - timedelta(days=CYCLE_LOOKBACK_DAYS)
        for row in (found or {}).get("applications", []):
            when = _as_date(row.get("created_at"))
            if when is None or when < cutoff:
                continue
            member_id = str(row["member_id"])
            if member_id not in out or when > out[member_id]:
                out[member_id] = when
        return out


def _velocity_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "member_id": row.get("member_id"),
        "employer_id": row.get("employer_id"),
        "branch_id": row.get("branch_id"),
        "applied_at": _as_date(row.get("created_at")),
    }


def _duplicates(findings: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    """Members the document service says share an identity with this one."""
    out: set[str] = set()
    for finding in findings:
        if str(finding.get("code")) != "INT-04":
            continue
        detail = finding.get("detail") or {}
        for key in ("also_used_by", "members", "member_ids"):
            value = detail.get(key)
            if isinstance(value, list):
                out.update(str(v) for v in value)
    return tuple(sorted(out))


def _latest_application(applications: Any) -> date | None:
    if not applications:
        return None
    dates = [d for d in (_as_date(a.get("created_at")) for a in applications) if d]
    return max(dates) if dates else None


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
