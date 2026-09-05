"""Generate Pydantic v2 models from contracts/schemas (T-004).

Output: libs/cio_contracts/cio_contracts/models.py — generated, never hand-edited
(CLAUDE.md §2.2). Run via `make codegen`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle import bundle

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "schemas"
OUT = ROOT / "libs" / "cio_contracts" / "cio_contracts" / "models.py"

HEADER = '''"""Contract models generated from contracts/schemas.

DO NOT EDIT. Regenerate with `make codegen`.
Changing a contract requires an ADR and a schema version bump (CLAUDE.md §2.2).
"""
'''


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # The contract files cross-reference each other by relative filename; the
    # generator needs one resolvable document, so bundle first.
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(bundle(), fh, indent=2)
        bundled = Path(fh.name)

    cmd = [
        sys.executable,
        "-m",
        "datamodel_code_generator",
        "--input",
        str(bundled),
        "--input-file-type",
        "jsonschema",
        "--output",
        str(OUT),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.12",
        "--use-standard-collections",
        "--use-union-operator",
        "--use-schema-description",
        "--use-field-description",
        "--field-constraints",
        "--enum-field-as-literal",
        "all",
        "--collapse-root-models",
        "--use-title-as-name",
        "--disable-timestamp",
        "--extra-fields",
        "forbid",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        bundled.unlink(missing_ok=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        return proc.returncode

    # ship the bundle beside the models so the runtime validator needs no
    # $ref resolution at import time
    bundle_out = OUT.parent / "bundle.json"
    bundle_out.write_text(json.dumps(bundle(), indent=2) + "\n")
    print(f"  generated {bundle_out.relative_to(ROOT)}")

    body = OUT.read_text()
    if not body.startswith('"""'):
        OUT.write_text(HEADER + "\n" + body)
    print(f"  generated {OUT.relative_to(ROOT)} ({len(OUT.read_text().splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
