#!/usr/bin/env python3
"""
Check SEC submissions for new 10-Q / 10-K filings and optionally refresh data.

Usage:
    python scripts/check_new_filings.py
    python scripts/check_new_filings.py --ticker ARCC,FSK
    python scripts/check_new_filings.py --run
    python scripts/check_new_filings.py --force --json

The first run records the current latest filing per ticker. Later runs report
only newly seen accessions. `--run` re-extracts the affected tickers and then
rebuilds financials, filing links, credit metrics, consolidated holdings,
analytics and quality data.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "app" / "public" / "data"
OUTPUT_DIR = REPO_ROOT / "output"
SUBMISSIONS_DIR = OUTPUT_DIR / "filings" / "submissions"
STATE_PATH = OUTPUT_DIR / "filing_monitor_state.json"
UA = os.environ.get(
    "SEC_USER_AGENT", "bdc-data-pipeline/1.0 (contact: local@example.com)"
)
FORMS = ("10-Q", "10-Q/A", "10-K", "10-K/A")


def load_ciks() -> Dict[str, str]:
    ciks: Dict[str, str] = {}
    path = REPO_ROOT / "data" / "company_tickers.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for item in raw.values():
                t = (item.get("ticker") or "").upper()
                c = item.get("cik_str")
                if t and c:
                    ciks[t] = str(c).zfill(10)
        except Exception:
            pass
    try:
        sys.path.insert(0, str(REPO_ROOT / "src" / "extraction"))
        from scrapers.base import CIK_OVERRIDES  # type: ignore

        for t, info in CIK_OVERRIDES.items():
            ciks[t.upper()] = str(info["cik_str"]).zfill(10)
    except Exception:
        pass
    return ciks


def all_tickers() -> List[str]:
    return sorted(
        p.name for p in (DATA_DIR / "investments").iterdir()
        if p.is_dir() and p.name.isupper()
    )


def load_state() -> Dict[str, dict]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: Dict[str, dict]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fetch_submissions(cik: str) -> dict:
    SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    cache = SUBMISSIONS_DIR / f"CIK{cik}.json"
    headers = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=45)
            r.raise_for_status()
            data = r.json()
            cache.write_text(json.dumps(data), encoding="utf-8")
            return data
        except Exception as exc:
            last_err = exc
            time.sleep(2 + attempt * 3)
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    raise RuntimeError(f"SEC submissions unavailable for CIK {cik}: {last_err}")


def recent_filings(data: dict, limit: int = 12) -> List[dict]:
    recent = ((data.get("filings") or {}).get("recent") or {})
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    periods = recent.get("reportDate") or []
    docs = recent.get("primaryDocument") or []
    out = []
    for i, form in enumerate(forms):
        if form not in FORMS:
            continue
        out.append({
            "form": form,
            "filing_date": dates[i] if i < len(dates) else None,
            "accession": accessions[i] if i < len(accessions) else None,
            "period": periods[i] if i < len(periods) else None,
            "primary_document": docs[i] if i < len(docs) else None,
        })
        if len(out) >= limit:
            break
    out.sort(key=lambda x: (x.get("filing_date") or "", x.get("accession") or ""), reverse=True)
    return out


def run(cmd: List[str]) -> int:
    print("  $ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect new SEC BDC filings")
    parser.add_argument("--ticker", help="Comma-separated tickers (default: all)")
    parser.add_argument("--run", action="store_true",
                        help="Refresh data for tickers with new filings")
    parser.add_argument("--force", action="store_true",
                        help="Treat the latest filing as new")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args()

    ciks = load_ciks()
    tickers = ([t.strip().upper() for t in args.ticker.split(",")]
               if args.ticker else all_tickers())
    state = load_state()
    new_by_ticker: Dict[str, dict] = {}
    missing_cik = []

    for t in tickers:
        cik = ciks.get(t)
        if not cik:
            missing_cik.append(t)
            continue
        try:
            data = fetch_submissions(cik)
        except Exception as exc:
            print(f"{t}: WARN {exc}")
            continue
        filings = recent_filings(data)
        if not filings:
            continue
        latest = filings[0]
        previous = state.get(t) or {}
        is_new = args.force or (
            bool(previous.get("accession"))
            and previous.get("accession") != latest.get("accession")
        )
        state[t] = {
            "cik": cik,
            "accession": latest.get("accession"),
            "form": latest.get("form"),
            "filing_date": latest.get("filing_date"),
            "period": latest.get("period"),
            "checked_at": datetime.utcnow().isoformat() + "Z",
        }
        if is_new:
            new_by_ticker[t] = latest

    save_state(state)

    result = {
        "checked": len(tickers) - len(missing_cik),
        "new_count": len(new_by_ticker),
        "new_filings": new_by_ticker,
        "missing_cik": missing_cik,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if new_by_ticker:
            print("New filings:")
            for t, f in sorted(new_by_ticker.items()):
                print(f"  {t}: {f['form']} filed {f['filing_date']} "
                      f"(period {f['period']}, {f['accession']})")
        else:
            print(f"No new filings across {result['checked']} tickers.")
        if missing_cik:
            print("Missing CIK: " + ", ".join(missing_cik))

    if args.run and new_by_ticker:
        print("\nRefreshing affected tickers...")
        for t in sorted(new_by_ticker):
            run([sys.executable, "scripts/run_company_scraper.py",
                 "--ticker", t, "--years-back", "1",
                 "--output-dir", "output", "--data-dir", "data"])
        print("\nRebuilding derived data...")
        steps = [
            [sys.executable, "src/extraction/financial_statements_extractor.py", "--all"],
            [sys.executable, "src/processing/build_filing_links.py"],
            [sys.executable, "src/processing/extract_credit_quality.py"],
            [sys.executable, "src/consolidation/consolidate_investments.py"],
            [sys.executable, "src/processing/fix_multi_block_holdings.py"],
            [sys.executable, "src/processing/build_analytics.py"],
            [sys.executable, "src/processing/build_data_quality.py"],
        ]
        for step in steps:
            code = run(step)
            if code != 0:
                print(f"  step failed ({code}); continuing", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
