"""Making a contract schema something a grammar engine can compile.

vLLM and its peers turn a JSON Schema into a decoding grammar, and they
implement a subset of the vocabulary. The AgentOpinion contract uses
`propertyNames` to say that `factor_scores` is keyed by Decision Factor
family, and no grammar engine implements it: the request comes back
`Unimplemented keys: ["propertyNames"]`.

The rewrite is lossless in the direction that matters. `propertyNames` with an
enum says exactly the same thing as explicit properties plus
`additionalProperties: false`, and the runtime validates the real contract
afterwards regardless, so anything the simplification let through is caught
before the opinion is returned.
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = ["UNSUPPORTED", "prune_defs", "simplify"]

#: Keywords a grammar engine cannot express. `propertyNames` is rewritten into
#: explicit properties where it carries an enum; the rest are constraints the
#: runtime rechecks itself, so dropping them from the decoding grammar costs
#: nothing and keeps the request compilable.
UNSUPPORTED = (
    "propertyNames",
    "patternProperties",
    "dependentSchemas",
    "if",
    "then",
    "else",
    "not",
    "unevaluatedProperties",
    "contentMediaType",
    "contentEncoding",
)


def _expand_property_names(node: dict[str, Any]) -> dict[str, Any]:
    """Turn `propertyNames: {enum: [...]}` into the properties it describes."""
    names = node.get("propertyNames") or {}
    allowed = names.get("enum") if isinstance(names, dict) else None
    value_schema = node.get("additionalProperties")
    if allowed and isinstance(value_schema, dict):
        node["properties"] = {
            **(node.get("properties") or {}),
            **{name: copy.deepcopy(value_schema) for name in allowed},
        }
        node["additionalProperties"] = False
    return node


def simplify(schema: Any) -> Any:
    """A schema with the same meaning, in the subset a grammar engine takes."""
    if isinstance(schema, list):
        return [simplify(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    node = dict(schema)
    if "propertyNames" in node:
        node = _expand_property_names(node)
    for keyword in UNSUPPORTED:
        node.pop(keyword, None)
    return {key: simplify(value) for key, value in node.items()}


def _referenced(node: Any, found: set[str]) -> None:
    """Every `$defs` name reachable from a schema."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            # A pointer may reach inside a definition, as
            # `#/$defs/Common/$defs/Unit` does. The definition to keep is the
            # first segment; taking the whole tail dropped `Common` and left a
            # dangling pointer the grammar engine rejected.
            found.add(ref.split("/")[2])
        for key, value in node.items():
            if key != "$defs":
                _referenced(value, found)
    elif isinstance(node, list):
        for item in node:
            _referenced(item, found)


def prune_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop the definitions this schema does not reach.

    The published contract carries twelve definitions and an agent's output
    uses a handful. Sending all of them costs tokens on every call and makes
    the decoding grammar larger than it needs to be: measured, the untrimmed
    schema pushed a five-agent round past twenty-eight thousand tokens.
    """
    defs: dict[str, Any] = schema.get("$defs") or {}
    if not defs:
        return schema

    reachable: set[str] = set()
    _referenced({k: v for k, v in schema.items() if k != "$defs"}, reachable)
    # A definition may reference another, so follow until it settles.
    frontier = set(reachable)
    while frontier:
        name = frontier.pop()
        nested: set[str] = set()
        _referenced(defs.get(name, {}), nested)
        new = nested - reachable
        reachable |= new
        frontier |= new

    return {**schema, "$defs": {name: defs[name] for name in sorted(reachable) if name in defs}}
