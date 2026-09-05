"""Grants and call budgets: which agent may call which tool, how often.

The registry checks the grant before every call and audits every denial
(docs/06 §4). An agent that has not been granted a tool cannot reach it, whatever
its prompt says.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

__all__ = ["BudgetExceeded", "CallBudget", "Grant", "GrantRegistry", "ToolDenied"]


class ToolDenied(PermissionError):
    """The caller may not invoke this tool for this purpose or this case."""

    def __init__(self, message: str, *, agent_id: str, tool: str, reason: str) -> None:
        super().__init__(message)
        self.agent_id = agent_id
        self.tool = tool
        self.reason = reason


class BudgetExceeded(ToolDenied):
    """The run has spent its allowance of calls for this tool."""

    def __init__(self, message: str, *, agent_id: str, tool: str, limit: int) -> None:
        super().__init__(message, agent_id=agent_id, tool=tool, reason="BUDGET_EXCEEDED")
        self.limit = limit


@dataclass(frozen=True, slots=True)
class Grant:
    """One line of an agent's ``tools.yaml`` (docs/06 §1)."""

    agent_id: str
    tool: str
    max_calls: int = 1

    def __post_init__(self) -> None:
        if self.max_calls < 1:
            raise ValueError(f"grant {self.agent_id}->{self.tool} must allow at least one call")


class GrantRegistry:
    """The set of grants in force, keyed by agent."""

    def __init__(self, grants: list[Grant] | None = None) -> None:
        self._by_agent: dict[str, dict[str, Grant]] = defaultdict(dict)
        for grant in grants or []:
            self.add(grant)

    def add(self, grant: Grant) -> None:
        self._by_agent[grant.agent_id][grant.tool] = grant

    def grant(self, agent_id: str, tool: str) -> Grant | None:
        return self._by_agent.get(agent_id, {}).get(tool)

    def tools_for(self, agent_id: str) -> frozenset[str]:
        return frozenset(self._by_agent.get(agent_id, {}))

    def require(self, agent_id: str, tool: str) -> Grant:
        found = self.grant(agent_id, tool)
        if found is None:
            raise ToolDenied(
                f"agent {agent_id!r} has no grant for tool {tool!r}",
                agent_id=agent_id,
                tool=tool,
                reason="NO_GRANT",
            )
        return found


@dataclass
class CallBudget:
    """Per-run call accounting (docs/06 §1: max = sum of the agent's max_calls)."""

    max_total: int | None = None
    _per_tool: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    _total: int = 0

    def spent(self, agent_id: str, tool: str) -> int:
        return self._per_tool[(agent_id, tool)]

    @property
    def total(self) -> int:
        return self._total

    def charge(self, agent_id: str, tool: str, limit: int) -> None:
        """Record one call, or raise when the allowance is gone."""
        if self._per_tool[(agent_id, tool)] >= limit:
            raise BudgetExceeded(
                f"agent {agent_id!r} exhausted its {limit}-call budget for {tool!r}",
                agent_id=agent_id,
                tool=tool,
                limit=limit,
            )
        if self.max_total is not None and self._total >= self.max_total:
            raise BudgetExceeded(
                f"run exhausted its {self.max_total}-call budget",
                agent_id=agent_id,
                tool=tool,
                limit=self.max_total,
            )
        self._per_tool[(agent_id, tool)] += 1
        self._total += 1
