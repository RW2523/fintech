"""The circuit breaker in front of each route (docs/06 §6, docs/13 §3).

When a provider is failing, the useful thing is to stop asking. Every request
that waits for a timeout holds a committee run open, and the platform's rule is
to fail safe: a case with no model opinion routes to a person, which is a much
better outcome than a case that waits.

The breaker is per route, because one provider being down says nothing about
another.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

__all__ = ["Breaker", "CircuitOpenError", "breaker_for", "reset_breakers"]

#: docs/06 §6 — three consecutive failures open the circuit for thirty seconds.
FAILURE_THRESHOLD = 3
OPEN_SECONDS = 30.0


class CircuitOpenError(RuntimeError):
    """The route is being left alone until its provider recovers."""

    def __init__(self, route: str, seconds: float) -> None:
        super().__init__(
            f"route {route!r} is unavailable for another {seconds:.0f}s after repeated provider failures"
        )
        self.route = route
        self.retry_after = seconds


@dataclass
class Breaker:
    """One route's failure state."""

    route: str
    failures: int = 0
    opened_at: float | None = None
    #: Kept for the health endpoint, so an operator can see what went wrong
    #: without reading logs. The message only, never the prompt.
    last_error: str | None = None
    _clock: object = field(default=time.monotonic, repr=False)

    def _now(self) -> float:
        return float(self._clock())  # type: ignore[operator]

    @property
    def retry_after(self) -> float:
        if self.opened_at is None:
            return 0.0
        return max(0.0, OPEN_SECONDS - (self._now() - self.opened_at))

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if self.retry_after > 0:
            return True
        # The window has passed. The next call is allowed through and decides
        # whether the route is healthy again.
        self.opened_at = None
        self.failures = 0
        return False

    def check(self) -> None:
        if self.is_open:
            raise CircuitOpenError(self.route, self.retry_after)

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.last_error = None

    def record_failure(self, error: str) -> None:
        self.failures += 1
        self.last_error = error
        if self.failures >= FAILURE_THRESHOLD:
            self.opened_at = self._now()

    def as_status(self) -> dict[str, object]:
        return {
            "failures": self.failures,
            "open": self.is_open,
            "retry_after_seconds": round(self.retry_after, 1),
            "last_error": self.last_error,
        }


_BREAKERS: dict[str, Breaker] = {}


def breaker_for(route: str) -> Breaker:
    if route not in _BREAKERS:
        _BREAKERS[route] = Breaker(route=route)
    return _BREAKERS[route]


def reset_breakers() -> None:
    _BREAKERS.clear()
