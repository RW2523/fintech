"""Token accounting and per-run budgets (docs/06 §6, docs/06 §2.2).

A run has a token budget because an agent that keeps asking is a cost with no
ceiling and a latency with no ceiling. The gateway refuses a call that would
take a run past its budget rather than letting it start and truncating it: a
truncated opinion is worse than an absent one, because it looks complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Budget", "BudgetExceededError", "Ledger", "usage_ledger"]


class BudgetExceededError(RuntimeError):
    """This call would take the run past what it was allowed."""

    def __init__(self, run_id: str, spent: int, wanted: int, budget: int) -> None:
        super().__init__(
            f"run {run_id!r} has spent {spent} of {budget} tokens; a further {wanted} would exceed it"
        )
        self.run_id = run_id
        self.spent = spent
        self.wanted = wanted
        self.budget = budget


@dataclass(frozen=True, slots=True)
class Budget:
    """What a caller says a run may spend."""

    tokens: int = 0

    @property
    def unlimited(self) -> bool:
        return self.tokens <= 0


@dataclass
class Ledger:
    """What each run has spent so far, in this process."""

    spent: dict[str, int] = field(default_factory=dict)
    calls: dict[str, int] = field(default_factory=dict)

    def check(self, run_id: str, budget: Budget, wanted: int) -> None:
        if budget.unlimited or not run_id:
            return
        spent = self.spent.get(run_id, 0)
        if spent + wanted > budget.tokens:
            raise BudgetExceededError(run_id, spent, wanted, budget.tokens)

    def record(self, run_id: str, tokens: int) -> int:
        if not run_id:
            return 0
        self.spent[run_id] = self.spent.get(run_id, 0) + tokens
        self.calls[run_id] = self.calls.get(run_id, 0) + 1
        return self.spent[run_id]

    def report(self, run_id: str) -> dict[str, int]:
        return {"tokens": self.spent.get(run_id, 0), "calls": self.calls.get(run_id, 0)}

    def clear(self) -> None:
        self.spent.clear()
        self.calls.clear()


_LEDGER = Ledger()


def usage_ledger() -> Ledger:
    return _LEDGER
