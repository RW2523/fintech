"""`python -m synthetic` runs the command line.

docs/10 spells the entry point `python -m synthetic.cli`, which works and is
what the module is called. This exists so the shorter form works too: the
reset script and the Makefile both invoke the package, and a demo script that
fails on the difference between a package and its module is a demo script that
fails at the worst moment.
"""

from __future__ import annotations

import sys

from synthetic.cli import main

if __name__ == "__main__":
    sys.exit(main())
