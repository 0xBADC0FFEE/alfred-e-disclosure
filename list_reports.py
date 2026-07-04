#!/usr/bin/env python3
"""Alfred Script Filter formatter: turn ``edisclosure`` JSON into Script Filter items.

The workflow no longer fetches anything itself — it shells out to the globally
installed ``edisclosure`` CLI and reshapes its stdout. This file owns the one
branch the CLI can't make for us: **autocomplete a ticker prefix** vs **list a
company's reports**. That choice happens *before* the CLI call (a different verb
either way), so the branching and the subprocess edge live here, wrapped around a
pure rendering core (:func:`render`, :func:`render_autocomplete`).

CLI signature: ``list_reports.py <standard> --alfred-query "<query>"`` where
``<standard>`` ∈ ``msfo|rsbu|annual``.

Field-name bridge (the one silent break point on a future CLI rename): a listing
item carries ``file_url``; the action payload calls it ``url``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from typing import List, Optional, Tuple

import edisclosure_bin
import relative_time_ru

# The listing envelope's `status` field — the CLI's wire values, switched on in render().
STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_CHALLENGE = "challenge"
STATUS_ERROR = "error"

# Compact standard → the label shown in a report row's title.
_STANDARD_RU = {"msfo": "МСФО", "rsbu": "РСБУ", "annual": "Годовой"}

# How long the placeholder / stale rows wait before Alfred re-queries us (seconds).
# The detached CLI worker refreshes in the background; we re-poll until it lands ``ok``.
_RERUN_SECONDS = 0.5

_AUTOCOMPLETE_LIMIT = 10


# --- pure rendering core ---------------------------------------------------

def render(
    envelope: dict, standard: str, now: datetime, period_filter: Optional[str] = None
) -> dict:
    """Map a listing envelope to Script Filter JSON, dispatching on its status.

    ``ok`` → rows, no rerun. ``stale`` → rows (or an "updating" placeholder) plus
    a rerun so Alfred re-polls the background worker. ``challenge`` → a "solve the
    check" row that arms on ↵, saved rows beneath it, no rerun (a human is needed).
    ``error`` → a "reset and retry" row, saved rows beneath it.
    """
    status = envelope.get("status", STATUS_ERROR)
    ticker = (envelope.get("ticker") or "").upper()
    fetched_at = _parse_dt(envelope.get("fetched_at"))
    age_label = relative_time_ru.format(now, fetched_at) if fetched_at else None
    rows = _report_rows(envelope.get("items", []), standard, ticker, age_label, period_filter)

    if status == STATUS_OK:
        return {"items": rows or [_no_reports_item(ticker, period_filter)]}

    if status == STATUS_STALE:
        return {"items": rows or [_updating_item(ticker)], "rerun": _RERUN_SECONDS}

    banner = (
        _challenge_item(ticker, standard)
        if status == STATUS_CHALLENGE
        else _error_item(ticker, standard)
    )
    return {"items": [banner, *rows]}


def render_autocomplete(tickers: List[dict], query: str) -> dict:
    """Suggest companies whose ticker starts with the typed prefix."""
    prefix = query.strip().upper()
    matches = [t for t in tickers if t.get("ticker", "").upper().startswith(prefix)]
    items = [
        {
            "title": f"{t['ticker']} — {t.get('name', '')}".rstrip(" —"),
            "subtitle": t.get("sector", ""),
            "arg": f"{t['ticker']} ",  # trailing space: keep typing the period
            "autocomplete": t["ticker"],
            "valid": False,
            "match": f"{t['ticker']} {t.get('name', '')}".lower(),
        }
        for t in matches[:_AUTOCOMPLETE_LIMIT]
    ]
    if not items:
        items.append(
            {
                "title": "Нет подходящих тикеров",
                "subtitle": "Попробуйте другой префикс",
                "valid": False,
            }
        )
    return {"items": items}


def _payload(ticker: str, standard: str, **extra) -> str:
    """The thin JSON contract handed to the action script."""
    return json.dumps({"ticker": ticker, "standard": standard, **extra}, ensure_ascii=False)


def _report_rows(
    items: List[dict],
    standard: str,
    ticker: str,
    age_label: Optional[str],
    period_filter: Optional[str],
) -> List[dict]:
    prefix = (period_filter or "").strip().upper()
    rows = []
    for it in items:
        period = it.get("period", "")
        if prefix and not period.upper().startswith(prefix):
            continue
        rows.append(_report_row(it, standard, ticker, period, age_label))
    return rows


def _report_row(
    item: dict, standard: str, ticker: str, period: str, age_label: Optional[str]
) -> dict:
    label = _STANDARD_RU.get(standard, standard.upper())
    file_id = item.get("file_id", "")
    title = f"{label} - {period}" + (f" • {file_id}" if file_id else "")
    subtitle = " · ".join(
        p for p in (_fmt_date(item.get("publish_date", "")), item.get("size", ""), item.get("type", "")) if p
    )
    refresh_subtitle = f"↻ Обновить · Кэш: {age_label}" if age_label else "↻ Обновить"
    return {
        "title": title,
        "subtitle": subtitle,
        "arg": _payload(ticker, standard, url=item.get("file_url", "")),
        "valid": True,
        "mods": {
            "cmd": {
                "arg": _payload(ticker, standard, url=item.get("file_url", ""), save=True),
                "subtitle": "Сохранить в ~/Downloads",
                "valid": True,
            },
            "alt": {
                "arg": _payload(ticker, standard, force_refresh=True),
                "subtitle": refresh_subtitle,
                "valid": True,
            },
        },
    }


def _challenge_item(ticker: str, standard: str) -> dict:
    return {
        "title": f"Портал заблокировал запрос — {ticker}",
        "subtitle": "e-disclosure.ru показал проверку. ↵ — пройти проверку в браузере.",
        "arg": _payload(ticker, standard, arm=True),
        "valid": True,
    }


def _error_item(ticker: str, standard: str) -> dict:
    retry = _payload(ticker, standard, force_refresh=True)
    return {
        "title": f"Не удалось загрузить отчёты — {ticker}",
        "subtitle": "↵ или ⌥↵ — сбросить кэш и попробовать снова.",
        "arg": retry,
        "valid": True,
        "mods": {"alt": {"arg": retry, "subtitle": "↻ Сбросить кэш и попробовать снова", "valid": True}},
    }


def _updating_item(ticker: str) -> dict:
    return {
        "title": f"Обновляем для {ticker}…",
        "subtitle": "Загружаем отчёты с e-disclosure.ru",
        "valid": False,
    }


def _no_reports_item(ticker: str, period_filter: Optional[str]) -> dict:
    detail = "Нет отчётов по фильтру" if (period_filter or "").strip() else "Отчёты не найдены"
    return {"title": detail, "subtitle": f"{ticker} — другой период или команда.", "valid": False}


def _fmt_date(iso: str) -> str:
    dt = _parse_dt(iso)
    return dt.strftime("%d.%m.%Y") if dt else iso


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --- subprocess edge -------------------------------------------------------

def parse_query(text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Split ``"TICKER [PERIOD]"`` into its parts."""
    parts = (text or "").strip().split()
    if not parts:
        return None, None
    return parts[0], (parts[1] if len(parts) > 1 else None)


def _run_cli(cli: str, args: List[str]) -> Optional[list | dict]:
    """Call the CLI and parse its JSON stdout; ``None`` on failure."""
    cmd = [cli, *args]
    edisclosure_bin.debug("list_reports", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        edisclosure_bin.debug("list_reports", f"spawn failed: {exc}")
        return None
    if proc.stderr:
        edisclosure_bin.debug("list_reports", proc.stderr.strip())
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        edisclosure_bin.debug("list_reports", f"non-JSON stdout: {proc.stdout[:200]!r}")
        return None


def _is_complete_ticker(ticker: str, tickers: list) -> bool:
    """Whether the typed token is already an exact ticker (skip autocomplete)."""
    return any(r.get("ticker", "").upper() == ticker.upper() for r in tickers)


def _emit(data: dict) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("standard", choices=("msfo", "rsbu", "annual"))
    parser.add_argument("--alfred-query", dest="query", default="")
    args = parser.parse_args(argv)

    cli = edisclosure_bin.resolve()
    if cli is None:
        _emit(edisclosure_bin.missing_items())
        return

    ticker, period = parse_query(args.query)
    if not ticker:
        _emit({"items": [{"title": "Введите тикер", "subtitle": f"Использование: {args.standard} TICKER [PERIOD]", "valid": False}]})
        return

    # Autocomplete a bare prefix that isn't already an exact ticker; anything with
    # a period or a trailing space is a listing. The ticker dump is fetched once
    # and serves both the completeness check and the suggestions.
    query_has_space = " " in (args.query or "").strip()
    if not period and not query_has_space:
        tickers = _run_cli(cli, ["tickers", "--format", "json"]) or []
        if not _is_complete_ticker(ticker, tickers):
            _emit(render_autocomplete(tickers, ticker))
            return

    envelope = _run_cli(
        cli, ["list", ticker, "--standard", args.standard, "--async", "--format", "json"]
    )
    if envelope is None:
        _emit(render({"status": STATUS_ERROR, "ticker": ticker}, args.standard, datetime.now()))
        return
    _emit(render(envelope, args.standard, datetime.now(), period))


if __name__ == "__main__":
    main()
