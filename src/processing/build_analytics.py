#!/usr/bin/env python3
"""
Build frontend analytics JSON from consolidated holdings CSVs + financials JSON.

Outputs:
    app/public/data/analytics/{TICKER}.json   per-ticker, per-filing-period analytics
    app/public/data/analytics/peers.json      cross-BDC comparison (latest periods)

Usage:
    python src/processing/build_analytics.py
    python src/processing/build_analytics.py --ticker ARCC
"""

import argparse
import csv
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
INVESTMENTS_DIR = DATA_DIR / "investments"
FINANCIALS_DIR = DATA_DIR / "financials"
ANALYTICS_DIR = DATA_DIR / "analytics"

# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------

JUNK_INDUSTRY_PATTERNS = [
    re.compile(r"^\s*[-$0-9.,%/()]+", re.I),                 # starts with numbers/punct
    re.compile(r"\d{1,2}/\d{1,2}/\d{4}"),                    # dates
    re.compile(r"\b(shares?|units?)\b.*\b(class|common|preferred|series)\b", re.I),
    re.compile(r"\b(lien|senior secured|unitranche|subordinated)\b", re.I),
    re.compile(r"\bmembership interest\b", re.I),
    re.compile(r"\bper share\b", re.I),
    re.compile(r"^\s*(yes|no|n/a|none|na|—|-|other)\s*$", re.I),
]


def clean_industry(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "Not Reported"
    if any(p.search(text) for p in JUNK_INDUSTRY_PATTERNS):
        return "Not Reported"
    return text


def to_float(value) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    neg = text.startswith("(") and text.endswith(")")
    cleaned = text.replace(",", "").replace("$", "").replace("%", "").replace("(", "").replace(")", "")
    try:
        val = float(cleaned)
        return -val if neg else val
    except ValueError:
        return None


def parse_date_any(value):
    """Parse YYYY-MM-DD or MM/DD/YYYY into a date; None otherwise."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_year(value):
    d = parse_date_any(value)
    return d.year if d else None


def quarter_end_before(filing_date: str) -> str:
    """Estimate the fiscal quarter-end date for a given SEC filing date."""
    try:
        d = datetime.strptime(filing_date, "%Y-%m-%d").date()
    except ValueError:
        return filing_date
    candidates = []
    for year in (d.year, d.year - 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            qe = date(year, month, day)
            if qe <= d:
                candidates.append(qe)
    if not candidates:
        return filing_date
    return max(candidates).isoformat()


# ---------------------------------------------------------------------------
# Per-ticker analytics
# ---------------------------------------------------------------------------

def aggregate_period(rows: list[dict], filing_date: str) -> dict:
    fair_total = 0.0
    principal_total = 0.0
    cost_total = 0.0
    fair_count = 0
    companies = set()
    types = defaultdict(lambda: {"fair_value": 0.0, "count": 0, "principal": 0.0})
    sectors = defaultdict(lambda: {"fair_value": 0.0, "count": 0})
    vintage_years = defaultdict(lambda: {"fair_value": 0.0, "count": 0})
    vintage_fv_total = 0.0
    maturity_years = defaultdict(lambda: {"fair_value": 0.0, "count": 0})
    age_buckets = defaultdict(lambda: {"fair_value": 0.0, "count": 0})
    rate_fv_sum = 0.0
    rate_fv_weight = 0.0
    spread_fv_sum = 0.0
    spread_fv_weight = 0.0
    company_fv = defaultdict(float)

    asof = parse_date_any(filing_date)

    for row in rows:
        name = (row.get("company_name") or "").strip()
        if name:
            companies.add(name)
        fv = to_float(row.get("fair_value"))
        principal = to_float(row.get("principal_amount"))
        cost = to_float(row.get("amortized_cost"))
        if fv is not None:
            fair_total += fv
            fair_count += 1
        if principal is not None:
            principal_total += principal
        if cost is not None:
            cost_total += cost

        itype = (row.get("investment_type") or "").strip() or "Not Reported"
        sector = clean_industry(row.get("industry"))
        types[itype]["fair_value"] += fv or 0.0
        types[itype]["principal"] += principal or 0.0
        types[itype]["count"] += 1
        sectors[sector]["fair_value"] += fv or 0.0
        sectors[sector]["count"] += 1

        year = parse_year(row.get("acquisition_date"))
        if year is not None:
            vintage_years[str(year)]["fair_value"] += fv or 0.0
            vintage_years[str(year)]["count"] += 1
            vintage_fv_total += fv or 0.0
            if fv is not None and asof is not None:
                acq = parse_date_any(row.get("acquisition_date"))
                if acq:
                    age_years = (asof - acq).days / 365.25
                    bucket = (
                        "0–1 yrs" if age_years < 1 else
                        "1–2 yrs" if age_years < 2 else
                        "2–3 yrs" if age_years < 3 else
                        "3–4 yrs" if age_years < 4 else
                        "4–5 yrs" if age_years < 5 else "5+ yrs"
                    )
                    age_buckets[bucket]["fair_value"] += fv
                    age_buckets[bucket]["count"] += 1

        myear = parse_year(row.get("maturity_date"))
        if myear is not None:
            bucket = (
                f"{myear}" if myear <= 2028 else
                "2029–2030" if myear <= 2030 else
                "2031+" if myear <= 2040 else f"{myear}"
            )
            maturity_years[bucket]["fair_value"] += fv or 0.0
            maturity_years[bucket]["count"] += 1

        if fv is not None:
            rate = to_float(row.get("cash_rate"))
            if rate is not None and rate > 0:
                rate_fv_sum += rate * fv
                rate_fv_weight += fv
            spread = to_float(row.get("spread"))
            if spread is not None and spread > 0:
                spread_fv_sum += spread * fv
                spread_fv_weight += fv
            if name:
                company_fv[name] += fv

    def type_entry(name, agg):
        return {
            "type": name,
            "fair_value": round(agg["fair_value"], 0),
            "principal": round(agg["principal"], 0),
            "count": agg["count"],
            "pct": round(agg["fair_value"] / fair_total * 100, 2) if fair_total else 0.0,
        }

    type_list = sorted((type_entry(k, v) for k, v in types.items()), key=lambda x: -x["fair_value"])
    sector_list = sorted(
        ({"sector": k, "fair_value": round(v["fair_value"], 0), "count": v["count"],
          "pct": round(v["fair_value"] / fair_total * 100, 2) if fair_total else 0.0}
         for k, v in sectors.items()),
        key=lambda x: -x["fair_value"],
    )
    # Collapse sectors beyond the top 12 into "Other" for chart readability
    if len(sector_list) > 13:
        kept = sector_list[:12]
        other = {
            "sector": "Other",
            "fair_value": sum(s["fair_value"] for s in sector_list[12:]),
            "count": sum(s["count"] for s in sector_list[12:]),
            "pct": round(sum(s["fair_value"] for s in sector_list[12:]) / fair_total * 100, 2) if fair_total else 0.0,
        }
        sector_list = kept + [other]

    vintage_list = sorted(
        ({"year": k, "fair_value": round(v["fair_value"], 0), "count": v["count"],
          "pct": round(v["fair_value"] / fair_total * 100, 2) if fair_total else 0.0}
         for k, v in vintage_years.items()),
        key=lambda x: x["year"],
    )

    age_order = ["0–1 yrs", "1–2 yrs", "2–3 yrs", "3–4 yrs", "4–5 yrs", "5+ yrs"]
    age_list = [
        {"bucket": b, "fair_value": round(age_buckets.get(b, {}).get("fair_value", 0.0), 0),
         "count": age_buckets.get(b, {}).get("count", 0),
         "pct": round(age_buckets.get(b, {}).get("fair_value", 0.0) / fair_total * 100, 2) if fair_total else 0.0}
        for b in age_order
    ]

    mat_order = sorted(
        set(maturity_years),
        key=lambda b: (b.count("-") > 0, b)
    )
    maturity_list = [
        {"bucket": b, "fair_value": round(maturity_years[b]["fair_value"], 0),
         "count": maturity_years[b]["count"],
         "pct": round(maturity_years[b]["fair_value"] / fair_total * 100, 2) if fair_total else 0.0}
        for b in mat_order
    ]

    top_companies = sorted(
        ({"company": k, "fair_value": round(v, 0)} for k, v in company_fv.items()),
        key=lambda x: -x["fair_value"],
    )[:10]

    vintage_coverage = round(vintage_fv_total / fair_total * 100, 1) if fair_total else 0.0

    return {
        "filing_date": filing_date,
        "period_end": quarter_end_before(filing_date),
        "totals": {
            "fair_value": round(fair_total, 0),
            "principal": round(principal_total, 0),
            "cost": round(cost_total, 0),
            "rows": len(rows),
            "companies": len(companies),
            "weighted_avg_cash_rate": round(rate_fv_sum / rate_fv_weight, 2) if rate_fv_weight else None,
            "weighted_avg_spread": round(spread_fv_sum / spread_fv_weight, 2) if spread_fv_weight else None,
            "vintage_coverage_pct": vintage_coverage,
        },
        "types": type_list,
        "sectors": sector_list,
        "vintage_years": vintage_list,
        "age_buckets": age_list,
        "maturity_buckets": maturity_list,
        "top_companies": top_companies,
    }


def build_ticker_analytics(ticker: str) -> Optional[dict]:
    path = INVESTMENTS_DIR / f"{ticker}.csv"
    if not path.exists():
        logger.warning("No consolidated investments file for %s", ticker)
        return None
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_filing = defaultdict(list)
    for row in rows:
        fd = (row.get("filing_date") or "").strip()
        if fd:
            by_filing[fd].append(row)
    periods = []
    for fd in sorted(by_filing):
        try:
            periods.append(aggregate_period(by_filing[fd], fd))
        except Exception as exc:
            logger.warning("aggregate failed for %s %s: %s", ticker, fd, exc)
    payload = {
        "ticker": ticker,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "periods": periods,
    }
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    out = ANALYTICS_DIR / f"{ticker}.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    logger.info("Wrote %s (%d periods)", out, len(periods))
    return payload


def load_financials(ticker: str) -> Optional[dict]:
    path = FINANCIALS_DIR / f"{ticker}_financials.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def latest_fin_metric(fin: dict, metric: str):
    derived = (fin or {}).get("derived", {})
    node = derived.get(metric)
    if not node:
        return None
    series = node.get("series") or {}
    if not series:
        return None
    last_end = max(series)
    return series[last_end].get("val")


def build_peers(payloads: dict[str, dict]) -> dict:
    peers = []
    for ticker, payload in sorted(payloads.items()):
        periods = payload.get("periods") or []
        if not periods:
            continue
        latest = periods[-1]
        totals = latest.get("totals") or {}
        types = latest.get("types") or []
        sectors = latest.get("sectors") or []
        vintage = latest.get("vintage_years") or []
        age = latest.get("age_buckets") or []

        def type_pct(names):
            return round(sum(t["pct"] for t in types if t["type"] in names), 1)

        def median_year():
            years = [int(v["year"]) for v in vintage if str(v["year"]).isdigit()]
            if not years:
                return None
            years.sort()
            return years[len(years) // 2]

        def age_share(bucket):
            for a in age:
                if a["bucket"] == bucket:
                    return a["pct"]
            return 0.0

        fin = load_financials(ticker)
        nav = latest_fin_metric(fin, "nav_per_share")
        leverage = latest_fin_metric(fin, "debt_to_equity")
        nii_ps = latest_fin_metric(fin, "nii_per_share")
        coverage = latest_fin_metric(fin, "asset_coverage")

        peers.append({
            "ticker": ticker,
            "period": latest["filing_date"],
            "fair_value": totals.get("fair_value"),
            "companies": totals.get("companies"),
            "first_lien_pct": type_pct({"First Lien"}),
            "second_lien_pct": type_pct({"Second Lien"}),
            "subordinated_pct": type_pct({"Subordinated Debt", "Unsecured Debt", "Other Debt", "Delayed Draw"}),
            "equity_pct": type_pct({"Common Equity", "Preferred Equity", "Other Equity", "Partnership Interest", "Warrants"}),
            "top_sectors": [{"sector": s["sector"], "pct": s["pct"]} for s in sectors[:3]],
            "weighted_avg_cash_rate": totals.get("weighted_avg_cash_rate"),
            "weighted_avg_spread": totals.get("weighted_avg_spread"),
            "median_vintage_year": median_year(),
            "vintage_coverage_pct": totals.get("vintage_coverage_pct"),
            "age_0_2_pct": round(age_share("0–1 yrs") + age_share("1–2 yrs"), 1),
            "age_5plus_pct": age_share("5+ yrs"),
            "nav_per_share": round(nav, 2) if nav is not None else None,
            "debt_to_equity": round(leverage, 3) if leverage is not None else None,
            "asset_coverage": round(coverage, 2) if coverage is not None else None,
            "nii_per_share": round(nii_ps, 2) if nii_ps is not None else None,
        })
    out = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "peers": peers,
    }
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    with open(ANALYTICS_DIR / "peers.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    logger.info("Wrote peers.json (%d BDCs)", len(peers))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build frontend analytics JSON")
    parser.add_argument("--ticker", help="Only build for one ticker")
    args = parser.parse_args()

    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        with open(DATA_DIR / "investments_index.json", encoding="utf-8") as fh:
            index = json.load(fh)
        tickers = sorted(index.get("tickers", {}).keys())

    payloads = {}
    for ticker in tickers:
        payload = build_ticker_analytics(ticker)
        if payload:
            payloads[ticker] = payload

    build_peers(payloads)
    return 0


if __name__ == "__main__":
    main()
