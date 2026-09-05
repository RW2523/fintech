"""Canonical JSON, digests, hash chains and HMAC signatures.

The ledger and audit tables are hash-chained (docs/04 §5, §6): each row carries
`hash = sha256(prev_hash || canonical_json(payload))`. Canonicalisation must be
stable across processes and language runtimes, so it is defined here once.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

__all__ = [
    "GENESIS_HASH",
    "canonical_json",
    "chain_hash",
    "hmac_sign",
    "hmac_verify",
    "sha256",
    "verify_chain",
]

#: `prev_hash` of the first row in a chain.
GENESIS_HASH = "0" * 64


def _default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        # money crosses the boundary as a string, never a float (CLAUDE.md §7)
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"cannot canonicalise {type(obj).__name__}")


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, no whitespace, UTF-8. The only accepted serialisation."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default
    ).encode("utf-8")


def sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def chain_hash(prev_hash: str, payload: Any) -> str:
    """The next link: ``sha256(prev_hash || canonical_json(payload))``."""
    if len(prev_hash) != 64 or not all(c in "0123456789abcdef" for c in prev_hash):
        raise ValueError(f"prev_hash must be 64 lowercase hex characters, got {prev_hash!r}")
    return hashlib.sha256(prev_hash.encode("ascii") + canonical_json(payload)).hexdigest()


def verify_chain(rows: list[tuple[str, str, Any]], *, genesis: str = GENESIS_HASH) -> int | None:
    """Verify ``[(hash, prev_hash, payload)]`` in order.

    Returns the index of the first broken link, or ``None`` when the chain holds.
    """
    expected_prev = genesis
    for i, (row_hash, prev_hash, payload) in enumerate(rows):
        if prev_hash != expected_prev or chain_hash(prev_hash, payload) != row_hash:
            return i
        expected_prev = row_hash
    return None


def hmac_sign(secret: str, obj: Any, *, field: str = "signature") -> str:
    """HMAC-SHA256 over canonical JSON with ``field`` removed."""
    body = {k: v for k, v in obj.items() if k != field} if isinstance(obj, dict) else obj
    return hmac.new(secret.encode("utf-8"), canonical_json(body), hashlib.sha256).hexdigest()


def hmac_verify(secret: str, obj: dict[str, Any], *, field: str = "signature") -> bool:
    """Constant-time signature check."""
    presented = obj.get(field)
    if not isinstance(presented, str):
        return False
    return hmac.compare_digest(presented, hmac_sign(secret, obj, field=field))
