"""Persistent-profile arming: the profile lock guard and human_arm's verdict.

The browser + human solve isn't unit-tested; only the wiring around it —
profile locking in ``_stealthy_fetch_html`` and the ok-only success rule in
``human_arm`` — is exercised with ``_stealthy_arm`` / ``run_refresh`` stubbed.
"""
import os
from datetime import datetime

import pytest

import list_reports
import refresh_lock
import report_cache
from report_cache import Status

from conftest import TICKER

FILES_HTML = '<table class="files-table"></table>'


def _arm_returns(html):
    return lambda url, *, headless, deadline_ms: html


# --- _stealthy_fetch_html profile guard ---------------------------------------

def test_fetch_skips_when_profile_owned_by_other_process(monkeypatch):
    monkeypatch.setattr(refresh_lock, "owner", lambda k: os.getpid() + 1)
    monkeypatch.setattr(
        list_reports, "_stealthy_arm",
        lambda *a, **k: pytest.fail("must not open the profile another PID holds"),
    )
    assert list_reports._stealthy_fetch_html("http://x") is None


def test_fetch_acquires_and_releases_when_profile_free(monkeypatch):
    monkeypatch.setattr(refresh_lock, "owner", lambda k: None)
    monkeypatch.setattr(refresh_lock, "acquire", lambda k: True)
    released = []
    monkeypatch.setattr(refresh_lock, "release", lambda k, pid: released.append(k))
    monkeypatch.setattr(list_reports, "_stealthy_arm", _arm_returns(FILES_HTML))

    assert list_reports._stealthy_fetch_html("http://x") == FILES_HTML
    assert released == [list_reports.PROFILE_LOCK_KEY]  # released only what it took


def test_fetch_is_reentrant_when_profile_owned_by_self(monkeypatch):
    # The human-arm process already holds the profile; its follow-up refresh must
    # reuse it, not re-acquire (would fail) or release someone else's hold.
    monkeypatch.setattr(refresh_lock, "owner", lambda k: os.getpid())
    monkeypatch.setattr(
        refresh_lock, "acquire", lambda k: pytest.fail("re-entrant: must not re-acquire")
    )
    monkeypatch.setattr(
        refresh_lock, "release", lambda k, pid: pytest.fail("re-entrant: must not release")
    )
    monkeypatch.setattr(list_reports, "_stealthy_arm", _arm_returns(FILES_HTML))

    assert list_reports._stealthy_fetch_html("http://x") == FILES_HTML


# --- human_arm success rule ---------------------------------------------------

def test_human_arm_false_when_challenge_not_cleared(monkeypatch, challenge_html):
    monkeypatch.setattr(list_reports, "_stealthy_arm", _arm_returns(challenge_html))
    refreshed = []
    monkeypatch.setattr(list_reports, "run_refresh", lambda t, c: refreshed.append(c))

    assert list_reports.human_arm(TICKER, "МСФО") is False
    assert refreshed == []  # an unsolved challenge never triggers a refresh


def test_human_arm_true_when_any_refresh_is_ok(monkeypatch):
    monkeypatch.setattr(list_reports, "_stealthy_arm", _arm_returns(FILES_HTML))

    def fake_refresh(ticker, ct):
        if ct == "МСФО":
            return report_cache.ok([{"period": "2024Q1"}], datetime.now())
        return report_cache.failure(Status.CHALLENGE, datetime.now(), datetime.now(), None)

    monkeypatch.setattr(list_reports, "run_refresh", fake_refresh)
    assert list_reports.human_arm(TICKER, "РСБУ") is True


def test_human_arm_false_when_cleared_but_every_refresh_fails(monkeypatch):
    # The false-success fix: reaching the table but every follow-up refresh still
    # getting a challenge is a failure, not the old harvest-succeeded true.
    monkeypatch.setattr(list_reports, "_stealthy_arm", _arm_returns(FILES_HTML))
    monkeypatch.setattr(
        list_reports, "run_refresh",
        lambda t, c: report_cache.failure(Status.CHALLENGE, datetime.now(), datetime.now(), None),
    )
    assert list_reports.human_arm(TICKER, "МСФО") is False
