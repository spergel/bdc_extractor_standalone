#!/usr/bin/env python3
"""
Check extracted portfolio totals vs expected (from filing or investor relations).
Helps spot underreporting (e.g. missing tables or rows).

Usage:
  python scripts/check_extraction_totals.py CSWC 2026-02-02
  python scripts/check_extraction_totals.py CSWC 2026-02-02 --expected-millions 2000
"""

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output"


def main():
    import argparse
    p = argparse.ArgumentParser(description="Check extraction total fair value vs expected")
    p.add_argument("ticker", help="e.g. CSWC")
    p.add_argument("filing_date", help="e.g. 2026-02-02")
    p.add_argument("--expected-millions", type=float, default=None, help="Expected total portfolio in $ millions (e.g. 2000)")
    p.add_argument("--expected-thousands", type=float, default=None, help="Expected total in thousands (if known)")
    args = p.parse_args()

    path = OUTPUT_DIR / f"{args.ticker}_investments_{args.filing_date}.csv"
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    # Assume amounts are in thousands unless we have evidence otherwise (validation report says)
    total_raw = 0.0
    row_count = 0
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                fv = row.get("fair_value", "").strip().replace(",", "")
                if fv:
                    total_raw += float(fv)
                    row_count += 1
            except ValueError:
                pass

    # Most BDCs report in thousands
    total_millions = total_raw / 1_000.0
    total_thousands = total_raw

    print(f"# Extraction totals: {args.ticker} {args.filing_date}")
    print(f"  Rows with fair_value: {row_count}")
    print(f"  Sum fair_value (raw): {total_raw:,.0f}")
    print(f"  If in thousands -> ${total_millions:,.1f} million")
    print()

    if args.expected_millions is not None:
        gap = args.expected_millions - total_millions
        pct = 100 * total_millions / args.expected_millions if args.expected_millions else 0
        print(f"  Expected (you): ${args.expected_millions:,.0f} million")
        print(f"  Extracted:      ${total_millions:,.1f} million ({pct:.1f}% of expected)")
        print(f"  Gap:            ${gap:,.1f} million missing")
        if gap > 100:
            print()
            print("  Possible causes:")
            print("  - Main schedule table in a different document (exhibit) not processed")
            print("  - Table filtered out as 'year-end only' (wrong period_end_date) or failed investment-table detection")
            print("  - To see which tables were selected for this filing:")
            print(f"    python scripts/detect_filing_tables.py {args.ticker} {args.filing_date}")
    elif args.expected_thousands is not None:
        gap = args.expected_thousands - total_raw
        pct = 100 * total_raw / args.expected_thousands if args.expected_thousands else 0
        print(f"  Expected (raw thousands): {args.expected_thousands:,.0f}")
        print(f"  Extracted:                 {total_raw:,.0f} ({pct:.1f}%)")
        print(f"  Gap:                       {gap:,.0f} thousands")
    else:
        print("  Tip: pass --expected-millions 2000 to compare to filing total (e.g. CSWC ~$2B).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
