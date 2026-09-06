"""Fetching what an explanation is built from (docs/07 §6).

The explanation is assembled from records other services own: the decision,
the model run behind it, the fraud assessment and the documents on the case.
Governance reads them and rearranges them. It adds nothing.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from cio_common.errors import CioError, NotFound

__all__ = ["DecisionBundle", "ExplainSource", "HttpExplainSource", "StaticExplainSource"]


@dataclass
class DecisionBundle:
    """Everything the explanation may draw on, and nothing else."""

    decision: dict[str, Any]
    model_run: dict[str, Any] | None = None
    fraud: dict[str, Any] | None = None
    documents: dict[str, Any] | None = None
    #: One entry per document: the fields read from it, with the box each was
    #: read from. This is what lets an officer check a value against the paper
    #: rather than take the explanation's word for it (docs/09 §3.7).
    extractions: list[dict[str, Any]] = field(default_factory=list)
    human_decision: dict[str, Any] | None = None
    policy: dict[str, Any] | None = None
    sources: dict[str, str] = field(default_factory=dict)


class ExplainSource(Protocol):
    async def gather(self, record_id: str) -> DecisionBundle: ...


@dataclass
class StaticExplainSource:
    """Bundles handed in directly. Used by tests and the harness."""

    bundles: dict[str, DecisionBundle] = field(default_factory=dict)

    def add(self, record_id: str, bundle: DecisionBundle) -> DecisionBundle:
        self.bundles[record_id] = bundle
        return bundle

    async def gather(self, record_id: str) -> DecisionBundle:
        try:
            return self.bundles[record_id]
        except KeyError as exc:
            raise NotFound(f"no decision record {record_id}") from exc


class HttpExplainSource:
    """Asks the services that own each record."""

    def __init__(self, urls: dict[str, str] | None = None, *, timeout: float = 5.0) -> None:
        defaults = {
            "decision": os.environ.get("DECISION_URL", "http://decision:8012"),
            "risk": os.environ.get("RISK_URL", "http://risk:8006"),
            "fraud": os.environ.get("FRAUD_URL", "http://fraud:8007"),
            "document": os.environ.get("DOCUMENT_URL", "http://document:8002"),
        }
        self._urls = {**defaults, **(urls or {})}
        self._timeout = timeout

    async def _get(self, client: httpx.AsyncClient, url: str) -> Any | None:
        try:
            response = await client.get(url)
        except httpx.HTTPError:
            # A record that cannot be fetched is absent, and the explanation
            # says so. It is never filled in from somewhere else.
            return None
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def gather(self, record_id: str) -> DecisionBundle:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                decision = await self._get(client, f"{self._urls['decision']}/decision-records/{record_id}")
            except httpx.HTTPError as exc:
                raise CioError("decision service unreachable", record_id=record_id) from exc
            if decision is None:
                raise NotFound(f"no decision record {record_id}")

            case_id = decision.get("case_id") or decision.get("snapshot_id")
            model_run_id = (decision.get("model_versions") or {}).get("model_run_id")

            model_run, fraud, documents = await asyncio.gather(
                self._get(client, f"{self._urls['risk']}/risk/runs/{model_run_id}")
                if model_run_id
                else _none(),
                self._get(client, f"{self._urls['fraud']}/fraud/signals/{case_id}") if case_id else _none(),
                self._get(client, f"{self._urls['document']}/cases/{case_id}/documents")
                if case_id
                else _none(),
            )

            # The field boxes live one call further in, per document. Fetched
            # here rather than in the explanation builder so the builder stays
            # a pure function of what was gathered.
            listed = (documents or {}).get("documents") or []
            extractions = [
                extraction
                for extraction in await asyncio.gather(
                    *(
                        self._get(
                            client,
                            f"{self._urls['document']}/documents/{row['document_id']}/extraction",
                        )
                        for row in listed
                        if row.get("document_id")
                    )
                )
                if extraction
            ]

        return DecisionBundle(
            decision=decision,
            model_run=model_run,
            fraud=fraud,
            documents=documents,
            extractions=extractions,
            sources={
                "decision": "decision",
                "model_run": "risk" if model_run else "unavailable",
                "fraud": "fraud" if fraud else "unavailable",
                "documents": "document" if documents else "unavailable",
                "extractions": "document" if extractions else "unavailable",
            },
        )


async def _none() -> None:
    return None
