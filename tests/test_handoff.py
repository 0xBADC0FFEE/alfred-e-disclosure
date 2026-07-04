"""Fetching-surface cookie header: now just the manual override.

The armed session lives in the persistent browser profile, not a harvested
cookie store, so ``_resolve_cookie_header`` only honours ``EDISCLOSURE_COOKIE``.
"""
import list_reports


def test_no_cookie_returns_none(monkeypatch):
    monkeypatch.delenv("EDISCLOSURE_COOKIE", raising=False)
    assert list_reports._resolve_cookie_header() is None


def test_manual_override_is_used(monkeypatch):
    monkeypatch.setenv("EDISCLOSURE_COOKIE", "manual=1")
    assert list_reports._resolve_cookie_header() == "manual=1"


def test_missing_browser_binary_error_is_recognised():
    # A browser-binary-missing launch error must be told apart from a real fault,
    # so the arm surfaces the "patchright install chromium" hint instead of a
    # cryptic error.
    missing = Exception("Executable doesn't exist at /path/to/chromium")
    real_fault = Exception("net::ERR_CONNECTION_REFUSED")
    assert list_reports._looks_like_missing_browser(missing) is True
    assert list_reports._looks_like_missing_browser(real_fault) is False
