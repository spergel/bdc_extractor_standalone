#!/usr/bin/env python3
"""
Reconcile holdings fair value against reported balance-sheet investments.

For every filing period, compare the sum of holdings `fair_value` (thousands)
with the XBRL `Investments at Fair Value` fact for the same quarter end. The
reported value may be in dollars, thousands or millions, so a per-ticker scale
is inferred before comparing.

Outputs:
    app/public/data/quality/{TICKER}.json
    app/public/data/quality/quality_index.json
"""

import argparse
import csv
import glob
import json
import logging
import math
from datetime import date, datetime
from pathlib import Path
from statistics import median

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
INV_DIR = DATA_DIR / "investments"
FIN_DIR = DATA_DIR / "financials"
ANALYTICS_DIR = DATA_DIR / "analytics"
FILINGS_DIR = DATA_DIR / "filings"
OUT_DIR = DATA_DIR / "quality"

MATCH_PCT = 5.0
NEAR_PCT = 10.0


def quarter_end_before(filing_date: str) -> str:
    d = datetime.strptime(filing_date, "%Y-%m-%d").date()
    cands = [
        date(y, m, day)
        for y in (d.year, d.year - 1)
        for m, day in ((3, 31), (6, 30), (9, 30), (12, 31))
        if date(y, m, day) <= d
    ]
    return max(cands).isoformat() if cands else filing_date


def fair_value(row) -> float:
    try:
        return float(row.get("fair_value") or 0)
    except (TypeError, ValueError):
        return 0.0


def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def infer_scale(pairs):
    """pairs: (holdings_thousands, reported_raw). Return (scale, median_ratio)."""
    candidates = (1, 1000, 0.001, 1e6, 1e-6)
    best = None
    for scale in candidates:
        ratios = [h / (r / scale) for h, r in pairs if h > 0 and r > 0]
        if not ratios:
            continue
        med = median(ratios)
        err = abs(math.log(med)) if med > 0 else 99
        if best is None or err < best[0]:
            best = (err, scale, med)
    if not best:
        return 1, None
    return best[1], best[2]


def classify(delta_pct):
    if delta_pct is None:
        return "no_reported"
    a = abs(delta_pct)
    if a <= MATCH_PCT:
        return "match"
    if a <= NEAR_PCT:
        return "near"
    return "mismatch"


def trust_for(match, near, mismatch):
    checked = match + near + mismatch
    if checked == 0:
        return "unknown"
    pct = 100.0 * match / checked
    if mismatch == 0 and pct >= 80:
        return "good"
    if pct >= 50:
        return "warn"
    return "bad"


def build_ticker(ticker: str):
    fin = load_json(FIN_DIR / f"{ticker}_financials.json") or {}
    node = ((fin.get("statements") or {}).get("balance_sheet") or {}).get("Investments at Fair Value") or {}
    reported = {k: v.get("val") for k, v in (node.get("series") or {}).items() if v.get("val")}
    analytics = load_json(ANALYTICS_DIR / f"{ticker}.json") or {}
    flags_by_fd = {p.get("filing_date"): p.get("quality_flags") or [] for p in analytics.get("periods") or []}
    filings = (load_json(FILINGS_DIR / f"{ticker}.json") or {}).get("filings") or {}
    fin_by_filed = {
        p.get("filed"): p.get("end")
        for p in (fin.get("periods") or [])
        if p.get("filed") and p.get("end")
    }

    raw = []
    for path in sorted(glob.glob(str(INV_DIR / ticker / "*.csv"))):
        fd = Path(path).stem
        if len(fd) != 10:
            continue
        with open(path, encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        # Prefer the financial period filed on the same day; this handles
        # non-calendar fiscal years (e.g. SAR's Feb/May/Aug/Nov quarter ends).
        end = fin_by_filed.get(fd) or quarter_end_before(fd)
        rep = reported.get(end)
        raw.append((fd, end, sum(fair_value(r) for r in rows), len(rows), rep))

    pairs = [(h, r) for _, _, h, _, r in raw if h > 0 and r]
    scale, median_ratio = infer_scale(pairs)

    periods = []
    for fd, end, holdings, row_count, rep in raw:
        if rep:
            target = rep / scale
            delta = (holdings - target) / target * 100 if target else None
            status = classify(delta)
        else:
            target = None
            delta = None
            status = "no_reported"
        filing = filings.get(fd) or {}
        periods.append({
            "filing_date": fd,
            "period_end": end,
            "holdings_fv": holdings,
            "row_count": row_count,
            "reported_fv": rep,
            "reported_fv_k": target,
            "delta_pct": round(delta, 2) if delta is not None else None,
            "status": status,
            "quality_flags": flags_by_fd.get(fd) or [],
            "filing_url": filing.get("url"),
            "form": filing.get("form"),
        })

    match = sum(1 for p in periods if p["status"] == "match")
    near = sum(1 for p in periods if p["status"] == "near")
    mismatch = sum(1 for p in periods if p["status"] == "mismatch")
    checked = match + near + mismatch
    latest = periods[-1] if periods else None
    summary = {
        "ticker": ticker,
        "scale": scale,
        "median_ratio": round(median_ratio, 3) if median_ratio is not None else None,
        "periods": len(periods),
        "with_reported": checked,
        "match": match,
        "near": near,
        "mismatch": mismatch,
        "match_pct": round(100.0 * match / checked, 1) if checked else None,
        "trust": trust_for(match, near, mismatch),
        "latest": latest,
        "issue_periods": [
            {"filing_date": p["filing_date"], "delta_pct": p["delta_pct"], "status": p["status"],
             "holdings_fv": p["holdings_fv"], "reported_fv_k": p["reported_fv_k"],
             "quality_flags": p["quality_flags"]}
            for p in periods if p["status"] in ("near", "mismatch")
        ],
    }
    return {"ticker": ticker, "summary": summary, "periods": periods}


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile holdings against reported fair value")
    parser.add_argument("--ticker", help="Only process one ticker")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.ticker:
        tickers = [args.ticker.upper()]
    else:
        tickers = sorted(p.name for p in INV_DIR.iterdir() if p.is_dir())

    index = {"generated_at": datetime.utcnow().isoformat() + "Z", "tickers": {}}
    totals = {"periods": 0, "with_reported": 0, "match": 0, "near": 0, "mismatch": 0}
    trust_counts = {"good": 0, "warn": 0, "bad": 0, "unknown": 0}

    for ticker in tickers:
        try:
            payload = build_ticker(ticker)
        except Exception as exc:
            logger.warning("%s: failed: %s", ticker, exc)
            continue
        with open(OUT_DIR / f"{ticker}.json", "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        s = payload["summary"]
        index["tickers"][ticker] = s
        trust_counts[s["trust"]] = trust_counts.get(s["trust"], 0) + 1
        for key in totals:
            totals[key] += s.get(key) or 0
        logger.info("%s: %s (%s%% match, %d mismatch)", ticker, s["trust"], s["match_pct"], s["mismatch"])

    index["summary"] = {
        "tickers": len(index["tickers"]),
        **totals,
        "match_pct": round(100.0 * totals["match"] / totals["with_reported"], 1) if totals["with_reported"] else None,
        "trust_counts": trust_counts,
    }
    with open(OUT_DIR / "quality_index.json", "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)
    logger.info("Wrote quality index: %s", index["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
