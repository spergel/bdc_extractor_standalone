#!/usr/bin/env python3
"""
Report XBRL-extracted investment rows that have missing industry.

Scans output/*_investments_*.csv for XBRL tickers, finds rows where industry
is empty, and writes:
  - output/xbrl_missing_industry_summary.csv  (ticker, filing_date, total_rows, rows_missing_industry)
  - output/xbrl_missing_industry_detail.csv  (all rows with missing industry; ticker, filing_date added)

Usage (from repo root):
  python scripts/xbrl_missing_industry_report.py
"""
import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output"

# XBRL tickers only (so we don't mix in LLM-extracted data)
XBRL_TICKERS = [
    "ARCC", "BBDC", "BCIC", "BCSF", "BXSL", "CCAP", "CGBD", "CION",
    "CSWC", "FDUS", "FSK", "GAIN", "GBDC", "GECC", "GLAD", "GSBD",
    "HRZN", "HTGC", "ICMB", "KBDC", "LIEN", "MAIN", "MFIC", "MRCC", "MSDL",
    "MSIF", "NCDL", "NEWT", "NMFC", "OBDC", "OCSL", "OFS", "OXSQ",
    "PFLT", "PFX", "PNNT", "PSBD", "PSEC", "RAND", "RWAY", "SAR", "SCM",
    "SLRC", "TCPC", "TPVG", "TRIN", "TSLX", "WHF",
]
XBRL_SET = set(XBRL_TICKERS)


def main() -> None:
    summary_rows = []
    detail_rows = []
    detail_columns = [
        "ticker", "filing_date", "company_name", "investment_type", "industry",
        "fair_value", "reference_rate", "spread", "acquisition_date",
    ]

    for path in sorted(OUTPUT_DIR.glob("*_investments_*.csv")):
        # Ticker is prefix before _investments_
        name = path.stem
        if "_investments_" not in name:
            continue
        ticker = name.split("_investments_")[0].upper()
        filing_date = name.split("_investments_")[-1]

        if ticker not in XBRL_SET:
            continue

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            all_rows = list(reader)

        total = len(all_rows)
        missing = [r for r in all_rows if not (r.get("industry") or "").strip()]
        n_missing = len(missing)

        if total > 0:
            summary_rows.append({
                "ticker": ticker,
                "filing_date": filing_date,
                "total_rows": total,
                "rows_missing_industry": n_missing,
                "pct_missing": round(100.0 * n_missing / total, 1),
            })

        for r in missing:
            detail_rows.append({
                "ticker": ticker,
                "filing_date": filing_date,
                "company_name": r.get("company_name", ""),
                "investment_type": r.get("investment_type", ""),
                "industry": r.get("industry", ""),
                "fair_value": r.get("fair_value", ""),
                "reference_rate": r.get("reference_rate", ""),
                "spread": r.get("spread", ""),
                "acquisition_date": r.get("acquisition_date", ""),
            })

    # Write summary
    summary_path = OUTPUT_DIR / "xbrl_missing_industry_summary.csv"
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "filing_date", "total_rows", "rows_missing_industry", "pct_missing"])
        w.writeheader()
        w.writerows(summary_rows)
    print(f"Wrote {summary_path} ({len(summary_rows)} filings)")

    # Write detail (only filings that have at least one missing industry)
    detail_path = OUTPUT_DIR / "xbrl_missing_industry_detail.csv"
    with open(detail_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=detail_columns)
        w.writeheader()
        w.writerows(detail_rows)
    print(f"Wrote {detail_path} ({len(detail_rows)} rows with missing industry)")

    # Print tickers that have any missing industry (for quick scan)
    tickers_with_missing = sorted({r["ticker"] for r in summary_rows if r["rows_missing_industry"] > 0})
    if tickers_with_missing:
        print("\nXBRL tickers with at least one filing that has rows missing industry:")
        for t in tickers_with_missing:
            filings = [s for s in summary_rows if s["ticker"] == t and s["rows_missing_industry"] > 0]
            print(f"  {t}: {len(filings)} filing(s), e.g. {filings[0]['filing_date']} ({filings[0]['rows_missing_industry']} rows missing industry)")


if __name__ == "__main__":
    main()
