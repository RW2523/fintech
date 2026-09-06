"""The tool registry: the single door between agents and data (docs/06 §4).

``registry.call(tool, args, ctx)`` is the only supported call path. It checks the
grant, charges the run's budget, scopes the call to the case and identity,
validates input and output against the tool's schemas, masks fields outside the
caller's purpose, mints EvidenceRefs, and records an invocation for audit.
"""

from __future__ import annotations

import contextlib
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jsonschema import Draft202012Validator

from cio_common.errors import ValidationFailed
from cio_tools.evidence import attach_evidence, build_evidence, extract_evidence_ids
from cio_tools.grants import CallBudget, GrantRegistry, ToolDenied
from cio_tools.masking import apply_field_purposes, masked_field_count
from cio_tools.spec import EvidenceSpec, PermittedUse, SideEffect, ToolSpec

__all__ = ["Invocation", "ToolContext", "ToolRegistry", "registry", "tool"]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Who is calling, on whose behalf, for what, and within which budget."""

    agent_id: str
    run_id: str
    purpose: PermittedUse
    principal: str = "system"
    case_id: str | None = None
    member_id: str | None = None
    budget: CallBudget = field(default_factory=CallBudget)


@dataclass(frozen=True, slots=True)
class Invocation:
    """The audit record written for every call, granted or denied."""

    call_id: str
    tool: str
    version: str
    agent_id: str
    run_id: str
    principal: str
    purpose: str
    case_id: str | None
    member_id: str | None
    ok: bool
    seconds: float
    at: datetime
    evidence_ids: tuple[str, ...] = ()
    masked_paths: tuple[str, ...] = ()
    masked_values: int = 0
    denial_reason: str | None = None
    error: str | None = None


class ToolRegistry:
    """Holds tool specs and grants, and mediates every call."""

    def __init__(self, grants: GrantRegistry | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self.grants = grants or GrantRegistry()
        self.invocations: list[Invocation] = []

    # -- registration ------------------------------------------------------
    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} is already registered")
        Draft202012Validator.check_schema(spec.input_schema)
        Draft202012Validator.check_schema(spec.output_schema)
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"unknown tool {name!r}; registered: {', '.join(sorted(self._tools))}") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def clear(self) -> None:
        self._tools.clear()
        self.invocations.clear()
        self.grants = GrantRegistry()

    def with_grants(self, grants: GrantRegistry) -> ToolRegistry:
        """The same tools under a different set of grants.

        The specs are shared, not copied: a tool is one implementation with one
        schema, and two registries holding different copies of it could drift.
        What differs is who may call what, which is the whole question. The
        returned registry keeps its own invocation log, so a caller can see
        exactly what it did without reading everyone else's calls.
        """
        narrowed = ToolRegistry(grants)
        narrowed._tools = self._tools
        return narrowed

    # -- invocation --------------------------------------------------------
    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> Any:
        started = time.perf_counter()
        call_id = f"tc_{len(self.invocations) + 1:06d}"
        spec: ToolSpec | None = None

        try:
            spec = self.get(name)
            self._authorise(spec, ctx)
            self._validate(spec.input_schema, args, what=f"{name} input")
            scoped = self._scope_args(spec, args, ctx)

            result = spec.handler(**scoped)
            if inspect.isawaitable(result):
                result = await result

            self._validate(spec.output_schema, result, what=f"{name} output")

            masked, masked_paths = apply_field_purposes(result, spec.field_purposes, ctx.purpose)
            refs = self._evidence(spec, masked, ctx)
            final = attach_evidence(masked, refs)

        except ToolDenied as denied:
            self._record(
                call_id, name, spec, ctx, started, ok=False, denial_reason=denied.reason, error=str(denied)
            )
            raise
        except Exception as exc:
            self._record(call_id, name, spec, ctx, started, ok=False, error=str(exc))
            raise

        self._record(
            call_id,
            name,
            spec,
            ctx,
            started,
            ok=True,
            evidence_ids=tuple(sorted(extract_evidence_ids(final))),
            masked_paths=tuple(masked_paths),
            masked_values=masked_field_count(final),
        )
        return final

    # -- internals ---------------------------------------------------------
    def _authorise(self, spec: ToolSpec, ctx: ToolContext) -> None:
        grant = self.grants.require(ctx.agent_id, spec.name)

        if not spec.permits(ctx.purpose):
            raise ToolDenied(
                f"tool {spec.name!r} is not permitted for purpose {ctx.purpose}",
                agent_id=ctx.agent_id,
                tool=spec.name,
                reason="PURPOSE_NOT_PERMITTED",
            )

        if spec.side_effects is SideEffect.WRITE_PROPOSAL and ctx.purpose is PermittedUse.ANALYTICS:
            raise ToolDenied(
                f"tool {spec.name!r} may not write during an analytics call",
                agent_id=ctx.agent_id,
                tool=spec.name,
                reason="WRITE_IN_READ_CONTEXT",
            )

        ctx.budget.charge(ctx.agent_id, spec.name, grant.max_calls)

    def _scope_args(self, spec: ToolSpec, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        """Pin case and member arguments to the run's scope.

        An agent cannot widen its reach by passing another case's id.
        """
        scoped = dict(args)
        for key, bound in (("case_id", ctx.case_id), ("member_id", ctx.member_id)):
            if bound is None or key not in spec.input_schema.get("properties", {}):
                continue
            supplied = scoped.get(key)
            if supplied is not None and supplied != bound:
                raise ToolDenied(
                    f"tool {spec.name!r} was called with {key}={supplied!r} outside the run scope {bound!r}",
                    agent_id=ctx.agent_id,
                    tool=spec.name,
                    reason="OUT_OF_SCOPE",
                )
            scoped[key] = bound
        return scoped

    @staticmethod
    def _validate(schema: dict[str, Any], payload: Any, *, what: str) -> None:
        errors = sorted(
            Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.absolute_path)
        )
        if errors:
            detail = "; ".join(
                f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors[:3]
            )
            raise ValidationFailed(f"{what} does not match its schema: {detail}")

    @staticmethod
    def _evidence(spec: ToolSpec, payload: Any, ctx: ToolContext) -> list[dict[str, Any]]:
        if spec.evidence is None:
            return []
        return build_evidence(
            payload,
            spec.evidence,
            permitted_uses=spec.purpose_tags,
            version=spec.version,
        )

    def _record(
        self,
        call_id: str,
        name: str,
        spec: ToolSpec | None,
        ctx: ToolContext,
        started: float,
        *,
        ok: bool,
        evidence_ids: tuple[str, ...] = (),
        masked_paths: tuple[str, ...] = (),
        masked_values: int = 0,
        denial_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        self.invocations.append(
            Invocation(
                call_id=call_id,
                tool=name,
                version=spec.version if spec else "-",
                agent_id=ctx.agent_id,
                run_id=ctx.run_id,
                principal=ctx.principal,
                purpose=str(ctx.purpose),
                case_id=ctx.case_id,
                member_id=ctx.member_id,
                ok=ok,
                seconds=round(time.perf_counter() - started, 6),
                at=datetime.now(UTC),
                evidence_ids=evidence_ids,
                masked_paths=masked_paths,
                masked_values=masked_values,
                denial_reason=denial_reason,
                error=error,
            )
        )
        # Denials are the interesting series. A tool that is refused often is
        # either an agent reaching past its grants or a grant that is wrong,
        # and both need somebody to look; a dashboard that only counted
        # successful calls would show neither.
        _count_call(
            tool=name,
            agent_id=ctx.agent_id,
            outcome="ok" if ok else ("denied" if denial_reason else "error"),
        )


def _count_call(*, tool: str, agent_id: str, outcome: str) -> None:
    """Record one call for Prometheus, and never let telemetry break a call.

    Imported lazily so this library does not require the metrics package to be
    installed: the tool registry runs in tests and scripts that have no
    interest in a scrape endpoint.
    """
    try:
        from cio_common.metrics import tool_calls
    except Exception:  # pragma: no cover - metrics are optional here
        return
    with contextlib.suppress(Exception):
        tool_calls.labels(tool=tool, agent_id=agent_id, outcome=outcome).inc()


#: The process-wide registry. Services register their tools into this at import.
registry = ToolRegistry()


def tool(
    name: str,
    *,
    version: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    purpose_tags: set[PermittedUse] | frozenset[PermittedUse],
    side_effects: SideEffect,
    backing_service: str,
    evidence: EvidenceSpec | None = None,
    field_purposes: dict[str, frozenset[PermittedUse]] | None = None,
    into: ToolRegistry | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a tool and register it.

    Usage::

        @tool("risk.score", version="1.0", input_schema=..., output_schema=...,
              purpose_tags={PermittedUse.UNDERWRITING},
              side_effects=SideEffect.READ, backing_service="risk")
        async def risk_score(snapshot_id: str) -> dict: ...
    """

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        (into or registry).register(
            ToolSpec(
                name=name,
                version=version,
                handler=func,
                input_schema=input_schema,
                output_schema=output_schema,
                purpose_tags=frozenset(purpose_tags),
                side_effects=side_effects,
                backing_service=backing_service,
                evidence=evidence,
                field_purposes=field_purposes or {},
                description=(func.__doc__ or "").strip().splitlines()[0] if func.__doc__ else "",
            )
        )
        return func

    return decorate
