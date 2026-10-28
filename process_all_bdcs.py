#!/usr/bin/env python3
"""
Process BDC tickers for the frontend.

Extraction is handled by the per-company custom scrapers in
`src/extraction/scrapers/` (register new tickers in
`src/extraction/scrapers/__init__.py`).  After extraction, the shared
post-processing, consolidation, and company-resolution steps run unchanged.
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import List

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent
for p in (str(REPO_ROOT), str(REPO_ROOT / "src" / "extraction")):
    if p not in sys.path:
        sys.path.insert(0, p)

from scrapers import SCRAPERS  # noqa: E402

# Tickers with a custom scraper.  Consolidation imports this list and prefers
# custom_scraper_*.csv for these tickers over any old *_investments_*.csv.
CUSTOM_SCRAPER_TICKERS = sorted(SCRAPERS.keys())
ALL_BDCS = list(CUSTOM_SCRAPER_TICKERS)

SCRAPER_RUNNER = REPO_ROOT / "scripts" / "run_company_scraper.py"


def run(cmd: List[str], cwd: Path = REPO_ROOT) -> bool:
    """Run a command; return True on success, False on failure."""
    logger.info("Running: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, cwd=cwd)
        return True
    except subprocess.CalledProcessError as e:
        logger.error("Command failed (exit %s): %s", e.returncode, " ".join(cmd))
        return False


def extract_investments(ticker: str, years_back: int, force: bool) -> bool:
    """Extract investments for one ticker via its custom scraper."""
    cmd = [
        sys.executable,
        str(SCRAPER_RUNNER),
        "--ticker", ticker,
        "--years-back", str(years_back),
        "--output-dir", str(REPO_ROOT / "output"),
        "--data-dir", str(REPO_ROOT / "data"),
    ]
    if force:
        cmd.append("--force")
    return run(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Process BDCs for the frontend")
    parser.add_argument("--tickers", nargs="+", help="Specific tickers to process (default: all)")
    parser.add_argument("--years-back", type=int, default=1,
                        help="Number of years back to process (0 = latest quarter only)")
    parser.add_argument("--force", action="store_true",
                        help="Re-process even if output CSV exists")
    parser.add_argument("--skip-investments", action="store_true", help="Skip investment extraction")
    parser.add_argument("--skip-consolidation", action="store_true", help="Skip consolidation step")
    parser.add_argument("--skip-block-fix", action="store_true",
                        help="Skip splitting concatenated annual portfolio schedules")
    parser.add_argument("--skip-company-resolution", action="store_true",
                        help="Skip company ID resolution")
    parser.add_argument("--skip-profiles", action="store_true", help="Skip company profile build")
    parser.add_argument("--skip-financials", action="store_true",
                        help="Skip quarterly financial statement extraction (balance sheet / income / cash flow)")
    parser.add_argument("--skip-filings", action="store_true",
                        help="Skip SEC EDGAR filing-link build")
    parser.add_argument("--skip-credit", action="store_true",
                        help="Skip credit-quality extraction (non-accrual / risk rating)")
    parser.add_argument("--skip-analytics", action="store_true",
                        help="Skip frontend analytics rebuild (type/sector/vintage/peers JSON)")

    args = parser.parse_args()

    tickers = [t.upper() for t in (args.tickers or ALL_BDCS)]
    known = set(CUSTOM_SCRAPER_TICKERS)
    batch = [t for t in tickers if t in known]
    unknown = [t for t in tickers if t not in known]
    if unknown:
        logger.warning("No custom scraper registered for: %s", ", ".join(unknown))
    if not batch:
        logger.error("No supported tickers to process.")
        return 1

    logger.info("Processing %d BDCs via custom scrapers: %s", len(batch), ", ".join(batch))

    # ── 1. Investment extraction ──
    if not args.skip_investments:
        for ticker in batch:
            logger.info("--- Custom scraper: %s ---", ticker)
            extract_investments(ticker, args.years_back, args.force)

        logger.info("Post-processing extracted investment data...")
        run([sys.executable,
             str(REPO_ROOT / "src/processing/post_process_extraction.py"),
             "--directory", "output"])

    # ── 2. Consolidate and move to frontend ──
    if not args.skip_consolidation:
        logger.info("Consolidating investments...")
        run([sys.executable, str(REPO_ROOT / "src/consolidation/consolidate_investments.py")])

    if not args.skip_block_fix:
        logger.info("Splitting concatenated annual portfolio schedules...")
        run([sys.executable, str(REPO_ROOT / "src/processing/fix_multi_block_holdings.py")])

    if not args.skip_consolidation and not args.skip_company_resolution:
        logger.info("Resolving company names...")
        run([sys.executable, str(REPO_ROOT / "src/company_resolution/resolve_companies.py")])

        if not args.skip_profiles:
            logger.info("Building company profiles...")
            run([sys.executable, str(REPO_ROOT / "src/company_resolution/build_profiles.py"),
                 "--no-llm"])

    # ── 3. Quarterly financial statements + frontend analytics ──
    if not args.skip_financials:
        logger.info("Extracting quarterly financial statements (balance sheet / income / cash flow)...")
        run([sys.executable, str(REPO_ROOT / "src/extraction/financial_statements_extractor.py"),
             "--all"])

    if not args.skip_filings:
        logger.info("Building SEC EDGAR filing links...")
        run([sys.executable, str(REPO_ROOT / "src/processing/build_filing_links.py")])

    if not args.skip_credit:
        logger.info("Extracting credit quality (non-accrual / risk ratings)...")
        run([sys.executable, str(REPO_ROOT / "src/processing/extract_credit_quality.py")])

    if not args.skip_analytics:
        logger.info("Rebuilding frontend analytics (type/sector/vintage/peers)...")
        run([sys.executable, str(REPO_ROOT / "src/processing/build_analytics.py")])

    logger.info("Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
