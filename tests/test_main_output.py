"""main() emits honest Alfred JSON for every cache state, and never spins forever."""
import json
from datetime import datetime, timedelta

import list_reports
import refresh_lock
import report_cache
from report_cache import Status

from conftest import TICKER


def _item(period, doc_type="РСБУ"):
    return {
        "doc_type_raw": "Бухгалтерская (финансовая) отчетность (РСБУ)",
        "doc_type": doc_type,
        "period_raw": f"{period} период",
        "period": period,
        "publish_date": datetime(2024, 4, 25).isoformat(),
        "url": "https://www.e-disclosure.ru/portal/files.aspx?id=99&type=3",
        "file_id": "111002",
        "size": "zip 120.10 КБ",
    }


def _run(monkeypatch, argv, *, worker_live=False):
    """Run main() with the subprocess/lock boundary stubbed; return (json, spawns)."""
    spawns = []

    def fake_spawn(key, argv_):
        spawns.append(key)
        return False  # never actually spawn a real worker in tests

    monkeypatch.setattr(refresh_lock, "spawn_refresh", fake_spawn)
    monkeypatch.setattr(refresh_lock, "is_refreshing", lambda key: worker_live)

    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        list_reports.main(argv)
    return json.loads(buf.getvalue()), spawns


def _seed_failure(status, *, next_retry_at, prev=None):
    now = datetime.now()
    env = report_cache.failure(status, now, next_retry_at, prev)
    report_cache.write(TICKER, "РСБУ", env)


def test_challenge_without_prior_cache_is_terminal_error(monkeypatch):
    _seed_failure(Status.CHALLENGE, next_retry_at=datetime.now() + timedelta(hours=1))
    data, _ = _run(monkeypatch, ["rsbu", TICKER])

    assert "rerun" not in data
    item = data["items"][0]
    assert item["valid"] is True
    assert "заблокировал" in item["title"]


def test_network_error_without_prior_cache_is_distinct_terminal_error(monkeypatch):
    _seed_failure(Status.ERROR, next_retry_at=datetime.now() + timedelta(hours=1))
    data, _ = _run(monkeypatch, ["rsbu", TICKER])

    assert "rerun" not in data
    item = data["items"][0]
    assert item["valid"] is True
    assert "Не удалось загрузить" in item["title"]


def test_failure_with_prior_ok_serves_stale_items_with_badge(monkeypatch):
    prev = report_cache.ok([_item("2024Q1")], datetime.now() - timedelta(hours=5))
    _seed_failure(
        Status.CHALLENGE,
        next_retry_at=datetime.now() + timedelta(hours=1),
        prev=prev,
    )
    data, _ = _run(monkeypatch, ["rsbu", TICKER])

    assert "rerun" not in data
    badge = data["items"][0]
    assert badge["valid"] is False
    assert "Обновление не удалось" in badge["title"]
    assert "Кэш:" in badge["subtitle"]
    # The real report row is still present and actionable.
    assert any(it.get("valid") and "2024Q1" in it["title"] for it in data["items"])


def test_cold_cache_with_live_worker_shows_spinner_with_rerun(monkeypatch):
    data, _ = _run(monkeypatch, ["rsbu", TICKER], worker_live=True)

    assert data.get("rerun") == 0.5
    assert "Обновляем" in data["items"][0]["title"]


def test_fresh_failure_within_cooldown_does_not_spawn(monkeypatch):
    _seed_failure(Status.CHALLENGE, next_retry_at=datetime.now() + timedelta(hours=1))
    _, spawns = _run(monkeypatch, ["rsbu", TICKER])
    assert spawns == []


def test_expired_cooldown_spawns_again(monkeypatch):
    _seed_failure(Status.CHALLENGE, next_retry_at=datetime.now() - timedelta(seconds=1))
    _, spawns = _run(monkeypatch, ["rsbu", TICKER])
    assert len(spawns) == 1


def test_period_filter_still_works_over_cached_items(monkeypatch):
    report_cache.write(
        TICKER,
        "РСБУ",
        report_cache.ok([_item("2024Q1"), _item("2023")], datetime.now()),
    )
    data, _ = _run(monkeypatch, ["rsbu", TICKER, "2024"])

    titles = [it["title"] for it in data["items"]]
    assert any("2024Q1" in t for t in titles)
    assert not any("2023" in t for t in titles)


def test_ticker_autocomplete_still_works(monkeypatch):
    data, _ = _run(monkeypatch, ["rsbu", "SB"])
    assert any(it.get("autocomplete") == TICKER for it in data["items"])
