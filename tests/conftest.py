"""Shared test wiring: repo on the path, an isolated cache dir, page fixtures.

Tests exercise only external behaviour — the Alfred JSON `main()` emits and the
envelope the worker writes — never internal call sequences.
"""
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

# A ticker that exists in the shipped tickers.csv, so load_company_id resolves.
TICKER = "SBER"


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Point cache + lockfiles at a throwaway dir for every test."""
    monkeypatch.setenv("EDISCLOSURE_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


@pytest.fixture
def challenge_html() -> str:
    return (_FIXTURES / "challenge.html").read_text(encoding="utf-8")


@pytest.fixture
def spinner_challenge_html() -> str:
    return (_FIXTURES / "challenge_spinner.html").read_text(encoding="utf-8")


@pytest.fixture
def normal_html() -> str:
    return (_FIXTURES / "files_normal.html").read_text(encoding="utf-8")
