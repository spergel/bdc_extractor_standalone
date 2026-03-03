#!/usr/bin/env python3
"""
One-time download of SEC company_tickers.json to data/company_tickers.json.
After running, the pipeline will load from this file instead of the SEC.

  python scripts/download_company_tickers.py
"""
import json
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "data" / "company_tickers.json"
URL = "https://www.sec.gov/files/company_tickers.json"


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "BDC-Extractor/1.0 (local)"}
    print(f"Downloading {URL} ...")
    r = requests.get(URL, headers=headers, timeout=90)
    r.raise_for_status()
    data = r.json()
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=0)
    print(f"Saved {len(data)} entries to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
