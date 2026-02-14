#!/usr/bin/env python3
"""
Post-process extraction CSVs to remove rows that are clearly errors:
- LLM artifact rows (* *Wait, Undrawn = ..., Principal 12345, backtick/pipe junk)
- Empty header rows (company name only, duplicated on rows with data)

Run after extractions to fix files without re-scraping.

Usage:
    python src/processing/clean_extraction_errors.py --directory output/
    python src/processing/clean_extraction_errors.py --file output/GSBD_investments_2025-11-06.csv
"""

import argparse
import logging
import sys
from pathlib import Path

# Allow importing from extraction/data_cleaning when run from project root
_here = Path(__file__).resolve().parent
_extraction = _here.parent / "extraction"
if _extraction.exists() and str(_extraction) not in sys.path:
    sys.path.insert(0, str(_extraction))

from data_cleaning import filter_llm_artifact_rows, remove_empty_header_rows

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def clean_file(path: Path, dry_run: bool = False) -> tuple[int, int]:
    """
    Read CSV as lines, apply error-cleaning filters, write back.
    Returns (rows_before, rows_removed).
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        return 0, 0

    header = lines[0]
    before_count = len(lines) - 1  # data rows only

    # Apply filters (same order as in llm_table_scraper)
    cleaned = [header] + lines[1:]
    cleaned = remove_empty_header_rows(cleaned)
    cleaned = filter_llm_artifact_rows(cleaned)

    after_count = len(cleaned) - 1
    removed = before_count - after_count

    if removed > 0 and not dry_run:
        path.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
        logger.info("%s: removed %d error rows (%d -> %d)", path.name, removed, before_count, after_count)
    elif removed > 0 and dry_run:
        logger.info("%s [dry-run]: would remove %d error rows (%d -> %d)", path.name, removed, before_count, after_count)

    return before_count, removed


def main():
    parser = argparse.ArgumentParser(
        description="Remove clearly erroneous rows from extraction CSVs (LLM artifacts, empty headers)."
    )
    parser.add_argument("--directory", "-d", type=Path, default=None, help="Process all *_investments_*.csv in this directory")
    parser.add_argument("--file", "-f", type=Path, default=None, help="Process this single file")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be removed without writing")
    args = parser.parse_args()

    if args.file:
        if not args.file.is_file():
            logger.error("Not a file: %s", args.file)
            sys.exit(1)
        files = [args.file]
    elif args.directory:
        if not args.directory.is_dir():
            logger.error("Not a directory: %s", args.directory)
            sys.exit(1)
        files = sorted(args.directory.glob("*_investments_*.csv"))
        # Skip .BEFORE backups
        files = [p for p in files if not p.stem.endswith(".BEFORE")]
    else:
        parser.error("Provide --directory or --file")

    if not files:
        logger.warning("No investment CSV files found")
        return

    total_removed = 0
    files_changed = 0
    for path in files:
        _, removed = clean_file(path, dry_run=args.dry_run)
        if removed > 0:
            total_removed += removed
            files_changed += 1

    if files_changed:
        logger.info("Done: %d file(s) cleaned, %d total error rows removed", files_changed, total_removed)
    else:
        logger.info("Done: no error rows found in %d file(s)", len(files))


if __name__ == "__main__":
    main()
