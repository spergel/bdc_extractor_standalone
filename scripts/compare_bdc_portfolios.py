#!/usr/bin/env python3
"""
Compare portfolio overlap between two BDCs (e.g. MAIN vs MRCC).
Reads consolidated investment CSVs, computes unique companies per ticker,
then overlap, Jaccard similarity, and sample companies in each set.

Usage:
  python scripts/compare_bdc_portfolios.py MAIN MRCC
  python scripts/compare_bdc_portfolios.py --tickers MAIN MRCC --out output/main_mrcc_comparison.md
"""

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INVESTMENTS_DIR = REPO_ROOT / "frontend" / "public" / "data" / "investments"


def normalize_name(s: str) -> str:
    """Normalize for grouping (strip, collapse spaces)."""
    if not s:
        return ""
    return " ".join((s or "").strip().split())


def load_unique_companies(ticker: str) -> set[str]:
    """Load unique company names (or company_id if present) for a ticker."""
    path = INVESTMENTS_DIR / f"{ticker}.csv"
    if not path.exists():
        return set()
    names = set()
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("company_id") or "").strip()
            name = normalize_name(row.get("company_name") or "")
            key = cid if cid and cid.startswith("co_") else name
            if key:
                names.add(key)
    return names


def main():
    import argparse
    p = argparse.ArgumentParser(description="Compare portfolio overlap between two BDCs")
    p.add_argument("tickers", nargs=2, metavar="TICKER", help="Two tickers, e.g. MAIN MRCC")
    p.add_argument("--out", default=None, help="Write report to this path (default: print)")
    p.add_argument("--sample", type=int, default=15, help="Max sample size per category (default 15)")
    args = p.parse_args()
    a, b = args.tickers[0].upper(), args.tickers[1].upper()

    set_a = load_unique_companies(a)
    set_b = load_unique_companies(b)
    if not set_a:
        print(f"No data for {a} at {INVESTMENTS_DIR / f'{a}.csv'}", file=sys.stderr)
        sys.exit(1)
    if not set_b:
        print(f"No data for {b} at {INVESTMENTS_DIR / f'{b}.csv'}", file=sys.stderr)
        sys.exit(1)

    both = set_a & set_b
    only_a = set_a - set_b
    only_b = set_b - set_a
    union = set_a | set_b

    jaccard = len(both) / len(union) if union else 0.0
    overlap_pct = 100 * (2 * len(both)) / (len(set_a) + len(set_b)) if (set_a or set_b) else 0.0

    lines = [
        f"# Portfolio comparison: {a} vs {b}",
        "",
        "## Counts",
        f"- **{a}** unique companies: {len(set_a)}",
        f"- **{b}** unique companies: {len(set_b)}",
        f"- **In both**: {len(both)}",
        f"- **Only in {a}**: {len(only_a)}",
        f"- **Only in {b}**: {len(only_b)}",
        "",
        "## Similarity",
        f"- **Jaccard** (intersection / union): {jaccard:.2%}",
        f"- **Overlap** (2×both / (|A|+|B|)): {overlap_pct:.1f}%",
        "",
        f"## Sample: companies in both ({min(args.sample, len(both))} shown)",
        "",
    ]
    for name in sorted(both)[: args.sample]:
        lines.append(f"- {name}")
    if len(both) > args.sample:
        lines.append(f"- ... and {len(both) - args.sample} more")
    lines.extend([
        "",
        f"## Sample: only in {a} ({min(args.sample, len(only_a))} shown)",
        "",
    ])
    for name in sorted(only_a)[: args.sample]:
        lines.append(f"- {name}")
    if len(only_a) > args.sample:
        lines.append(f"- ... and {len(only_a) - args.sample} more")
    lines.extend([
        "",
        f"## Sample: only in {b} ({min(args.sample, len(only_b))} shown)",
        "",
    ])
    for name in sorted(only_b)[: args.sample]:
        lines.append(f"- {name}")
    if len(only_b) > args.sample:
        lines.append(f"- ... and {len(only_b) - args.sample} more")

    text = "\n".join(lines)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"Wrote {out_path}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
