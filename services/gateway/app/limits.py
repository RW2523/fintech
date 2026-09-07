"""Rate limiting at the front door (docs/13 §1).

The password store has no lockout: five wrong passwords in a row cost an
attacker nothing but five requests. That is fine behind a bench and not fine on
a public URL, so the limit does the work a lockout would, without the denial of
service that locking an account out invites — anybody who knows an address
could lock its owner out of their own platform.

A fixed window in Redis, keyed per caller. Fixed rather than sliding because
the failure mode of a fixed window is that somebody gets twice the allowance
across a boundary, and the failure mode of getting a sliding window subtly
wrong is that somebody gets none.

Redis being unreachable does not open the door and does not close it either:
the request is allowed and the fact is logged. A platform that stops serving
because its rate limiter is down has turned a cache outage into an outage, and
one that silently stops limiting has a control nobody can see failing.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from cio_common.errors import CioError

__all__ = ["RateLimitedError", "check", "spend"]

log = logging.getLogger(__name__)

_client: Any = None
_warned = False


class RateLimitedError(CioError):
    """Too many requests in this window."""

    status = 429
    code = "RATE_LIMITED"


def _redis() -> Any:
    global _client
    if _client is None:
        import os

        import redis.asyncio as redis

        _client = redis.from_url(
            os.environ.get("REDIS_URL", "redis://redis:6379/0"),
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
    return _client


async def spend(key: str, limit: int, *, window_seconds: int = 60) -> None:
    """Count one request against `key`, or raise when the window is full."""
    await _count(key, limit, window_seconds, increment=True)


async def check(key: str, limit: int, *, window_seconds: int = 60) -> None:
    """Raise if `key` has already spent its window, without spending any of it.

    For the work that has to happen before the thing being counted is known to
    have failed. A wrong password is counted; asking whether this caller has
    any allowance left is not, or the check would spend the allowance it exists
    to protect.
    """
    await _count(key, limit, window_seconds, increment=False)


async def _count(key: str, limit: int, window_seconds: int, *, increment: bool) -> None:
    if limit <= 0:
        return

    window = int(time.time()) // window_seconds
    counter = f"ratelimit:{key}:{window}"
    try:
        client = _redis()
        if increment:
            used = await client.incr(counter)
            if used == 1:
                # Only on the first increment, so a busy key does not keep
                # pushing its own expiry out and living forever.
                await client.expire(counter, window_seconds * 2)
        else:
            # What is about to be spent counts: `check` is asked before doing
            # the work, so the question is whether one more would be over.
            used = int(await client.get(counter) or 0) + 1
    except Exception as exc:  # pragma: no cover - exercised by stopping redis
        global _warned
        if not _warned:
            log.error("rate limiting is not working: %s", exc)
            _warned = True
        return

    if used > limit:
        raise RateLimitedError(
            f"too many requests: {limit} per {window_seconds}s",
            retry_after_seconds=window_seconds - int(time.time()) % window_seconds,
        )


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
