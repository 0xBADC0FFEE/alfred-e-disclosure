"""Handoff: the fetching surface's cookie header prefers a manual override,
then falls back to the armed store (harvested from a solved challenge)."""
import armed_cookies
import list_reports


def test_no_cookies_returns_none(monkeypatch):
    monkeypatch.delenv("EDISCLOSURE_COOKIE", raising=False)
    assert list_reports._resolve_cookie_header() is None


def test_armed_cookies_used_when_no_override(monkeypatch):
    monkeypatch.delenv("EDISCLOSURE_COOKIE", raising=False)
    armed_cookies.save({"spsc": "a", "spid": "b"})
    assert list_reports._resolve_cookie_header() == "spsc=a; spid=b"


def test_manual_override_wins_over_armed(monkeypatch):
    armed_cookies.save({"spsc": "armed"})
    monkeypatch.setenv("EDISCLOSURE_COOKIE", "manual=1")
    assert list_reports._resolve_cookie_header() == "manual=1"


def test_missing_browser_binary_error_is_recognised():
    # A browser-binary-missing launch error must be told apart from a real fault,
    # so the arm surfaces the "scrapling install" hint instead of a cryptic error.
    missing = Exception("Executable doesn't exist at /path/to/camoufox")
    real_fault = Exception("net::ERR_CONNECTION_REFUSED")
    assert list_reports._looks_like_missing_browser(missing) is True
    assert list_reports._looks_like_missing_browser(real_fault) is False
