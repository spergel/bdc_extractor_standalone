#!/usr/bin/env python3
"""
Scan investment CSVs for rows with industry "Other" or empty whose company_name
looks like it has an industry/section prefix we could strip (to set industry).

Useful to find more BCSF/TCPC/TRIN-style patterns to add to ticker cleanup.

Usage:
    python scripts/find_other_industry_candidates.py [--data-dir PATH] [--limit N]
    python scripts/find_other_industry_candidates.py --sample-other 30   # sample names that don't match any prefix

Output:
  - Counts of Other/empty industry rows per ticker.
  - Rows that would already be fixed by current clean_company_name (extracted_industry set).
  - "Candidates": Other/empty rows whose company_name starts with a known prefix-like
    pattern but cleanup does not set extracted_industry (suggesting a new rule).
  - With --sample-other N: N sample company_name that are Other/empty, not fixed, and
    don't start with a known prefix (to spot new patterns).
"""

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src" / "processing"))

from standardization_rules import clean_company_name, standardize_industry

INVESTMENTS_DIR = REPO_ROOT / "frontend" / "public" / "data" / "investments"

# Prefixes that often indicate "section/industry + company" in company_name.
# Order: longer/more specific first (we match first hit).
PREFIX_PATTERNS = [
    "Non-controlled/Non-Affiliated Investments ",
    "Non-Controlled/Non-Affiliated Investments ",
    "Non-Controlled/Affiliated Investments ",
    "Non-Controlled/Affiliate Investments ",
    "Portfolio Company Debt Securities- ",
    "Portfolio Company Warrant Investments ",
    "Portfolio Company Equity Investments ",
    "Equity Securities Internet Software and Services ",
    "Equity Securities Internet Software and Service ",
    "Equity Securities Healthcare Providers and Services ",
    "Equity Securities Professional Services ",
    "Equity Securities Software ",
    "Equity Securities ",
    "Debt Investments ",
    "Equity and Other Investments ",
    "Other Investments ",
    "Affiliate Investments ",
    "Portfolio Company ",
    "Services: Business ",
    "Services: ",
    "FIRE: Finance ",
    "FIRE: Insurance ",
    "-FIRE: Insurance",
    "Healthcare & Pharmaceuticals ",
    "Healthcare & ",
    "Healthcare ",
    "Consumer Goods: Non-Durable ",
    "Consumer Goods: Durable ",
    "Consumer Goods: Wholesale ",
    "Consumer Goods: ",
    "Capital Equipment ",
    "Construction & Building Service ",
    "Construction & Building ",
    "Chemicals Plastics & Rubber ",
    "Chemicals ",
    "Beverage Food & Tobacco ",
    "Beverage ",
    "Aerospace & Defense ",
    "Automotive ",
    "Professional Services ",
    "Professional Service ",
    "Internet Software and Services ",
    "Internet Software and Service ",
    "Software & Technology ",
    "Software ",
    "Application Software ",
    "Healthcare Technology ",
    "Digital Assets Technology and Services ",
    "Digital Assets ",
    "Real Estate Technology ",
    "Marketing Media and Entertainment ",
    "Biotechnology ",
    "Pharmaceuticals ",
]


def normalize_prefix(s: str) -> str:
    """First matching prefix from PREFIX_PATTERNS (case-insensitive), or None."""
    lower = s.strip()
    if not lower:
        return None
    for p in PREFIX_PATTERNS:
        if lower.startswith(p.lower()):
            return p.strip()
    return None


def main():
    parser = argparse.ArgumentParser(description="Find Other/empty industry rows that look like prefix+company")
    parser.add_argument("--data-dir", type=Path, default=INVESTMENTS_DIR, help="Investments CSV directory")
    parser.add_argument("--limit", type=int, default=0, help="Max CSVs to scan (0 = all)")
    parser.add_argument("--verbose", action="store_true", help="Print each candidate row")
    parser.add_argument("--sample-other", type=int, default=0, metavar="N", help="Print N sample Other/empty names that don't match any prefix (to spot new patterns)")
    args = parser.parse_args()

    data_dir = args.data_dir
    if not data_dir.exists():
        print(f"ERROR: {data_dir} not found", file=sys.stderr)
        return 1

    csv_files = sorted(data_dir.glob("*.csv")) + sorted(data_dir.glob("*/*.csv"))
    if args.limit:
        csv_files = csv_files[: args.limit]

    if not csv_files:
        print("No CSV files found")
        return 0

    # Counts: (ticker, industry) -> count
    other_empty_by_ticker = defaultdict(int)
    # Rows with Other/empty that cleanup would fix (extracted_industry set)
    would_fix = 0
    # Candidates: (ticker, prefix) -> (count, set of sample company_name)
    candidate_counts: dict[tuple[str, str], int] = defaultdict(int)
    candidate_samples: dict[tuple[str, str], list[str]] = defaultdict(list)
    # For --sample-other: collect names that are Other/empty, not fixed, no prefix match
    sample_other: list[tuple[str, str]] = []  # (ticker, company_name)
    total_other_empty = 0

    for csv_path in csv_files:
        ticker = csv_path.parent.name if csv_path.parent != data_dir else csv_path.stem
        if csv_path.parent == data_dir and csv_path.suffix == ".csv":
            ticker = csv_path.stem

        try:
            with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames or "company_name" not in reader.fieldnames:
                    continue
                for row in reader:
                    ind = (row.get("industry") or "").strip()
                    name = (row.get("company_name") or "").strip()
                    if ind and ind != "Other":
                        continue
                    total_other_empty += 1
                    other_empty_by_ticker[ticker] += 1

                    cleaned_name, extracted = clean_company_name(name, ticker=ticker)
                    if extracted and standardize_industry(extracted):
                        would_fix += 1
                        continue
                    prefix = normalize_prefix(name)
                    if prefix:
                        key = (ticker, prefix)
                        candidate_counts[key] += 1
                        if name not in candidate_samples[key] and len(candidate_samples[key]) < 5:
                            candidate_samples[key].append(name)
                    elif args.sample_other and len(sample_other) < args.sample_other:
                        if name and (ticker, name) not in [(t, n) for t, n in sample_other]:
                            sample_other.append((ticker, name))
        except Exception as e:
            print(f"  ERROR reading {csv_path}: {e}", file=sys.stderr)

    # Report
    print("=" * 70)
    print("Other/empty industry scan")
    print("=" * 70)
    print(f"CSVs scanned:     {len(csv_files)}")
    print(f"Total rows with industry Other or empty: {total_other_empty:,}")
    print(f"Would be fixed by current cleanup (extracted_industry): {would_fix:,}")
    print(f"Candidates (Other/empty + name starts with known prefix, not fixed): {sum(candidate_counts.values()):,}")
    print()

    print("By ticker (Other/empty count):")
    for t in sorted(other_empty_by_ticker.keys(), key=lambda t: -other_empty_by_ticker[t]):
        print(f"  {t}: {other_empty_by_ticker[t]:,}")
    print()

    if candidate_counts:
        print("Candidates by (ticker, prefix) — consider adding cleanup rules:")
        for (ticker, prefix), count in sorted(candidate_counts.items(), key=lambda x: -x[1]):
            print(f"  [{ticker}] prefix \"{prefix}\"  count={count}")
            for sample in candidate_samples.get((ticker, prefix), [])[:5]:
                short = sample[:78] + "..." if len(sample) > 78 else sample
                print(f"    e.g. {short!r}")
            if args.verbose and len(candidate_samples.get((ticker, prefix), [])) > 5:
                for sample in candidate_samples[(ticker, prefix)][5:]:
                    print(f"    {sample!r}")
        print()
    else:
        print("No candidates (all Other/empty with a prefix are already fixed by cleanup).")

    if args.sample_other and sample_other:
        print("Sample Other/empty names (no prefix match, not fixed by cleanup):")
        for ticker, name in sample_other[: args.sample_other]:
            short = name[:72] + "..." if len(name) > 72 else name
            print(f"  [{ticker}] {short!r}")
        if len(sample_other) < args.sample_other:
            print(f"  (only {len(sample_other)} collected)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
