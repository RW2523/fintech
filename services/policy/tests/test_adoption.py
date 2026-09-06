"""T-073 — adopting a sandbox candidate as a new pack version (docs/05 §7).

Write-once is the property under test. A version somebody decided under must
still read back exactly as it did, because every ledger entry citing it is a
claim about a document, and a document that changed after the fact is not
evidence. Re-adopting the same change gets the next sequence rather than
overwriting the last.

The pack root is redirected to a temporary directory here. These tests write
policy versions, and writing them into the repository's own packs would leave
the demo deciding under something a test invented.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from app.packs import PackError, available_packs, load_pack, write_pack
from cio_common.assets import policy_pack_root

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def packs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the real packs, somewhere a test may write."""
    root = tmp_path / "policy_packs"
    shutil.copytree(ROOT / "policy_packs", root)
    monkeypatch.setenv("CIO_POLICY_PACK_ROOT", str(root))
    # The root is resolved once per process and cached, so the override only
    # takes effect after the cache is cleared, and the next test in the suite
    # would otherwise inherit this temporary directory.
    policy_pack_root.cache_clear()
    yield root
    monkeypatch.delenv("CIO_POLICY_PACK_ROOT", raising=False)
    policy_pack_root.cache_clear()


def bodies(product: str = "PF-STD", version: str = "2026.09.1") -> dict[str, object]:
    pack = load_pack(product, version)
    return {"policy": pack.policy, "dff": pack.dff, "autonomy": pack.autonomy}


def test_a_new_version_is_written_and_loads_back(packs: Path) -> None:
    written = bodies()
    written["dff"] = {
        **written["dff"],
        "weights": {  # type: ignore[dict-item]
            "CAPACITY": 0.35,
            "CONDUCT": 0.20,
            "COMMITMENT": 0.30,
            "CONDITIONS": 0.10,
            "INTEGRITY": 0.05,
        },
    }
    write_pack("PF-STD", "2099.01.1", written)

    reloaded = load_pack("PF-STD", "2099.01.1")
    assert reloaded.dff["weights"]["COMMITMENT"] == 0.30
    assert ("PF-STD", "2099.01.1") in available_packs()


def test_a_version_is_never_written_over(packs: Path) -> None:
    """The whole point. A pack that changed after a decision cited it turns
    every one of those decisions into a claim nobody can check."""
    write_pack("PF-STD", "2099.01.1", bodies())
    with pytest.raises(PackError, match="already exists"):
        write_pack("PF-STD", "2099.01.1", bodies())


def test_a_version_that_would_not_load_is_not_left_on_disk(packs: Path) -> None:
    """Validated by loading it back before the write is called done.

    A half-written version on disk is worse than a failed write: the next
    request to list versions finds it, and the next decision may be made under
    it.
    """
    broken = bodies()
    broken["dff"] = {"schema": "dff/1.0"}  # missing everything the schema needs
    with pytest.raises(PackError):
        write_pack("PF-STD", "2099.01.2", broken)

    # Whatever is on disk must not be loadable as a pack.
    with pytest.raises(PackError):
        load_pack("PF-STD", "2099.01.2")


def test_a_missing_document_is_refused_before_anything_is_written(packs: Path) -> None:
    partial = bodies()
    del partial["autonomy"]
    with pytest.raises(PackError, match=r"autonomy\.yaml is missing"):
        write_pack("PF-STD", "2099.01.3", partial)


def test_the_written_pack_is_the_candidate_not_the_original(packs: Path) -> None:
    """The original on disk is untouched by an adoption.

    `apply_candidate` deep-copies, and this asserts the copy actually held:
    a candidate that mutated the pack in force would change the meaning of
    every decision already made under it.
    """
    from app.sandbox import apply_candidate

    current = load_pack("PF-STD", "2026.09.1")
    amended = apply_candidate(current, {"dff": {"thresholds": {"approve": 60}}})
    write_pack(
        "PF-STD", "2099.01.4", {"policy": amended.policy, "dff": amended.dff, "autonomy": amended.autonomy}
    )

    assert load_pack("PF-STD", "2099.01.4").dff["thresholds"]["approve"] == 60
    assert load_pack("PF-STD", "2026.09.1").dff["thresholds"]["approve"] != 60

    on_disk = yaml.safe_load((packs / "PF-STD" / "2026.09.1" / "dff.yaml").read_text())
    assert on_disk["thresholds"]["approve"] != 60
