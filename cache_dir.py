"""Shared root directory for the parsed-report cache and refresh lockfiles.

Both live under one directory so a single ``EDISCLOSURE_CACHE_DIR`` override
relocates the whole cache — which is what tests use to stay off the real
OS tmp area.
"""
import os
import tempfile
from pathlib import Path

ENV_ROOT = "EDISCLOSURE_CACHE_DIR"
_DIR_NAME = "alfred-e-disclosure-cache"


def root() -> Path:
    override = os.getenv(ENV_ROOT)
    base = Path(override) if override else Path(tempfile.gettempdir()) / _DIR_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base
