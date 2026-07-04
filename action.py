#!/usr/bin/env python3
"""Alfred action script: run the CLI verb the selected row asked for.

One file behind every connection out of the Script Filter. It branches on the
thin payload the formatter built:

- ``arm``  → ``edisclosure arm``: open a headed window for the human to solve the
  check (blocking); it refills the shared cache.
- ``force_refresh`` → ``edisclosure list --force-refresh --async``: kick a fresh
  background refresh (the "reset and retry" on a challenge/error row).
- otherwise (a report row) → ``edisclosure download --url``: take ``pdfs[0]`` and
  ``open`` it, or ``cp`` it to ``~/Downloads`` when the ⌘ payload set ``save``.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import edisclosure_bin


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, help="JSON payload from the Script Filter.")
    args = parser.parse_args(argv)

    cli = edisclosure_bin.resolve()
    if cli is None:
        print(f"{edisclosure_bin.NOT_INSTALLED_TITLE}. {edisclosure_bin.NOT_INSTALLED_SUBTITLE}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(args.payload)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Некорректный payload: {exc}", file=sys.stderr)
        return 1

    ticker = payload.get("ticker", "")
    standard = payload.get("standard", "msfo")
    try:
        if payload.get("arm"):
            return _arm(cli, ticker, standard)
        if payload.get("force_refresh"):
            return _force_refresh(cli, ticker, standard)
        return _download(cli, ticker, standard, payload.get("url", ""), bool(payload.get("save")))
    except KeyboardInterrupt:  # pragma: no cover - manual interruption
        return 130
    except Exception as exc:  # pragma: no cover - user feedback
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


def _arm(cli: str, ticker: str, standard: str) -> int:
    """Hand the terminal to a headed solve; the CLI blocks until human or timeout."""
    edisclosure_bin.debug("action", f"arm {ticker} {standard}")
    return subprocess.run([cli, "arm", ticker, "--standard", standard]).returncode


def _force_refresh(cli: str, ticker: str, standard: str) -> int:
    """Bypass the cache and spawn a fresh detached refresh, then hand back at once."""
    edisclosure_bin.debug("action", f"force-refresh {ticker} {standard}")
    subprocess.run(
        [cli, "list", ticker, "--standard", standard, "--force-refresh", "--async", "--format", "json"],
        capture_output=True,
    )
    print(f"Обновляем {ticker.upper()}…")
    return 0


def _download(cli: str, ticker: str, standard: str, url: str, save: bool) -> int:
    pdf = _staged_pdf(cli, ticker, standard, url)
    if save:
        dest = Path.home() / "Downloads" / pdf.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf, dest)
        print(f"Сохранено: {dest}")
    else:
        subprocess.run(["open", str(pdf)], check=False)
        print(f"Открыто: {pdf}")
    return 0


def _staged_pdf(cli: str, ticker: str, standard: str, url: str) -> Path:
    """Download+extract via the CLI and return the staged PDF path."""
    if not url:
        raise ValueError("payload без url")
    edisclosure_bin.debug("action", f"download {ticker} {standard} {url}")
    proc = subprocess.run(
        [cli, "download", ticker, "--standard", standard, "--url", url, "--format", "json"],
        capture_output=True,
        text=True,
    )
    if proc.stderr:
        edisclosure_bin.debug("action", proc.stderr.strip())
    try:
        out = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"CLI вернул не-JSON: {proc.stdout[:200]!r}") from exc
    pdfs = out.get("pdfs") or []
    if out.get("status") != "ok" or not pdfs:
        raise RuntimeError(f"Скачивание не удалось (статус {out.get('status')})")
    return Path(pdfs[0])


if __name__ == "__main__":
    sys.exit(main())
