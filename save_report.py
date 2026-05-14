#!/usr/bin/env python3
"""
Download and save the selected e-disclosure report PDF to ~/Downloads.
"""
from __future__ import annotations

import sys
import os

# Add bundled dependencies to path
script_dir = os.path.dirname(os.path.abspath(__file__))
lib_dir = os.path.join(script_dir, 'lib')
if os.path.exists(lib_dir):
    sys.path.insert(0, lib_dir)

from typing import Optional

import report_cache
from open_report import (
    ensure_pdf_cached,
    load_payload,
    parse_args,
    save_pdf_to_downloads,
)


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = parse_args(argv)
        payload = load_payload(args)
        payload.save_to_downloads = True
        if payload.force_refresh:
            report_cache.delete(payload.ticker, payload.doc_type)
            print(f"Cache invalidated for {payload.ticker.upper()}/{payload.doc_type}")
            return 0
        pdf_cache = ensure_pdf_cached(payload)
        saved_path = save_pdf_to_downloads(pdf_cache, payload)
        print(f"Saved {saved_path}")
        return 0
    except KeyboardInterrupt:  # pragma: no cover - manual interruption
        return 130
    except Exception as exc:  # pragma: no cover - user feedback
        print(f"Failed to save report: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

