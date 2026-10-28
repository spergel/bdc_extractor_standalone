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
FILINGS_DIR = DATA_DIR / "filings"
CREDIT_DIR = DATA_DIR / "credit"

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
    vintage_years = defaultdict(lambda: {"fair_value": 0.0, "count": 0, "cost": 0.0, "unrealized_gl": 0.0})
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
    wall_bucket = None

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
            if cost is not None:
                vintage_years[str(acq_year)]["cost"] += cost
                vintage_years[str(acq_year)]["unrealized_gl"] += fv - cost
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
            wall_bucket = wall
            maturity_wall[wall]["fair_value"] += fv
            maturity_wall[wall]["count"] += 1

        # --- rates / structure ---
        cash_rate = to_float(row.get("cash_rate"))
        if cash_rate is not None and cash_rate > 0:
            rate_fv_sum += cash_rate * fv
            rate_fv_weight += fv
            coverage_fv["cash_rate"] += fv
            if wall_bucket:
                mw = maturity_wall[wall_bucket]
                mw["rate_sum"] = mw.get("rate_sum", 0.0) + cash_rate * fv
                mw["rate_weight"] = mw.get("rate_weight", 0.0) + fv

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
                if wall_bucket:
                    mw = maturity_wall[wall_bucket]
                    mw["floating_fv"] = mw.get("floating_fv", 0.0) + fv
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
        ({"year": k, "fair_value": round(v["fair_value"], 0),
          "cost": round(v["cost"], 0) if v["cost"] else None,
          "count": v["count"],
          "pct": pct(v["fair_value"]),
          "unrealized_gl": round(v["unrealized_gl"], 0) if v["cost"] else None,
          "mark_pct": round(v["unrealized_gl"] / v["cost"] * 100, 1) if v["cost"] else None}
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
    wall_list = []
    for b in wall_order:
        agg = maturity_wall.get(b, {})
        if not (agg.get("count", 0) > 0 or b in ("2027", "2028", "2029", "2030")):
            continue
        fv_total = agg.get("fair_value", 0.0)
        rate_weight = agg.get("rate_weight", 0.0)
        wall_list.append({
            "year": b,
            "fair_value": round(fv_total, 0),
            "count": agg.get("count", 0),
            "pct": pct(fv_total),
            "weighted_avg_cash_rate": round(agg.get("rate_sum", 0.0) / rate_weight, 2) if rate_weight else None,
            "floating_pct": round(agg.get("floating_fv", 0.0) / fv_total * 100, 1) if fv_total else None,
        })

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
# Valuation / distribution quality
# ---------------------------------------------------------------------------

def load_json_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def load_profile(ticker: str):
    return load_json_file(DATA_DIR / ticker / "profile.json")


def load_credit(ticker: str):
    return load_json_file(CREDIT_DIR / f"{ticker}.json")


def load_filings(ticker: str):
    data = load_json_file(FILINGS_DIR / f"{ticker}.json")
    return (data or {}).get("filings")


def annualize(entry):
    if not entry:
        return None
    val = entry.get("val")
    if val is None:
        return None
    days = entry.get("duration_days")
    if days and days > 0:
        return val * 365.0 / days
    return None


def quarterly_value(entry):
    """Return a per-quarter value for a flow series entry."""
    if not entry:
        return None
    days = entry.get("duration_days")
    if days and 75 <= days <= 110:
        return entry.get("val")
    ann = annualize(entry)
    return ann / 4.0 if ann is not None else None


def series_at(node, end):
    return ((node or {}).get("series") or {}).get(end)


def build_quality(fin, profile):
    """Quarterly distribution/NAV quality series plus latest valuation snapshot."""
    if not fin:
        return {"periods": [], "latest": None}
    inc = (fin.get("statements") or {}).get("income_statement") or {}
    bal = (fin.get("statements") or {}).get("balance_sheet") or {}
    derived = fin.get("derived") or {}
    nii_node = inc.get("Net Investment Income") or {}
    dps_node = inc.get("Dividends per Share (Declared)") or inc.get("Dividends per Share (Paid)") or {}
    pik_node = inc.get("PIK Interest") or inc.get("Interest Income (PIK)") or {}
    tii_node = inc.get("Total Investment Income") or {}
    equity_node = bal.get("Stockholders' Equity") or bal.get("Net Assets") or {}
    nav_node = derived.get("nav_per_share") or {}

    form_by_end = {p.get("end"): p for p in (fin.get("periods") or []) if p.get("end")}
    nav_series = nav_node.get("series") or {}
    rows = []
    prev = None
    for end in sorted(nav_series.keys()):
        nav_ps = (nav_series.get(end) or {}).get("val")
        if nav_ps is None:
            continue
        info = form_by_end.get(end) or {}
        dps_entry = series_at(dps_node, end)
        nii_entry = series_at(nii_node, end)
        equity = (series_at(equity_node, end) or {}).get("val")
        dps_q = quarterly_value(dps_entry)
        dps_ann = annualize(dps_entry)
        nii_q = quarterly_value(nii_entry)
        shares = (equity / nav_ps) if (equity and nav_ps) else None
        nii_ps_q = (nii_q / shares) if (nii_q is not None and shares) else None
        coverage = (nii_ps_q / dps_q) if (nii_ps_q is not None and dps_q) else None
        distribution_rate = (dps_ann / nav_ps * 100) if (dps_ann is not None and nav_ps) else None
        pik_ann = annualize(series_at(pik_node, end))
        tii_ann = annualize(series_at(tii_node, end))
        pik_share = (pik_ann / tii_ann * 100) if (pik_ann is not None and tii_ann) else None
        nav_tr = None
        if prev is not None:
            try:
                gap = (datetime.strptime(end, "%Y-%m-%d").date() - datetime.strptime(prev[0], "%Y-%m-%d").date()).days
            except ValueError:
                gap = 0
            if 70 <= gap <= 110 and prev[1]:
                div = dps_q if dps_q is not None else ((dps_ann or 0.0) * gap / 365.0)
                nav_tr = ((nav_ps - prev[1] + (div or 0.0)) / prev[1]) * 100
        rows.append({
            "end": end,
            "filed": info.get("filed"),
            "form": info.get("form"),
            "nav_ps": round(nav_ps, 3),
            "nii_ps_q": round(nii_ps_q, 3) if nii_ps_q is not None else None,
            "dps_q": round(dps_q, 3) if dps_q is not None else None,
            "coverage": round(coverage, 3) if coverage is not None else None,
            "distribution_rate_pct": round(distribution_rate, 2) if distribution_rate is not None else None,
            "pik_income_share_pct": round(pik_share, 2) if pik_share is not None else None,
            "nav_tr_pct": round(nav_tr, 2) if nav_tr is not None else None,
        })
        prev = (end, nav_ps)

    rows = rows[-12:]
    latest = rows[-1] if rows else {}
    price = market_cap = div_yield = div_rate = high52 = low52 = beta = None
    price_asof = p_nav_yahoo = book_yahoo = None
    if profile:
        price = (profile.get("price") or {}).get("regularMarketPrice")
        summary = profile.get("summaryDetail") or {}
        market_cap = summary.get("marketCap")
        div_yield = summary.get("dividendYield")
        div_rate = summary.get("dividendRate")
        hist = profile.get("historical") or {}
        high52 = (profile.get("price") or {}).get("fiftyTwoWeekHigh") or hist.get("fiftyTwoWeekHigh")
        low52 = (profile.get("price") or {}).get("fiftyTwoWeekLow") or hist.get("fiftyTwoWeekLow")
        beta = summary.get("beta")
        p_nav_yahoo = summary.get("priceToBook")
        book_yahoo = summary.get("bookValue")
        price_asof = (profile.get("generated_at") or "")[:10] or None

    cov_row = next((r for r in reversed(rows) if r.get("coverage") is not None), latest)
    pik_row = next((r for r in reversed(rows) if r.get("pik_income_share_pct") is not None), latest)
    tr_row = next((r for r in reversed(rows) if r.get("nav_tr_pct") is not None), latest)

    nav_latest = latest.get("nav_ps")
    p_nav = (price / nav_latest) if (price and nav_latest) else None
    if div_yield is None and div_rate and price:
        div_yield = div_rate / price * 100
    trs = [r.get("nav_tr_pct") for r in rows if r.get("nav_tr_pct") is not None][-4:]
    nav_tr_4q = None
    if len(trs) == 4:
        growth = 1.0
        for r in trs:
            growth *= (1 + r / 100.0)
        nav_tr_4q = (growth - 1) * 100

    valuation = {
        "price": round(price, 2) if price is not None else None,
        "price_asof": price_asof,
        "market_cap": market_cap,
        "p_nav": round(p_nav, 3) if p_nav is not None else None,
        "premium_discount_pct": round((p_nav - 1) * 100, 2) if p_nav is not None else None,
        "nav_per_share": nav_latest,
        "p_nav_yahoo": round(p_nav_yahoo, 3) if p_nav_yahoo is not None else None,
        "book_value_yahoo": book_yahoo,
        "dividend_yield_pct": round(div_yield, 2) if div_yield is not None else None,
        "dividend_rate": div_rate,
        "nii_ps_q": cov_row.get("nii_ps_q"),
        "dps_q": cov_row.get("dps_q"),
        "nii_coverage": cov_row.get("coverage"),
        "payout_ratio_pct": round(100.0 / cov_row["coverage"], 1) if cov_row.get("coverage") else None,
        "distribution_rate_pct": latest.get("distribution_rate_pct") or cov_row.get("distribution_rate_pct"),
        "pik_income_share_pct": pik_row.get("pik_income_share_pct"),
        "nav_tr_1q_pct": tr_row.get("nav_tr_pct"),
        "nav_tr_4q_pct": round(nav_tr_4q, 2) if nav_tr_4q is not None else None,
        "fifty_two_week_high": high52,
        "fifty_two_week_low": low52,
        "beta": beta,
        "profile_available": bool(profile),
        "latest_period": latest.get("end"),
        "coverage_period": cov_row.get("end"),
        "pik_period": pik_row.get("end"),
    }
    flags = []
    if p_nav is not None and p_nav < 0.35:
        flags.append("P/NAV below 0.35x — verify price and NAV basis")
    if div_yield is not None and div_yield > 20:
        flags.append("Dividend yield above 20% — possible distress or stale price")
    if valuation.get("nii_coverage") is not None and valuation["nii_coverage"] < 0.8:
        flags.append("NII coverage below 0.8x — distribution not covered by net investment income")
    valuation["flags"] = flags
    return {"periods": rows, "latest": valuation}


def build_vintage_history(periods):
    out = []
    for period in (periods or [])[-12:]:
        years = []
        for v in period.get("vintage_years") or []:
            if v.get("mark_pct") is None:
                continue
            years.append({
                "year": v.get("year"),
                "mark_pct": v.get("mark_pct"),
                "fair_value": v.get("fair_value"),
                "pct": v.get("pct"),
            })
        out.append({
            "filing_date": period.get("filing_date"),
            "period_end": period.get("period_end"),
            "years": years,
        })
    return out


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
    quality = build_quality(fin, load_profile(ticker))
    latest = periods[-1] if periods else None
    payload = {
        "ticker": ticker,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "meta": {
            "latest_holdings_filing": latest["filing_date"] if latest else None,
            "latest_holdings_period_end": latest["period_end"] if latest else None,
            "financials": build_fin_meta(fin),
        },
        "valuation": quality["latest"],
        "quality_periods": quality["periods"],
        "vintage_history": build_vintage_history(periods),
        "filings": load_filings(ticker),
        "credit": load_credit(ticker),
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
        quality = build_quality(fin, load_profile(ticker))
        val = quality.get("latest") or {}
        credit = load_credit(ticker) or {}
        nav = val.get("nav_per_share") if val.get("nav_per_share") is not None else latest_fin_metric(fin, "nav_per_share")
        leverage = latest_fin_metric(fin, "debt_to_equity")
        nii_ps = val.get("nii_ps_q") if val.get("nii_ps_q") is not None else latest_fin_metric(fin, "nii_per_share")
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
            "price": val.get("price"),
            "price_asof": val.get("price_asof"),
            "market_cap": val.get("market_cap"),
            "p_nav": val.get("p_nav"),
            "premium_discount_pct": val.get("premium_discount_pct"),
            "dividend_yield_pct": val.get("dividend_yield_pct"),
            "nii_coverage": val.get("nii_coverage"),
            "payout_ratio_pct": val.get("payout_ratio_pct"),
            "pik_income_share_pct": val.get("pik_income_share_pct"),
            "distribution_rate_pct": val.get("distribution_rate_pct"),
            "nav_tr_1q_pct": val.get("nav_tr_1q_pct"),
            "nav_tr_4q_pct": val.get("nav_tr_4q_pct"),
            "non_accrual_cost_pct": credit.get("non_accrual_cost_pct"),
            "non_accrual_fv_pct": credit.get("non_accrual_fv_pct"),
            "risk_rating": credit.get("risk_rating"),
        })

    metric_keys = [
        "fair_value", "first_lien_pct", "second_lien_pct", "subordinated_pct", "equity_pct",
        "weighted_avg_cash_rate", "weighted_avg_spread", "median_vintage_year", "age_0_2_pct",
        "age_5plus_pct", "nav_per_share", "debt_to_equity", "asset_coverage", "nii_per_share",
        "pik_share_pct", "floating_pct", "top10_pct", "unrealized_gl_pct",
        "p_nav", "premium_discount_pct", "dividend_yield_pct", "nii_coverage",
        "payout_ratio_pct", "pik_income_share_pct", "distribution_rate_pct",
        "nav_tr_1q_pct", "nav_tr_4q_pct", "non_accrual_cost_pct", "non_accrual_fv_pct",
        "risk_rating", "market_cap",
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
