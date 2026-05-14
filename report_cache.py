"""Persistent parsed-JSON cache per (ticker, doc_type).

Envelope: {"fetched_at": ISO8601, "items": [...]}.
Storage: OS tmp area, atomic writes via tmp + os.replace.
"""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

_DIR_NAME = "alfred-e-disclosure-cache"


def _dir() -> Path:
    d = Path(tempfile.gettempdir()) / _DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(ticker: str, doc_type: str) -> Path:
    safe_dt = doc_type.replace("/", "_")
    return _dir() / f"{ticker.upper()}_{safe_dt}.json"


def read(ticker: str, doc_type: str) -> Optional[dict]:
    p = _path(ticker, doc_type)
    if not p.is_file():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            env = json.load(f)
        # Sanity check.
        if not isinstance(env, dict) or "fetched_at" not in env or "items" not in env:
            return None
        return env
    except (OSError, ValueError):
        return None


def write(ticker: str, doc_type: str, items: list, fetched_at: datetime) -> None:
    p = _path(ticker, doc_type)
    env = {"fetched_at": fetched_at.isoformat(), "items": items}
    fd, tmp_path = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(env, f, ensure_ascii=False)
        os.replace(tmp_path, p)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def delete(ticker: str, doc_type: str) -> None:
    try:
        _path(ticker, doc_type).unlink()
    except FileNotFoundError:
        pass


def age(env: dict, now: datetime) -> Optional[float]:
    """Seconds since fetched_at, or None if envelope malformed."""
    try:
        then = datetime.fromisoformat(env["fetched_at"])
    except (KeyError, ValueError, TypeError):
        return None
    return (now - then).total_seconds()


def fetched_at(env: dict) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(env["fetched_at"])
    except (KeyError, ValueError, TypeError):
        return None
