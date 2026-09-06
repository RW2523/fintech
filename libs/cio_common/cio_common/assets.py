"""Finding repository assets that are data rather than code (docs/02 §3).

Policy packs and contract schemas are read from disk at runtime, and their
location differs between a checkout (`<repo>/policy_packs`) and a service image
(`/srv/policy_packs`). Counting directories up from `__file__` is right in
exactly one of those layouts, so this searches for the directory instead. The
same reasoning as `cio_common.models`, applied to data.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

__all__ = ["AssetNotFoundError", "asset_root", "policy_pack_root"]


class AssetNotFoundError(RuntimeError):
    """A required asset directory is not on disk anywhere above the caller."""


def asset_root(name: str, marker: str, start: Path | None = None) -> Path:
    """The `name` directory, identified by a `marker` path inside it.

    The marker matters: a directory that merely shares the name is not the
    asset. Searches upward from `start` (the caller's file by default) and
    then from the working directory.
    """
    here = (start or Path(__file__)).resolve()
    roots = (here, *here.parents, Path.cwd(), *Path.cwd().parents)
    seen: set[Path] = set()
    for candidate in roots:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / name / marker).exists():
            return candidate / name
    raise AssetNotFoundError(
        f"could not find {name}/{marker} above {here} or {Path.cwd()}; "
        f"set the environment override, or check that the image copies {name}/"
    )


@lru_cache(maxsize=1)
def policy_pack_root() -> Path:
    """Where policy packs live. Overridden by CIO_POLICY_PACK_ROOT."""
    if override := os.environ.get("CIO_POLICY_PACK_ROOT"):
        return Path(override)
    # The schema directory is what makes a `policy_packs` directory the real
    # one: a product folder alone could be a fixture.
    return asset_root("policy_packs", "schema", start=Path(__file__))
