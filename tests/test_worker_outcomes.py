"""The worker always persists an outcome envelope: ok / challenge / error."""
import urllib.error

import list_reports
import report_cache
from report_cache import Status

from conftest import TICKER


def test_challenge_writes_challenge_envelope(challenge_html):
    def fetcher(company_id, doc_page_type):
        return challenge_html

    list_reports.run_refresh(TICKER, "РСБУ", fetcher)

    env = report_cache.read(TICKER, "РСБУ")
    assert env.status is Status.CHALLENGE
    assert env.items == []
    assert env.attempts == 1
    assert env.next_retry_at is not None


def test_normal_html_writes_ok_with_items(normal_html):
    def fetcher(company_id, doc_page_type):
        return normal_html

    list_reports.run_refresh(TICKER, "РСБУ", fetcher)

    env = report_cache.read(TICKER, "РСБУ")
    assert env.status is Status.OK
    assert len(env.items) == 1
    assert env.items[0]["doc_type"] == "РСБУ"
    assert env.items[0]["period"] == "2024Q1"


def test_network_error_writes_error_envelope():
    def fetcher(company_id, doc_page_type):
        raise urllib.error.URLError("connection refused")

    list_reports.run_refresh(TICKER, "РСБУ", fetcher)

    env = report_cache.read(TICKER, "РСБУ")
    assert env.status is Status.ERROR
    assert env.items == []
    assert env.attempts == 1


def test_empty_but_successful_fetch_still_writes_ok():
    # Regression: the old "skip cache write on empty result" left the spinner
    # spinning. An empty successful parse must now persist an ok envelope.
    empty_table = '<table class="files-table"><tbody></tbody></table>'

    def fetcher(company_id, doc_page_type):
        return empty_table

    list_reports.run_refresh(TICKER, "РСБУ", fetcher)

    env = report_cache.read(TICKER, "РСБУ")
    assert env.status is Status.OK
    assert env.items == []


def test_unknown_ticker_writes_error_envelope(normal_html):
    # An unknown ticker must resolve to an error outcome, not crash the worker
    # (load_company_id raises rather than sys.exit-ing).
    list_reports.run_refresh("ZZZZ", "РСБУ", lambda c, t: normal_html)

    env = report_cache.read("ZZZZ", "РСБУ")
    assert env.status is Status.ERROR


def test_consecutive_failures_increment_attempts_and_push_retry(challenge_html):
    def fetcher(company_id, doc_page_type):
        return challenge_html

    list_reports.run_refresh(TICKER, "РСБУ", fetcher)
    first = report_cache.read(TICKER, "РСБУ")
    list_reports.run_refresh(TICKER, "РСБУ", fetcher)
    second = report_cache.read(TICKER, "РСБУ")

    assert first.attempts == 1
    assert second.attempts == 2


def test_success_after_failure_resets_to_ok(challenge_html, normal_html):
    list_reports.run_refresh(TICKER, "РСБУ", lambda c, t: challenge_html)
    list_reports.run_refresh(TICKER, "РСБУ", lambda c, t: normal_html)

    env = report_cache.read(TICKER, "РСБУ")
    assert env.status is Status.OK
    assert env.attempts == 0
    assert len(env.items) == 1
