#!/usr/bin/env python3
"""
CLI runner for the DSPy-based Schedule of Investments scraper.

Targets BDC tickers that produce empty or incorrect output from the XBRL
and HTML-only pipelines:

    EQS  SSSS  BXSL  GSBD  LIEN  NEWT  OXSQ  PSBD  TPVG  ICMB

Examples
--------
# Scrape one ticker, recent filings only
python scripts/run_dspy_scraper.py --ticker BXSL

# Scrape specific date range
python scripts/run_dspy_scraper.py --ticker GSBD --start 2023-01-01 --end 2025-12-31

# Scrape all broken tickers
python scripts/run_dspy_scraper.py --all

# Limit to 3 filings per ticker (fast test)
python scripts/run_dspy_scraper.py --all --max-filings 3

# 10-K only
python scripts/run_dspy_scraper.py --ticker ICMB --form 10-K

# Use a different Gemini model
python scripts/run_dspy_scraper.py --ticker NEWT --model gemini/gemini-1.5-flash
"""

import argparse
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup so we can import from src/extraction regardless of cwd
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src" / "extraction"))

from dspy_table_scraper import DSPyTableScraper, TARGET_TICKERS


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="DSPy-based Schedule of Investments scraper for broken BDC tickers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--ticker",
        metavar="TICKER",
        help="Single BDC ticker to scrape (e.g. BXSL).",
    )
    target.add_argument(
        "--all",
        action="store_true",
        help=f"Scrape all broken tickers: {', '.join(TARGET_TICKERS)}.",
    )

    p.add_argument(
        "--form",
        nargs="+",
        default=["10-Q", "10-K"],
        metavar="FORM",
        help="SEC form type(s) to process. Default: 10-Q 10-K.",
    )
    p.add_argument(
        "--start",
        metavar="YYYY-MM-DD",
        help="Earliest filing date to process.",
    )
    p.add_argument(
        "--end",
        metavar="YYYY-MM-DD",
        help="Latest filing date to process (default: today).",
    )
    p.add_argument(
        "--max-filings",
        type=int,
        metavar="N",
        help="Max filings per ticker per form type (useful for testing).",
    )
    p.add_argument(
        "--output-dir",
        default=str(_REPO_ROOT / "output"),
        metavar="DIR",
        help="Directory for output CSV files. Default: <repo>/output/",
    )
    p.add_argument(
        "--data-dir",
        default=str(_REPO_ROOT / "data"),
        metavar="DIR",
        help="Directory for SEC API cache. Default: <repo>/data/",
    )
    p.add_argument(
        "--model",
        default="gemini/gemini-2.0-flash",
        metavar="MODEL",
        help=(
            "DSPy/litellm model string. "
            "Default: gemini/gemini-2.0-flash. "
            "Other options: gemini/gemini-1.5-flash, gemini/gemini-2.5-flash-preview, etc."
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-extract even if output CSV already exists.",
    )
    p.add_argument(
        "--no-cot",
        action="store_true",
        help="Disable Chain-of-Thought (use TypedPredictor instead). Faster but less accurate.",
    )
    p.add_argument(
        "--rows-per-chunk",
        type=int,
        default=80,
        metavar="N",
        help="Max table rows per DSPy call. Default: 80.",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG logging.",
    )

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Determine which tickers to process
    tickers = TARGET_TICKERS if args.all else [args.ticker.upper()]

    # Build scraper (configures DSPy on construction)
    try:
        scraper = DSPyTableScraper(
            gemini_model=args.model,
            output_dir=args.output_dir,
            data_dir=args.data_dir,
            use_chain_of_thought=not args.no_cot,
            rows_per_chunk=args.rows_per_chunk,
        )
    except (ImportError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    total_files: list[str] = []
    failed_tickers: list[str] = []

    for ticker in tickers:
        print(f"\n{'='*60}")
        print(f"  Scraping {ticker}")
        print(f"{'='*60}")
        try:
            paths = scraper.scrape_ticker(
                ticker=ticker,
                filing_types=args.form,
                start_date=args.start,
                end_date=args.end,
                max_filings=args.max_filings,
                force=args.force,
            )
            total_files.extend(paths)
            print(f"  OK  {ticker}: wrote {len(paths)} file(s)")
            for p in paths:
                print(f"       {p}")
        except Exception as exc:
            logging.exception("Fatal error scraping %s", ticker)
            failed_tickers.append(ticker)
            print(f"  FAILED  {ticker}: {exc}")

    # Summary
    print(f"\n{'='*60}")
    print(f"Done.  {len(total_files)} CSV file(s) written.")
    if failed_tickers:
        print(f"Failed tickers: {', '.join(failed_tickers)}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
