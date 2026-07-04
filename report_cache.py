"""Persistent parsed-report cache per (ticker, doc_type).

The envelope carries an explicit outcome ``status`` so callers can tell the
three states apart without guessing:

- **no envelope on disk** → never fetched ("loading").
- ``status == "ok"`` → ``items`` are the freshly fetched listing.
- ``status in {"challenge", "error"}`` → the last fetch failed. ``items`` /
  ``fetched_at`` still hold the most recent *successful* listing (possibly
  empty / ``None`` if we never succeeded) so ``main()`` can serve stale data,
  while ``attempts`` / ``next_retry_at`` drive the bounded-retry backoff.

Making the status explicit keeps "loading" (no envelope) distinct from
"failed" (envelope with a non-ok status) — an illegal blend of the two is not
representable.

Storage: JSON under :func:`cache_dir.root`, atomic writes via tmp + os.replace.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional
import json
import os
import tempfile

import cache_dir


class Status(str, Enum):
    OK = "ok"
    CHALLENGE = "challenge"
    ERROR = "error"


@dataclass(frozen=True)
class Envelope:
    status: Status
    items: list
    fetched_at: Optional[datetime]  # when ``items`` were last fetched ok
    attempts: int = 0  # consecutive failures since the last ok (0 when ok)
    next_retry_at: Optional[datetime] = None  # earliest allowed retry (None when ok)

    @property
    def is_ok(self) -> bool:
        return self.status is Status.OK

    @property
    def has_items(self) -> bool:
        return bool(self.items)


def ok(items: list, fetched_at: datetime) -> Envelope:
    return Envelope(status=Status.OK, items=items, fetched_at=fetched_at)


def failure(
    status: Status,
    now: datetime,
    next_retry_at: datetime,
    prev: Optional[Envelope],
) -> Envelope:
    """Failure envelope that carries forward the last good listing from ``prev``."""
    if status is Status.OK:
        raise ValueError("failure() requires a non-ok status")
    prev_attempts = prev.attempts if prev is not None else 0
    return Envelope(
        status=status,
        items=prev.items if prev is not None else [],
        fetched_at=prev.fetched_at if prev is not None else None,
        attempts=prev_attempts + 1,
        next_retry_at=next_retry_at,
    )


def _path(ticker: str, doc_type: str) -> Path:
    safe_dt = doc_type.replace("/", "_")
    return cache_dir.root() / f"{ticker.upper()}_{safe_dt}.json"


def _parse_dt(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _from_dict(raw: dict) -> Optional[Envelope]:
    if not isinstance(raw, dict):
        return None
    items = raw.get("items")
    if not isinstance(items, list):
        return None
    fetched_at = _parse_dt(raw.get("fetched_at"))

    status_raw = raw.get("status")
    if status_raw is None:
        # Legacy envelope ({fetched_at, items}) predates the status field:
        # a stored listing means it was fetched ok.
        if fetched_at is None:
            return None
        return Envelope(status=Status.OK, items=items, fetched_at=fetched_at)

    try:
        status = Status(status_raw)
    except ValueError:
        return None
    if status is Status.OK and fetched_at is None:
        return None

    attempts = raw.get("attempts", 0)
    if not isinstance(attempts, int) or attempts < 0:
        attempts = 0
    return Envelope(
        status=status,
        items=items,
        fetched_at=fetched_at,
        attempts=attempts,
        next_retry_at=_parse_dt(raw.get("next_retry_at")),
    )


def _to_dict(env: Envelope) -> dict:
    return {
        "status": env.status.value,
        "items": env.items,
        "fetched_at": env.fetched_at.isoformat() if env.fetched_at else None,
        "attempts": env.attempts,
        "next_retry_at": env.next_retry_at.isoformat() if env.next_retry_at else None,
    }


def read(ticker: str, doc_type: str) -> Optional[Envelope]:
    p = _path(ticker, doc_type)
    if not p.is_file():
        return None
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    return _from_dict(raw)


def write(ticker: str, doc_type: str, env: Envelope) -> None:
    p = _path(ticker, doc_type)
    fd, tmp_path = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_to_dict(env), f, ensure_ascii=False)
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
