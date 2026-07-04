"""Armed-cookie store: a bare name->value map, round-trips, no TTL."""
import json

import armed_cookies
import cache_dir


def test_load_missing_returns_empty():
    assert armed_cookies.load() == {}


def test_roundtrip_save_load():
    cookies = {"spsc": "a", "spjs": "b", "spid": "c"}
    armed_cookies.save(cookies)
    assert armed_cookies.load() == cookies


def test_save_overwrites_previous():
    armed_cookies.save({"spsc": "1", "spjs": "old"})
    armed_cookies.save({"spsc": "2", "spid": "9"})
    assert armed_cookies.load() == {"spsc": "2", "spid": "9"}


def test_no_ttl_or_metadata_persisted():
    # A dead cookie must just re-surface the challenge (self-healing expiry):
    # the store carries the bare cookie map, never an expiry to reason about.
    armed_cookies.save({"spsc": "x"})
    raw = json.loads((cache_dir.root() / "armed_cookies.json").read_text(encoding="utf-8"))
    assert raw == {"spsc": "x"}


def test_clear_removes_store():
    armed_cookies.save({"spsc": "x"})
    armed_cookies.clear()
    assert armed_cookies.load() == {}
    armed_cookies.clear()  # idempotent, no error when already gone
