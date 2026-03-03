#!/usr/bin/env python3
"""
Shared runner for per-ticker custom (non-LLM) table scrapers.

Each BDC has its own script (run_custom_scraper_bcic.py, run_custom_scraper_bcsf.py, ...)
that imports this module and calls run_one_filing / run_historical with a ticker and
optional table_filter. The runner handles:
  - Fetching filing, extracting tables, fallback_table_detection
  - Optional table_filter(tables, filing_result, ticker) -> filtered tables
  - Parsing tables with custom_table_scraper.rows_to_csv, dedupe, write CSV
"""

import logging
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Any

# Run from project root; ensure src is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src" / "extraction") not in sys.path:
    sys.path.insert(0, str(ROOT / "src" / "extraction"))

from llm_table_scraper import LLMTableScraper
from custom_table_scraper import rows_to_csv
from table_detection import fallback_table_detection

logger = logging.getLogger(__name__)

HEADER_CSV = "company_name,investment_type,industry,cash_rate,pik_rate,reference_rate,spread,acquisition_date,maturity_date,principal_amount,amortized_cost,fair_value,percent_of_net_assets,cost,commitment_limit,undrawn_commitment\n"
HEADER_CSV_WITH_SCHEDULE = "company_name,investment_type,industry,cash_rate,pik_rate,reference_rate,spread,acquisition_date,maturity_date,principal_amount,amortized_cost,fair_value,percent_of_net_assets,cost,commitment_limit,undrawn_commitment,schedule\n"

# Type: (list of (table_html, table_text, table_num, table_id), filing_result, ticker) -> filtered list
TableFilter = Callable[[List[Tuple[str, str, int, str]], Any, str], List[Tuple[str, str, int, str]]]


def process_one_filing(
    scraper: LLMTableScraper,
    ticker: str,
    filing_type: str,
    index_url: str,
    output_dir: Path,
    debug_headers: bool,
    force: bool,
    table_filter: Optional[TableFilter] = None,
) -> bool:
    """Fetch one filing, extract tables, optionally filter, write CSV. Returns True on success."""
    filing_result = scraper.sec_client.fetch_filing_by_index_url(
        index_url=index_url,
        ticker=ticker,
        filing_type=filing_type,
        save_to_file=False,
        document_types=[".htm", ".html"],
    )
    if not filing_result:
        logger.warning("Failed to fetch %s", index_url)
        return False

    filing_date = filing_result.filing_date
    out_path = output_dir / f"custom_scraper_{ticker}_{filing_date}.csv"
    if not force and out_path.exists() and out_path.stat().st_size > len(HEADER_CSV):
        logger.info("SKIP (exists): %s", out_path.name)
        return True

    logger.info("Filing date: %s", filing_date)

    tables = scraper.extract_tables_from_filing_documents(filing_result)
    if not tables:
        logger.warning("No tables found in filing %s", filing_date)
        out_path.write_text(HEADER_CSV, encoding="utf-8")
        return True

    period_end_date = getattr(filing_result, "period_end_date", None)
    investment_tables = fallback_table_detection(
        tables,
        filing_date=filing_date,
        filing_type=filing_type,
        period_end_date=period_end_date,
    )
    if not investment_tables:
        investment_tables = tables

    affiliate_table_ids = set()
    if table_filter:
        result = table_filter(investment_tables, filing_result, ticker)
        if isinstance(result, tuple) and len(result) == 2:
            investment_tables, affiliate_table_ids = result[0], result[1]
        else:
            investment_tables = result
        if not investment_tables:
            logger.warning("Table filter removed all tables for %s", filing_date)
            out_path.write_text(HEADER_CSV, encoding="utf-8")
            return True

    if debug_headers and investment_tables:
        table_html, _, _, table_id = investment_tables[0]
        rows = scraper.parse_table_to_rows(table_html)
        if rows:
            debug_path = output_dir / f"custom_scraper_{ticker}_{filing_date}_headers.txt"
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write("HEADERS (after collapse):\n")
                f.write(repr(rows[0]) + "\n\n")
                f.write("FIRST 2 DATA ROWS:\n")
                for r in rows[1:3]:
                    f.write(repr(r) + "\n")
            logger.info("Wrote headers to %s", debug_path)

    all_csv_lines = []
    carry_industry = ""
    use_schedule_column = bool(affiliate_table_ids)
    for table_html, table_text, table_num, table_id in investment_tables:
        rows = scraper.parse_table_to_rows(table_html)
        if not rows:
            continue
        csv_content, carry_industry = rows_to_csv(rows, initial_section_industry=carry_industry)
        lines = [ln.strip() for ln in csv_content.strip().split("\n") if ln.strip()]
        if len(lines) <= 1:
            continue
        schedule_suffix = ",Affiliate" if table_id in affiliate_table_ids else ",Non-Affiliate"
        if use_schedule_column:
            schedule_suffix = schedule_suffix  # per row
        if not all_csv_lines:
            all_csv_lines.append(lines[0] + (",schedule" if use_schedule_column else ""))
        for ln in lines[1:]:
            all_csv_lines.append(ln + (schedule_suffix if use_schedule_column else ""))

    if not all_csv_lines:
        out_path.write_text(HEADER_CSV, encoding="utf-8")
        logger.info("Wrote header-only CSV: %s", out_path)
        return True

    seen = set()
    unique = [all_csv_lines[0]]
    for r in all_csv_lines[1:]:
        if r not in seen:
            seen.add(r)
            unique.append(r)

    out_path.write_text("\n".join(unique), encoding="utf-8")
    logger.info("Wrote %d rows (header + %d data) to %s", len(unique), len(unique) - 1, out_path)
    return True


def run_historical(
    ticker: str,
    years_back: int,
    output_dir: Path,
    debug_headers: bool,
    force: bool,
    table_filter: Optional[TableFilter] = None,
) -> int:
    """Process all 10-Q and 10-K filings for the last N years. Returns 0 on success, 1 on failure."""
    scraper = LLMTableScraper(use_llm=False, output_dir=str(output_dir))
    filings_10q = scraper.sec_client.get_historical_10q_filings(ticker, years_back=years_back)
    filings_10k = scraper.sec_client.get_historical_10k_filings(ticker, years_back=years_back)
    filings = []
    for f in filings_10q:
        f["filing_type"] = "10-Q"
        filings.append(f)
    for f in filings_10k:
        f["filing_type"] = "10-K"
        filings.append(f)
    filings.sort(key=lambda x: (x["date"], x.get("form", "")))
    if not filings:
        logger.error("No historical filings found for %s (years_back=%s)", ticker, years_back)
        return 1
    logger.info("Processing %d filings (10-Q + 10-K) for last %s years", len(filings), years_back)
    ok = 0
    for f in filings:
        index_url = f["index_url"]
        ft = f["filing_type"]
        logger.info("--- %s %s ---", ft, f["date"])
        if process_one_filing(scraper, ticker, ft, index_url, output_dir, debug_headers, force, table_filter):
            ok += 1
    logger.info("Done: %d/%d filings written to %s", ok, len(filings), output_dir)
    return 0 if ok else 1


def run_single_filing(
    ticker: str,
    filing_type: str,
    year: Optional[int],
    quarter: Optional[str],
    output_dir: Path,
    debug_headers: bool,
    force: bool,
    table_filter: Optional[TableFilter] = None,
) -> int:
    """Process one filing (latest or specified year/quarter). Returns 0 on success, 1 on failure."""
    scraper = LLMTableScraper(use_llm=False, output_dir=str(output_dir))
    index_url = scraper.sec_client.get_filing_index_url(
        ticker=ticker,
        filing_type=filing_type,
        year=year,
        quarter=quarter if filing_type == "10-Q" else None,
    )
    if not index_url:
        logger.error("No filing found for %s %s", ticker, filing_type)
        return 1
    success = process_one_filing(
        scraper, ticker, filing_type, index_url, output_dir, debug_headers, force, table_filter
    )
    return 0 if success else 1
