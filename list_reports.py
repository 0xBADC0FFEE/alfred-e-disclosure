#!/usr/bin/env python3
import sys
import os

# Add bundled dependencies to path
script_dir = os.path.dirname(os.path.abspath(__file__))
lib_dir = os.path.join(script_dir, 'lib')
if os.path.exists(lib_dir):
    sys.path.insert(0, lib_dir)

import argparse
import csv
import gzip
import json
import threading
import urllib.request
import urllib.response
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import refresh_lock
import relative_time_ru
import report_cache

REFRESH_AGE_THRESHOLD_SECONDS = 3600

try:
    from curl_cffi import requests as cf_requests  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional dependency
    cf_requests = None

_cf_local = threading.local()  # per-thread cf_requests.Session
_warned_no_cf = False
_warned_stealthy_missing = False


BASE_URL = "https://www.e-disclosure.ru/portal/"


def _is_challenge_page(html: str) -> bool:
    """ServicePipe Cybert anti-bot challenge — spinner page instead of real HTML.

    Real e-disclosure pages embed ServicePipe tracking JS too, so detect by the
    spinner div and absence of the actual files-table marker.
    """
    if not html:
        return True
    if "files-table" in html:
        return False
    lowered = html.lower()
    return "id_spinner" in lowered or len(html) < 5000


def _log(msg: str) -> None:
    print(f"[list_reports] {msg}", file=sys.stderr, flush=True)


def _stealthy_fetch_html(url: str) -> Optional[str]:
    """Fallback fetch through Patchright-backed StealthyFetcher (lazy import)."""
    global _warned_stealthy_missing
    _log(f"stealthy import…")
    try:
        from scrapling.fetchers import StealthyFetcher  # type: ignore[import-not-found]
    except ImportError:
        if not _warned_stealthy_missing:
            _log("scrapling NOT installed — install: pip install 'scrapling[fetchers]' && scrapling install")
            _warned_stealthy_missing = True
        return None
    _log(f"stealthy fetch {url}")
    page = StealthyFetcher.fetch(
        url,
        headless=True,
        network_idle=True,
        wait=12000,
        humanize=True,
        spoof_fingerprint=True,
        timeout=90000,
    )
    html = getattr(page, "html_content", None) or getattr(page, "body", None) or ""
    _log(f"stealthy done ({len(html)} bytes)")
    return html


@dataclass
class Document:
    doc_type_raw: str
    doc_type: str  # compact: "МСФО" or "РСБУ"
    period_raw: str
    period: str
    publish_date: datetime
    url: str


class FilesTableParser(HTMLParser):
    """Parse the files table on files.aspx into structured rows."""

    def __init__(self) -> None:
        super().__init__()
        self.in_files_table = False
        self.in_row = False
        self.current_cell_index = -1
        self.current_row: dict = {}
        self.rows: List[dict] = []
        self.in_file_cell = False

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        if tag == "table" and "files-table" in attrs_dict.get("class", ""):
            # Start of the main files table.
            self.in_files_table = True
        elif tag == "tr" and self.in_files_table:
            self.in_row = True
            self.current_cell_index = -1
            self.current_row = {
                "type": "",
                "period": "",
                "publish_date": "",
                "file_url": "",
            }
            self.in_file_cell = False
        elif tag == "td" and self.in_row:
            self.current_cell_index += 1
            if self.current_cell_index == 5:
                self.in_file_cell = True
        elif (
            tag == "a"
            and self.in_row
            and self.in_file_cell
            and "file-link" in attrs_dict.get("class", "")
        ):
            href = attrs_dict.get("href")
            if href:
                self.current_row["file_url"] = href

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self.in_row:
            self.in_row = False
            self.in_file_cell = False
            if self.current_row.get("file_url"):
                self.rows.append(self.current_row)
        elif tag == "table" and self.in_files_table:
            self.in_files_table = False

    def handle_data(self, data: str) -> None:
        if not self.in_row:
            return
        text = data.strip()
        if not text:
            return
        if self.current_cell_index == 1:
            # Тип документа
            if self.current_row["type"]:
                self.current_row["type"] += " "
            self.current_row["type"] += text
        elif self.current_cell_index == 2:
            # Отчетный период
            if self.current_row["period"]:
                self.current_row["period"] += " "
            self.current_row["period"] += text
        elif self.current_cell_index == 4:
            # Дата размещения (second date column)
            if self.current_row["publish_date"]:
                self.current_row["publish_date"] += " "
            self.current_row["publish_date"] += text


def normalize_doc_type(raw: str) -> Optional[str]:
    """Map verbose 'Тип документа' to compact 'МСФО' or 'РСБУ'."""
    if not raw:
        return None
    up = raw.upper()
    if "МСФО" in up:
        return "МСФО"
    # Treat everything else as РСБУ
    return "РСБУ"


def normalize_period(text: str) -> str:
    """
    Convert 'Отчетный период' to YYYYPN or YYYY.

    Examples:
    - '2025, 9 месяцев' -> '2025M9'
    - '2025, 6 месяцев' or '2025, полугодие' -> '2025H1'
    - 'I квартал 2024 года' -> '2024Q1'
    - '2024' -> '2024'
    """
    import re

    text = text.strip()
    if not text:
        return text

    m_year = re.search(r"(19|20)\d{2}", text)
    if not m_year:
        return text
    year = m_year.group(0)

    lowered = text.lower()

    # Full year if no explicit period markers.
    if not any(
        marker in lowered
        for marker in ("месяц", "месяцев", "квартал", "полугод", "полугодие")
    ):
        return year

    # Quarter: roman or arabic.
    roman_map = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
    m_q_roman = re.search(r"\b([ivx]+)\s*кварт", lowered)
    if m_q_roman:
        rom = m_q_roman.group(1)
        q = roman_map.get(rom)
        if q:
            return f"{year}Q{q}"
    m_q_arabic = re.search(r"\b(\d+)\s*кварт", lowered)
    if m_q_arabic:
        q = int(m_q_arabic.group(1))
        return f"{year}Q{q}"

    # Half-year.
    if "полугод" in lowered or "6 месяцев" in lowered:
        return f"{year}H1"

    # N months.
    m_months = re.search(r"(\d+)\s*месяц", lowered)
    if m_months:
        n = int(m_months.group(1))
        return f"{year}M{n}"

    return year


def parse_publish_date(text: str) -> Optional[datetime]:
    text = text.strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def decode_http_body(resp: urllib.response.addinfourl) -> str:
    """
    Decode an HTTP response supporting gzip/deflate compression.
    """
    raw = resp.read()
    encoding = (resp.headers.get("Content-Encoding") or "").lower()

    try:
        if "gzip" in encoding:
            raw = gzip.decompress(raw)
        elif "deflate" in encoding:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except OSError as exc:
        print(f"Failed to decompress response body: {exc}", file=sys.stderr)

    charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="ignore")


def load_company_id(ticker: str) -> str:
    ticker_up = ticker.upper()
    csv_path = Path(__file__).resolve().parent / "tickers.csv"
    if not csv_path.is_file():
        print(f"Ticker mapping file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Ticker", "").upper() == ticker_up:
                company_id = row.get("EDID")
                if company_id:
                    return company_id
    print(f"Unknown ticker: {ticker}", file=sys.stderr)
    sys.exit(1)


def load_tickers() -> List[Tuple[str, str, str]]:
    """Load all tickers, their EDIDs, and company names from CSV."""
    csv_path = Path(__file__).resolve().parent / "tickers.csv"
    if not csv_path.is_file():
        print(f"Ticker mapping file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    tickers = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get("Ticker", "").strip()
            company_id = row.get("EDID", "").strip()
            company_name = row.get("Name", "").strip()
            if ticker and company_id:
                tickers.append((ticker.upper(), company_id, company_name))
    return tickers


def search_tickers(query: str, all_tickers: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    """Search tickers that start with the query (case insensitive)."""
    if not query:
        return []
    query_up = query.upper()
    return [ticker for ticker in all_tickers if ticker[0].startswith(query_up)]


def fetch_table_html(company_id: str, doc_page_type: int) -> str:
    url = f"{BASE_URL}files.aspx?id={company_id}&type={doc_page_type}"
    # e-disclosure may return anti-bot/captcha pages to non-browser clients.
    # We try to mimic a browser as much as possible and, optionally, allow the
    # user to pass real browser cookies via the EDISCLOSURE_COOKIE env var.
    headers = {
        # Copied to closely match a real Chrome request.
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/142.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "sec-ch-ua": (
            '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"'
        ),
        "sec-ch-ua-mobile": "?0",
        'sec-ch-ua-platform': '"macOS"',
    }
    cookie = os.getenv("EDISCLOSURE_COOKIE")
    if cookie:
        headers["Cookie"] = cookie

    html: Optional[str] = None
    _log(f"type={doc_page_type} GET {url}")
    if cf_requests is not None:
        session = getattr(_cf_local, "session", None)
        if session is None:
            impersonate = os.getenv("EDISCLOSURE_IMPERSONATE", "chrome124")
            session = cf_requests.Session(impersonate=impersonate)
            _cf_local.session = session
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        html = response.text
        _log(f"type={doc_page_type} curl_cffi {response.status_code} ({len(html)} bytes)")
    else:
        global _warned_no_cf
        if not _warned_no_cf:
            print(
                "curl_cffi is not installed; falling back to urllib which may trigger anti-bot pages.\n"
                "Install it via `python3 -m pip install --user --break-system-packages curl_cffi` "
                "for Chrome-like TLS fingerprinting.",
                file=sys.stderr,
            )
            _warned_no_cf = True

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            html = decode_http_body(resp)

    if _is_challenge_page(html):
        _log(f"type={doc_page_type} challenge detected → stealthy fallback")
        stealthy_html = _stealthy_fetch_html(url)
        if stealthy_html and not _is_challenge_page(stealthy_html):
            return stealthy_html
        _log(f"type={doc_page_type} stealthy fallback failed; returning original html")
    return html


def collect_documents(company_id: str, wanted_compact_type: str) -> List[Document]:
    """
    Collect documents of a certain compact type ('МСФО' or 'РСБУ').

    For 'МСФО' we look both at type=4 and type=3 pages because
    sometimes IFRS reports appear on the RSBU page.
    For 'РСБУ' we use only type=3.
    """
    docs: List[Document] = []

    if wanted_compact_type == "МСФО":
        page_types = (4, 3)
    else:
        page_types = (3,)

    _log(f"collect id={company_id} types={page_types}")
    if len(page_types) > 1:
        with ThreadPoolExecutor(max_workers=len(page_types)) as executor:
            htmls = list(executor.map(lambda t: fetch_table_html(company_id, t), page_types))
    else:
        htmls = [fetch_table_html(company_id, page_types[0])]

    for page_type, html in zip(page_types, htmls):
        parser = FilesTableParser()
        parser.feed(html)
        _log(f"parsed type={page_type}: {len(parser.rows)} rows")

        for row in parser.rows:
            raw_type = row.get("type", "").strip()
            compact_type = normalize_doc_type(raw_type)
            if compact_type != wanted_compact_type:
                continue

            period_raw = row.get("period", "").strip()
            publish_raw = row.get("publish_date", "").strip()
            url_raw = row.get("file_url", "").strip()
            url = urljoin(BASE_URL, url_raw)

            if not url or not publish_raw:
                continue

            pub_dt = parse_publish_date(publish_raw)
            if not pub_dt:
                continue

            period_norm = normalize_period(period_raw)

            docs.append(
                Document(
                    doc_type_raw=raw_type,
                    doc_type=compact_type,
                    period_raw=period_raw,
                    period=period_norm,
                    publish_date=pub_dt,
                    url=url,
                )
            )

    docs.sort(key=lambda d: d.publish_date, reverse=True)
    return docs


def docs_to_cache_items(docs: List[Document]) -> List[dict]:
    return [
        {
            "doc_type_raw": d.doc_type_raw,
            "doc_type": d.doc_type,
            "period_raw": d.period_raw,
            "period": d.period,
            "publish_date": d.publish_date.isoformat(),
            "url": d.url,
        }
        for d in docs
    ]


def _refresh_key(ticker: str, doc_type: str) -> str:
    return f"{ticker.upper()}_{doc_type}"


def build_script_filter_items(
    cache_items: List[dict],
    ticker: str,
    period_filter: Optional[str],
    cache_age_label: Optional[str],
) -> dict:
    items = []
    pf_raw = (period_filter or "").strip()
    pf_upper = pf_raw.upper()

    for ci in cache_items:
        period = ci.get("period", "")
        if pf_upper and not period.upper().startswith(pf_upper):
            continue

        doc_type = ci.get("doc_type", "")
        doc_type_raw = ci.get("doc_type_raw", "")
        url = ci.get("url", "")
        publish_iso = ci.get("publish_date", "")
        try:
            pub_dt = datetime.fromisoformat(publish_iso)
            pub_date_str = pub_dt.strftime("%d.%m.%Y")
            pub_iso_date = pub_dt.date().isoformat()
        except ValueError:
            pub_date_str = publish_iso
            pub_iso_date = publish_iso

        title = f"{doc_type} - {period}"
        subtitle = f"{pub_date_str} — {doc_type_raw}"
        arg_payload = {
            "ticker": ticker.upper(),
            "url": url,
            "period": period,
            "doc_type": doc_type,
            "publish_date": pub_iso_date,
            "period_raw": ci.get("period_raw", ""),
            "doc_type_raw": doc_type_raw,
        }
        cmd_payload = dict(arg_payload)
        cmd_payload["save_to_downloads"] = True
        mods = {
            "cmd": {
                "arg": json.dumps(cmd_payload, ensure_ascii=False),
                "subtitle": "Save to ~/Downloads",
                "valid": True,
            }
        }
        if cache_age_label:
            alt_payload = dict(arg_payload)
            alt_payload["force_refresh"] = True
            mods["alt"] = {
                "arg": json.dumps(alt_payload, ensure_ascii=False),
                "subtitle": f"↻ Обновить · Кэш: {cache_age_label}",
                "valid": True,
            }
        items.append(
            {
                "title": title,
                "subtitle": subtitle,
                "arg": json.dumps(arg_payload, ensure_ascii=False),
                "valid": True,
                "mods": mods,
            }
        )

    if not items:
        detail = "No reports match the filter" if pf_raw else "No reports found"
        items.append(
            {
                "title": detail,
                "subtitle": f"{ticker.upper()} — try another period or command.",
                "valid": False,
            }
        )

    return {"items": items}


def build_autocomplete_items(matching_tickers: List[Tuple[str, str, str]], command: str) -> dict:
    """Build script filter items for ticker autocomplete."""
    items = []

    for ticker, company_id, company_name in matching_tickers[:10]:  # Limit to 10 suggestions
        title = f"{ticker} - {company_name}" if company_name else ticker
        items.append(
            {
                "title": title,
                "subtitle": f"Search {command.upper()} reports for {ticker}",
                "arg": f"{ticker} ",  # Add space to continue typing period filter
                "autocomplete": ticker,
                "valid": False,
                "match": ticker.lower(),  # For Alfred's built-in filtering
            }
        )

    if not items:
        items.append(
            {
                "title": "No matching tickers found",
                "subtitle": "Try a different ticker prefix",
                "valid": False,
            }
        )

    return {"items": items}


def parse_query(text: str) -> Tuple[Optional[str], Optional[str]]:
    if not text:
        return None, None
    parts = text.strip().split()
    if not parts:
        return None, None
    ticker = parts[0]
    period = parts[1] if len(parts) > 1 else None
    return ticker, period


def emit_error(message: str, detail: str = "") -> None:
    items = [
        {
            "title": message,
            "subtitle": detail or "Check ticker, period, or network connectivity.",
            "valid": False,
        }
    ]
    json.dump({"items": items}, sys.stdout, ensure_ascii=False, indent=2)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="List RSBU/MSFO reports from e-disclosure.ru as Alfred Script Filter JSON."
    )
    parser.add_argument(
        "command",
        choices=["rsbu", "msfo"],
        help="Which compact document type to list.",
    )
    parser.add_argument(
        "pos_ticker",
        nargs="?",
        help="Company ticker, e.g. STSB.",
    )
    parser.add_argument(
        "pos_period",
        nargs="?",
        help="Optional normalized period prefix filter (e.g. 2024, 2024Q1).",
    )
    parser.add_argument(
        "--period",
        dest="period_override",
        help="Explicit normalized period prefix filter.",
    )
    parser.add_argument(
        "--alfred-query",
        dest="alfred_query",
        help="Raw query forwarded by Alfred (ticker [period]).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Synchronous slow-fetch worker: rewrites cache then exits silently.",
    )

    args = parser.parse_args(argv)

    if args.refresh:
        _run_refresh_worker(args)
        return

    ticker = args.pos_ticker
    period = args.period_override or args.pos_period

    if args.alfred_query:
        q_ticker, q_period = parse_query(args.alfred_query)
        ticker = q_ticker or ticker
        period = q_period or period

    # Load all tickers for autocomplete
    all_tickers = load_tickers()

    # Determine if we should show autocomplete or reports
    if ticker and not period:
        # Check if ticker is complete (has space after it in query or is exact match)
        query_has_space = args.alfred_query and " " in args.alfred_query.strip()
        ticker_is_complete = any(t[0] == ticker.upper() for t in all_tickers)

        if not query_has_space and not ticker_is_complete:
            # Show autocomplete suggestions
            matching_tickers = search_tickers(ticker, all_tickers)
            data = build_autocomplete_items(matching_tickers, args.command)
            json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
            return

    # Show reports (existing logic)
    if not ticker:
        emit_error("Ticker is required", "Usage: msfo TICKER [PERIOD]")
        return

    compact_type = "МСФО" if args.command == "msfo" else "РСБУ"

    cache_items: Optional[List[dict]] = None
    cache_age_label: Optional[str] = None
    cache_age_seconds: Optional[float] = None
    now = datetime.now()

    env = report_cache.read(ticker, compact_type)
    if env is not None:
        items = env.get("items")
        fetched_at = report_cache.fetched_at(env)
        if isinstance(items, list) and fetched_at is not None:
            cache_items = items
            cache_age_seconds = (now - fetched_at).total_seconds()
            cache_age_label = relative_time_ru.format(now, fetched_at)
            _log(f"cache hit {ticker}/{compact_type} age={cache_age_label} items={len(items)}")

    key = _refresh_key(ticker, compact_type)
    needs_refresh = cache_items is None or (
        cache_age_seconds is not None and cache_age_seconds >= REFRESH_AGE_THRESHOLD_SECONDS
    )
    if needs_refresh:
        spawn_argv = _refresh_worker_argv(args.command, ticker, compact_type)
        spawned = refresh_lock.spawn_refresh(key, spawn_argv)
        if spawned:
            _log(f"spawned refresh worker {key}")
    worker_live = refresh_lock.is_refreshing(key)

    if cache_items is None:
        data = _build_placeholder(ticker, worker_live)
    else:
        data = build_script_filter_items(cache_items, ticker, period, cache_age_label)
        if worker_live:
            data["rerun"] = 0.5

    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)


def _refresh_worker_argv(command: str, ticker: str, doc_type: str) -> List[str]:
    return [
        sys.executable,
        os.path.abspath(__file__),
        command,
        ticker,
        "--refresh",
    ]


def _build_placeholder(ticker: str, worker_live: bool) -> dict:
    data = {
        "items": [
            {
                "title": f"Обновляем для {ticker.upper()}…",
                "subtitle": "Загружаем отчёты с e-disclosure.ru",
                "valid": False,
            }
        ]
    }
    if worker_live:
        data["rerun"] = 0.5
    return data


def _run_refresh_worker(args: argparse.Namespace) -> None:
    ticker = args.pos_ticker
    if args.alfred_query:
        q_ticker, _ = parse_query(args.alfred_query)
        ticker = q_ticker or ticker
    if not ticker:
        _log("refresh: ticker required")
        return
    compact_type = "МСФО" if args.command == "msfo" else "РСБУ"
    key = _refresh_key(ticker, compact_type)
    pid = os.getpid()
    try:
        company_id = load_company_id(ticker)
        _log(f"refresh worker pid={pid} {ticker}/{compact_type} edid={company_id}")
        docs = collect_documents(company_id, compact_type)
        cache_items = docs_to_cache_items(docs)
        if not cache_items:
            _log(f"refresh worker pid={pid} empty result, skipping cache write")
        else:
            report_cache.write(ticker, compact_type, cache_items, datetime.now())
            _log(f"refresh worker pid={pid} wrote {len(cache_items)} items")
    except Exception as exc:  # pragma: no cover - worker errors
        _log(f"refresh worker pid={pid} failed: {exc}")
    finally:
        refresh_lock.release(key, pid)


if __name__ == "__main__":
    main()

