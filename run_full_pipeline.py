#!/usr/bin/env python3
"""
Run the full BDC pipeline in one go:

  1. Extract investments (XBRL or LLM, auto-routed per ticker)
  2. Post-process extraction (standardize columns)
  3. Consolidate investments to frontend
  4. Resolve company names (company_id, index, exposures)
  5. Build company profiles
  6. (Optional) Consolidate financial statements

Usage:
  python run_full_pipeline.py --ticker CSWC --years-back 1
  python run_full_pipeline.py --tickers ARCC BBDC CSWC --years-back 2 --force
  python run_full_pipeline.py --tickers CSWC --skip-investments   # re-run only consolidate/resolve/profiles
  python run_full_pipeline.py --tickers OBDC --pipeline llm       # force LLM for a specific ticker
"""

import subprocess
import sys
import logging
import argparse
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent

# Import pipeline assignments from process_all_bdcs
from process_all_bdcs import XBRL_TICKERS, LLM_TICKERS, CUSTOM_SCRAPER_TICKERS, CUSTOM_SCRAPER_SCRIPTS


def run(cmd: list[str], cwd: Path = REPO_ROOT) -> bool:
    """Run a command; return True on success, False on failure."""
    logger.info("Running: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, cwd=cwd)
        return True
    except subprocess.CalledProcessError as e:
        logger.error("Command failed with exit code %s: %s", e.returncode, " ".join(cmd))
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the full pipeline: extraction, post-process, consolidate, resolve, profiles"
    )
    parser.add_argument("--ticker", help="Single ticker (e.g. CSWC)")
    parser.add_argument("--tickers", nargs="+", help="One or more tickers (e.g. ARCC BBDC CSWC)")
    parser.add_argument("--years-back", type=int, default=1, help="Years of filings to extract (default 1)")
    parser.add_argument("--force", action="store_true", help="Re-extract even if output CSV exists")
    parser.add_argument("--pipeline", choices=["auto", "xbrl", "llm"], default="auto",
                        help="Force a specific extraction pipeline (default: auto-route per ticker)")
    parser.add_argument("--skip-investments", action="store_true", help="Skip extraction")
    parser.add_argument("--skip-financials", action="store_true", help="Skip financial statement extraction")
    parser.add_argument("--skip-consolidation", action="store_true", help="Skip consolidate + resolve + profiles")
    parser.add_argument("--skip-profiles", action="store_true", help="Skip build_profiles step")
    parser.add_argument("--with-llm", action="store_true", help="Use LLM for profile descriptions")
    args = parser.parse_args()

    tickers = []
    if args.tickers:
        tickers = args.tickers
    if args.ticker:
        tickers = [args.ticker] if not tickers else tickers
    if not tickers:
        parser.error("Provide --ticker T or --tickers T1 T2 ...")

    # Partition tickers into XBRL vs LLM vs custom scraper
    xbrl_set = set(XBRL_TICKERS)
    custom_set = set(CUSTOM_SCRAPER_TICKERS)
    if args.pipeline == "xbrl":
        xbrl_batch = list(tickers)
        llm_batch = []
        custom_batch = []
    elif args.pipeline == "llm":
        xbrl_batch = []
        llm_batch = list(tickers)
        custom_batch = []
    else:
        custom_batch = [t for t in tickers if t in custom_set]
        xbrl_batch = [t for t in tickers if t in xbrl_set]
        llm_batch = [t for t in tickers if t not in xbrl_set and t not in custom_set]

    logger.info("Full pipeline for %s (years_back=%s): %d XBRL, %d LLM, %d custom",
                tickers, args.years_back, len(xbrl_batch), len(llm_batch), len(custom_batch))

    # 1. Investment extraction (per ticker, routed by pipeline)
    if not args.skip_investments:
        for ticker in custom_batch:
            script = CUSTOM_SCRAPER_SCRIPTS.get(ticker)
            if not script:
                logger.warning("No custom scraper script for %s, skipping", ticker)
                continue
            logger.info("--- Custom scraper: %s ---", ticker)
            cmd = [
                sys.executable,
                str(REPO_ROOT / script),
                "--years-back", str(args.years_back),
                "--output-dir", str(REPO_ROOT / "output"),
            ]
            if args.force:
                cmd.append("--force")
            if not run(cmd):
                return 1

        for ticker in xbrl_batch:
            logger.info("--- XBRL extraction: %s ---", ticker)
            cmd = [
                sys.executable,
                str(REPO_ROOT / "src/extraction/xbrl_investment_extractor.py"),
                "--ticker", ticker,
                "--years-back", str(args.years_back),
                "--output-dir", str(REPO_ROOT / "output"),
            ]
            if args.force:
                cmd.append("--force")
            if not run(cmd):
                return 1

        for ticker in llm_batch:
            logger.info("--- LLM extraction: %s ---", ticker)
            cmd = [
                sys.executable,
                str(REPO_ROOT / "src/extraction/llm_table_scraper.py"),
                "--ticker", ticker,
                "--years-back", str(args.years_back),
            ]
            if args.force:
                cmd.append("--force")
            if not run(cmd):
                return 1

        # Financial statements (always XBRL, for all tickers)
        if not args.skip_financials:
            for ticker in tickers:
                logger.info("--- Financials: %s ---", ticker)
                cmd = [
                    sys.executable,
                    str(REPO_ROOT / "src/extraction/financial_statements_extractor.py"),
                    "--ticker", ticker,
                    "--years-back", str(args.years_back),
                    "--filing-type", "10-Q",
                ]
                if args.force:
                    cmd.append("--force")
                if not run(cmd):
                    return 1

        logger.info("Post-processing extraction...")
        if not run([sys.executable, str(REPO_ROOT / "src/processing/post_process_extraction.py"),
                    "--directory", "output"]):
            return 1

    # 2. Consolidate + resolve + profiles
    if not args.skip_consolidation:
        logger.info("Consolidating investments...")
        if not run([sys.executable, str(REPO_ROOT / "src/consolidation/consolidate_investments.py")]):
            return 1
        logger.info("Resolving company names...")
        if not run([sys.executable, str(REPO_ROOT / "src/company_resolution/resolve_companies.py")]):
            return 1
        if not args.skip_profiles:
            logger.info("Building company profiles...")
            cmd = [sys.executable, str(REPO_ROOT / "src/company_resolution/build_profiles.py")]
            if not args.with_llm:
                cmd.append("--no-llm")
            if not run(cmd):
                return 1
        logger.info("Consolidating financial statements...")
        if not run([sys.executable, str(REPO_ROOT / "src/consolidation/consolidate_financial_statements.py")]):
            return 1

    logger.info("Pipeline finished successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
