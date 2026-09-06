"""Month arithmetic over the history window (docs/10 §8)."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

__all__ = ["cycle_label", "due_date_for", "month_end", "month_start", "months_between"]


def month_start(anchor: date, month_index: int) -> date:
    """The first day of ``month_index`` (1-based) from ``anchor``."""
    total = anchor.month - 1 + (month_index - 1)
    return date(anchor.year + total // 12, total % 12 + 1, 1)


def month_end(anchor: date, month_index: int) -> date:
    start = month_start(anchor, month_index)
    return date(start.year, start.month, monthrange(start.year, start.month)[1])


def due_date_for(anchor: date, month_index: int, due_day: int) -> date:
    """The due date in a month, clamped to that month's length."""
    start = month_start(anchor, month_index)
    last = monthrange(start.year, start.month)[1]
    return date(start.year, start.month, min(due_day, last))


def cycle_label(anchor: date, month_index: int) -> str:
    """`2025-03`, the label a deduction cycle is filed under."""
    start = month_start(anchor, month_index)
    return f"{start.year:04d}-{start.month:02d}"


def months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def add_days(value: date, days: float) -> date:
    return value + timedelta(days=round(days))
