"""Tool implementations over the service APIs (docs/06 §4).

Importing this package registers every tool. The registry is process-wide, so
the agent runtime imports it once and the grants decide what any given agent
may reach.

The longitudinal tools arrived with the engine behind them (T-060 to T-063):
`features.temporal`, `baseline.get`, `changepoints.get`, `state.get`,
`lmi.score` and `survival.get`. Until then they were deliberately absent, on
the principle that a tool returning something plausible from a service that
cannot answer is worse than no tool at all.
"""

from __future__ import annotations

from ai.tools import decision, documents, evidence, longitudinal, member  # noqa: F401
from cio_tools.registry import registry

__all__ = ["registry", "tool_names"]


def tool_names() -> tuple[str, ...]:
    return registry.names()
