#!/usr/bin/env python3
import argparse
import csv
import gzip
import json
import os
import sys
import urllib.request
import urllib.response
import zlib
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin

try:
    from curl_cffi import requests as cf_requests  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional dependency
    cf_requests = None

_cf_session: Optional["cf_requests.Session"] = None  # type: ignore[name-defined]
_warned_no_cf = False


BASE_URL = "https://www.e-disclosure.ru/portal/"


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
            if row.get("ticker", "").upper() == ticker_up:
                company_id = row.get("id")
                if company_id:
                    return company_id
    print(f"Unknown ticker: {ticker}", file=sys.stderr)
    sys.exit(1)


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

    if cf_requests is not None:
        global _cf_session
        if _cf_session is None:
            impersonate = os.getenv("EDISCLOSURE_IMPERSONATE", "chrome124")
            _cf_session = cf_requests.Session(impersonate=impersonate)
        response = _cf_session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.text

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
        return decode_http_body(resp)


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

    for page_type in page_types:
        html = fetch_table_html(company_id, page_type)
        parser = FilesTableParser()
        parser.feed(html)

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


def build_script_filter_items(
    docs: List[Document], ticker: str, period_filter: Optional[str]
) -> dict:
    items = []
    pf_raw = (period_filter or "").strip()
    pf_upper = pf_raw.upper()

    for doc in docs:
        doc_period_upper = doc.period.upper()
        if pf_upper and not doc_period_upper.startswith(pf_upper):
            continue

        title = f"{doc.doc_type} - {doc.period}"
        subtitle = doc.publish_date.strftime("%d.%m.%Y")
        arg_payload = {
            "ticker": ticker.upper(),
            "url": doc.url,
            "period": doc.period,
            "doc_type": doc.doc_type,
            "publish_date": doc.publish_date.date().isoformat(),
            "period_raw": doc.period_raw,
            "doc_type_raw": doc.doc_type_raw,
        }
        items.append(
            {
                "title": title,
                "subtitle": subtitle,
                "arg": json.dumps(arg_payload, ensure_ascii=False),
                "valid": True,
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

    args = parser.parse_args(argv)

    ticker = args.pos_ticker
    period = args.period_override or args.pos_period

    if args.alfred_query:
        q_ticker, q_period = parse_query(args.alfred_query)
        ticker = q_ticker or ticker
        period = q_period or period

    if not ticker:
        emit_error("Ticker is required", "Usage: msfo TICKER [PERIOD]")
        return

    compact_type = "МСФО" if args.command == "msfo" else "РСБУ"

    try:
        company_id = load_company_id(ticker)
        docs = collect_documents(company_id, compact_type)
    except Exception as exc:  # pragma: no cover - network/filesystem errors
        emit_error("Failed to fetch reports", str(exc))
        return

    data = build_script_filter_items(docs, ticker, period)
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
