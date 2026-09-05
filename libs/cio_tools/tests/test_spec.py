"""T-006 — tool declarations are validated at registration time."""

from __future__ import annotations

import pytest

from cio_tools import PermittedUse, SideEffect, ToolRegistry, ToolSpec

UNDERWRITING = PermittedUse.UNDERWRITING


def test_a_tool_with_an_invalid_schema_cannot_be_registered() -> None:
    from jsonschema.exceptions import SchemaError

    registry = ToolRegistry()
    with pytest.raises(SchemaError):
        registry.register(
            ToolSpec(
                name="bad",
                version="1.0",
                handler=lambda: None,
                input_schema={"type": "not-a-type"},
                output_schema={"type": "object"},
                purpose_tags=frozenset({UNDERWRITING}),
                side_effects=SideEffect.READ,
                backing_service="test",
            )
        )


def test_registering_the_same_tool_twice_is_refused() -> None:
    registry = ToolRegistry()
    spec = ToolSpec(
        name="dup",
        version="1.0",
        handler=lambda: None,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        purpose_tags=frozenset({UNDERWRITING}),
        side_effects=SideEffect.READ,
        backing_service="test",
    )
    registry.register(spec)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec)


def test_a_tool_must_declare_a_purpose() -> None:
    with pytest.raises(ValueError, match="no purpose tags"):
        ToolSpec(
            name="x",
            version="1.0",
            handler=lambda: None,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            purpose_tags=frozenset(),
            side_effects=SideEffect.READ,
            backing_service="t",
        )
