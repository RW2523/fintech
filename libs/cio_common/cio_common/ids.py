"""Prefixed ULID identifiers (CLAUDE.md §7).

Every id is `<prefix>_<26-char Crockford base32 ULID>`. ULIDs sort by creation
time, which keeps ledger and event tables clustered in insertion order.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from ulid import ULID

__all__ = ["PREFIXES", "id_prefix", "is_id", "new_id", "timestamp_of"]

# prefix -> what it identifies (docs/03 preamble)
PREFIXES: dict[str, str] = {
    "snap": "CaseSnapshot",
    "ev": "EvidenceRef",
    "op": "AgentOpinion",
    "dr": "DecisionRecord",
    "hd": "HumanDecision",
    "act": "ActionProposal",
    "tok": "ApprovalToken",
    "run": "CommitteeRun",
    "case": "Case",
    "app": "Application",
    "doc": "Document",
    "calc": "Calculation",
    "mr": "ModelRun",
    "fs": "FeatureSnapshot",
    "tc": "TemporalContext",
    "alert": "Alert",
    "msg": "Message",
    "sbx": "SandboxRun",
    "smp": "SampleReview",
    "ent": "LedgerEntry",
    "aud": "AuditEntry",
    "inv": "AgentInvocation",
    "evt": "Event",
    "fc": "Forecast",
    "asmt": "FraudAssessment",
    "ext": "Extraction",
    "fnd": "Finding",
}

_ULID_BODY = "[0-9A-HJKMNP-TV-Z]{26}"
_PATTERN = re.compile(rf"^(?P<prefix>[a-z_]+)_(?P<body>{_ULID_BODY})$")


def new_id(prefix: str) -> str:
    """Mint a new identifier, e.g. ``new_id("snap")`` -> ``snap_01J…``."""
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix {prefix!r}; known: {', '.join(sorted(PREFIXES))}")
    return f"{prefix}_{ULID()}"


def is_id(value: object, prefix: str | None = None) -> bool:
    """True when ``value`` is a well-formed id, optionally of a given prefix."""
    if not isinstance(value, str):
        return False
    m = _PATTERN.match(value)
    if not m:
        return False
    return prefix is None or m.group("prefix") == prefix


def id_prefix(value: str) -> str:
    m = _PATTERN.match(value)
    if not m:
        raise ValueError(f"not an identifier: {value!r}")
    return m.group("prefix")


def timestamp_of(value: str) -> datetime:
    """The creation time embedded in the ULID, as an aware UTC datetime."""
    m = _PATTERN.match(value)
    if not m:
        raise ValueError(f"not an identifier: {value!r}")
    return ULID.from_str(m.group("body")).datetime.astimezone(UTC)
