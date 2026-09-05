"""Bundle contracts/schemas/*.json into one document with local $defs.

The contract files are standalone and cross-reference each other by relative
filename. Both code generators and the runtime validator want a single
resolvable document, so this flattens them into `#/$defs/<Title>` form.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts" / "schemas"

# contracts/schemas/<name>.<major>.<minor>.json
FILENAME = re.compile(r"^(?P<name>[a-z_]+)\.(?P<major>\d+)\.(?P<minor>\d+)\.json$")


def schema_files() -> list[Path]:
    return sorted(p for p in SCHEMAS.glob("*.json") if FILENAME.match(p.name))


def title_of(path: Path) -> str:
    return json.loads(path.read_text())["title"]


def _rewrite(node: Any, by_file: dict[str, str]) -> Any:
    """Point every relative file $ref at the bundled #/$defs entry."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and not value.startswith("#"):
                file_part, _, fragment = value.partition("#")
                target = by_file[file_part]
                out[key] = f"#/$defs/{target}" if not fragment else f"#/$defs/{target}{fragment}"
            else:
                out[key] = _rewrite(value, by_file)
        return out
    if isinstance(node, list):
        return [_rewrite(item, by_file) for item in node]
    return node


def bundle() -> dict[str, Any]:
    files = schema_files()
    by_file = {p.name: title_of(p) for p in files}

    defs: dict[str, Any] = {}
    for path in files:
        raw = json.loads(path.read_text())
        title = raw["title"]
        body = {k: v for k, v in raw.items() if k not in ("$schema", "$id")}
        defs[title] = _rewrite(body, by_file)

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://cio.local/schemas/bundle/1.0",
        "title": "CioContracts",
        "description": "All backbone contracts bundled for code generation and validation.",
        "type": "object",
        "$defs": defs,
    }


def schema_id_map() -> dict[str, Path]:
    """$id -> file, for the runtime validator's reference registry."""
    return {json.loads(p.read_text())["$id"]: p for p in schema_files()}


if __name__ == "__main__":
    print(json.dumps(bundle(), indent=2))
