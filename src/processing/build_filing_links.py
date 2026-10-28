#!/usr/bin/env python3
"""
Build SEC EDGAR filing links for every holdings period.

Maps each ticker/filing-date to its 10-Q/10-K accession number, primary
document and EDGAR URL using the SEC submissions API (cached).

Outputs:
    app/public/data/filings/{TICKER}.json
    app/public/data/filings/filings_index.json

Usage:
    python src/processing/build_filing_links.py --all
    python src/processing/build_filing_links.py --ticker ARCC
"""

import argparse
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
FILINGS_DIR = DATA_DIR / "filings"
CACHE_DIR = REPO_ROOT / "output" / "filings" / "submissions"

HEADERS = {"User-Agent": "BDC-Research research@example.com"}
FILING_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A", "10-KT"}


def load_ticker_ciks() -> dict:
    path = REPO_ROOT / "data" / "company_tickers.json"
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    ciks = {}
    for value in raw.values():
        if isinstance(value, dict) and value.get("ticker"):
            ciks[value["ticker"].upper()] = {
                "cik": str(int(value["cik_str"])).zfill(10),
                "title": value.get("title", ""),
            }
    overrides = {
        "BCRED": ("0001803498", "Blackstone Private Credit Fund"),
        "LRFC": ("0001278752", "Logan Ridge Finance Corp"),
        "MRCC": ("0001512931", "Monroe Capital Corporation"),
        "SSSS": ("0001509470", "Neostellar Capital Corp."),
    }
    ciks.update({t: {"cik": c, "title": title} for t, (c, title) in overrides.items()})
    return ciks


def fetch_submissions(cik: str, refresh: bool = False) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"CIK{cik}.json"
    if cache.exists() and not refresh:
        with open(cache, encoding="utf-8") as fh:
            return json.load(fh)
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    time.sleep(0.15)
    return data


def build_filing_map(submissions: dict, cik: str) -> dict:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    fields = ["filingDate", "reportDate", "form", "accessionNumber", "primaryDocument", "primaryDocDescription"]
    rows = zip(*[recent.get(f, []) for f in fields])
    out = {}
    for filed, report, form, acc, doc, desc in rows:
        if form not in FILING_FORMS or not acc or not doc:
            continue
        nodash = acc.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nodash}"
        entry = {
            "form": form,
            "filed": filed,
            "report_date": report,
            "accession": acc,
            "primary_document": doc,
            "description": desc,
            "url": f"{base}/{doc}",
            "index_url": f"{base}/",
        }
        existing = out.get(filed)
        # Prefer the original filing over an amendment
        if existing and "/A" not in existing.get("form", "") and "/A" in form:
            continue
        out[filed] = entry
    return out


def nearest_period_filing(period: str, filings: dict):
    if period in filings:
        return filings[period]
    try:
        d = datetime.strptime(period, "%Y-%m-%d").date()
    except ValueError:
        return None
    best = None
    for filed, entry in filings.items():
        try:
            fd = datetime.strptime(filed, "%Y-%m-%d").date()
        except ValueError:
            continue
        diff = abs((fd - d).days)
        if diff <= 7 and (best is None or diff < best[0]):
            best = (diff, entry)
    return best[1] if best else None


def process_ticker(ticker: str, cik_info: dict, index: dict, refresh: bool = False):
    cik = cik_info["cik"]
    try:
        subs = fetch_submissions(cik, refresh=refresh)
    except Exception as exc:
        logger.warning("Submissions fetch failed for %s: %s", ticker, exc)
        return None
    filings = build_filing_map(subs, cik)
    periods = ((index.get("tickers") or {}).get(ticker) or {}).get("periods") or []
    matched = {}
    missing = []
    for p in periods:
        hit = nearest_period_filing(p, filings)
        if hit:
            matched[p] = hit
        else:
            missing.append(p)
    latest = max(filings.values(), key=lambda x: x["filed"]) if filings else None
    payload = {
        "ticker": ticker,
        "cik": cik,
        "entity_name": subs.get("name") or cik_info.get("title"),
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "filings": filings,
        "matched_periods": matched,
        "latest": latest,
        "missing_periods": missing,
    }
    FILINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(FILINGS_DIR / f"{ticker}.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    logger.info("%s: %d filings, %d/%d periods matched", ticker, len(filings), len(matched), len(periods))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Build SEC EDGAR filing links")
    parser.add_argument("--ticker", help="Only build one ticker")
    parser.add_argument("--refresh", action="store_true", help="Bypass submissions cache")
    args = parser.parse_args()

    with open(DATA_DIR / "investments_index.json", encoding="utf-8") as fh:
        index = json.load(fh)
    ciks = load_ticker_ciks()
    tickers = [args.ticker.upper()] if args.ticker else sorted((index.get("tickers") or {}).keys())

    results = {}
    for ticker in tickers:
        if ticker not in ciks:
            logger.warning("No CIK for %s", ticker)
            continue
        payload = process_ticker(ticker, ciks[ticker], index, refresh=args.refresh)
        if payload:
            results[ticker] = payload

    summary = {}
    for ticker, payload in results.items():
        summary[ticker] = {
            "cik": payload["cik"],
            "filings": len(payload["filings"]),
            "periods_matched": len(payload["matched_periods"]),
            "latest": (payload.get("latest") or {}).get("filed"),
        }
    with open(FILINGS_DIR / "filings_index.json", "w", encoding="utf-8") as fh:
        json.dump({"generated_at": datetime.utcnow().isoformat() + "Z", "tickers": summary}, fh)
    logger.info("Wrote filing links for %d tickers", len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
