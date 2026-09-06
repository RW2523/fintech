"""Prefixed ULID identifiers (CLAUDE.md §7).

Every id is `<prefix>_<26-char Crockford base32 ULID>`. ULIDs sort by creation
time, which keeps ledger and event tables clustered in insertion order.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from ulid import ULID

__all__ = ["PREFIXES", "derived_id", "id_prefix", "is_id", "new_id", "timestamp_of"]

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
    "stp": "SagaStep",
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


#: Crockford base32, the alphabet a ULID body is written in.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def derived_id(prefix: str, *parts: str | bytes) -> str:
    """An id determined entirely by what it identifies.

    Most ids are minted fresh, because two identical requests are still two
    events. A few must not be: re-scoring the same snapshot with the same
    model has to yield the same `model_run_id`, or a decision could not be
    shown to be reproducible. Those are derived from a hash of their inputs
    and are still valid ids, so nothing downstream needs to know the
    difference.
    """
    if prefix not in PREFIXES:
        raise ValueError(f"unknown id prefix {prefix!r}; known: {', '.join(sorted(PREFIXES))}")
    joined = b"\x1f".join(p if isinstance(p, bytes) else p.encode() for p in parts)
    digest = int(hashlib.sha256(joined).hexdigest(), 16)
    body = ""
    for _ in range(26):
        digest, remainder = divmod(digest, 32)
        body = _CROCKFORD[remainder] + body
    return f"{prefix}_{body}"
