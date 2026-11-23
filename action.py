#!/usr/bin/env python3
"""Alfred action script: run the CLI verb the selected row asked for.

One file behind every connection out of the Script Filter. It branches on the
thin payload the formatter built:

- ``arm``  → ``edisclosure arm``: open a headed window for the human to solve the
  check (blocking); it refills the shared cache.
- ``force_refresh`` → ``edisclosure list --force-refresh`` behind a detached
  process: the blocking verb is the one that escalates to a headed captcha solve,
  and detaching keeps Alfred from waiting on it.
- otherwise (a report row) → ``edisclosure download --url``: take every path in
  ``pdfs`` and ``open`` them, or ``cp`` them to ``~/Downloads`` when the ⌘ payload
  set ``save``. An archive may hold several independent documents.
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
        return _fail(f"{edisclosure_bin.NOT_INSTALLED_TITLE}. {edisclosure_bin.NOT_INSTALLED_SUBTITLE}")

    try:
        payload = json.loads(args.payload)
    except (json.JSONDecodeError, ValueError) as exc:
        return _fail(f"Некорректный payload: {exc}")

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
        return _fail(f"Ошибка: {exc}")


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _arm(cli: str, ticker: str, standard: str) -> int:
    """Hand the terminal to a headed solve; the CLI blocks until human or timeout."""
    edisclosure_bin.debug("action", f"arm {ticker} {standard}")
    proc = subprocess.run(
        [cli, "arm", ticker, "--standard", standard, "--format", "json"],
        capture_output=True,
        text=True,
    )
    if proc.stderr:
        edisclosure_bin.debug("action", proc.stderr.strip())
    if proc.returncode == 0:
        return 0
    return _fail("Проверка не пройдена — попробуйте ещё раз.")


def _force_refresh(cli: str, ticker: str, standard: str) -> int:
    """Bypass the cache and hand the refresh to a detached blocking run of the CLI.

    The blocking verb is the one that escalates to a headed human solve when the
    portal demands a captcha; the async worker stops at the wall. Detaching keeps
    Alfred snappy — the browser window, when it appears, is the progress signal."""
    edisclosure_bin.debug("action", f"force-refresh {ticker} {standard}")
    subprocess.Popen(
        [cli, "list", ticker, "--standard", standard, "--force-refresh", "--format", "json"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return 0


def _download(cli: str, ticker: str, standard: str, url: str, save: bool) -> int:
    pdfs = _staged_pdfs(cli, ticker, standard, url)
    if save:
        _save_all(pdfs)
    else:
        _open_all(pdfs)
    return 0


def _staged_pdfs(cli: str, ticker: str, standard: str, url: str) -> List[Path]:
    """Download+extract via the CLI and return every staged PDF path.

    Always a list, sorted by the CLI as the archive ordered its members — a
    single-PDF report yields a one-element list.
    """
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
    return [Path(p) for p in pdfs]


def _open_all(pdfs: List[Path]) -> None:
    """Open every document, leaving the first one frontmost.

    macOS stacks the last opened window on top, so the list is walked backwards.
    A failing ``open`` does not stop the rest.
    """
    for pdf in reversed(pdfs):
        subprocess.run(["open", str(pdf)], check=False)


def _save_all(pdfs: List[Path]) -> None:
    """Copy every document into ``~/Downloads``.

    Raises ``RuntimeError`` naming the file the first refusal fell on; whatever
    was copied before that stays on disk.
    """
    downloads = Path.home() / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    for pdf in pdfs:
        try:
            shutil.copy2(pdf, downloads / pdf.name)
        except OSError as exc:
            raise RuntimeError(f"Не удалось сохранить {pdf.name}: {exc}") from exc


if __name__ == "__main__":
    sys.exit(main())
