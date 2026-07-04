"""Armed ServicePipe cookie store.

Holds the cookies harvested from a browser once a challenge is cleared, so the
`curl_cffi` fetching surface can reuse them (the handoff). A bare ``name -> value``
map, stored as JSON under :func:`cache_dir.root`, atomic writes via tmp +
os.replace — mirroring :mod:`report_cache`.

There is deliberately no TTL: armed cookies have no known lifetime, so none is
guessed. When they die, the next fetch is simply a fresh challenge, which
re-surfaces the solve row (self-healing expiry — see CONTEXT.md).
"""
from pathlib import Path
from typing import Dict
import json
import os
import tempfile

import cache_dir

_FILE_NAME = "armed_cookies.json"


def _path() -> Path:
    return cache_dir.root() / _FILE_NAME


def load() -> Dict[str, str]:
    """Return the stored cookie map, or ``{}`` when absent/unreadable."""
    p = _path()
    if not p.is_file():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def save(cookies: Dict[str, str]) -> None:
    """Overwrite the store with ``cookies`` (a bare name -> value map)."""
    p = _path()
    fd, tmp_path = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dict(cookies), f, ensure_ascii=False)
        os.replace(tmp_path, p)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def clear() -> None:
    try:
        _path().unlink()
    except FileNotFoundError:
        pass
