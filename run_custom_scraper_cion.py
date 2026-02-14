#!/usr/bin/env python3
"""
Run custom (non-LLM) table scraper for CION using the same table input we'd give the LLM.
Uses header-based column mapping to produce the standard 16-column CSV.
No Gemini API key required.

Usage (from project root):
    python run_custom_scraper_cion.py
    python run_custom_scraper_cion.py --filing-type 10-K --year 2024
"""

import argparse
import logging
import sys
from pathlib import Path

# Run from project root; ensure src is on path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src" / "extraction") not in sys.path:
    sys.path.insert(0, str(ROOT / "src" / "extraction"))

from llm_table_scraper import LLMTableScraper
from custom_table_scraper import rows_to_csv

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Custom scraper for CION (no LLM)")
    parser.add_argument("--ticker", default="CION", help="Ticker (default: CION)")
    parser.add_argument("--filing-type", default="10-Q", choices=("10-Q", "10-K"))
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--quarter", default="Q2", help="For 10-Q: Q1, Q2, Q3, Q4")
    parser.add_argument("--output-dir", default=None, help="Defaults to output/")
    parser.add_argument("--debug-headers", action="store_true", help="Write first table headers + 2 rows to output dir for inspection")
    args = parser.parse_args()

    output_dir = Path(args.output_dir or ROOT / "output")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Scraper with LLM disabled so we only use table parsing + SEC client
    scraper = LLMTableScraper(use_llm=False, output_dir=str(output_dir))

    # Get filing index URL and fetch
    index_url = scraper.sec_client.get_filing_index_url(
        ticker=args.ticker,
        filing_type=args.filing_type,
        year=args.year,
        quarter=args.quarter if args.filing_type == "10-Q" else None,
    )
    if not index_url:
        logger.error("No filing found for %s %s", args.ticker, args.filing_type)
        return 1

    filing_result = scraper.sec_client.fetch_filing_by_index_url(
        index_url=index_url,
        ticker=args.ticker,
        filing_type=args.filing_type,
        save_to_file=False,
        document_types=[".htm", ".html"],
    )
    if not filing_result:
        logger.error("Failed to fetch filing")
        return 1

    logger.info("Filing date: %s", filing_result.filing_date)

    tables = scraper.extract_tables_from_filing_documents(filing_result)
    if not tables:
        logger.error("No tables found in filing")
        return 1
    logger.info("Extracted %d tables from documents", len(tables))

    period_end_date = getattr(filing_result, "period_end_date", None)
    investment_tables = scraper.table_detector.filter_investment_tables_simple(
        tables,
        filing_result.filing_date,
        args.filing_type,
        period_end_date,
    )
    if not investment_tables:
        logger.warning("No investment tables after filter; using all tables")
        investment_tables = tables

    logger.info("Processing %d investment tables with custom scraper", len(investment_tables))

    if args.debug_headers and investment_tables:
        table_html, _, _, table_id = investment_tables[0]
        rows = scraper.parse_table_to_rows(table_html)
        if rows:
            debug_path = output_dir / f"custom_scraper_{args.ticker}_first_table_headers.txt"
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write("HEADERS (after collapse):\n")
                f.write(repr(rows[0]) + "\n\n")
                f.write("FIRST 2 DATA ROWS:\n")
                for r in rows[1:3]:
                    f.write(repr(r) + "\n")
            logger.info("Wrote first table headers to %s", debug_path)

    all_csv_lines = []
    for table_html, table_text, table_num, table_id in investment_tables:
        rows = scraper.parse_table_to_rows(table_html)
        if not rows:
            logger.debug("Table %s: no rows parsed", table_id)
            continue
        csv_content = rows_to_csv(rows)
        lines = [ln.strip() for ln in csv_content.strip().split("\n") if ln.strip()]
        if len(lines) <= 1:
            continue
        if not all_csv_lines:
            all_csv_lines.append(lines[0])
        for ln in lines[1:]:
            all_csv_lines.append(ln)

    if not all_csv_lines:
        logger.warning("No data rows produced")
        out_path = output_dir / f"custom_scraper_{args.ticker}_{filing_result.filing_date}.csv"
        out_path.write_text("company_name,investment_type,industry,cash_rate,pik_rate,reference_rate,spread,acquisition_date,maturity_date,principal_amount,amortized_cost,fair_value,percent_of_net_assets,cost,commitment_limit,undrawn_commitment\n", encoding="utf-8")
        logger.info("Wrote header-only CSV: %s", out_path)
        return 0

    # Dedupe by full row (simple)
    seen = set()
    unique = [all_csv_lines[0]]
    for r in all_csv_lines[1:]:
        if r not in seen:
            seen.add(r)
            unique.append(r)

    csv_out = "\n".join(unique)
    out_path = output_dir / f"custom_scraper_{args.ticker}_{filing_result.filing_date}.csv"
    out_path.write_text(csv_out, encoding="utf-8")
    logger.info("Wrote %d rows (header + %d data) to %s", len(unique), len(unique) - 1, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
