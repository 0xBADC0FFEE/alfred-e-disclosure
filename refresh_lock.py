"""Per-key PID lockfile + detached background spawn.

A lockfile pointing to a dead PID is treated as stale and auto-cleared.
Spawned children survive the parent via `start_new_session=True` + redirected
stdio.
"""
from __future__ import annotations

import errno
import os
import subprocess
from pathlib import Path
from typing import Sequence

import cache_dir


def _path(key: str) -> Path:
    safe = key.replace("/", "_")
    return cache_dir.root() / f"{safe}.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def _read_pid(p: Path) -> int | None:
    try:
        text = p.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def is_refreshing(key: str) -> bool:
    p = _path(key)
    if not p.is_file():
        return False
    pid = _read_pid(p)
    if pid is None or not _pid_alive(pid):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return False
    return True


def acquire(key: str, pid: int | None = None) -> bool:
    """Claim the lock for an in-process holder (e.g. the human-arm solve).

    Returns True if the key was free and is now held by ``pid`` (default: this
    process). Mirrors ``spawn_refresh``'s no-double-spawn guard so a background
    worker won't run while a human is solving the captcha.
    """
    if is_refreshing(key):
        return False
    if pid is None:
        pid = os.getpid()
    try:
        _path(key).write_text(str(pid), encoding="utf-8")
    except OSError:
        return False
    return True


def spawn_refresh(key: str, argv: Sequence[str]) -> bool:
    """Spawn detached worker if none is live. Returns True if spawned."""
    if is_refreshing(key):
        return False
    p = _path(key)
    devnull = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=devnull,
            stdout=devnull,
            stderr=devnull,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        return False
    try:
        p.write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass
    return True


def release(key: str, pid: int | None = None) -> None:
    """Remove lockfile if it belongs to `pid` (or unconditionally if None)."""
    p = _path(key)
    if pid is None:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return
    current = _read_pid(p)
    if current == pid:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
