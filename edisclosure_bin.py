"""Locate the ``edisclosure`` CLI even under Alfred's truncated PATH.

Alfred launches Script Filters with a minimal ``PATH`` that usually omits
``~/.local/bin`` (where ``uv tool install`` drops its shim), so ``shutil.which``
alone misses it. Both the listing formatter and the action script call the CLI by
absolute path via :func:`resolve`, and share the one "not installed" guard so a
missing CLI reads as a clear hint instead of an empty result.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

_FALLBACK = Path.home() / ".local" / "bin" / "edisclosure"

NOT_INSTALLED_TITLE = "edisclosure CLI не установлен"
NOT_INSTALLED_SUBTITLE = "Установите: uv tool install edisclosure"

_DEBUG = os.getenv("EDISCLOSURE_DEBUG", "").lower() in {"1", "true", "yes", "on"}


def resolve() -> Optional[str]:
    """Absolute path to the CLI, or ``None`` when it is not installed."""
    found = shutil.which("edisclosure")
    if found:
        return found
    if os.access(_FALLBACK, os.X_OK):
        return str(_FALLBACK)
    return None


def missing_items() -> dict:
    """Script Filter payload shown when the CLI is absent."""
    return {
        "items": [
            {
                "title": NOT_INSTALLED_TITLE,
                "subtitle": NOT_INSTALLED_SUBTITLE,
                "valid": False,
            }
        ]
    }


def debug(tag: str, message: str) -> None:
    """Emit a wrapper diagnostic to stderr when ``EDISCLOSURE_DEBUG`` is set."""
    if _DEBUG:
        print(f"[{tag}] {message}", file=sys.stderr)
