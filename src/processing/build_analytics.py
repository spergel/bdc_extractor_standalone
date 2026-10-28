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

FLOATING_BENCHMARKS = {"SOFR", "LIBOR", "Prime", "EURIBOR", "SONIA", "CDOR", "BBSY", "CORRA", "Base rate", "Other"}

SPREAD_BUCKETS = ["<3%", "3–4%", "4–5%", "5–6%", "6–7%", "7%+"]


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


def normalize_reference(raw: str) -> str:
    """Map messy reference-rate strings to a benchmark bucket."""
    text = (raw or "").strip().upper()
    if not text or text in ("N/A", "NA", "NONE", "-", "—", "0"):
        return ""
    if "SOFR" in text:
        return "SOFR"
    if "LIBOR" in text or re.match(r"^L\s*[+\-]", text):
        return "LIBOR"
    if "EURIBOR" in text or re.match(r"^E\s*\+", text):
        return "EURIBOR"
    if "PRIME" in text or re.match(r"^P\s*\+", text):
        return "Prime"
    if "SONIA" in text:
        return "SONIA"
    if "CDOR" in text:
        return "CDOR"
    if "BBSY" in text:
        return "BBSY"
    if "CORRA" in text:
        return "CORRA"
    if "FIXED" in text:
        return "Fixed"
    if "BASE RATE" in text:
        return "Base rate"
    return "Other"


def spread_bucket(spread: float) -> str:
    if spread < 3:
        return "<3%"
    if spread < 4:
        return "3–4%"
    if spread < 5:
        return "4–5%"
    if spread < 6:
        return "5–6%"
    if spread < 7:
        return "6–7%"
    return "7%+"


def maturity_wall_bucket(year: int) -> str:
    if year <= 2026:
        return "≤2026"
    if year >= 2036:
        return "2036+"
    return str(year)


def normalize_company(name: str) -> str:
    text = (name or "").lower().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


# ---------------------------------------------------------------------------
# Per-ticker / per-period analytics
# ---------------------------------------------------------------------------

def aggregate_period(rows: list, filing_date: str) -> dict:
    fair_total = 0.0
    principal_total = 0.0
    cost_total = 0.0
    companies = set()
    types = defaultdict(lambda: {"fair_value": 0.0, "count": 0, "principal": 0.0, "cost": 0.0, "unrealized_gl": 0.0})
    sectors = defaultdict(lambda: {"fair_value": 0.0, "count": 0, "cost": 0.0, "unrealized_gl": 0.0})
    vintage_years = defaultdict(lambda: {"fair_value": 0.0, "count": 0})
    vintage_fv_total = 0.0
    maturity_years = defaultdict(lambda: {"fair_value": 0.0, "count": 0})
    maturity_wall = defaultdict(lambda: {"fair_value": 0.0, "count": 0})
    age_buckets = defaultdict(lambda: {"fair_value": 0.0, "count": 0})
    reference_rates = defaultdict(lambda: {"fair_value": 0.0, "count": 0})
    rate_types = defaultdict(lambda: {"fair_value": 0.0, "count": 0})
    spread_buckets = defaultdict(lambda: {"fair_value": 0.0, "count": 0})
    company_fv = defaultdict(float)
    company_gl = defaultdict(float)
    company_display = {}
    rate_fv_sum = 0.0
    rate_fv_weight = 0.0
    spread_fv_sum = 0.0
    spread_fv_weight = 0.0
    floor_fv_sum = 0.0
    floor_fv_weight = 0.0
    unrealized_gl_total = 0.0
    cost_with_fv_total = 0.0
    pik_fv = 0.0
    fixed_fv = 0.0
    floating_fv = 0.0
    flagged_rows = 0
    coverage_fv = defaultdict(float)

    asof = parse_date_any(filing_date)

    for row in rows:
        name_raw = (row.get("company_name") or "").strip()
        name = normalize_company(name_raw)
        if name:
            companies.add(name)
            company_display.setdefault(name, name_raw)
        fv = to_float(row.get("fair_value"))
        principal = to_float(row.get("principal_amount"))
        cost = to_float(row.get("amortized_cost"))
        if fv is not None:
            fair_total += fv
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

        if fv is None:
            continue  # everything below is fair-value weighted

        coverage_fv["fair_value"] += fv
        if itype != "Not Reported":
            coverage_fv["investment_type"] += fv
        if sector != "Not Reported":
            coverage_fv["industry"] += fv
        if principal is not None:
            coverage_fv["principal"] += fv
        if cost is not None:
            coverage_fv["amortized_cost"] += fv
            cost_with_fv_total += fv
            gl = fv - cost
            unrealized_gl_total += gl
            types[itype]["cost"] += cost
            types[itype]["unrealized_gl"] += gl
            sectors[sector]["cost"] += cost
            sectors[sector]["unrealized_gl"] += gl
            if name:
                company_gl[name] += gl

        if (row.get("data_quality_flags") or "").strip():
            flagged_rows += 1

        # --- vintage / age ---
        acq_year = parse_year(row.get("acquisition_date"))
        if acq_year is not None:
            vintage_years[str(acq_year)]["fair_value"] += fv
            vintage_years[str(acq_year)]["count"] += 1
            vintage_fv_total += fv
            coverage_fv["acquisition_date"] += fv
            acq = parse_date_any(row.get("acquisition_date"))
            if acq and asof is not None:
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

        # --- maturity ---
        myear = parse_year(row.get("maturity_date"))
        if myear is not None:
            coverage_fv["maturity_date"] += fv
            bucket = (
                f"{myear}" if myear <= 2028 else
                "2029–2030" if myear <= 2030 else
                "2031+" if myear <= 2040 else f"{myear}"
            )
            maturity_years[bucket]["fair_value"] += fv
            maturity_years[bucket]["count"] += 1
            wall = maturity_wall_bucket(myear)
            maturity_wall[wall]["fair_value"] += fv
            maturity_wall[wall]["count"] += 1

        # --- rates / structure ---
        cash_rate = to_float(row.get("cash_rate"))
        if cash_rate is not None and cash_rate > 0:
            rate_fv_sum += cash_rate * fv
            rate_fv_weight += fv
            coverage_fv["cash_rate"] += fv

        ref_raw = (row.get("reference_rate") or "").strip()
        ref = normalize_reference(ref_raw)
        if ref:
            coverage_fv["reference_rate"] += fv
            reference_rates[ref]["fair_value"] += fv
            reference_rates[ref]["count"] += 1
            if ref in FLOATING_BENCHMARKS:
                rate_types["Floating"]["fair_value"] += fv
                rate_types["Floating"]["count"] += 1
                floating_fv += fv
            else:
                rate_types["Fixed"]["fair_value"] += fv
                rate_types["Fixed"]["count"] += 1
                fixed_fv += fv
        else:
            rate_types["Not Reported"]["fair_value"] += fv
            rate_types["Not Reported"]["count"] += 1

        pik_rate = to_float(row.get("pik_rate"))
        if pik_rate is not None and pik_rate > 0:
            pik_fv += fv
            coverage_fv["pik_rate"] += fv

        floor = to_float(row.get("floor_rate"))
        if floor is not None and floor > 0:
            floor_fv_sum += floor * fv
            floor_fv_weight += fv
            coverage_fv["floor"] += fv

        spread = to_float(row.get("spread"))
        if spread is not None and spread > 0:
            spread_fv_sum += spread * fv
            spread_fv_weight += fv
            coverage_fv["spread"] += fv
            spread_buckets[spread_bucket(spread)]["fair_value"] += fv
            spread_buckets[spread_bucket(spread)]["count"] += 1

        if name:
            company_fv[name] += fv

    def pct(value):
        return round(value / fair_total * 100, 2) if fair_total else 0.0

    def type_entry(name, agg):
        return {
            "type": name,
            "fair_value": round(agg["fair_value"], 0),
            "principal": round(agg["principal"], 0),
            "count": agg["count"],
            "pct": pct(agg["fair_value"]),
            "unrealized_gl": round(agg["unrealized_gl"], 0) if agg["cost"] else None,
            "unrealized_gl_pct": round(agg["unrealized_gl"] / agg["cost"] * 100, 1) if agg["cost"] else None,
        }

    type_list = sorted((type_entry(k, v) for k, v in types.items()), key=lambda x: -x["fair_value"])
    sector_list = sorted(
        ({"sector": k, "fair_value": round(v["fair_value"], 0), "count": v["count"],
          "pct": pct(v["fair_value"]),
          "unrealized_gl": round(v["unrealized_gl"], 0) if v["cost"] else None,
          "unrealized_gl_pct": round(v["unrealized_gl"] / v["cost"] * 100, 1) if v["cost"] else None}
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
            "unrealized_gl": None,
            "unrealized_gl_pct": None,
        }
        sector_list = kept + [other]

    vintage_list = sorted(
        ({"year": k, "fair_value": round(v["fair_value"], 0), "count": v["count"],
          "pct": pct(v["fair_value"])}
         for k, v in vintage_years.items()),
        key=lambda x: x["year"],
    )

    age_order = ["0–1 yrs", "1–2 yrs", "2–3 yrs", "3–4 yrs", "4–5 yrs", "5+ yrs"]
    age_list = [
        {"bucket": b, "fair_value": round(age_buckets.get(b, {}).get("fair_value", 0.0), 0),
         "count": age_buckets.get(b, {}).get("count", 0),
         "pct": pct(age_buckets.get(b, {}).get("fair_value", 0.0))}
        for b in age_order
    ]

    mat_order = sorted(set(maturity_years), key=lambda b: (b.count("-") > 0, b))
    maturity_list = [
        {"bucket": b, "fair_value": round(maturity_years[b]["fair_value"], 0),
         "count": maturity_years[b]["count"],
         "pct": pct(maturity_years[b]["fair_value"])}
        for b in mat_order
    ]

    wall_order = ["≤2026"] + [str(y) for y in range(2027, 2036)] + ["2036+"]
    wall_list = [
        {"year": b, "fair_value": round(maturity_wall.get(b, {}).get("fair_value", 0.0), 0),
         "count": maturity_wall.get(b, {}).get("count", 0),
         "pct": pct(maturity_wall.get(b, {}).get("fair_value", 0.0))}
        for b in wall_order
        if maturity_wall.get(b, {}).get("count", 0) > 0 or b in ("2027", "2028", "2029", "2030")
    ]

    ref_list = sorted(
        ({"rate": k, "fair_value": round(v["fair_value"], 0), "count": v["count"], "pct": pct(v["fair_value"])}
         for k, v in reference_rates.items()),
        key=lambda x: -x["fair_value"],
    )
    rate_type_list = sorted(
        ({"type": k, "fair_value": round(v["fair_value"], 0), "count": v["count"], "pct": pct(v["fair_value"])}
         for k, v in rate_types.items()),
        key=lambda x: -x["fair_value"],
    )
    spread_list = [
        {"bucket": b, "fair_value": round(spread_buckets.get(b, {}).get("fair_value", 0.0), 0),
         "count": spread_buckets.get(b, {}).get("count", 0),
         "pct": pct(spread_buckets.get(b, {}).get("fair_value", 0.0))}
        for b in SPREAD_BUCKETS
    ]

    top_companies = sorted(
        ({"company": company_display.get(k, k), "fair_value": round(v, 0),
          "unrealized_gl": round(company_gl.get(k, 0.0), 0) if k in company_gl else None}
         for k, v in company_fv.items()),
        key=lambda x: -x["fair_value"],
    )[:10]

    gl_rows = [
        {"company": company_display.get(k, k), "unrealized_gl": round(gl, 0),
         "fair_value": round(company_fv.get(k, 0.0), 0)}
        for k, gl in company_gl.items() if gl
    ]
    gainers = sorted(gl_rows, key=lambda x: -x["unrealized_gl"])[:5]
    losers = sorted(gl_rows, key=lambda x: x["unrealized_gl"])[:5]

    top10_fv = sum(c["fair_value"] for c in top_companies)
    coverage = {k: round(v / fair_total * 100, 1) if fair_total else 0.0 for k, v in coverage_fv.items()}
    for required in ("investment_type", "industry", "reference_rate", "cash_rate", "spread",
                     "pik_rate", "floor", "acquisition_date", "maturity_date", "amortized_cost", "principal"):
        coverage.setdefault(required, 0.0)

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
            "weighted_avg_floor": round(floor_fv_sum / floor_fv_weight, 2) if floor_fv_weight else None,
            "vintage_coverage_pct": round(vintage_fv_total / fair_total * 100, 1) if fair_total else 0.0,
            "unrealized_gl": round(unrealized_gl_total, 0) if cost_with_fv_total else None,
            "unrealized_gl_pct": round(unrealized_gl_total / cost_with_fv_total * 100, 1) if cost_with_fv_total else None,
            "cost_coverage_pct": round(cost_with_fv_total / fair_total * 100, 1) if fair_total else 0.0,
            "pik_share_pct": round(pik_fv / fair_total * 100, 1) if fair_total else 0.0,
            "floating_pct": round(floating_fv / fair_total * 100, 1) if fair_total else 0.0,
            "fixed_pct": round(fixed_fv / fair_total * 100, 1) if fair_total else 0.0,
            "top10_pct": round(top10_fv / fair_total * 100, 1) if fair_total else 0.0,
            "flagged_rows_pct": round(flagged_rows / len(rows) * 100, 1) if rows else 0.0,
        },
        "coverage": coverage,
        "types": type_list,
        "sectors": sector_list,
        "vintage_years": vintage_list,
        "age_buckets": age_list,
        "maturity_buckets": maturity_list,
        "maturity_wall": wall_list,
        "reference_rates": ref_list,
        "rate_types": rate_type_list,
        "spread_buckets": spread_list,
        "top_companies": top_companies,
        "top_gainers": gainers,
        "top_losers": losers,
    }


# ---------------------------------------------------------------------------
# Quarter-over-quarter changes
# ---------------------------------------------------------------------------

def company_value_map(rows: list) -> tuple:
    """Return ({normalized_name: fair_value}, {normalized_name: display_name})."""
    values = defaultdict(float)
    names = {}
    for row in rows:
        raw = (row.get("company_name") or "").strip()
        key = normalize_company(raw)
        if not key:
            continue
        fv = to_float(row.get("fair_value"))
        values[key] += fv or 0.0
        names.setdefault(key, raw)
    return values, names


def build_changes(prev_rows: list, cur_rows: list, prev_fd: str, cur_fd: str) -> dict:
    prev_vals, prev_names = company_value_map(prev_rows)
    cur_vals, cur_names = company_value_map(cur_rows)

    adds, exits, increases, decreases = [], [], [], []
    for key, cur_fv in cur_vals.items():
        prev_fv = prev_vals.get(key, 0.0)
        delta = cur_fv - prev_fv
        display = cur_names.get(key) or prev_names.get(key) or key
        if key not in prev_vals or prev_fv == 0:
            adds.append({"company": display, "fair_value": round(cur_fv, 0), "delta": round(cur_fv, 0)})
        elif delta > 0:
            increases.append({"company": display, "delta": round(delta, 0),
                              "prev_fair_value": round(prev_fv, 0), "fair_value": round(cur_fv, 0)})
        elif delta < 0:
            decreases.append({"company": display, "delta": round(delta, 0),
                              "prev_fair_value": round(prev_fv, 0), "fair_value": round(cur_fv, 0)})

    for key, prev_fv in prev_vals.items():
        if key not in cur_vals or cur_vals.get(key, 0.0) == 0:
            exits.append({"company": prev_names.get(key) or key, "fair_value": round(prev_fv, 0),
                          "delta": round(-prev_fv, 0)})

    adds.sort(key=lambda x: -x["fair_value"])
    exits.sort(key=lambda x: -x["fair_value"])
    increases.sort(key=lambda x: -x["delta"])
    decreases.sort(key=lambda x: x["delta"])

    total_prev = sum(prev_vals.values())
    total_cur = sum(cur_vals.values())
    return {
        "from_filing_date": prev_fd,
        "to_filing_date": cur_fd,
        "new_positions": adds[:15],
        "exited_positions": exits[:15],
        "increases": increases[:15],
        "decreases": decreases[:15],
        "n_new": len(adds),
        "n_exited": len(exits),
        "new_fair_value": round(sum(a["fair_value"] for a in adds), 0),
        "exited_fair_value": round(sum(e["fair_value"] for e in exits), 0),
        "net_change": round(total_cur - total_prev, 0),
        "total_prev": round(total_prev, 0),
        "total_cur": round(total_cur, 0),
    }


# ---------------------------------------------------------------------------
# Ticker-level build (periods, changes, financial meta)
# ---------------------------------------------------------------------------

def latest_statement_value(fin: dict, label: str):
    node = ((fin or {}).get("statements", {}).get("balance_sheet") or {}).get(label)
    if not node:
        return None, None
    series = node.get("series") or {}
    if not series:
        return None, None
    end = max(series)
    return series[end].get("val"), end


def build_fin_meta(fin: Optional[dict]) -> dict:
    if not fin:
        return {"available": False}
    periods = fin.get("periods") or []
    assets, assets_end = latest_statement_value(fin, "Assets")
    liabilities, _ = latest_statement_value(fin, "Liabilities")
    equity, _ = latest_statement_value(fin, "Stockholders' Equity")
    if equity is None:
        equity, _ = latest_statement_value(fin, "Net Assets")
    balance_delta = None
    balance_ok = None
    if assets is not None and liabilities is not None and equity is not None:
        balance_delta = assets - (liabilities + equity)
        tolerance = max(1.0, abs(assets) * 0.03)
        balance_ok = abs(balance_delta) <= tolerance
    return {
        "available": True,
        "period_count": len(periods),
        "latest_period": periods[-1]["end"] if periods else None,
        "latest_filing": periods[-1].get("filed") if periods else None,
        "has_cash_flow": bool((fin.get("statements") or {}).get("cash_flow")),
        "balance_end": assets_end,
        "balance_check_ok": balance_ok,
        "balance_delta": balance_delta,
        "derived_metrics": sorted((fin.get("derived") or {}).keys()),
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

    filing_dates = sorted(by_filing)
    periods = []
    changes_by_fd = {}
    for i, fd in enumerate(filing_dates):
        try:
            period = aggregate_period(by_filing[fd], fd)
        except Exception as exc:
            logger.warning("aggregate failed for %s %s: %s", ticker, fd, exc)
            continue
        if i > 0:
            prev_fd = filing_dates[i - 1]
            try:
                changes_by_fd[fd] = build_changes(by_filing[prev_fd], by_filing[fd], prev_fd, fd)
            except Exception as exc:
                logger.warning("changes failed for %s %s: %s", ticker, fd, exc)
        periods.append(period)

    for period in periods:
        period["changes"] = changes_by_fd.get(period["filing_date"])

    fin = load_financials(ticker)
    latest = periods[-1] if periods else None
    payload = {
        "ticker": ticker,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "meta": {
            "latest_holdings_filing": latest["filing_date"] if latest else None,
            "latest_holdings_period_end": latest["period_end"] if latest else None,
            "financials": build_fin_meta(fin),
        },
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


# ---------------------------------------------------------------------------
# Peers
# ---------------------------------------------------------------------------

def build_peers(payloads: dict) -> dict:
    peers = []
    sector_pcts = defaultdict(list)
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
        coverage = latest.get("coverage") or {}
        changes = latest.get("changes") or {}

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

        for s in sectors:
            sector_pcts[s["sector"]].append(s["pct"])

        fin = load_financials(ticker)
        nav = latest_fin_metric(fin, "nav_per_share")
        leverage = latest_fin_metric(fin, "debt_to_equity")
        nii_ps = latest_fin_metric(fin, "nii_per_share")
        coverage_metric = latest_fin_metric(fin, "asset_coverage")
        fin_meta = build_fin_meta(fin)

        peers.append({
            "ticker": ticker,
            "period": latest["filing_date"],
            "period_end": latest.get("period_end"),
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
            "asset_coverage": round(coverage_metric, 2) if coverage_metric is not None else None,
            "nii_per_share": round(nii_ps, 2) if nii_ps is not None else None,
            "pik_share_pct": totals.get("pik_share_pct"),
            "floating_pct": totals.get("floating_pct"),
            "fixed_pct": totals.get("fixed_pct"),
            "top10_pct": totals.get("top10_pct"),
            "unrealized_gl_pct": totals.get("unrealized_gl_pct"),
            "rate_coverage_pct": coverage.get("reference_rate"),
            "cost_coverage_pct": totals.get("cost_coverage_pct"),
            "total_net_change": changes.get("net_change"),
            "balance_check_ok": fin_meta.get("balance_check_ok"),
            "fin_periods": fin_meta.get("period_count"),
            "fin_latest": fin_meta.get("latest_period"),
            "has_cash_flow": fin_meta.get("has_cash_flow"),
        })

    metric_keys = [
        "fair_value", "first_lien_pct", "second_lien_pct", "subordinated_pct", "equity_pct",
        "weighted_avg_cash_rate", "weighted_avg_spread", "median_vintage_year", "age_0_2_pct",
        "age_5plus_pct", "nav_per_share", "debt_to_equity", "asset_coverage", "nii_per_share",
        "pik_share_pct", "floating_pct", "top10_pct", "unrealized_gl_pct",
    ]
    medians = {key: median([p.get(key) for p in peers]) for key in metric_keys}
    sector_medians = []
    for sector, values in sector_pcts.items():
        if sector in ("Not Reported", "Other") or len(values) < 3:
            continue
        sector_medians.append({
            "sector": sector,
            "median_pct": round(median(values), 2),
            "bdc_count": len(values),
        })
    sector_medians.sort(key=lambda x: -x["median_pct"])

    out = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "medians": medians,
        "sector_medians": sector_medians,
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
