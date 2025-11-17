#!/usr/bin/env python3
"""
Download, cache, and open e-disclosure report archives.
"""
from __future__ import annotations

import sys
import os

# Add bundled dependencies to path
script_dir = os.path.dirname(os.path.abspath(__file__))
lib_dir = os.path.join(script_dir, 'lib')
if os.path.exists(lib_dir):
    sys.path.insert(0, lib_dir)

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

try:
    from curl_cffi import requests as cf_requests  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional dependency
    cf_requests = None


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36"
    ),
    "Accept": "application/zip,application/octet-stream;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

_cf_session: Optional["cf_requests.Session"] = None  # type: ignore[name-defined]


@dataclass
class ReportPayload:
    ticker: str
    url: str
    period: str
    doc_type: str
    publish_date: str  # YYYY-MM-DD
    period_raw: Optional[str] = None
    doc_type_raw: Optional[str] = None
    save_to_downloads: bool = False

    @property
    def base_name(self) -> str:
        return f"{self.ticker.upper()}_{self.doc_type}_{self.period}_{self.publish_date}"

    @property
    def cache_dir(self) -> Path:
        root = Path("/tmp") / "alfred-e-disclosure"
        return root / self.ticker.upper()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and open cached e-disclosure report archives."
    )
    parser.add_argument("--payload", help="JSON payload passed from Alfred list script.")
    parser.add_argument("--ticker", help="Ticker, e.g. STSB.")
    parser.add_argument("--url", help="Download URL to the ZIP file.")
    parser.add_argument("--period", help="Normalized report period, e.g. 9M2024.")
    parser.add_argument("--doc-type", dest="doc_type", help="Compact doc type.")
    parser.add_argument("--publish-date", dest="publish_date", help="ISO publish date.")
    parser.add_argument("--period-raw", dest="period_raw", help="Original period text.")
    parser.add_argument("--doc-type-raw", dest="doc_type_raw", help="Original doc type.")
    return parser.parse_args(argv)


def load_payload(args: argparse.Namespace) -> ReportPayload:
    data: Dict[str, str]
    if args.payload:
        data = json.loads(args.payload)
    else:
        data = {
            "ticker": args.ticker,
            "url": args.url,
            "period": args.period,
            "doc_type": args.doc_type,
            "publish_date": args.publish_date,
            "period_raw": args.period_raw,
            "doc_type_raw": args.doc_type_raw,
        }
    missing = [key for key in ("ticker", "url", "period", "doc_type", "publish_date") if not data.get(key)]
    if missing:
        raise ValueError(f"Missing payload fields: {', '.join(missing)}")
    return ReportPayload(
        ticker=data["ticker"],
        url=data["url"],
        period=data["period"],
        doc_type=data["doc_type"],
        publish_date=data["publish_date"],
        period_raw=data.get("period_raw"),
        doc_type_raw=data.get("doc_type_raw"),
        save_to_downloads=bool(data.get("save_to_downloads")),
    )


def download_zip(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cookie = os.getenv("EDISCLOSURE_COOKIE")
    headers = dict(HEADERS)
    if cookie:
        headers["Cookie"] = cookie

    if cf_requests is not None:
        global _cf_session
        if _cf_session is None:
            impersonate = os.getenv("EDISCLOSURE_IMPERSONATE", "chrome124")
            _cf_session = cf_requests.Session(impersonate=impersonate)
        response = _cf_session.get(url, headers=headers, timeout=60)
        response.raise_for_status()
        dest.write_bytes(response.content)
        return

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)


def safe_extract(zip_path: Path, target_dir: Path) -> None:
    if target_dir.exists():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = target_dir / member.filename
            resolved = member_path.resolve()
            if not str(resolved).startswith(str(target_dir.resolve())):
                raise RuntimeError(f"Unsafe entry in archive: {member.filename}")
        archive.extractall(target_dir)


def ensure_pdf_cached(payload: ReportPayload) -> Path:
    cache_dir = payload.cache_dir
    base_name = payload.base_name
    zip_path = cache_dir / f"{base_name}.zip"
    extract_dir = cache_dir / base_name
    pdf_cache = cache_dir / f"{base_name}.pdf"

    if not pdf_cache.exists():
        if not zip_path.exists():
            download_zip(payload.url, zip_path)
        safe_extract(zip_path, extract_dir)
        stage_pdf(extract_dir, pdf_cache)

    return pdf_cache


def stage_pdf(extract_dir: Path, final_pdf: Path) -> Path:
    if final_pdf.exists():
        return final_pdf
    pdfs = sorted(extract_dir.rglob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError("Archive does not contain PDF files.")
    final_pdf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdfs[0], final_pdf)
    return final_pdf


def open_pdf(path: Path) -> None:
    subprocess.run(["open", str(path)], check=False)


def save_pdf_to_downloads(pdf_path: Path, payload: ReportPayload) -> Path:
    downloads_dir = Path.home() / "Downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    destination = downloads_dir / f"{payload.base_name}.pdf"
    shutil.copy2(pdf_path, destination)
    return destination


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = parse_args(argv)
        payload = load_payload(args)
        pdf_cache = ensure_pdf_cached(payload)

        if payload.save_to_downloads:
            saved_path = save_pdf_to_downloads(pdf_cache, payload)
            print(f"Saved {saved_path}")
        else:
            open_pdf(pdf_cache)
            print(f"Opened {pdf_cache}")
        return 0
    except KeyboardInterrupt:  # pragma: no cover - manual interruption
        return 130
    except Exception as exc:  # pragma: no cover - user feedback
        print(f"Failed to open report: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

