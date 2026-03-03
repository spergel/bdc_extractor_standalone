#!/usr/bin/env python3
"""
Re-apply standardization rules to all existing investment CSVs.

Iterates over:
  - frontend/public/data/investments/*.csv          (consolidated per-BDC files)
  - frontend/public/data/investments/<TICKER>/*.csv  (per-period files)

Uses restandardize_csv() from standardization_rules.py which applies the
canonical industry, investment-type and company-name cleaning in-place.

Usage:
    python scripts/restandardize_all.py [--dry-run]
"""

import argparse
import sys
from pathlib import Path

# Ensure repo root is on sys.path so we can import from src/
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src" / "processing"))

from standardization_rules import restandardize_csv, propagate_industries, ALLOWED_INDUSTRIES

INVESTMENTS_DIR = REPO_ROOT / "frontend" / "public" / "data" / "investments"

# Canonical investment types (must match backend standardize_investment_type output)
CANONICAL_INVESTMENT_TYPES = {
    "First Lien", "Second Lien", "Delayed Draw", "Revolver",
    "Subordinated Debt", "Unsecured Debt", "Preferred Equity", "Common Equity",
    "Partnership Interest", "Warrants", "Other Equity", "Other Debt",
    "Other", "",
}


def main():
    parser = argparse.ArgumentParser(description="Re-standardize all investment CSVs")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing")
    args = parser.parse_args()

    if not INVESTMENTS_DIR.exists():
        print(f"ERROR: {INVESTMENTS_DIR} not found")
        return 1

    # Collect all CSV files: top-level *.csv + subdirectory *.csv
    csv_files = sorted(INVESTMENTS_DIR.glob("*.csv")) + sorted(INVESTMENTS_DIR.glob("*/*.csv"))

    if not csv_files:
        print("No CSV files found")
        return 1

    print(f"Found {len(csv_files)} CSV files to restandardize")
    if args.dry_run:
        print("(dry run — no files will be written)\n")

    total_rows = 0
    total_changes = 0
    files_changed = 0

    for csv_path in csv_files:
        result = restandardize_csv(csv_path, dry_run=args.dry_run)
        if "error" in result:
            print(f"  ERROR: {result['error']}")
            continue
        total_rows += result["rows"]
        total_changes += result["changes"]
        if result["changes"] > 0:
            files_changed += 1
            rel = csv_path.relative_to(INVESTMENTS_DIR)
            print(f"  {rel}: {result['rows']} rows, {result['changes']} changed")

    print(f"\n{'='*60}")
    print(f"Files scanned:  {len(csv_files)}")
    print(f"Files changed:  {files_changed}")
    print(f"Total rows:     {total_rows:,}")
    print(f"Total changes:  {total_changes:,}")

    # Propagate industry by company_id: fill Other/empty when same company has industry elsewhere
    if not args.dry_run and total_changes >= 0:
        prop = propagate_industries(INVESTMENTS_DIR, dry_run=False)
        if prop.get("rows_filled", 0) > 0:
            print(f"\nIndustry propagation (company_id): {prop['rows_filled']:,} rows filled in {prop['files_changed']} files")

    # Quick audit: check for non-canonical values remaining
    if not args.dry_run:
        _audit_canonical(csv_files)

    return 0


def _audit_canonical(csv_files: list[Path]):
    """Scan CSVs for any industry / investment_type values outside the canonical sets."""
    import csv as csv_mod

    bad_industries: dict[str, int] = {}
    bad_types: dict[str, int] = {}
    allowed_industries_set = set(ALLOWED_INDUSTRIES) | {"Other", ""}

    for path in csv_files:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv_mod.DictReader(f)
            for row in reader:
                ind = row.get("industry", "")
                if ind and ind not in allowed_industries_set:
                    bad_industries[ind] = bad_industries.get(ind, 0) + 1
                inv = row.get("investment_type", "")
                if inv and inv not in CANONICAL_INVESTMENT_TYPES:
                    bad_types[inv] = bad_types.get(inv, 0) + 1

    if bad_industries:
        print(f"\nWARNING: {len(bad_industries)} non-canonical industry values remain:")
        for val, count in sorted(bad_industries.items(), key=lambda x: -x[1])[:20]:
            print(f"  {val!r}: {count}")
    else:
        print("\nAll industry values are canonical.")

    if bad_types:
        print(f"\nWARNING: {len(bad_types)} non-canonical investment type values remain:")
        for val, count in sorted(bad_types.items(), key=lambda x: -x[1])[:20]:
            print(f"  {val!r}: {count}")
    else:
        print("All investment type values are canonical.")


if __name__ == "__main__":
    sys.exit(main())
