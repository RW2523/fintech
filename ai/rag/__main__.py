"""`python -m ai.rag` runs the retrieval command line.

Same reason as `synthetic/__main__.py`: the reset script invokes the package,
and the difference between a package and the module inside it is not something
a demo should discover at half past six in the evening.
"""

from __future__ import annotations

import sys

from ai.rag.cli import main

if __name__ == "__main__":
    sys.exit(main())
