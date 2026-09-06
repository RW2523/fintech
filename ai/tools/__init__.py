"""Tool implementations over the service APIs (docs/06 §4).

Importing this package registers every tool. The registry is process-wide, so
the agent runtime imports it once and the grants decide what any given agent
may reach.

The longitudinal tools arrived with the engine behind them (T-060 to T-063):
`features.temporal`, `baseline.get`, `changepoints.get`, `state.get`,
`lmi.score` and `survival.get`. Until then they were deliberately absent, on
the principle that a tool returning something plausible from a service that
cannot answer is worse than no tool at all.

`self_service` arrived with the member assistant (T-071). Its tools are the
only ones in this package a member's own words can reach, which is why they
read nothing but that member's record and return no score of any kind.

`metrics` arrived with the manager copilot (T-072). Everything in it is an
aggregate, and no path through it can return a member, a case or an account:
a copilot told not to name members will eventually name one, and a copilot
whose tools cannot return one cannot.
"""

from __future__ import annotations

from ai.tools import (  # noqa: F401
    case,
    decision,
    documents,
    evidence,
    longitudinal,
    member,
    metrics,
    self_service,
)
from cio_tools.registry import registry

__all__ = ["registry", "tool_names"]


def tool_names() -> tuple[str, ...]:
    return registry.names()
