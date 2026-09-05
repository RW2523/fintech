"""T-005 — canonical JSON, hash chains and HMAC signatures."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cio_common.hashing import (
    GENESIS_HASH,
    canonical_json,
    chain_hash,
    hmac_sign,
    hmac_verify,
    sha256,
    verify_chain,
)


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert canonical_json({"a": 1}) == b'{"a":1}'


def test_canonical_json_serialises_money_as_a_string() -> None:
    """CLAUDE.md §7 — Decimal must never become a float."""
    assert canonical_json({"amount": Decimal("12500.00")}) == b'{"amount":"12500.00"}'


def test_canonical_json_is_stable_for_nested_structures() -> None:
    a = {"x": [{"q": 1, "p": 2}], "y": {"n": None, "m": True}}
    b = {"y": {"m": True, "n": None}, "x": [{"p": 2, "q": 1}]}
    assert canonical_json(a) == canonical_json(b)


def test_canonical_json_refuses_types_it_cannot_represent() -> None:
    with pytest.raises(TypeError):
        canonical_json({"f": object()})


def test_chain_hash_is_deterministic_and_position_dependent() -> None:
    first = chain_hash(GENESIS_HASH, {"kind": "SNAPSHOT"})
    assert first == chain_hash(GENESIS_HASH, {"kind": "SNAPSHOT"})
    assert chain_hash(first, {"kind": "SNAPSHOT"}) != first


def test_chain_hash_rejects_a_malformed_previous_hash() -> None:
    for bad in ("", "xyz", "Z" * 64, "0" * 63):
        with pytest.raises(ValueError):
            chain_hash(bad, {"a": 1})


def _build_chain(payloads: list[dict]) -> list[tuple[str, str, dict]]:
    rows, prev = [], GENESIS_HASH
    for payload in payloads:
        h = chain_hash(prev, payload)
        rows.append((h, prev, payload))
        prev = h
    return rows


def test_verify_chain_accepts_an_intact_ledger() -> None:
    rows = _build_chain([{"i": i} for i in range(5)])
    assert verify_chain(rows) is None


def test_verify_chain_locates_a_tampered_payload() -> None:
    """docs/14 — the tamper test must go red."""
    rows = _build_chain([{"i": i} for i in range(5)])
    rows[2] = (rows[2][0], rows[2][1], {"i": 99})
    assert verify_chain(rows) == 2


def test_verify_chain_detects_a_removed_row() -> None:
    rows = _build_chain([{"i": i} for i in range(5)])
    del rows[2]
    assert verify_chain(rows) == 2


def test_verify_chain_detects_a_reordered_row() -> None:
    rows = _build_chain([{"i": i} for i in range(5)])
    rows[1], rows[3] = rows[3], rows[1]
    assert verify_chain(rows) == 1


def test_hmac_signature_excludes_the_signature_field() -> None:
    body = {"token_id": "tok_1", "scope": {"max_amount": "10000.00"}}
    signature = hmac_sign("s" * 32, body)
    signed = {**body, "signature": signature}
    assert hmac_verify("s" * 32, signed)


def test_hmac_verify_rejects_tampering_and_wrong_keys() -> None:
    body = {"token_id": "tok_1", "max_amount": "10000.00"}
    signed = {**body, "signature": hmac_sign("s" * 32, body)}
    assert not hmac_verify("s" * 32, {**signed, "max_amount": "99000.00"})
    assert not hmac_verify("t" * 32, signed)
    assert not hmac_verify("s" * 32, {**body})


def test_sha256_accepts_text_and_bytes() -> None:
    assert sha256("abc") == sha256(b"abc")
    assert len(sha256("abc")) == 64
