"""T-005 — prefixed ULID identifiers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cio_common.ids import PREFIXES, id_prefix, is_id, new_id, timestamp_of


@pytest.mark.parametrize("prefix", sorted(PREFIXES))
def test_every_prefix_mints_a_recognisable_id(prefix: str) -> None:
    value = new_id(prefix)
    assert value.startswith(f"{prefix}_")
    assert is_id(value, prefix)
    assert id_prefix(value) == prefix


def test_unknown_prefixes_are_refused() -> None:
    with pytest.raises(ValueError, match="unknown id prefix"):
        new_id("nope")


def test_ids_are_unique_and_sort_by_creation_time() -> None:
    ids = [new_id("snap") for _ in range(200)]
    assert len(set(ids)) == 200
    assert ids == sorted(ids), "ULIDs must sort lexicographically by creation time"


def test_ids_carry_their_creation_time() -> None:
    before = datetime.now(UTC)
    value = new_id("run")
    after = datetime.now(UTC)
    assert before.replace(microsecond=0) <= timestamp_of(value) <= after


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "snap",
        "snap_",
        "snap_tooshort",
        42,
        None,
        "snap_01ILOU00000000000000000000",  # Crockford excludes I, L, O and U
        "SNAP_01JQZK7M8N9P0Q1R2S3T4V5W6X",  # prefix is lowercase
    ],
)
def test_malformed_values_are_not_ids(bad: object) -> None:
    assert not is_id(bad)


def test_prefix_filter_is_enforced() -> None:
    assert not is_id(new_id("snap"), "run")
