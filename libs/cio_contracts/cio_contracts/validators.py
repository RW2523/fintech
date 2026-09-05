"""JSON Schema validation for the backbone contracts.

The Pydantic models in ``models`` are the ergonomic surface; these validators are
the normative check. They run against the generated ``bundle.json``, which is the
contract files flattened into one resolvable document, so validation never
depends on network or filesystem $ref resolution.

Runtime rules that JSON Schema cannot express (evidence ids must have been
returned by tools in this run, factor scores must equal tool output) live with
the agent runtime, not here — see docs/03 §3.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_BUNDLE_PATH = Path(__file__).with_name("bundle.json")

__all__ = [
    "ContractViolation",
    "bundle",
    "contract_names",
    "iter_errors",
    "validate",
    "validator_for",
]


class ContractViolation(ValueError):  # noqa: N818 - reads better than ...Error
    """A payload does not satisfy its contract."""

    def __init__(self, contract: str, errors: list[ValidationError]) -> None:
        self.contract = contract
        self.errors = errors
        detail = "; ".join(
            f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors[:5]
        )
        more = f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""
        super().__init__(f"{contract}: {detail}{more}")


@lru_cache(maxsize=1)
def bundle() -> dict[str, Any]:
    """The flattened contract document."""
    return json.loads(_BUNDLE_PATH.read_text())


def contract_names() -> tuple[str, ...]:
    """Every contract that can be validated, e.g. ``AgentOpinion``."""
    return tuple(sorted(bundle()["$defs"]))


@lru_cache(maxsize=64)
def validator_for(contract: str) -> Draft202012Validator:
    doc = bundle()
    if contract not in doc["$defs"]:
        raise KeyError(f"unknown contract {contract!r}; known: {', '.join(contract_names())}")
    schema = {
        "$schema": doc["$schema"],
        "$ref": f"#/$defs/{contract}",
        "$defs": doc["$defs"],
    }
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def iter_errors(payload: Any, contract: str) -> list[ValidationError]:
    """Every violation, ordered by position in the document."""
    return sorted(validator_for(contract).iter_errors(payload), key=lambda e: list(e.absolute_path))


def validate(payload: Any, contract: str) -> None:
    """Raise :class:`ContractViolation` unless ``payload`` satisfies ``contract``."""
    errors = iter_errors(payload, contract)
    if errors:
        raise ContractViolation(contract, errors)
