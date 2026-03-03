#!/usr/bin/env python3
"""
Find "similar instances" to DaySmart / Curio / International Cruise:
- Company names that still contain industry/section prefixes (so we can add cleanup rules).
- Companies with missing or Other primary_industry in company_exposures.

Scans: company_exposures.csv, companies_index.json, and optionally investment CSVs.

Usage:
    python scripts/find_similar_prefix_instances.py [--data-dir PATH]
    python scripts/find_similar_prefix_instances.py --scan-csvs   # also scan investment CSVs for names containing phrases
"""

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "frontend" / "public" / "data"

# Phrases that often appear as section/industry prefixes in company names.
# If a name STARTS WITH one of these (case-insensitive), it's a "similar instance".
# Keep longer/more specific first for display; we match any that the name starts with.
INDUSTRY_PREFIX_PHRASES = [
    "Human Resource Support Services ",
    "Hotels Restaurants & Leisure ",
    "Hotel Gaming and Leisure ",
    "Hotel Gaming Leisure ",
    "Household durables - ",
    "Household durables-",
    "Household Durables ",
    "Non-controlled/Non-Affiliated Investments ",
    "Equity Securities Internet Software and Services ",
    "Equity Securities Professional Services ",
    "Equity Securities Healthcare ",
    "Equity Securities Software ",
    "Portfolio Company Debt Securities- ",
    "Portfolio Company Warrant Investments ",
    "Portfolio Company Equity Investments ",
    "Services: Business ",
    "Services: ",
    "FIRE: Finance ",
    "FIRE: Insurance ",
    "Healthcare & Pharmaceuticals ",
    "Consumer Goods: Non-Durable ",
    "Consumer Goods: Durable ",
    "Consumer Goods: Wholesale ",
    "Capital Equipment ",
    "Construction & Building ",
    "Chemicals Plastics & Rubber ",
    "Beverage Food & Tobacco ",
    "Aerospace & Defense ",
    "Automotive ",
    "Professional Services ",
    "Professional Service ",
    "Internet Software and Services ",
    "Internet Services ",
    "Pharmaceuticals ",
    "Biotechnology ",
    "Advertising Printing & Publishing ",
    "Wireless Telecommunication Services ",
    "Consumer Services ",
    "Commercial Services & Supplies ",
    "Media & Entertainment ",
    "Automobile Components ",
    "Paper & Forest Products ",
    "Personal Care Products ",
    "Trading companies & distributors-",
    "Professional services - ",
    "Aerospace & defense - ",
    "Building products - ",
    "Household durables - ",
]


def name_starts_with_industry_phrase(name: str) -> str | None:
    """If name starts with any INDUSTRY_PREFIX_PHRASES (case-insensitive), return the phrase; else None."""
    if not name or not name.strip():
        return None
    lower = name.strip().lower()
    for phrase in INDUSTRY_PREFIX_PHRASES:
        pl = phrase.lower().rstrip()
        if not pl:
            continue
        if lower.startswith(pl):
            return phrase.strip()
    return None


def main():
    parser = argparse.ArgumentParser(description="Find company names with industry prefix or missing primary_industry")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Data directory (company_exposures.csv, companies_index.json)")
    parser.add_argument("--scan-csvs", action="store_true", help="Also scan investment CSVs for company_name starting with a phrase")
    parser.add_argument("--limit", type=int, default=0, help="Max CSV files to scan (0 = all), only if --scan-csvs")
    args = parser.parse_args()

    data_dir = args.data_dir
    if not data_dir.exists():
        print(f"ERROR: {data_dir} not found", file=sys.stderr)
        return 1

    exposures_path = data_dir / "company_exposures.csv"
    index_path = data_dir / "companies_index.json"

    # ---- 1. company_exposures: names with prefix, and missing primary_industry ----
    names_with_prefix: list[tuple[str, str, str]] = []  # (company_id, company_name, phrase)
    missing_industry: list[tuple[str, str, str]] = []   # (company_id, company_name, bdcs)

    if exposures_path.exists():
        with open(exposures_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cid = (row.get("company_id") or "").strip()
                name = (row.get("company_name") or "").strip()
                ind = (row.get("primary_industry") or "").strip()
                bdcs = (row.get("bdcs_invested") or "").strip()
                phrase = name_starts_with_industry_phrase(name)
                if phrase:
                    names_with_prefix.append((cid, name, phrase))
                if not ind or ind == "Other":
                    missing_industry.append((cid, name, bdcs))

    # ---- 2. companies_index: canonical_name with prefix ----
    index_with_prefix: list[tuple[str, str]] = []  # (canonical_name, phrase)

    if index_path.exists():
        with open(index_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        for ent in data if isinstance(data, list) else []:
            canonical = (ent.get("canonical_name") or "").strip()
            phrase = name_starts_with_industry_phrase(canonical)
            if phrase:
                index_with_prefix.append((canonical, phrase))
            for raw in ent.get("raw_names") or []:
                r = (raw if isinstance(raw, str) else "").strip()
                phrase = name_starts_with_industry_phrase(r)
                if phrase and (r, phrase) not in index_with_prefix:
                    index_with_prefix.append((r, phrase))

    # ---- 3. Optional: scan investment CSVs for company_name starting with phrase ----
    csv_matches: dict[tuple[str, str], list[str]] = defaultdict(list)  # (ticker, phrase) -> [sample names]
    if args.scan_csvs:
        inv_dir = data_dir / "investments"
        if inv_dir.exists():
            files = sorted(inv_dir.glob("*.csv")) + sorted(inv_dir.glob("*/*.csv"))
            if args.limit:
                files = files[: args.limit]
            for csv_path in files:
                ticker = csv_path.parent.name if csv_path.parent != inv_dir else csv_path.stem
                if csv_path.parent == inv_dir and csv_path.suffix == ".csv":
                    ticker = csv_path.stem
                try:
                    with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            name = (row.get("company_name") or "").strip()
                            phrase = name_starts_with_industry_phrase(name)
                            if phrase:
                                key = (ticker, phrase)
                                if name not in csv_matches[key]:
                                    csv_matches[key].append(name)
                                if len(csv_matches[key]) > 5:
                                    csv_matches[key] = csv_matches[key][:5]
                except Exception as e:
                    print(f"  ERROR {csv_path}: {e}", file=sys.stderr)

    # ---- Report ----
    print("=" * 70)
    print("Similar instances (industry/section prefix still in name)")
    print("=" * 70)

    print("\n1. company_exposures.csv — company_name starts with industry phrase:")
    if names_with_prefix:
        # Dedupe by company_id
        seen = set()
        for cid, name, phrase in names_with_prefix:
            if cid in seen:
                continue
            seen.add(cid)
            short = name[:60] + "..." if len(name) > 60 else name
            print(f"   {cid}  phrase={phrase!r}")
            print(f"      name={short!r}")
        print(f"   Total: {len(names_with_prefix)} row(s), {len(seen)} unique company_id(s)")
    else:
        print("   None found.")

    print("\n2. companies_index.json — canonical_name or raw_name starts with phrase:")
    if index_with_prefix:
        seen = set()
        for name, phrase in index_with_prefix[:50]:
            k = (name[:50], phrase)
            if k in seen:
                continue
            seen.add(k)
            short = name[:58] + "..." if len(name) > 58 else name
            print(f"   phrase={phrase!r}  -> {short!r}")
        if len(index_with_prefix) > 50:
            print(f"   ... and {len(index_with_prefix) - 50} more.")
        print(f"   Total: {len(index_with_prefix)} name(s)")
    else:
        print("   None found.")

    print("\n3. Missing or Other primary_industry (company_exposures):")
    if missing_industry:
        for cid, name, bdcs in missing_industry[:40]:
            short = name[:50] + "..." if len(name) > 50 else name
            print(f"   {cid}  bdcs={bdcs}  {short!r}")
        if len(missing_industry) > 40:
            print(f"   ... and {len(missing_industry) - 40} more.")
        print(f"   Total: {len(missing_industry)} company(ies)")
    else:
        print("   None.")

    if args.scan_csvs and csv_matches:
        print("\n4. Investment CSVs — company_name starts with phrase (by ticker):")
        for (ticker, phrase), samples in sorted(csv_matches.items(), key=lambda x: -len(x[1])):
            print(f"   [{ticker}] phrase={phrase!r}  count={len(samples)} sample(s)")
            for s in samples[:3]:
                print(f"      {s[:70]!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
