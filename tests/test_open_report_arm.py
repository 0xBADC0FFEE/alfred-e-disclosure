"""open_report's arm action: payload contract + lock handling.

The headed solve itself (browser + human) isn't unit-tested; only the wiring
around it — payload parsing, the refresh-lock hold, and the browser-missing
hint — is exercised here with ``human_arm`` stubbed.
"""
import json

import list_reports
import open_report
import refresh_lock

from conftest import TICKER


def _payload(**extra):
    data = {"ticker": TICKER, "doc_type": "МСФО"}
    data.update(extra)
    args = open_report.parse_args(["--payload", json.dumps(data)])
    return open_report.load_payload(args)


def test_arm_payload_requires_only_ticker_and_doctype():
    payload = _payload(arm=True)
    assert payload.arm is True
    assert payload.ticker == TICKER
    assert payload.doc_type == "МСФО"


def test_run_arm_holds_and_releases_lock(monkeypatch):
    key = list_reports.refresh_key(TICKER, "МСФО")
    held_during = {}

    def fake_human_arm(ticker, compact_type):
        held_during["value"] = refresh_lock.is_refreshing(key)
        return True

    monkeypatch.setattr(list_reports, "human_arm", fake_human_arm)

    rc = open_report.run_arm(_payload(arm=True))

    assert rc == 0
    assert held_during["value"] is True  # lock held while solving
    assert refresh_lock.is_refreshing(key) is False  # released after


def test_run_arm_reports_missing_browser(monkeypatch):
    def boom(ticker, compact_type):
        raise list_reports.BrowserMissingError("scrapling install")

    monkeypatch.setattr(list_reports, "human_arm", boom)

    rc = open_report.run_arm(_payload(arm=True))
    assert rc == 1


def test_run_arm_skips_when_worker_already_live(monkeypatch):
    key = list_reports.refresh_key(TICKER, "МСФО")
    monkeypatch.setattr(refresh_lock, "is_refreshing", lambda k: True)
    called = []
    monkeypatch.setattr(list_reports, "human_arm", lambda t, c: called.append(1) or True)

    rc = open_report.run_arm(_payload(arm=True))

    assert rc == 0
    assert called == []  # didn't run the solve while a refresh was live
