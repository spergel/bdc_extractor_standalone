#!/usr/bin/env python3
"""
Download BCIC SEC filing artifacts for inspection and custom scraper development:
  - XBRL instance document (e.g. bcic-YYYYMMDD_htm.xml)
  - All HTML documents (main 10-Q/10-K and exhibits)

Files are saved under output/bcic_artifacts/{filing_date}/ so you can:
  1. Inspect XBRL dimension strings and company names
  2. Inspect HTML schedule-of-investments table structure
  3. Build a custom scraper (see run_custom_scraper_bcic.py)

Usage (from project root):
  python scripts/download_bcic_filing.py
  python scripts/download_bcic_filing.py --filing-type 10-K --year 2024
  python scripts/download_bcic_filing.py --ticker BCIC --year 2025 --quarter Q3
"""

import argparse
import logging
import re
import sys
from pathlib import Path

import requests

# Run from project root
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src" / "extraction") not in sys.path:
    sys.path.insert(0, str(ROOT / "src" / "extraction"))

from sec_api_client import SECAPIClient

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _xbrl_instance_pattern(filename: str) -> bool:
    """True if filename looks like an XBRL instance (not schema/calc/def/lab/pre)."""
    fn = filename.lower()
    if any(skip in fn for skip in ["schema", "cal.xml", "def.xml", "lab.xml", "pre.xml", "calculation", "definition", "label", "presentation"]):
        return False
    if any(pat in fn for pat in ["_htm.xml", "xbrl.htm", "xbrl.html", "instance.xml", "xbrl.xml"]) or (fn.endswith(".xml") and re.search(r"\d{8}", fn)):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Download BCIC XBRL + HTML filing artifacts")
    parser.add_argument("--ticker", default="BCIC", help="Ticker (default: BCIC)")
    parser.add_argument("--filing-type", default="10-Q", choices=("10-Q", "10-K"))
    parser.add_argument("--year", type=int, default=None, help="Filing year (default: latest)")
    parser.add_argument("--quarter", default=None, help="For 10-Q: Q1, Q2, Q3, Q4")
    parser.add_argument("--output-dir", default=None, help="Base dir for artifacts (default: output/bcic_artifacts)")
    args = parser.parse_args()

    base = Path(args.output_dir or ROOT / "output" / "bcic_artifacts")
    base.mkdir(parents=True, exist_ok=True)

    client = SECAPIClient(data_dir=str(ROOT / "data"))
    index_url = client.get_filing_index_url(
        ticker=args.ticker,
        filing_type=args.filing_type,
        year=args.year,
        quarter=args.quarter if args.filing_type == "10-Q" else None,
    )
    if not index_url:
        logger.error("No %s filing found for %s", args.filing_type, args.ticker)
        return 1

    # Filing date from cache (report date) or fetch index page
    filing_date = getattr(client, "_cached_period_date", {}).get(index_url)
    if not filing_date:
        try:
            resp = requests.get(index_url, headers=client.headers, timeout=90)
            resp.raise_for_status()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.content, "html.parser")
            text = soup.get_text()
            m = re.search(r"(?:Filing Date|Date Filed)[:\s]+(\d{4}-\d{2}-\d{2})", text, re.I)
            if m:
                filing_date = m.group(1)
            else:
                for td in soup.find_all("td"):
                    t = td.get_text(strip=True)
                    if re.match(r"\d{4}-\d{2}-\d{2}", t):
                        filing_date = t[:10]
                        break
        except Exception as e:
            logger.warning("Could not get filing date from index: %s", e)
        if not filing_date:
            filing_date = "unknown"

    out_dir = base / filing_date
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Saving artifacts to %s", out_dir)

    documents = client.get_documents_from_index(index_url)
    if not documents:
        logger.error("No documents in index")
        return 1

    saved = []
    xbrl_path = None
    for doc in documents:
        fn = doc.filename
        try:
            resp = requests.get(doc.url, headers=client.headers, timeout=90)
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", fn, e)
            continue

        if _xbrl_instance_pattern(fn):
            path = out_dir / fn
            path.write_bytes(resp.content)
            xbrl_path = path
            saved.append(str(path))
            logger.info("Saved XBRL instance: %s (%d KB)", path.name, len(resp.content) // 1024)
        elif fn.lower().endswith(".htm") or fn.lower().endswith(".html"):
            if "xml" in fn.lower():
                continue
            path = out_dir / fn
            path.write_text(resp.text, encoding="utf-8", errors="replace")
            saved.append(str(path))
            logger.info("Saved HTML: %s", path.name)

    # Save index page for reference
    try:
        resp = requests.get(index_url, headers=client.headers, timeout=90)
        resp.raise_for_status()
        (out_dir / "index.html").write_text(resp.text, encoding="utf-8", errors="replace")
        saved.append(str(out_dir / "index.html"))
    except Exception as e:
        logger.warning("Could not save index: %s", e)

    if not saved:
        logger.error("No files saved")
        return 1
    logger.info("Done. Saved %d file(s) to %s", len(saved), out_dir)
    if xbrl_path:
        logger.info("Inspect XBRL company names/dimensions in: %s", xbrl_path.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
