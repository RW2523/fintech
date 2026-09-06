"""Writing to the audit trail from any service (docs/04 §6).

The audit trail answers a different question from the Decision Ledger. The
ledger says what the platform decided; the audit trail says what it did, who
asked, and what the state was before and after. A decision that was never acted
on still belongs in the ledger, and a configuration change nobody decided still
belongs in the audit trail.

Services call `record()`. Inside audit-service that writes to the table
directly; everywhere else it posts to audit-service, and a failure to reach it
is raised rather than swallowed: an action that happened without an audit entry
is an action nobody can account for.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = ["WRITE_DEADLINE_SECONDS", "Actor", "AuditEntry", "AuditUnavailableError", "record"]


class AuditUnavailableError(RuntimeError):
    """The audit trail could not be written.

    Raised rather than logged. A caller that carries on regardless has taken
    an action nobody can account for, and the platform's whole claim is that
    it can account for what it did.
    """


@dataclass(frozen=True, slots=True)
class Actor:
    """Who did it. A service acting on its own behalf says so."""

    id: str
    role: str = "SYSTEM"
    kind: str = "SERVICE"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditEntry:
    """One thing that happened, with the state either side of it."""

    actor: Actor
    action: str
    service: str
    case_id: str | None = None
    run_id: str | None = None
    object_ref: dict[str, Any] = field(default_factory=dict)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    policy_version: str | None = None
    model_versions: dict[str, str] = field(default_factory=dict)
    trace_id: str | None = None
    ip: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor.as_dict(),
            "action": self.action,
            "service": self.service,
            "case_id": self.case_id,
            "run_id": self.run_id,
            "object_ref": self.object_ref,
            "before": self.before,
            "after": self.after,
            "policy_version": self.policy_version,
            "model_versions": self.model_versions,
            "trace_id": self.trace_id,
            "ip": self.ip,
        }


#: The deadline on an audit write. Short: a slow audit trail must not hold a
#: decision open, and a write that misses this is an outage the caller has to
#: deal with rather than wait out.
WRITE_DEADLINE_SECONDS = 10.0


async def record(entry: AuditEntry, *, url: str | None = None) -> dict[str, Any]:
    """Write one audit entry through audit-service."""
    import httpx

    base = (url or os.environ.get("AUDIT_URL", "http://audit:8015")).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=WRITE_DEADLINE_SECONDS) as client:
            response = await client.post(f"{base}/audit", json=entry.as_dict())
            response.raise_for_status()
            return dict(response.json())
    except httpx.HTTPError as exc:
        raise AuditUnavailableError(f"{base}/audit: {exc}") from exc
