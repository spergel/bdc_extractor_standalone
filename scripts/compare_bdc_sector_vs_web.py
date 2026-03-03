#!/usr/bin/env python3
"""
Compare sector from BDC filings vs sector from web search (profile LLM).
For companies in MAIN and/or MRCC, we have:
- BDC sector: primary_industry from company_exposures (from filings)
- Web sector: industry from company_profiles (from Tavily/Gemini when built with --with-llm)

Both are normalized to the same taxonomy so we can measure agreement.

Usage:
  python scripts/compare_bdc_sector_vs_web.py MAIN MRCC
  python scripts/compare_bdc_sector_vs_web.py MAIN MRCC --out output/main_mrcc_sector_comparison.md
"""

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "frontend" / "public" / "data"

# Import normalization so we compare apples to apples
sys.path.insert(0, str(REPO_ROOT))
from src.processing.standardization_rules import normalize_industry


def load_exposures_by_bdc(tickers: list[str]) -> dict[str, dict]:
    """Load company_id -> {primary_industry, company_name, bdcs_invested} for companies in any of the tickers."""
    path = DATA_DIR / "company_exposures.csv"
    out = {}
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bdcs = (row.get("bdcs_invested") or "").strip()
            if not any(t in bdcs for t in tickers):
                continue
            cid = (row.get("company_id") or "").strip()
            if not cid:
                continue
            out[cid] = {
                "primary_industry": (row.get("primary_industry") or "").strip(),
                "company_name": (row.get("company_name") or "").strip(),
                "bdcs_invested": bdcs,
            }
    return out


def load_profile_industries() -> dict[str, str]:
    """Load company_id -> industry from company_profiles.csv (web/LLM)."""
    path = DATA_DIR / "company_profiles.csv"
    out = {}
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("company_id") or "").strip()
            ind = (row.get("industry") or "").strip()
            if cid:
                out[cid] = ind
    return out


def load_profiles_full() -> dict[str, dict]:
    """Load company_id -> {industry, industry_initial, canonical_name} from company_profiles.csv."""
    path = DATA_DIR / "company_profiles.csv"
    out = {}
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("company_id") or "").strip()
            if not cid:
                continue
            out[cid] = {
                "industry": (row.get("industry") or "").strip(),
                "industry_initial": (row.get("industry_initial") or "").strip(),
                "canonical_name": (row.get("canonical_name") or "").strip(),
            }
    return out


def main():
    import argparse
    p = argparse.ArgumentParser(description="Compare BDC-reported sector vs web/LLM sector for given BDCs")
    p.add_argument("tickers", nargs="+", metavar="TICKER", help="e.g. MAIN MRCC")
    p.add_argument("--out", default=None, help="Write report to this path (default: print)")
    p.add_argument("--sample", type=int, default=20, help="Max sample size for disagreements (default 20)")
    p.add_argument("--use-profiles-only", action="store_true", help="Compare industry_initial vs industry within profiles (more rows; industry may be from LLM or from BDC)")
    args = p.parse_args()
    tickers = [t.upper() for t in args.tickers]

    exposures = load_exposures_by_bdc(tickers)
    profiles = load_profile_industries()
    profiles_full = load_profiles_full() if args.use_profiles_only else {}

    # Build comparison: BDC sector from exposures, web sector from profiles
    rows = []
    for cid, ex in exposures.items():
        bdc_raw = ex["primary_industry"]
        web_raw = profiles.get(cid) or ""
        if args.use_profiles_only and cid in profiles_full:
            # Use industry_initial as BDC (from filings), industry as web/LLM
            pf = profiles_full[cid]
            bdc_raw = bdc_raw or pf.get("industry_initial") or ""
            web_raw = web_raw or pf.get("industry") or ""
        if not bdc_raw and not web_raw:
            continue
        bdc_norm = normalize_industry(bdc_raw) if bdc_raw else ""
        web_norm = normalize_industry(web_raw) if web_raw else ""
        company_name = ex["company_name"]
        if args.use_profiles_only and cid in profiles_full:
            company_name = company_name or profiles_full[cid].get("canonical_name") or cid
        rows.append({
            "company_id": cid,
            "company_name": company_name,
            "bdcs_invested": ex["bdcs_invested"],
            "bdc_raw": bdc_raw,
            "web_raw": web_raw,
            "bdc_norm": bdc_norm or "(empty)",
            "web_norm": web_norm or "(empty)",
            "match": bdc_norm == web_norm and bool(bdc_norm and web_norm),
        })

    comparable = [r for r in rows if r["bdc_norm"] != "(empty)" and r["web_norm"] != "(empty)"]
    matches = [r for r in comparable if r["match"]]
    disagreements = [r for r in comparable if not r["match"]]
    only_bdc = [r for r in rows if r["bdc_norm"] != "(empty)" and r["web_norm"] == "(empty)"]
    only_web = [r for r in rows if r["bdc_norm"] == "(empty)" and r["web_norm"] != "(empty)"]

    n_comp = len(comparable)
    n_match = len(matches)
    match_pct = 100 * n_match / n_comp if n_comp else 0

    lines = [
        f"# BDC sector vs web sector: {', '.join(tickers)}",
        "",
        "## Summary",
        f"- Companies in these BDCs (with exposure data): {len(exposures)}",
        f"- Companies with both BDC sector and web sector: {n_comp}",
        f"- **Exact match (normalized)**: {n_match} / {n_comp} = **{match_pct:.1f}%**" + (f" (n={n_comp})" if n_comp else ""),
        f"- Disagreements: {len(disagreements)}",
        f"- Has BDC sector only (no profile/web): {len(only_bdc)}",
        f"- Has web sector only (no BDC primary_industry): {len(only_web)}",
        "",
    ]
    if n_comp < 50 and len(exposures) > 100:
        lines.append("> **Tip:** Only profiles built with LLM (e.g. `build_profiles.py --with-llm`) have web sector. Run with `--with-llm` for more companies to compare.")
        lines.append("")
    lines.extend([
        "## Sample: disagreements (BDC vs web)",
        "",
    ])
    for r in sorted(disagreements, key=lambda x: x["company_name"])[: args.sample]:
        lines.append(f"- **{r['company_name']}** — BDC: `{r['bdc_raw']}` → `{r['bdc_norm']}` | Web: `{r['web_raw']}` → `{r['web_norm']}`")
    if len(disagreements) > args.sample:
        lines.append(f"- ... and {len(disagreements) - args.sample} more")
    lines.extend([
        "",
        "## Sample: agreements (BDC and web match after normalization)",
        "",
    ])
    for r in sorted(matches, key=lambda x: x["company_name"])[: args.sample]:
        lines.append(f"- **{r['company_name']}** — `{r['bdc_norm']}`")
    if len(matches) > args.sample:
        lines.append(f"- ... and {len(matches) - args.sample} more")

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
