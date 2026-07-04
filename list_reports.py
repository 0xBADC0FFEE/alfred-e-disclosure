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
import random
import threading
import urllib.request
import urllib.response
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from urllib.parse import urljoin

import cache_dir
import refresh_lock
import relative_time_ru
import report_cache
import retry_policy
from report_cache import Status

REFRESH_AGE_THRESHOLD_SECONDS = 3600

# Origin-wide mutex key guarding the persistent browser profile. Playwright
# forbids two instances on one user_data_dir, so every open serialises on this.
PROFILE_LOCK_KEY = "browser-profile"

# The content marker whose appearance means the challenge is cleared.
FILES_TABLE_SELECTOR = "table.files-table"

# How long the arming browser waits for the table to appear (ms). The headed
# solve gives the human minutes; the headless auto-arm only needs Flow A's PoW.
ARM_DEADLINE_MS = 180_000
HEADLESS_ARM_DEADLINE_MS = 20_000

# Substrings that mark a "stealth browser binary is missing" failure (as opposed
# to the scrapling package being absent), so we can point the user at
# `scrapling install` instead of a cryptic launch error.
_MISSING_BROWSER_MARKERS = (
    "executable",
    "doesn't exist",
    "not found",
    "no such file",
    "camoufox",
    "install",
)

try:
    from curl_cffi import requests as cf_requests  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional dependency
    cf_requests = None

_cf_local = threading.local()  # per-thread cf_requests.Session
_warned_no_cf = False
_warned_stealthy_missing = False

# Serialises browser opens *within* this process. The profile lockfile guards
# it across processes; this guards МСФО's two concurrent page fetches, which
# would otherwise open the one profile twice at once.
_profile_lock = threading.Lock()


BASE_URL = "https://www.e-disclosure.ru/portal/"


def _profile_dir() -> Path:
    """The persistent camoufox profile bridging the headed solve and the
    headless refresh (separate processes): the solved ServicePipe session lives
    here on disk, so a later launch reopens an already-armed browser state — no
    cookie harvest, no curl_cffi handoff.
    """
    return cache_dir.root() / "camoufox-profile"


class ChallengeError(Exception):
    """Portal served an anti-bot challenge instead of the files table."""


class BrowserMissingError(Exception):
    """The stealth browser for the human-arm solve is not installed."""


# Robo-check markers seen on ServicePipe block/CAPTCHA pages. Two forms are
# served in the wild, so we match both: the classic ~2 KB spinner interstitial
# (`id_spinner` / the `js-challenge-loader` mount / `is_captcha` options) and the
# newer ~15 KB rotate-image CAPTCHA ("разверните картинку", robots NOINDEX).
# A challenge is the absence of the `files-table` content marker *plus* any of
# these. Bare "servicepipe" is excluded: real content pages embed its tracking JS.
_CHALLENGE_MARKERS = (
    "проверк",
    "noindex",
    "разверните картинку",
    "id_spinner",
    "js-challenge-loader",
    "id_captcha_frame_div",
    "is_captcha",
)


def _is_challenge_page(html: str) -> bool:
    """True when ``html`` is an anti-bot challenge rather than the real listing."""
    if not html:
        return True
    if "files-table" in html:
        return False
    lowered = html.lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


def _log(msg: str) -> None:
    print(f"[list_reports] {msg}", file=sys.stderr, flush=True)


def _files_url(company_id: str, page_type: int) -> str:
    """URL of a company's filings listing for one portal page type."""
    return f"{BASE_URL}files.aspx?id={company_id}&type={page_type}"


def _page_types(compact_type: str) -> Tuple[int, ...]:
    """Portal page types to read for a compact doc type.

    МСФО (IFRS) sometimes appears on the РСБУ page, so it reads both type=4 and
    type=3; РСБУ reads only type=3. The first entry is the primary page used for
    arming (one arm serves the whole origin anyway).
    """
    return (4, 3) if compact_type == "МСФО" else (3,)


def _looks_like_missing_browser(exc: Exception) -> bool:
    """Whether ``exc`` reads like a missing stealth-browser binary (vs a real fault)."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _MISSING_BROWSER_MARKERS)


def _stealthy_arm(url: str, *, headless: bool, deadline_ms: int) -> str:
    """Open ``url`` in a StealthyFetcher bound to the persistent profile and wait
    for the ``files-table`` marker. Differs only by ``headless`` — headless
    clears Flow A on its own; headed (``headless=False``) holds the window open
    for a human to clear Flow B.

    The cleared ServicePipe session is written into the on-disk profile
    (:func:`_profile_dir`), so a later process reopening the same profile is
    already armed. Returns the page HTML; a never-cleared challenge yields the
    challenge HTML (the caller detects it via :func:`_is_challenge_page`). Raises
    :class:`BrowserMissingError` when scrapling or its browser binary is missing.
    """
    try:
        from scrapling.fetchers import StealthyFetcher  # type: ignore[import-not-found]
    except ImportError as exc:
        raise BrowserMissingError(
            "install: pip install 'scrapling[fetchers]' && scrapling install"
        ) from exc

    def solve(page):
        # scrapling runs page_action *before* its own wait_selector, so block on
        # the table here, keeping the window open while Flow A clears (or the
        # human clears Flow B). A timeout raises, which scrapling swallows.
        page.wait_for_selector(FILES_TABLE_SELECTOR, timeout=deadline_ms)
        return page

    _log(f"stealthy arm headless={headless} {url}")
    try:
        page = StealthyFetcher.fetch(
            url,
            headless=headless,
            network_idle=True,
            humanize=True,
            spoof_fingerprint=True,
            user_data_dir=str(_profile_dir()),
            timeout=deadline_ms + 30_000,
            page_action=solve,
        )
    except Exception as exc:  # noqa: BLE001
        if _looks_like_missing_browser(exc):
            raise BrowserMissingError(str(exc)) from exc
        raise
    html = getattr(page, "html_content", None) or getattr(page, "body", None) or ""
    _log(f"stealthy arm done ({len(html)} bytes)")
    return html


@contextmanager
def _profile_guard():
    """Serialise access to the one browser profile, yielding whether to proceed.

    Yields ``False`` when another live process holds the profile — the caller
    serves stale rather than opening a second instance on the same
    ``user_data_dir`` (Playwright forbids that). Re-entrant within the owning
    PID: a holder that already owns the lock (the human-arm mid-solve) proceeds
    without re-acquiring or later releasing it. The in-process ``_profile_lock``
    additionally serialises concurrent opens within one process — МСФО fans its
    two page fetches through a thread pool.
    """
    with _profile_lock:
        own = refresh_lock.owner(PROFILE_LOCK_KEY)
        if own is not None and own != os.getpid():
            _log(f"profile busy (pid={own}) — serving stale")
            yield False
            return
        took = own is None and refresh_lock.acquire(PROFILE_LOCK_KEY)
        if own is None and not took:
            yield False  # lost the profile to another process
            return
        try:
            yield True
        finally:
            if took:
                refresh_lock.release(PROFILE_LOCK_KEY, os.getpid())


def _stealthy_fetch_html(url: str) -> Optional[str]:
    """Auto-arm fallback: a headless StealthyFetcher clears Flow A against the
    persistent profile and returns the real listing. Never raises — the
    background refresh chain must degrade, not crash. A profile held by another
    process yields stale; a missing browser warns once. Both return ``None``.
    """
    global _warned_stealthy_missing
    with _profile_guard() as proceed:
        if not proceed:
            return None
        try:
            return _stealthy_arm(
                url, headless=True, deadline_ms=HEADLESS_ARM_DEADLINE_MS
            )
        except BrowserMissingError as exc:
            if not _warned_stealthy_missing:
                _log(f"stealth browser unavailable ({exc}) — run: scrapling install")
                _warned_stealthy_missing = True
            return None
        except Exception as exc:  # noqa: BLE001 - background path must not crash
            _log(f"stealthy fetch failed: {exc}")
            return None


def human_arm(ticker: str, compact_type: str) -> bool:
    """Solve the challenge in a headed browser, filling the persistent profile,
    then refresh both filing types through that armed profile.

    The cleared session lives in the on-disk profile, so the normal refresh path
    (curl_cffi → challenge → headless StealthyFetcher on the same profile) now
    reaches the real listing. Returns True only if a refresh actually produced an
    ``ok`` envelope — reaching the table but still getting a challenge on refresh
    is a failure, not the old "harvest happened" false positive.

    Raises :class:`BrowserMissingError` when the stealth browser is unavailable.
    """
    company_id = load_company_id(ticker)
    page_type = _page_types(compact_type)[0]
    url = _files_url(company_id, page_type)

    _log(f"headed solve {url}")
    html = _stealthy_arm(url, headless=False, deadline_ms=ARM_DEADLINE_MS)
    if _is_challenge_page(html):
        _log("headed solve did not reach the files table")
        return False

    # One solve arms the profile for the whole origin, so refill both caches.
    # Both refreshes must run (they fill different caches), so this can't
    # collapse to a short-circuiting any().
    armed = False
    for ct in ("МСФО", "РСБУ"):
        env = run_refresh(ticker, ct)
        if env.is_ok:
            armed = True
    return armed


@dataclass
class Document:
    doc_type_raw: str
    doc_type: str  # compact: "МСФО" or "РСБУ"
    period_raw: str
    period: str
    publish_date: datetime
    url: str
    file_id: str = ""
    size: str = ""


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
        self.in_file_link = False

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
                "file_id": "",
                "size": "",
            }
            self.in_file_cell = False
            self.in_file_link = False
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
            file_id = attrs_dict.get("data-fileid")
            if file_id:
                self.current_row["file_id"] = file_id.strip()
            self.in_file_link = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.in_file_link:
            self.in_file_link = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            self.in_file_cell = False
            self.in_file_link = False
            if self.current_row.get("file_url"):
                self.rows.append(self.current_row)
        elif tag == "table" and self.in_files_table:
            self.in_files_table = False

    def handle_data(self, data: str) -> None:
        if not self.in_row:
            return
        if self.in_file_link:
            # Link text carries the archive size, e.g. "zip, 250.77 КБ".
            chunk = data.strip()
            if chunk:
                if self.current_row["size"]:
                    self.current_row["size"] += " "
                self.current_row["size"] += chunk
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


def normalize_size(text: str) -> str:
    """Tidy the listing's size label: 'zip,\\xa0250.77\\xa0КБ' -> 'zip 250.77 КБ'."""
    if not text:
        return ""
    cleaned = text.replace("\xa0", " ").replace(",", " ")
    return " ".join(cleaned.split())


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


class UnknownTickerError(Exception):
    """Ticker has no e-disclosure company id in tickers.csv."""


def load_company_id(ticker: str) -> str:
    ticker_up = ticker.upper()
    csv_path = Path(__file__).resolve().parent / "tickers.csv"
    if not csv_path.is_file():
        raise UnknownTickerError(f"Ticker mapping file not found: {csv_path}")

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Ticker", "").upper() == ticker_up:
                company_id = row.get("EDID")
                if company_id:
                    return company_id
    raise UnknownTickerError(f"Unknown ticker: {ticker}")


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


def _resolve_cookie_header() -> Optional[str]:
    """Manual cookie override for the fetching surface, if the user set one.

    ``EDISCLOSURE_COOKIE`` stays a manual escape hatch; the armed session now
    lives in the browser profile, not a cookie handoff, so there is nothing else
    to inject here.
    """
    return os.getenv("EDISCLOSURE_COOKIE") or None


def fetch_table_html(company_id: str, doc_page_type: int) -> str:
    url = _files_url(company_id, doc_page_type)
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
    cookie = _resolve_cookie_header()
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


HtmlFetcher = Callable[[str, int], str]


def collect_documents(
    company_id: str,
    wanted_compact_type: str,
    fetcher: HtmlFetcher = fetch_table_html,
) -> List[Document]:
    """
    Collect documents of a certain compact type ('МСФО' or 'РСБУ').

    For 'МСФО' we look both at type=4 and type=3 pages because
    sometimes IFRS reports appear on the RSBU page.
    For 'РСБУ' we use only type=3.

    ``fetcher`` is injectable so tests can drive fixtures without the network.
    Raises :class:`ChallengeError` if any page comes back as an anti-bot
    challenge; network/parse failures propagate to the caller.
    """
    docs: List[Document] = []

    page_types = _page_types(wanted_compact_type)

    _log(f"collect id={company_id} types={page_types}")
    if len(page_types) > 1:
        with ThreadPoolExecutor(max_workers=len(page_types)) as executor:
            htmls = list(executor.map(lambda t: fetcher(company_id, t), page_types))
    else:
        htmls = [fetcher(company_id, page_types[0])]

    for page_type, html in zip(page_types, htmls):
        if _is_challenge_page(html):
            raise ChallengeError(f"challenge on type={page_type}")
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
                    file_id=row.get("file_id", "").strip(),
                    size=normalize_size(row.get("size", "")),
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
            "file_id": d.file_id,
            "size": d.size,
        }
        for d in docs
    ]


def refresh_key(ticker: str, doc_type: str) -> str:
    return f"{ticker.upper()}_{doc_type}"


def build_script_filter_items(
    cache_items: List[dict],
    ticker: str,
    period_filter: Optional[str],
    cache_age_label: Optional[str],
    doc_type: Optional[str] = None,
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

        file_id = ci.get("file_id", "")
        size = ci.get("size", "")

        title = f"{doc_type} - {period}"
        if file_id:
            title += f" • {file_id}"
        # Order: date → size → long name (long name truncates last).
        subtitle = " · ".join(p for p in (pub_date_str, size, doc_type_raw) if p)
        arg_payload = {
            "ticker": ticker.upper(),
            "url": url,
            "period": period,
            "doc_type": doc_type,
            "publish_date": pub_iso_date,
            "period_raw": ci.get("period_raw", ""),
            "doc_type_raw": doc_type_raw,
            "file_id": file_id,
            "size": size,
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
        if doc_type:
            refresh_payload = {
                "ticker": ticker.upper(),
                "doc_type": doc_type,
                "force_refresh": True,
            }
            placeholder = {
                "title": detail,
                "subtitle": f"{ticker.upper()} — ↵ или ⌥↵ чтобы сбросить кэш и попробовать снова.",
                "arg": json.dumps(refresh_payload, ensure_ascii=False),
                "valid": True,
                "mods": {
                    "alt": {
                        "arg": json.dumps(refresh_payload, ensure_ascii=False),
                        "subtitle": "↻ Сбросить кэш и попробовать снова",
                        "valid": True,
                    }
                },
            }
        else:
            placeholder = {
                "title": detail,
                "subtitle": f"{ticker.upper()} — try another period or command.",
                "valid": False,
            }
        items.append(placeholder)

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

    now = datetime.now()
    env = report_cache.read(ticker, compact_type)
    key = refresh_key(ticker, compact_type)

    if _should_spawn(env, now):
        spawn_argv = _refresh_worker_argv(args.command, ticker, compact_type)
        if refresh_lock.spawn_refresh(key, spawn_argv):
            _log(f"spawned refresh worker {key} ({_spawn_reason(env, now)})")
    worker_live = refresh_lock.is_refreshing(key)

    data = _build_output(env, ticker, period, compact_type, now, worker_live)
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)


def _should_spawn(env: Optional[report_cache.Envelope], now: datetime) -> bool:
    """Whether ``main()`` may spawn a refresh worker for this key right now.

    The cooldown lives here: a fresh failure envelope whose ``next_retry_at`` is
    still in the future is left alone, so we don't respawn a worker on every tick.
    ``spawn_refresh`` separately refuses to double-spawn while one is live.
    """
    if env is None:
        return True
    if env.is_ok:
        if env.fetched_at is None:
            return True
        age = (now - env.fetched_at).total_seconds()
        return age >= REFRESH_AGE_THRESHOLD_SECONDS
    # Failure envelope: only once the backoff cooldown has elapsed.
    return env.next_retry_at is None or now >= env.next_retry_at


def _spawn_reason(env: Optional[report_cache.Envelope], now: datetime) -> str:
    if env is None:
        return "cold cache"
    if env.is_ok:
        return "stale cache"
    return f"retry after cooldown (attempt {env.attempts + 1})"


def _build_output(
    env: Optional[report_cache.Envelope],
    ticker: str,
    period: Optional[str],
    compact_type: str,
    now: datetime,
    worker_live: bool,
) -> dict:
    have_items = env is not None and env.has_items
    if have_items:
        cache_age_label = relative_time_ru.format(now, env.fetched_at)
        data = build_script_filter_items(
            env.items, ticker, period, cache_age_label, compact_type
        )
        stale_failed = not env.is_ok and not worker_live
        if stale_failed:
            data["items"].insert(0, _stale_badge_item(cache_age_label))
        if worker_live:
            data["rerun"] = 0.5
        return data

    # No usable items yet.
    if worker_live:
        return _build_placeholder(ticker, worker_live=True)
    if env is not None and not env.is_ok:
        return _build_error_item(ticker, env.status, compact_type)
    # Cold cache and the worker didn't come up (e.g. spawn failed): show the
    # placeholder once, without a rerun so it can't spin forever.
    return _build_placeholder(ticker, worker_live=False)


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


def _stale_badge_item(cache_age_label: str) -> dict:
    return {
        "title": "⚠︎ Обновление не удалось",
        "subtitle": f"Показаны сохранённые данные · Кэш: {cache_age_label}",
        "valid": False,
    }


def _build_error_item(ticker: str, status: Status, doc_type: str) -> dict:
    """Terminal error row (no ``rerun``) distinguishing block from network fault.

    On a **challenge** the block is a captcha a human can clear: ↵ opens a headed
    browser to solve it (arm); ⌘↵ falls back to the cache-reset retry. On a plain
    network **error** both ↵ and ⌘↵ carry ``force_refresh`` (reset + retry).
    """
    ticker_up = ticker.upper()
    retry_arg = json.dumps(
        {"ticker": ticker_up, "doc_type": doc_type, "force_refresh": True},
        ensure_ascii=False,
    )
    retry_mod = {
        "arg": retry_arg,
        "subtitle": "↻ Сбросить кэш и попробовать снова",
        "valid": True,
    }

    if status is Status.CHALLENGE:
        arm_arg = json.dumps(
            {"ticker": ticker_up, "doc_type": doc_type, "arm": True},
            ensure_ascii=False,
        )
        item = {
            "title": f"Портал заблокировал запрос — {ticker_up}",
            "subtitle": "e-disclosure.ru показал проверку. ↵ — пройти проверку в браузере · ⌘↵ — сбросить кэш.",
            "arg": arm_arg,
            "valid": True,
            "mods": {"cmd": retry_mod},
        }
    else:
        item = {
            "title": f"Не удалось загрузить отчёты — {ticker_up}",
            "subtitle": "Сетевая ошибка. ↵ или ⌘↵ — попробовать снова.",
            "arg": retry_arg,
            "valid": True,
            "mods": {"cmd": retry_mod},
        }
    return {"items": [item]}


def _record_failure(
    ticker: str,
    doc_type: str,
    status: Status,
    prev: Optional[report_cache.Envelope],
    reason: str,
) -> report_cache.Envelope:
    now = datetime.now()
    attempts = (prev.attempts if prev is not None else 0) + 1
    nra = retry_policy.next_retry_at(attempts, now, random.random())
    env = report_cache.failure(status, now, nra, prev)
    report_cache.write(ticker, doc_type, env)
    _log(
        f"refresh worker {status.value} attempt={attempts} "
        f"next_retry={nra.isoformat()} reason={reason}"
    )
    return env


def _run_refresh_worker(args: argparse.Namespace) -> None:
    ticker = args.pos_ticker
    if args.alfred_query:
        q_ticker, _ = parse_query(args.alfred_query)
        ticker = q_ticker or ticker
    if not ticker:
        _log("refresh: ticker required")
        return
    compact_type = "МСФО" if args.command == "msfo" else "РСБУ"
    key = refresh_key(ticker, compact_type)
    pid = os.getpid()
    try:
        run_refresh(ticker, compact_type)
    finally:
        refresh_lock.release(key, pid)


def run_refresh(
    ticker: str,
    compact_type: str,
    fetcher: Optional[HtmlFetcher] = None,
) -> report_cache.Envelope:
    """Fetch, classify the outcome, and always persist an envelope.

    Returns the written envelope. ``fetcher`` is injectable for tests; the
    outcome is one of ok / challenge / error — the previous "skip cache write on
    empty result" path is gone, so an empty-but-successful fetch writes ``ok``.
    """
    prev = report_cache.read(ticker, compact_type)
    pid = os.getpid()
    try:
        company_id = load_company_id(ticker)
        _log(f"refresh worker pid={pid} {ticker}/{compact_type} edid={company_id}")
        if fetcher is None:
            docs = collect_documents(company_id, compact_type)
        else:
            docs = collect_documents(company_id, compact_type, fetcher)
    except ChallengeError as exc:
        return _record_failure(ticker, compact_type, Status.CHALLENGE, prev, str(exc))
    except Exception as exc:  # unknown ticker / network / parse failure
        return _record_failure(ticker, compact_type, Status.ERROR, prev, str(exc))

    cache_items = docs_to_cache_items(docs)
    env = report_cache.ok(cache_items, datetime.now())
    report_cache.write(ticker, compact_type, env)
    _log(f"refresh worker pid={pid} wrote ok {len(cache_items)} items")
    return env


if __name__ == "__main__":
    main()

