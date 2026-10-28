#!/usr/bin/env python3
"""
Unified runner for the per-company custom scrapers.

Each ticker's parsing logic lives in src/extraction/scrapers/<ticker>.py and
is registered in src/extraction/scrapers/__init__.py.  This script handles
CLI concerns shared by every ticker: SEC download, filing iteration, and
output naming (output/custom_scraper_TICKER_{filing_date}.csv).

Usage (from project root):
  python scripts/run_company_scraper.py --ticker ARCC
  python scripts/run_company_scraper.py --ticker ARCC --years-back 2
  python scripts/run_company_scraper.py --ticker ARCC --filing-type 10-K --year 2025
  python scripts/run_company_scraper.py --ticker ARCC --debug-headers --force
"""

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src" / "extraction")):
    if p not in sys.path:
        sys.path.insert(0, p)

from scrapers import get_scraper  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a per-company custom scraper")
    parser.add_argument("--ticker", required=True, help="Ticker symbol (e.g. ARCC)")
    parser.add_argument("--filing-type", default="10-Q", choices=("10-Q", "10-K"),
                        help="Filing type when processing a single filing (default: 10-Q)")
    parser.add_argument("--year", type=int, default=None,
                        help="Filing year for a single filing (default: latest)")
    parser.add_argument("--quarter", default=None,
                        help="For 10-Q: Q1, Q2, Q3 (default: latest)")
    parser.add_argument("--years-back", type=int, default=None,
                        help="Process all 10-Q and 10-K filings for the last N years")
    parser.add_argument("--output-dir", default=None, help="Defaults to output/")
    parser.add_argument("--data-dir", default=None, help="Defaults to data/")
    parser.add_argument("--force", action="store_true",
                        help="Re-process filings even if the output CSV already exists")
    parser.add_argument("--debug-headers", action="store_true",
                        help="Dump parsed header rows for the first SOI table to a debug file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    output_dir = Path(args.output_dir or ROOT / "output")
    data_dir = Path(args.data_dir or ROOT / "data")

    try:
        scraper = get_scraper(args.ticker, output_dir=str(output_dir), data_dir=str(data_dir))
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.years_back is not None:
        return scraper.run_historical(args.years_back, force=args.force)

    return scraper.run_single_filing(
        filing_type=args.filing_type,
        year=args.year,
        quarter=args.quarter,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())
