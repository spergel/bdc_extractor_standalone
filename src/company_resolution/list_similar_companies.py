#!/usr/bin/env python3
"""
List company_id pairs whose canonical names are very similar (possible same company split).

Reads companies_index.json, uses block key + token_sort_ratio (same logic as resolve_companies
and diagnose_data_quality). Use this to review merge candidates after running resolve.

  python src/company_resolution/list_similar_companies.py
  python src/company_resolution/list_similar_companies.py --threshold 85 --out output/merge_candidates.csv
"""

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "frontend" / "public" / "data"


def _block_key(normalized: str) -> str:
    """First token lowercased (same as resolve_companies)."""
    if not normalized or not normalized.strip():
        return "__empty__"
    tokens = re.split(r"[\s,]+", normalized.strip().lower(), maxsplit=1)
    return tokens[0] if tokens else "__empty__"


def find_merge_candidates(
    cid_to_canonical: Dict[str, str],
    threshold: int = 88,
) -> List[Dict]:
    """Pairs of company_ids with very similar canonical names."""
    if not fuzz:
        return []
    canonical_list = [(cid, name) for cid, name in cid_to_canonical.items() if name]
    block_to_pairs: Dict[str, List[tuple]] = defaultdict(list)
    for cid, name in canonical_list:
        block_to_pairs[_block_key(name)].append((cid, name))

    pairs: List[Dict] = []
    for block, group in block_to_pairs.items():
        if block == "__empty__" or len(group) < 2:
            continue
        for i, (cid1, name1) in enumerate(group):
            for (cid2, name2) in group[i + 1 :]:
                if cid1 == cid2:
                    continue
                score = fuzz.token_sort_ratio(name1, name2)
                if score >= threshold:
                    pairs.append({
                        "company_id_1": cid1,
                        "company_id_2": cid2,
                        "canonical_1": name1,
                        "canonical_2": name2,
                        "similarity": score,
                    })
    pairs.sort(key=lambda x: -x["similarity"])
    return pairs


def load_companies_index(data_dir: Path) -> Dict[str, str]:
    """company_id -> canonical_name."""
    path = data_dir / "companies_index.json"
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for c in data.get("companies", []):
        cid = (c.get("company_id") or "").strip()
        canonical = (c.get("canonical_name") or "").strip()
        if cid:
            out[cid] = canonical
    return out


def main():
    import argparse
    p = argparse.ArgumentParser(description="List similar canonical company names (merge candidates)")
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Data dir containing companies_index.json")
    p.add_argument("--threshold", type=int, default=88, help="Fuzz token_sort_ratio threshold (0-100)")
    p.add_argument("--out", type=Path, default=None, help="Write CSV to this path")
    p.add_argument("-q", "--quiet", action="store_true", help="Only print counts, no table")
    args = p.parse_args()

    cid_to_canonical = load_companies_index(args.data_dir)
    if not cid_to_canonical:
        print("No companies_index.json found at", args.data_dir / "companies_index.json")
        return

    if not fuzz:
        print("Install rapidfuzz to use this script: pip install rapidfuzz")
        return

    candidates = find_merge_candidates(cid_to_canonical, threshold=args.threshold)
    print(f"Merge candidates (similarity >= {args.threshold}): {len(candidates)} pairs")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["similarity", "canonical_1", "company_id_1", "canonical_2", "company_id_2"])
            w.writeheader()
            for m in candidates:
                w.writerow({
                    "similarity": m["similarity"],
                    "canonical_1": m["canonical_1"],
                    "company_id_1": m["company_id_1"],
                    "canonical_2": m["canonical_2"],
                    "company_id_2": m["company_id_2"],
                })
        print(f"Wrote {args.out}")

    if not args.quiet and candidates:
        print()
        for m in candidates[:50]:
            sim = int(m["similarity"]) if isinstance(m["similarity"], float) else m["similarity"]
            print(f"  {sim:3d}  {m['canonical_1'][:45]:45s}  {m['company_id_1']}  |  {m['canonical_2'][:45]:45s}  {m['company_id_2']}")
        if len(candidates) > 50:
            print(f"  ... and {len(candidates) - 50} more (use --out to export all)")


if __name__ == "__main__":
    main()
