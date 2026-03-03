#!/usr/bin/env python3
"""
Run custom (non-LLM) table scraper for BCSF only.
Uses shared custom_scraper_runner; no ticker-specific table filtering.
Output: output/custom_scraper_BCSF_{date}.csv
Company names and section rows normalized via custom_table_scraper (BCSF patterns).

Usage (from project root):
  python scripts/run_custom_scraper_bcsf.py
  python scripts/run_custom_scraper_bcsf.py --filing-type 10-K --year 2024
  python scripts/run_custom_scraper_bcsf.py --years-back 2
  python scripts/run_custom_scraper_bcsf.py --debug-headers
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from custom_scraper_runner import run_historical, run_single_filing

# Ensure extraction module is on path for runner's imports
if str(ROOT / "src" / "extraction") not in sys.path:
    sys.path.insert(0, str(ROOT / "src" / "extraction"))

import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

TICKER = "BCSF"


def main() -> int:
    parser = argparse.ArgumentParser(description="Custom table scraper for BCSF (no LLM)")
    parser.add_argument("--filing-type", default="10-Q", choices=("10-Q", "10-K"))
    parser.add_argument("--year", type=int, default=None, help="Filing year (default: latest)")
    parser.add_argument("--quarter", default=None, help="For 10-Q: Q1, Q2, Q3, Q4")
    parser.add_argument("--years-back", type=int, default=None,
                        help="Process all 10-Q and 10-K for the last N years")
    parser.add_argument("--output-dir", default=None, help="Defaults to output/")
    parser.add_argument("--debug-headers", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir or ROOT / "output")
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.years_back is not None:
        return run_historical(
            TICKER, args.years_back, output_dir,
            args.debug_headers, args.force,
            table_filter=None,
        )
    return run_single_filing(
        TICKER, args.filing_type, args.year, args.quarter,
        output_dir, args.debug_headers, args.force,
        table_filter=None,
    )


if __name__ == "__main__":
    sys.exit(main())
