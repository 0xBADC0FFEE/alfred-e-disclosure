"""Bounded exponential backoff with jitter and a ceiling."""
import retry_policy


def test_backoff_grows_with_attempts():
    mid = 0.5  # neutral jitter
    assert retry_policy.backoff_seconds(1, mid) < retry_policy.backoff_seconds(2, mid)
    assert retry_policy.backoff_seconds(2, mid) < retry_policy.backoff_seconds(3, mid)


def test_backoff_is_capped_by_ceiling():
    # A huge attempt count with max jitter must not exceed ceiling * (1 + jitter).
    upper = retry_policy.CEILING_SECONDS * (1 + retry_policy.JITTER_FRACTION)
    assert retry_policy.backoff_seconds(50, 0.999999) <= upper


def test_jitter_bounds():
    base = retry_policy.BASE_SECONDS
    low = retry_policy.backoff_seconds(1, 0.0)
    high = retry_policy.backoff_seconds(1, 0.999999)
    assert low == base * (1 - retry_policy.JITTER_FRACTION)
    assert high < base * (1 + retry_policy.JITTER_FRACTION)


def test_next_retry_at_is_in_the_future():
    from datetime import datetime

    now = datetime(2024, 1, 1, 12, 0, 0)
    assert retry_policy.next_retry_at(1, now, 0.5) > now
