"""Make this service's own ``app`` package importable when pytest runs here.

Each service keeps its code in ``app/`` (CLAUDE.md §3), so the package name is
shared across services. Services are therefore tested one directory at a time
(``make test``) rather than in a single collection pass.
"""

import sys
from pathlib import Path

_ROOT = str(Path(__file__).parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
