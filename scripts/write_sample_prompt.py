#!/usr/bin/env python3
"""
Build and save the full LLM prompt for one table (no LLM call, no fetch). Uses the
detect file if present, otherwise fetches the filing and uses first table. Use this
to inspect exactly what _build_llm_prompt sends.

Usage (from project root):
  python scripts/write_sample_prompt.py CSWC 2026-02-02
  python scripts/write_sample_prompt.py CSWC 2026-02-02 -o output/CSWC_sample_prompt.txt
  python scripts/write_sample_prompt.py CSWC 2026-02-02 --detect-file output/CSWC_detect_2026-02-02.txt
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src" / "extraction") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src" / "extraction"))

from llm_table_scraper import LLMTableScraper


def parse_detect_file(path: Path):
    """Parse detect file; return list of {num, table_id, length, body}."""
    text = path.read_text(encoding="utf-8")
    tables = []
    blocks = re.split(r"\[TABLE\s+(\d+)\]\s*\n", text)
    i = 1
    while i + 1 < len(blocks):
        num = blocks[i].strip()
        content = blocks[i + 1]
        table_id = ""
        length = ""
        body_lines = []
        for line in content.split("\n"):
            if line.startswith("table_id="):
                table_id = line.replace("table_id=", "").strip()
            elif line.startswith("length="):
                length = line.replace("length=", "").strip()
            elif line.strip() and not line.startswith("table_") and not line.startswith("length="):
                body_lines.append(line.strip())
        body = " ".join(body_lines) if body_lines else content.strip()
        tables.append({"num": num, "table_id": table_id, "length": length, "body": body})
        i += 2
    return tables


def main():
    import argparse
    p = argparse.ArgumentParser(description="Write full LLM prompt for one table (no LLM call)")
    p.add_argument("ticker", help="e.g. CSWC")
    p.add_argument("filing_date", help="e.g. 2026-02-02")
    p.add_argument("-o", "--output", default=None, help="Output path (default: output/{ticker}_sample_prompt.txt)")
    p.add_argument("--detect-file", default=None, help="Use this detect file instead of fetching (e.g. output/CSWC_detect_2026-02-02.txt)")
    p.add_argument("--table", type=int, default=0, help="Which table index (0-based) to use (default 0)")
    args = p.parse_args()

    out_path = args.output
    if not out_path:
        out_path = REPO_ROOT / "output" / f"{args.ticker}_sample_prompt.txt"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Get table text: from detect file or by fetching
    table_text = None
    if args.detect_file:
        detect_path = Path(args.detect_file)
        if not detect_path.is_absolute():
            detect_path = REPO_ROOT / detect_path
        if detect_path.exists():
            tables = parse_detect_file(detect_path)
            if tables and args.table < len(tables):
                # Detect file body is space-joined; we need one line per row for the prompt.
                # The detect format is the OLD flattened text (single line). So we have one long line.
                # For the prompt we now expect tab-sep cells and newline-sep rows. So if the detect
                # file was generated after our change, it would already be row-per-line. If it's old,
                # body is one line. Pass it as-is; the prompt will still work (one row).
                table_text = tables[args.table]["body"]
            if not table_text:
                print("Detect file has no table at index", args.table)
                sys.exit(1)
        else:
            print("Detect file not found:", detect_path)
            sys.exit(1)
    else:
        # Fetch filing and get first table text (row-per-line)
        from sec_api_client import SECAPIClient
        sec = SECAPIClient()
        filings_10q = sec.get_historical_10q_filings(args.ticker, years_back=2)
        filings_10k = sec.get_historical_10k_filings(args.ticker, years_back=2)
        all_filings = filings_10q + filings_10k
        all_filings.sort(key=lambda f: f["date"], reverse=True)
        match = None
        for f in all_filings:
            if f["date"] == args.filing_date:
                match = f
                break
        if not match:
            print("No filing found for", args.ticker, args.filing_date)
            sys.exit(1)
        if match.get("period_end_date"):
            if not hasattr(sec, "_cached_period_date"):
                sec._cached_period_date = {}
            sec._cached_period_date[match["index_url"]] = match["period_end_date"]
        scraper = LLMTableScraper(output_dir=str(REPO_ROOT / "output"))
        scraper.sec_client._cached_period_date = getattr(scraper.sec_client, "_cached_period_date", {}) or {}
        if match.get("period_end_date"):
            scraper.sec_client._cached_period_date[match["index_url"]] = match["period_end_date"]
        filing_result = scraper.sec_client.fetch_filing_by_index_url(
            index_url=match["index_url"], ticker=args.ticker, filing_type=match["form"], save_to_file=False,
            document_types=[".htm", ".html"],
            main_document_only=True,
        )
        if not filing_result:
            print("Failed to fetch filing")
            sys.exit(1)
        tables = scraper.extract_tables_from_filing_documents(filing_result)
        from table_detection import fallback_table_detection, select_current_quarter_tables
        investment_tables = fallback_table_detection(
            tables, filing_result.filing_date, match["form"],
            getattr(filing_result, "period_end_date", None),
        )
        current_quarter_tables, _ = select_current_quarter_tables(
            investment_tables, filing_result.filing_date, match["form"],
            getattr(filing_result, "period_end_date", None),
        )
        if not current_quarter_tables:
            print("No current-quarter tables selected")
            sys.exit(1)
        _, table_text, _, _ = current_quarter_tables[args.table] if args.table < len(current_quarter_tables) else current_quarter_tables[0]

    scraper = LLMTableScraper(output_dir=str(REPO_ROOT / "output"))
    filing_info = {
        "ticker": args.ticker,
        "filing_type": "10-Q",
        "filing_date": args.filing_date,
        "unit_scale": "thousands",
    }
    chunks = scraper._chunk_text_table(table_text)
    prompt = scraper._build_llm_prompt(chunks[0], args.table, filing_info, None)
    out_path.write_text(prompt, encoding="utf-8")
    print(f"Wrote prompt ({len(prompt):,} chars) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
