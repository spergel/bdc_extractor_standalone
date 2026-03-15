#!/usr/bin/env python3
"""
Run custom (non-LLM) table scraper for BCIC only.
Uses shared custom_scraper_runner; skips "Non-Controlled Affiliated" table to avoid double-counting.
Output: output/custom_scraper_BCIC_{date}.csv

Usage (from project root):
  python scripts/run_custom_scraper_bcic.py
  python scripts/run_custom_scraper_bcic.py --filing-type 10-K --year 2024
  python scripts/run_custom_scraper_bcic.py --years-back 2
  python scripts/run_custom_scraper_bcic.py --debug-headers
"""

import argparse
import logging
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from custom_scraper_runner import run_historical, run_single_filing

if str(ROOT / "src" / "extraction") not in sys.path:
    sys.path.insert(0, str(ROOT / "src" / "extraction"))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

TICKER = "BCIC"


def _get_context_before_table(table_id: str, text_map: dict, max_chars: int = 800) -> str:
    """Get text that appears in the document before this table (for section caption detection)."""
    if not text_map:
        return ""
    m = re.match(r"^(.+)_table_(\d+)$", table_id)
    if not m:
        return ""
    doc_name, idx_str = m.group(1), m.group(2)
    doc_name = doc_name.strip()
    table_index = int(idx_str)
    html = text_map.get(doc_name)
    if not html:
        return ""
    pos = 0
    for _ in range(table_index + 1):
        idx = html.find("<table", pos)
        if idx == -1:
            return ""
        pos = idx + 1
    table_start = pos - 1
    before_html = html[:table_start]
    before_text = BeautifulSoup(before_html, "html.parser").get_text(separator=" ", strip=True)
    return before_text[-max_chars:] if len(before_text) > max_chars else before_text


def _debug_write_captions(investment_tables, text_map: dict, path: str = "output/bcic_captions_debug.txt") -> None:
    """Write each table's preceding context (last 400 chars) to a file for inspection."""
    lines = []
    for table_html, _t, table_num, table_id in investment_tables:
        ctx = _get_context_before_table(table_id, text_map, max_chars=800)
        caption = ctx[-400:] if len(ctx) > 400 else ctx
        lines.append(f"=== table_num={table_num} table_id={table_id} ===\n{caption}\n")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote caption debug to %s", path)


def _bcic_table_filter(investment_tables, filing_result, ticker: str):
    """Keep all tables including affiliate; mark affiliate table so runner can add schedule column.
    Returns (filtered_tables, affiliate_table_ids) so Top 10 includes Series A and total can filter to Non-Affiliate (~$540M)."""
    if ticker != TICKER:
        return investment_tables, set()
    text_map = getattr(filing_result, "text_map", None) or {}
    if not text_map and investment_tables:
        logger.warning("BCIC: no text_map on filing_result; cannot filter by context")
    period_end = getattr(filing_result, "period_end_date", None) or ""
    affiliate_table_ids = set()
    filtered = []
    for table_html, table_text, table_num, table_id in investment_tables:
        # Skip exhibit-99 files (press releases / supplemental PDFs filed as ex99*.htm).
        # These duplicate the main SOI and cause ~2x row counts.
        m = re.match(r"^(.+)_table_(\d+)$", table_id)
        doc_name = m.group(1) if m else ""
        if re.search(r"ex99", doc_name, re.I):
            logger.info("BCIC: skipping exhibit-99 table %s (%s)", table_num, table_id)
            continue
        context = _get_context_before_table(table_id, text_map, max_chars=800).lower()
        caption = context[-350:] if len(context) > 350 else context
        # Mark "Affiliate Investments" table (do not skip – so Series A-Great Lakes etc. are included)
        is_affiliate_only = (
            ("affiliate" in caption or "affiliates" in caption)
            and "non-affiliate" not in caption
            and "non-affiliated" not in caption
        )
        if is_affiliate_only:
            affiliate_table_ids.add(table_id)
            logger.info("BCIC: marking affiliate table %s (%s) for schedule column", table_num, table_id)
            filtered.append((table_html, table_text, table_num, table_id))
            continue
        # For 10-Q filings: skip schedule tables that show only the prior year-end (comparative).
        # Detect prior-year tables by their caption: contains "december 31, {year}" but not
        # the current period date (e.g. "june 30, 2025" / "september 30, 2025").
        if period_end and str(period_end)[:4].isdigit():
            from datetime import datetime as _dt
            try:
                _pe = _dt.strptime(str(period_end)[:10], "%Y-%m-%d")
                _month_names = {3: "march", 6: "june", 9: "september", 12: "december"}
                _cur_month = _month_names.get(_pe.month, "")
                _cur_str = f"{_cur_month} {_pe.day}, {_pe.year}"  # e.g. "june 30, 2025"
                _prior_yr_str = f"december 31, {_pe.year - 1}"    # e.g. "december 31, 2024"
                if (_prior_yr_str in caption and _cur_str not in caption):
                    if "schedule of investments" in caption or "investments - continued" in caption:
                        logger.info("Skipping prior year-end table %s (%s) for current-period total", table_num, table_id)
                        continue
            except (ValueError, AttributeError):
                pass
        filtered.append((table_html, table_text, table_num, table_id))
    return (filtered if filtered else investment_tables, affiliate_table_ids)


def main() -> int:
    parser = argparse.ArgumentParser(description="Custom table scraper for BCIC (no LLM)")
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
            table_filter=_bcic_table_filter,
        )
    return run_single_filing(
        TICKER, args.filing_type, args.year, args.quarter,
        output_dir, args.debug_headers, args.force,
        table_filter=_bcic_table_filter,
    )


if __name__ == "__main__":
    sys.exit(main())
