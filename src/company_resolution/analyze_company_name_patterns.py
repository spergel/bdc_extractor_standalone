#!/usr/bin/env python3
"""
Find naming patterns in portfolio company names: prefixes shared by multiple names,
and suffixes that differ. Helps decide how to normalize for company_id (e.g. truncate after LLC).

Usage (from repo root):
  python src/company_resolution/analyze_company_name_patterns.py
  python src/company_resolution/analyze_company_name_patterns.py --min-prefix-len 15 --top 80
"""

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import List, Set, Tuple


def load_all_raw_names(investments_dir: Path) -> Set[str]:
    """Load unique raw company_name from all TICKER.csv files."""
    names = set()
    for path in sorted(investments_dir.glob("*.csv")):
        if path.name.endswith(".BEFORE.csv") or path.parent != investments_dir:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    raw = (row.get("company_name") or "").strip()
                    if raw:
                        names.add(raw)
        except Exception as e:
            print(f"Warning: {path}: {e}")
    return names


def find_prefix_suffix_pairs(names: Set[str]) -> List[Tuple[str, str, str]]:
    """
    Find (prefix, full_name, suffix) where full_name = prefix + separator + suffix
    and prefix is itself in names (so we have two names: prefix and full_name).
    Separator is space or " - ".
    """
    sorted_names = sorted(names)
    out = []
    for full in sorted_names:
        # Try: full = prefix + " " + suffix or prefix + " - " + suffix
        for sep in (" - ", " "):
            if sep not in full:
                continue
            idx = full.find(sep)
            if idx <= 0:
                continue
            prefix = full[:idx].strip()
            suffix = full[idx + len(sep) :].strip()
            if not prefix or not suffix:
                continue
            if prefix in names and full != prefix:
                out.append((prefix, full, suffix))
                break  # one way to split is enough
    return out


def find_common_prefix_groups(names: Set[str], min_chars: int = 10) -> List[Tuple[str, List[str]]]:
    """
    Group names by common prefix (first N chars). Returns groups where at least 2 names
    share the same prefix of length >= min_chars and differ in the rest.
    """
    by_prefix: dict[str, list[str]] = defaultdict(list)
    for n in names:
        for length in range(min_chars, min(len(n), 80)):
            p = n[:length]
            # Only consider prefix that ends at a word boundary (space or end)
            if length < len(n) and n[length] not in " \t-":
                continue
            prefix = n[:length].rstrip()
            if len(prefix) < min_chars:
                continue
            by_prefix[prefix].append(n)
    return [(p, sorted(v)) for p, v in sorted(by_prefix.items()) if len(v) >= 2]


def main():
    import argparse
    p = argparse.ArgumentParser(description="Analyze company name prefix/suffix patterns")
    p.add_argument("--investments-dir", default="frontend/public/data/investments", help="Dir with TICKER.csv")
    p.add_argument("--min-prefix-len", type=int, default=12, help="Min prefix length for common-prefix groups")
    p.add_argument("--top", type=int, default=50, help="Max number of prefix groups to print")
    p.add_argument("--out", default=None, help="Optional CSV path to write prefix,suffix samples")
    args = p.parse_args()

    inv_dir = Path(args.investments_dir)
    if not inv_dir.exists():
        print(f"Directory not found: {inv_dir}")
        return 1

    names = load_all_raw_names(inv_dir)
    print(f"Loaded {len(names)} unique raw company names from {inv_dir}\n")

    # 1) Strict prefix pairs: A and "A something" both exist
    pairs = find_prefix_suffix_pairs(names)
    print(f"=== Names where a shorter name is a strict prefix ({len(pairs)} pairs) ===\n")
    by_prefix: dict[str, list[str]] = defaultdict(list)
    for prefix, full, suffix in pairs:
        by_prefix[prefix].append(suffix)

    for i, (prefix, suffixes) in enumerate(sorted(by_prefix.items(), key=lambda x: -len(x[1]))):
        if i >= args.top:
            print(f"... and {len(by_prefix) - args.top} more prefix groups")
            break
        unique_suffixes = sorted(set(suffixes))
        print(f"  Prefix ({len(suffixes)} variants): {prefix!r}")
        for s in unique_suffixes[:5]:
            print(f"    + {s!r}")
        if len(unique_suffixes) > 5:
            print(f"    ... and {len(unique_suffixes) - 5} more")
        print()

    # 2) Common prefix by first N chars (catch "same start, different tail")
    print(f"\n=== Names sharing same start (prefix length >= {args.min_prefix_len}) ===\n")
    groups = find_common_prefix_groups(names, min_chars=args.min_prefix_len)
    # Sort by number of names in group desc, then by prefix length desc
    groups.sort(key=lambda x: (-len(x[1]), -len(x[0])))

    for i, (prefix, group) in enumerate(groups):
        if i >= args.top:
            print(f"... and {len(groups) - args.top} more prefix groups")
            break
        # Show suffix after prefix for each name
        print(f"  Prefix {len(prefix)} chars: {prefix!r}  ({len(group)} names)")
        for full in group[:6]:
            tail = full[len(prefix):].strip()
            if not tail:
                tail = "(exact match)"
            print(f"    -> tail: {tail[:70]!r}" + ("..." if len(tail) > 70 else ""))
        if len(group) > 6:
            print(f"    ... and {len(group) - 6} more")
        print()

    if args.out:
        rows = []
        for prefix, suffixes in sorted(by_prefix.items(), key=lambda x: -len(x[1])):
            for s in sorted(set(suffixes)):
                rows.append({"prefix": prefix, "suffix": s})
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["prefix", "suffix"])
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} rows to {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
