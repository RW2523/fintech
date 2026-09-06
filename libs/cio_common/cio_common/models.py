"""Finding the model package from a service (docs/07 §2.2).

Trained artifacts and the code that loads them live in `ml/`, which is not an
installed package: its package directory is its own project root, and an
editable install cannot add a path prefix. A service that scores therefore
imports it from the repository, and the layout differs between a checkout
(`<repo>/ml`) and the service image (`/srv/ml`). Rather than count directories
and be wrong in one of them, this walks up until it finds the package.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

__all__ = ["ModelPathError", "add_model_path", "model_root"]

#: What proves a directory is the model package rather than a directory that
#: happens to be called `ml`.
_MARKER = Path("ml") / "common" / "registry.py"


class ModelPathError(RuntimeError):
    """The model package is not on disk anywhere above this service."""


@lru_cache(maxsize=1)
def model_root(start: Path | None = None) -> Path:
    """The directory that holds `ml/`."""
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / _MARKER).is_file():
            return candidate
    # A checkout has it above this library; an image has it beside `app`.
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / _MARKER).is_file():
            return candidate
    raise ModelPathError(
        f"could not find {_MARKER} above {here} or {Path.cwd()}; "
        "the service image must copy `ml/` alongside `app/`"
    )


def add_model_path(start: Path | None = None) -> Path:
    """Put the model package on the import path, once."""
    root = model_root(start)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root
