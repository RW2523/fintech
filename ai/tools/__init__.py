"""Tool implementations over the service APIs (docs/06 §4).

Importing this package registers every tool. The registry is process-wide, so
the agent runtime imports it once and the grants decide what any given agent
may reach.

Four tools the spec lists are not here yet, because the services behind them
do not exist: `features.temporal`, `baseline.get`, `changepoints.get`,
`state.get`, `lmi.score` and `survival.get` are the longitudinal engine's, and
arrive with it. A tool that returned something plausible from a service that
cannot answer would be worse than its absence.
"""

from __future__ import annotations

from ai.tools import decision, documents, evidence, member  # noqa: F401
from cio_tools.registry import registry

__all__ = ["registry", "tool_names"]


def tool_names() -> tuple[str, ...]:
    return registry.names()
