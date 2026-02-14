#!/usr/bin/env python3
"""
List unique portfolio companies for a given BDC ticker (e.g. GSBD).
Reads the ticker's consolidated CSV, aggregates by company (name and optionally company_id),
optionally resolves to canonical_name via companies_index.json, and writes a review file.

Usage:
  python src/company_resolution/list_unique_companies.py --ticker GSBD
  python src/company_resolution/list_unique_companies.py --ticker GSBD --out output/GSBD_unique_companies.csv
"""

import csv
import json
import logging
from pathlib import Path
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def load_companies_index(data_dir: Path) -> dict:
    """Return dict: company_id -> { canonical_name, name_variants }, and name -> company_id."""
    path = data_dir / "companies_index.json"
    if not path.exists():
        return {}, {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    by_id = {}
    name_to_id = {}
    for c in data.get("companies", []):
        cid = c.get("company_id")
        canon = c.get("canonical_name", "")
        variants = c.get("name_variants") or []
        if cid:
            by_id[cid] = {"canonical_name": canon, "name_variants": variants}
            name_to_id[canon] = cid
            for v in variants:
                name_to_id[v] = cid
    return by_id, name_to_id


def list_unique_companies(
    ticker: str,
    investments_dir: Path,
    data_dir: Path,
    out_path: Path,
) -> None:
    """
    Load ticker CSV (e.g. GSBD.csv), aggregate by company_name (and company_id if present),
    resolve to canonical via companies_index, write CSV for review.
    """
    ticker = ticker.upper()
    csv_path = investments_dir / f"{ticker}.csv"
    if not csv_path.exists():
        logger.error("File not found: %s", csv_path)
        return

    by_id, name_to_id = load_companies_index(data_dir)
    # Aggregate: key = (company_id or "", company_name) -> { count, industry (first), raw_names }
    agg = defaultdict(lambda: {"count": 0, "industry": "", "raw_names": set()})
    has_company_id = False

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("company_name") or "").strip()
            if not name:
                continue
            cid = (row.get("company_id") or "").strip()
            if cid:
                has_company_id = True
            industry = (row.get("industry") or "").strip()
            key = (cid or name, name)
            agg[key]["count"] += 1
            agg[key]["raw_names"].add(name)
            if industry and not agg[key]["industry"]:
                agg[key]["industry"] = industry

    # Resolve to canonical where possible
    rows_out = []
    for (cid_or_name, raw_name), data in sorted(agg.items(), key=lambda x: (-x[1]["count"], x[0][1])):
        canonical = ""
        resolved_id = (cid_or_name if cid_or_name.startswith("co_") else "") or ""
        if name_to_id and raw_name in name_to_id:
            resolved_id = name_to_id[raw_name]
            canonical = by_id.get(resolved_id, {}).get("canonical_name", "")
        elif resolved_id and resolved_id in by_id:
            canonical = by_id[resolved_id].get("canonical_name", "")
        rows_out.append({
            "company_name": raw_name,
            "company_id": resolved_id,
            "canonical_name": canonical or raw_name,
            "num_holdings": data["count"],
            "industry": data["industry"],
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["company_name", "company_id", "canonical_name", "num_holdings", "industry"]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows_out)
    logger.info("Wrote %d unique companies to %s (CSV has company_id: %s)", len(rows_out), out_path, has_company_id)


def main():
    import argparse
    p = argparse.ArgumentParser(description="List unique companies for a BDC ticker")
    p.add_argument("--ticker", default="GSBD", help="BDC ticker (e.g. GSBD)")
    p.add_argument("--investments-dir", default="frontend/public/data/investments", help="Directory with TICKER.csv")
    p.add_argument("--data-dir", default="frontend/public/data", help="Directory with companies_index.json")
    _repo_root = Path(__file__).resolve().parent.parent.parent
    p.add_argument("--out", default=None, help="Output CSV path (default: repo root output/<TICKER>_unique_companies.csv)")
    args = p.parse_args()
    out = Path(args.out) if args.out else (_repo_root / "output" / f"{args.ticker.upper()}_unique_companies.csv")
    list_unique_companies(
        args.ticker,
        Path(args.investments_dir),
        Path(args.data_dir),
        out,
    )


if __name__ == "__main__":
    main()
