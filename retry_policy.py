"""Bounded exponential backoff with jitter for failed refresh attempts.

Keeps a downed portal from being hammered: each consecutive failure pushes the
next allowed retry further out, doubling from ``BASE_SECONDS`` up to
``CEILING_SECONDS``, then spread by ±``JITTER_FRACTION`` so many workflows don't
retry in lockstep.

The randomness is injected (``rand`` in ``[0, 1)``) so the schedule is pure and
testable — callers pass ``random.random()`` in production.
"""
from datetime import datetime, timedelta

BASE_SECONDS = 30.0
CEILING_SECONDS = 1800.0  # 30 minutes
JITTER_FRACTION = 0.5


def backoff_seconds(attempts: int, rand: float) -> float:
    """Delay before the ``attempts``-th consecutive retry (1-based).

    ``rand`` in ``[0, 1)`` maps to a jitter factor in
    ``[1 - JITTER_FRACTION, 1 + JITTER_FRACTION)``.
    """
    n = max(attempts, 1)
    capped = min(BASE_SECONDS * (2 ** (n - 1)), CEILING_SECONDS)
    jitter = (1.0 - JITTER_FRACTION) + rand * (2.0 * JITTER_FRACTION)
    return capped * jitter


def next_retry_at(attempts: int, now: datetime, rand: float) -> datetime:
    return now + timedelta(seconds=backoff_seconds(attempts, rand))
