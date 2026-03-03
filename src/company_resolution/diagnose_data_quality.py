#!/usr/bin/env python3
"""
Diagnose sector/industry assignment and company merge quality.

Reads investment CSVs + companies_index + company_exposures, then writes an HTML report to:
  - output/data_quality_report.html

Use this to see:
  1. Sector conflicts: companies where primary_industry disagrees with the distribution
     of industry values on rows (e.g. "Automotive" chosen but most rows say "Healthcare").
  2. Possible merge misses: different company_ids whose canonical names are very similar
     (could be the same company split across two IDs).

Run from repo root:
  python src/company_resolution/diagnose_data_quality.py
  python src/company_resolution/diagnose_data_quality.py --out docs/data_quality_report.html
"""

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

# Repo paths
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent

# Use same normalization as resolve_companies so we compare apples to apples
try:
    import sys
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from src.processing.standardization_rules import normalize_industry as _normalize_industry
except ImportError:
    _normalize_industry = lambda x: (x or "").strip()
DEFAULT_INV_DIR = REPO_ROOT / "frontend" / "public" / "data" / "investments"
DEFAULT_DATA_DIR = REPO_ROOT / "frontend" / "public" / "data"
DEFAULT_OUT = REPO_ROOT / "output" / "data_quality_report.html"


def _block_key(normalized: str) -> str:
    if not normalized or not normalized.strip():
        return "__empty__"
    tokens = re.split(r"[\s,]+", normalized.strip().lower(), maxsplit=1)
    return tokens[0] if tokens else "__empty__"


def load_investment_rows(investments_dir: Path) -> List[Dict[str, str]]:
    """Load all rows from top-level TICKER.csv files (no subdirs)."""
    rows: List[Dict[str, str]] = []
    for path in sorted(investments_dir.glob("*.csv")):
        if path.name.endswith(".BEFORE.csv"):
            continue
        if path.parent != investments_dir:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    rows.append(dict(row))
        except Exception as e:
            print(f"Warning: failed to read {path}: {e}")
    return rows


def aggregate_industry_by_company(
    rows: List[Dict[str, str]],
) -> Dict[str, Dict[str, Any]]:
    """Per company_id: { industries: Counter, canonical_name: str, row_count: int }."""
    agg: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"industries": Counter(), "canonical_name": "", "row_count": 0}
    )
    for row in rows:
        cid = (row.get("company_id") or "").strip()
        if not cid or not cid.startswith("co_"):
            continue
        raw_ind = (row.get("industry") or row.get("industry_clean") or "").strip()
        ind = _normalize_industry(raw_ind)
        if ind:
            agg[cid]["industries"][ind] += 1
        name = (row.get("company_name") or "").strip()
        if name and not agg[cid]["canonical_name"]:
            agg[cid]["canonical_name"] = name
        agg[cid]["row_count"] += 1
    return dict(agg)


def load_company_exposures(data_dir: Path) -> Dict[str, str]:
    """company_id -> primary_industry."""
    path = data_dir / "company_exposures.csv"
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            cid = (row.get("company_id") or "").strip()
            primary = (row.get("primary_industry") or "").strip()
            if cid:
                out[cid] = primary
    return out


def load_companies_index(data_dir: Path) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """company_id -> canonical_name; company_id -> name_variants list."""
    path = data_dir / "companies_index.json"
    cid_to_canonical: Dict[str, str] = {}
    cid_to_variants: Dict[str, List[str]] = defaultdict(list)
    if not path.exists():
        return cid_to_canonical, dict(cid_to_variants)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for c in data.get("companies", []):
        cid = (c.get("company_id") or "").strip()
        if not cid:
            continue
        cid_to_canonical[cid] = (c.get("canonical_name") or "").strip()
        cid_to_variants[cid] = list(c.get("name_variants") or [])
    return cid_to_canonical, dict(cid_to_variants)


def find_sector_conflicts(
    agg: Dict[str, Dict[str, Any]],
    primary_by_cid: Dict[str, str],
    cid_to_canonical: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    Companies where primary_industry might be wrong: either it's a minority choice,
    or there are many distinct industries (no clear winner).
    """
    conflicts: List[Dict[str, Any]] = []
    for cid, data in agg.items():
        industries = data["industries"]
        if not industries:
            continue
        primary = primary_by_cid.get(cid, "")
        total = sum(industries.values())
        most_common = industries.most_common(1)[0]
        top_name, top_count = most_common[0], most_common[1]
        top_pct = (top_count / total * 100) if total else 0
        distinct = len(industries)

        # Conflict if: primary differs from actual mode, or many distinct with no majority
        is_wrong_primary = primary and primary != top_name
        is_fragmented = distinct >= 3 and top_pct < 60

        if is_wrong_primary or is_fragmented:
            distro = ", ".join(f"{n} ({c})" for n, c in industries.most_common(10))
            conflicts.append({
                "company_id": cid,
                "canonical_name": cid_to_canonical.get(cid) or data.get("canonical_name", ""),
                "primary_industry": primary,
                "actual_most_common": top_name,
                "actual_most_common_pct": round(top_pct, 1),
                "distinct_industries": distinct,
                "distribution": distro,
                "row_count": data["row_count"],
                "reason": "wrong_primary" if is_wrong_primary else "fragmented",
            })
    # Sort by row_count desc then by wrong_primary first
    conflicts.sort(key=lambda x: (-x["row_count"], x["reason"] != "wrong_primary"))
    return conflicts


def find_merge_candidates(
    cid_to_canonical: Dict[str, str],
    threshold: int = 88,
) -> List[Dict[str, Any]]:
    """
    Pairs of company_ids whose canonical names are very similar (possible same company).
    Uses same block-key + token_sort_ratio idea as resolve_companies.
    """
    if not fuzz:
        return []
    canonical_list = [(cid, name) for cid, name in cid_to_canonical.items() if name]
    block_to_pairs: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for cid, name in canonical_list:
        block_to_pairs[_block_key(name)].append((cid, name))

    pairs: List[Dict[str, Any]] = []
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


def write_html_report(
    conflicts: List[Dict[str, Any]],
    merge_candidates: List[Dict[str, Any]],
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_parts: List[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Data quality report</title>",
        "<style>",
        "body { font-family: system-ui,sans-serif; margin: 1rem 2rem; }",
        "h1 { font-size: 1.4rem; }",
        "h2 { font-size: 1.1rem; margin-top: 1.5rem; }",
        "table { border-collapse: collapse; width: 100%; margin-top: 0.5rem; }",
        "th, td { border: 1px solid #ccc; padding: 0.35rem 0.6rem; text-align: left; }",
        "th { background: #eee; }",
        ".reason-wrong { background: #fdd; }",
        ".reason-frag { background: #ffb; }",
        ".num { text-align: right; }",
        "p.summary { color: #666; }",
        "</style></head><body>",
        "<h1>Data quality report</h1>",
        f"<p class='summary'>Sector conflicts: {len(conflicts)} companies where primary_industry may be wrong or fragmented. "
        f"Merge candidates: {len(merge_candidates)} pairs of company_ids with very similar canonical names.</p>",
        "<h2>1. Sector / industry conflicts</h2>",
        "<p class='summary'>Companies where the assigned primary_industry disagrees with the most common industry on rows, "
        "or where many different industry values appear (no clear winner).</p>",
        "<table><thead><tr>",
        "<th>Company</th><th>company_id</th><th>Assigned primary</th><th>Actual most common</th>",
        "<th>%</th><th>Distinct</th><th>Rows</th><th>Distribution (top 10)</th>",
        "</tr></thead><tbody>",
    ]
    for c in conflicts:
        tr_class = "reason-wrong" if c["reason"] == "wrong_primary" else "reason-frag"
        html_parts.append(
            f"<tr class='{tr_class}'>"
            f"<td>{_esc(c['canonical_name'][:50])}</td>"
            f"<td><code>{_esc(c['company_id'])}</code></td>"
            f"<td>{_esc(c['primary_industry'][:30])}</td>"
            f"<td>{_esc(c['actual_most_common'][:30])}</td>"
            f"<td class='num'>{c['actual_most_common_pct']}%</td>"
            f"<td class='num'>{c['distinct_industries']}</td>"
            f"<td class='num'>{c['row_count']}</td>"
            f"<td style='font-size:0.85em'>{_esc(c['distribution'][:120])}</td>"
            "</tr>"
        )
    html_parts.append("</tbody></table>")
    html_parts.append("<h2>2. Possible merge misses</h2>")
    html_parts.append(
        "<p class='summary'>Different company_ids whose canonical names are very similar (same company might be split).</p>"
    )
    html_parts.append(
        "<table><thead><tr>"
        "<th>Similarity</th><th>Canonical 1</th><th>company_id 1</th><th>Canonical 2</th><th>company_id 2</th>"
        "</tr></thead><tbody>"
    )
    for m in merge_candidates:
        html_parts.append(
            "<tr>"
            f"<td class='num'>{m['similarity']}</td>"
            f"<td>{_esc(m['canonical_1'][:45])}</td>"
            f"<td><code>{_esc(m['company_id_1'])}</code></td>"
            f"<td>{_esc(m['canonical_2'][:45])}</td>"
            f"<td><code>{_esc(m['company_id_2'])}</code></td>"
            "</tr>"
        )
    html_parts.append("</tbody></table></body></html>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html_parts))
    print(f"Wrote {out_path}")


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Diagnose sector and merge data quality; write HTML report.")
    parser.add_argument("--investments-dir", default=str(DEFAULT_INV_DIR), help="Directory with TICKER.csv files")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Directory with companies_index.json, company_exposures.csv")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output HTML path")
    parser.add_argument("--merge-threshold", type=int, default=88, help="Similarity threshold for merge candidates (0-100)")
    args = parser.parse_args()

    inv_dir = Path(args.investments_dir)
    data_dir = Path(args.data_dir)
    out_path = Path(args.out)

    if not inv_dir.exists():
        print(f"Investments dir not found: {inv_dir}")
        return
    rows = load_investment_rows(inv_dir)
    print(f"Loaded {len(rows)} investment rows from {inv_dir}")
    agg = aggregate_industry_by_company(rows)
    print(f"Aggregated {len(agg)} companies with industry data")

    primary_by_cid = load_company_exposures(data_dir)
    cid_to_canonical, _ = load_companies_index(data_dir)
    conflicts = find_sector_conflicts(agg, primary_by_cid, cid_to_canonical)
    merge_candidates = find_merge_candidates(cid_to_canonical, threshold=args.merge_threshold)

    print(f"Sector conflicts: {len(conflicts)}")
    print(f"Merge candidates: {len(merge_candidates)}")
    write_html_report(conflicts, merge_candidates, out_path)


if __name__ == "__main__":
    main()
