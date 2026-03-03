#!/usr/bin/env python3
"""
Quick XBRL data quality audit across all BDC tickers.
For each ticker, fetches the most recent filing, runs the XBRL extractor,
and reports key quality metrics (row count, FV total, rate coverage, etc.).

Usage:
    python xbrl_quality_audit.py                          # audit all tickers
    python xbrl_quality_audit.py --tickers BCIC KBDC SCM  # audit specific tickers
    python xbrl_quality_audit.py --merge                  # merge new results into existing JSON
"""
import sys, os, csv, json, argparse
from pathlib import Path

# Add src/extraction to path
_script_dir = Path(__file__).resolve().parent
_repo_root = _script_dir.parent
sys.path.insert(0, str(_repo_root / "src" / "extraction"))

from xbrl_investment_extractor import XBRLInvestmentExtractor

# Full pipeline ticker list — kept in sync with process_all_bdcs.py
TICKERS = [
    "ARCC", "BBDC", "BCIC", "BCSF", "BXSL", "CCAP", "CGBD", "CION",
    "CSWC", "EQS", "FDUS", "FSK", "GAIN", "GBDC", "GECC", "GLAD", "GSBD",
    "HRZN", "HTGC", "ICMB", "KBDC", "LIEN", "MAIN", "MFIC", "MRCC", "MSDL",
    "MSIF", "NCDL", "NEWT", "NMFC", "OBDC", "OCSL", "OFS", "OXSQ",
    "PFLT", "PFX", "PNNT", "PSBD", "PSEC", "RAND", "RWAY", "SAR", "SCM",
    "SLRC", "SSSS", "TCPC", "TPVG", "TRIN", "TSLX", "WHF",
]

OUTPUT_DIR = _repo_root / "output" / "xbrl_audit"
DATA_DIR = _repo_root / "data"


def audit_ticker(ticker: str, ext: XBRLInvestmentExtractor) -> dict:
    """Run XBRL extraction on latest filing for ticker, return quality metrics."""
    result = {
        "ticker": ticker,
        "status": "unknown",
        "rows": 0,
        "companies": 0,
        "total_fv_millions": 0,
        "has_cost": 0,
        "has_rate": 0,
        "has_principal": 0,
        "has_spread": 0,
        "has_ref_rate": 0,
        "rate_pct": 0,
        "cost_pct": 0,
        "filing_date": "",
        "period_end": "",
    }

    try:
        # Get most recent 10-Q
        filings = list(ext.sec_client.get_historical_10q_filings(ticker, years_back=1))
        if not filings:
            # Try 10-K
            filings = list(ext.sec_client.get_historical_10k_filings(ticker, years_back=1))
        if not filings:
            result["status"] = "no_filings"
            return result

        latest = filings[0]
        result["filing_date"] = latest["date"]
        result["period_end"] = latest.get("period_end_date", "")

        csv_path = ext.process_filing(
            ticker=ticker,
            filing_type=latest.get("form", "10-Q"),
            index_url=latest["index_url"],
        )
        if not csv_path:
            result["status"] = "no_xbrl_soi"
            return result

        rows = list(csv.DictReader(open(csv_path)))
        result["rows"] = len(rows)
        names = set(r["company_name"].upper().strip() for r in rows if r.get("company_name"))
        result["companies"] = len(names)

        total_fv = sum(float(r.get("fair_value", 0) or 0) for r in rows)
        result["total_fv_millions"] = round(total_fv / 1000, 1)

        rows_with_fv = [r for r in rows if r.get("fair_value") and float(r.get("fair_value", 0)) != 0]
        n = max(len(rows_with_fv), 1)

        result["has_cost"] = sum(1 for r in rows_with_fv if r.get("amortized_cost") or r.get("cost"))
        result["has_rate"] = sum(1 for r in rows_with_fv if r.get("cash_rate"))
        result["has_principal"] = sum(1 for r in rows_with_fv if r.get("principal_amount"))
        result["has_spread"] = sum(1 for r in rows_with_fv if r.get("spread"))
        result["has_ref_rate"] = sum(1 for r in rows_with_fv if r.get("reference_rate"))

        result["cost_pct"] = round(100 * result["has_cost"] / n)
        result["rate_pct"] = round(100 * result["has_rate"] / n)
        result["status"] = "ok"

    except Exception as e:
        result["status"] = f"error: {str(e)[:80]}"

    return result


def grade(r: dict) -> str:
    if r["status"] != "ok":
        return "F"
    if r["rate_pct"] >= 40 and r["cost_pct"] >= 60:
        return "A"
    if r["cost_pct"] >= 60:
        return "B"
    if r["rows"] > 0:
        return "C"
    return "F"


def print_summary(results: list):
    print("\n" + "=" * 100)
    print(f"{'Ticker':>6s}  {'Status':>15s}  {'Rows':>5s}  {'Cos':>4s}  {'FV ($M)':>10s}  {'Cost%':>5s}  {'Rate%':>5s}  {'Spread%':>7s}  Grade")
    print("=" * 100)

    xbrl_good, xbrl_partial, needs_llm = [], [], []

    for r in sorted(results, key=lambda x: x["ticker"]):
        spread_pct = round(100 * r["has_spread"] / max(r["has_rate"], 1)) if r["has_rate"] else 0
        g = grade(r)

        print(
            f"{r['ticker']:>6s}  {r['status']:>15s}  {r['rows']:>5d}  {r['companies']:>4d}  "
            f"${r['total_fv_millions']:>9,.1f}M  {r['cost_pct']:>4d}%  {r['rate_pct']:>4d}%  "
            f"{spread_pct:>6d}%  {g}"
        )

        if g == "A":
            xbrl_good.append(r["ticker"])
        elif g in ("B", "C"):
            xbrl_partial.append(r["ticker"])
        else:
            needs_llm.append(r["ticker"])

    print(f"\n{'='*60}")
    print(f"XBRL sufficient (A):  {', '.join(xbrl_good)}")
    print(f"XBRL partial (B/C):   {', '.join(xbrl_partial)}")
    print(f"Needs LLM (F):        {', '.join(needs_llm)}")


def main():
    parser = argparse.ArgumentParser(description="XBRL data quality audit for BDC tickers")
    parser.add_argument(
        "--tickers", nargs="+", metavar="TICKER",
        help="Only audit these tickers (default: all)"
    )
    parser.add_argument(
        "--merge", action="store_true",
        help="Merge new results into existing xbrl_audit_results.json instead of overwriting"
    )
    args = parser.parse_args()

    tickers_to_run = args.tickers if args.tickers else TICKERS
    # Validate
    unknown = set(tickers_to_run) - set(TICKERS)
    if unknown:
        print(f"Warning: unknown tickers (not in master list): {sorted(unknown)}", flush=True)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ext = XBRLInvestmentExtractor(
        data_dir=str(DATA_DIR),
        output_dir=str(OUTPUT_DIR),
    )

    new_results = []
    for i, ticker in enumerate(tickers_to_run):
        print(f"\n[{i+1}/{len(tickers_to_run)}] {ticker}...", flush=True)
        r = audit_ticker(ticker, ext)
        new_results.append(r)
        status_line = (
            f"  {r['status']:15s} {r['rows']:>5d} rows  {r['companies']:>4d} cos  "
            f"FV=${r['total_fv_millions']:>9,.1f}M  cost:{r['cost_pct']:>3d}%  rate:{r['rate_pct']:>3d}%"
        )
        print(status_line, flush=True)

    # Merge or overwrite
    json_path = OUTPUT_DIR / "xbrl_audit_results.json"
    if args.merge and json_path.exists():
        existing = json.loads(json_path.read_text())
        # Index existing by ticker, overlay with new results
        by_ticker = {r["ticker"]: r for r in existing}
        for r in new_results:
            by_ticker[r["ticker"]] = r
        results = sorted(by_ticker.values(), key=lambda x: x["ticker"])
        print(f"\nMerged {len(new_results)} new results into {len(existing)} existing -> {len(results)} total")
    else:
        results = new_results

    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved {len(results)} results to {json_path}")

    print_summary(results)


if __name__ == "__main__":
    main()
