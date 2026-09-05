"""Generate zod schemas and TypeScript types from contracts/schemas (T-004).

Output: apps/web/src/contracts/index.ts — generated, never hand-edited.
The web app validates every API payload against these before rendering, so a
contract change breaks the build rather than the screen (docs/09 preamble).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle import bundle  # noqa: E402

OUT = ROOT / "apps" / "web" / "src" / "contracts" / "index.ts"

HEADER = """/* Contract schemas generated from contracts/schemas.
 *
 * DO NOT EDIT. Regenerate with `make codegen`.
 * Changing a contract requires an ADR and a schema version bump (CLAUDE.md §2.2).
 */
import { z } from "zod";

"""


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def esc_regex(pattern: str) -> str:
    """Escape a JSON Schema pattern for a TypeScript regex literal."""
    return pattern.replace("/", "\\/")


def ref_name(ref: str) -> str:
    """`#/$defs/A/$defs/B` -> `B`; `#/$defs/A` -> `A`."""
    return ref.split("/")[-1]


def to_zod(node: Any, defs: dict[str, Any], depth: int = 0) -> str:
    """Render one schema node as a zod expression."""
    if not isinstance(node, dict):
        return "z.unknown()"

    if "$ref" in node:
        return f"{ref_name(node['$ref'])}Schema"

    if "const" in node:
        return f'z.literal("{esc(str(node["const"]))}")'

    if "enum" in node:
        values = [v for v in node["enum"] if v is not None]
        expr = "z.enum([" + ", ".join(f'"{esc(str(v))}"' for v in values) + "])"
        return f"{expr}.nullable()" if len(values) != len(node["enum"]) else expr

    for combinator in ("oneOf", "anyOf"):
        if combinator in node:
            arms = [to_zod(a, defs, depth + 1) for a in node[combinator]]
            arms = [a for a in arms if a != "z.null()"] or ["z.unknown()"]
            nullable = any(a.get("type") == "null" for a in node[combinator] if isinstance(a, dict))
            expr = arms[0] if len(arms) == 1 else "z.union([" + ", ".join(arms) + "])"
            return f"{expr}.nullable()" if nullable else expr

    kind = node.get("type")
    if isinstance(kind, list):
        non_null = [k for k in kind if k != "null"]
        inner = to_zod({**node, "type": non_null[0]}, defs, depth + 1) if non_null else "z.unknown()"
        return f"{inner}.nullable()" if "null" in kind else inner

    if kind == "object":
        props = node.get("properties")
        if not props:
            return "z.record(z.string(), z.unknown())"
        required = set(node.get("required", []))
        lines = []
        pad = "  " * (depth + 1)
        for key, sub in props.items():
            expr = to_zod(sub, defs, depth + 1)
            if key not in required:
                expr += ".optional()"
            lines.append(f'{pad}"{key}": {expr},')
        closing = "  " * depth
        body = "z.object({\n" + "\n".join(lines) + f"\n{closing}}})"
        return body if node.get("additionalProperties", True) is False else f"{body}.passthrough()"

    if kind == "array":
        item = to_zod(node.get("items", {}), defs, depth + 1)
        expr = f"z.array({item})"
        if "minItems" in node:
            expr += f".min({node['minItems']})"
        if "maxItems" in node:
            expr += f".max({node['maxItems']})"
        return expr

    if kind == "string":
        expr = "z.string()"
        if node.get("format") == "date-time":
            expr += ".datetime({ offset: true })"
        if "pattern" in node:
            expr += f".regex(/{esc_regex(node['pattern'])}/)"
        if "minLength" in node:
            expr += f".min({node['minLength']})"
        if "maxLength" in node:
            expr += f".max({node['maxLength']})"
        return expr

    if kind in ("number", "integer"):
        expr = "z.number()" + (".int()" if kind == "integer" else "")
        if "minimum" in node:
            expr += f".min({node['minimum']})"
        if "maximum" in node:
            expr += f".max({node['maximum']})"
        return expr

    if kind == "boolean":
        return "z.boolean()"
    if kind == "null":
        return "z.null()"
    return "z.unknown()"


def collect(defs: dict[str, Any]) -> list[tuple[str, Any]]:
    """Flatten `$defs`, including nested ones, into declaration order."""
    flat: list[tuple[str, Any]] = []
    for name, body in defs.items():
        for sub_name, sub_body in (body.get("$defs") or {}).items():
            flat.append((sub_name, sub_body))
        flat.append((name, {k: v for k, v in body.items() if k != "$defs"}))
    return flat


def dependencies(node: Any, acc: set[str]) -> set[str]:
    if isinstance(node, dict):
        if "$ref" in node:
            acc.add(ref_name(node["$ref"]))
        for value in node.values():
            dependencies(value, acc)
    elif isinstance(node, list):
        for item in node:
            dependencies(item, acc)
    return acc


def main() -> int:
    doc = bundle()
    defs = doc["$defs"]
    flat = collect(defs)
    known = {name for name, _ in flat}

    # topological order so a schema is declared before it is referenced
    remaining = dict(flat)
    emitted: list[str] = []
    order: list[str] = []
    while remaining:
        ready = [
            n for n, b in remaining.items() if not (dependencies(b, set()) & known & set(remaining)) - {n}
        ]
        if not ready:  # cycle: emit the rest as-is
            ready = list(remaining)
        for name in ready:
            order.append(name)
            remaining.pop(name)

    for name in order:
        body = dict(flat)[name]
        if name == "CioContracts":
            continue
        expr = to_zod(body, defs)
        desc = body.get("description", "")
        doc_comment = f"/** {desc.strip().splitlines()[0]} */\n" if desc else ""
        emitted.append(f"{doc_comment}export const {name}Schema = {expr};")
        emitted.append(f"export type {name} = z.infer<typeof {name}Schema>;")
        emitted.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(HEADER + "\n".join(emitted))
    print(f"  generated {OUT.relative_to(ROOT)} ({len(order) - 1} contracts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
