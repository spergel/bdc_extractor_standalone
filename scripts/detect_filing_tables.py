#!/usr/bin/env python3
"""
Run table detection (no LLM) for the exact filing that produced a given CSV date.
Use this to debug underreporting: see which tables were selected for that filing.

Usage (from project root):
  python scripts/detect_filing_tables.py CSWC 2026-02-02
  python scripts/detect_filing_tables.py CSWC 2026-02-02 --out output/CSWC_detect_2026-02-02.txt
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src" / "extraction") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src" / "extraction"))

from llm_table_scraper import LLMTableScraper
from sec_api_client import SECAPIClient


def main():
    import argparse
    p = argparse.ArgumentParser(description="Run detection-only for a specific (ticker, filing_date)")
    p.add_argument("ticker", help="e.g. CSWC")
    p.add_argument("filing_date", help="e.g. 2026-02-02")
    p.add_argument("--out", default=None, help="Output path for table text (default: output/{ticker}_detect_{filing_date}.txt)")
    p.add_argument("--years-back", type=int, default=2, help="Years to search for the filing (default 2)")
    args = p.parse_args()

    sec = SECAPIClient()
    filings_10q = sec.get_historical_10q_filings(args.ticker, years_back=args.years_back)
    filings_10k = sec.get_historical_10k_filings(args.ticker, years_back=args.years_back)
    all_filings = filings_10q + filings_10k
    all_filings.sort(key=lambda f: f["date"], reverse=True)

    match = None
    for f in all_filings:
        if f["date"] == args.filing_date:
            match = f
            break
    if not match:
        print(f"No filing found for {args.ticker} with date {args.filing_date}. Available recent dates:")
        for f in all_filings[:15]:
            print(f"  {f['date']}  {f['form']}")
        sys.exit(1)

    out_path = args.out
    if not out_path:
        out_path = REPO_ROOT / "output" / f"{args.ticker}_detect_{args.filing_date}.txt"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scraper = LLMTableScraper(output_dir=str(REPO_ROOT / "output"))
    # So that fetch_filing_by_index_url gets correct period_end_date (for 10-Q year-end filtering)
    if match.get("period_end_date"):
        if not hasattr(scraper.sec_client, "_cached_period_date"):
            scraper.sec_client._cached_period_date = {}
        scraper.sec_client._cached_period_date[match["index_url"]] = match["period_end_date"]
    result = scraper.process_filing(
        args.ticker,
        match["form"],
        index_url=match["index_url"],
        detect_to_file=str(out_path),
    )
    if result:
        print(f"Detection completed. Table list written to: {out_path}")
    else:
        print("Detection produced no output.")
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
