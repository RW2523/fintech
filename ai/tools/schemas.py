"""Small JSON Schemas the tools declare (docs/06 §4).

Every tool states what it takes and what it returns. The input schema is what
stops an agent asking for a member it has no scope over; the output schema is
what stops a service change reaching an agent unnoticed.

They are deliberately loose about the shape of records and strict about the
identifiers, because the identifiers are what carry scope and evidence.
"""

from __future__ import annotations

from typing import Any

__all__ = ["ARRAY_OF_OBJECTS", "OBJECT", "inputs"]

#: Identifier patterns (CLAUDE.md §7).
CASE_ID = {"type": "string", "pattern": r"^case_[0-9A-HJKMNP-TV-Z]{26}$"}
SNAPSHOT_ID = {"type": "string", "pattern": r"^snap_[0-9A-HJKMNP-TV-Z]{26}$"}
MEMBER_ID = {"type": "string", "pattern": r"^M-[0-9]{6}$"}
DOCUMENT_ID = {"type": "string", "minLength": 3, "maxLength": 64}
ANY_ID = {"type": "string", "minLength": 3, "maxLength": 64}

OBJECT: dict[str, Any] = {"type": "object"}
ARRAY_OF_OBJECTS: dict[str, Any] = {"type": "array", "items": {"type": "object"}}


def inputs(**properties: Any) -> dict[str, Any]:
    """An input schema requiring every named property and nothing else.

    Optional inputs are declared by wrapping the schema in `optional(...)`.
    """
    required = [name for name, schema in properties.items() if not schema.get("_optional")]
    cleaned = {
        name: {k: v for k, v in schema.items() if k != "_optional"} for name, schema in properties.items()
    }
    return {"type": "object", "additionalProperties": False, "required": required, "properties": cleaned}


def optional(schema: dict[str, Any]) -> dict[str, Any]:
    return {**schema, "_optional": True}
