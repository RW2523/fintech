"""Purpose-based field masking on tool output (docs/06 §4, docs/13 §2).

A tool may return fields that are legitimate for one purpose and not another:
a collections call has no business reading underwriting-only attributes. Fields
outside the caller's purpose are replaced with ``MASK`` rather than removed, so
the agent can see that something exists without seeing its value.
"""

from __future__ import annotations

from typing import Any

from cio_tools.spec import PermittedUse

__all__ = ["MASK", "apply_field_purposes", "mask_paths", "masked_field_count"]

MASK = "***"


def _walk(node: Any, parts: list[str], apply: Any) -> Any:
    """Rewrite ``node`` at the dotted path ``parts``. ``[]`` means every item."""
    if not parts:
        return apply(node)

    head, rest = parts[0], parts[1:]

    if head == "[]":
        if isinstance(node, list):
            return [_walk(item, rest, apply) for item in node]
        return node

    if isinstance(node, dict) and head in node:
        return {**node, head: _walk(node[head], rest, apply)}

    return node


def mask_paths(payload: Any, paths: list[str]) -> Any:
    """Replace every value at the given dotted paths with :data:`MASK`."""
    result = payload
    for path in paths:
        result = _walk(result, path.split("."), lambda _value: MASK)
    return result


def apply_field_purposes(
    payload: Any,
    field_purposes: dict[str, frozenset[PermittedUse]],
    purpose: PermittedUse,
) -> tuple[Any, list[str]]:
    """Mask every field whose declared purposes exclude ``purpose``.

    Returns the masked payload and the paths that were masked, which the
    invocation record keeps for audit.
    """
    disallowed = [path for path, allowed in field_purposes.items() if purpose not in allowed]
    if not disallowed:
        return payload, []
    return mask_paths(payload, disallowed), sorted(disallowed)


def masked_field_count(payload: Any) -> int:
    """How many values in ``payload`` are masked. Logged per call (docs/13 §2)."""
    if payload == MASK:
        return 1
    if isinstance(payload, dict):
        return sum(masked_field_count(v) for v in payload.values())
    if isinstance(payload, list):
        return sum(masked_field_count(v) for v in payload)
    return 0
