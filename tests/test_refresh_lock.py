"""refresh_lock.owner: the live PID holder, self vs other, stale-clear."""
import os

import refresh_lock

from list_reports import PROFILE_LOCK_KEY


def test_owner_none_when_no_lock():
    assert refresh_lock.owner(PROFILE_LOCK_KEY) is None


def test_owner_returns_self_after_acquire():
    refresh_lock.acquire(PROFILE_LOCK_KEY)
    try:
        assert refresh_lock.owner(PROFILE_LOCK_KEY) == os.getpid()
    finally:
        refresh_lock.release(PROFILE_LOCK_KEY, os.getpid())


def test_owner_clears_stale_dead_pid():
    # A lockfile pointing at a dead PID is stale: owner clears it and reads None,
    # so a crashed solve never wedges the profile forever.
    p = refresh_lock._path(PROFILE_LOCK_KEY)
    p.write_text("999999999", encoding="utf-8")
    assert refresh_lock.owner(PROFILE_LOCK_KEY) is None
    assert not p.is_file()
