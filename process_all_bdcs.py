#!/usr/bin/env python3
"""
Process all BDC tickers for the frontend.

Two extraction pipelines based on XBRL data quality:
  - XBRL pipeline:  BDCs with sufficient structured data (cost + rates in XBRL)
  - HTML pipeline:  BDCs that need HTML table scraping via BeautifulSoup (no LLM)

After extraction, both pipelines feed into the same post-processing,
consolidation, company resolution, and profile build steps.
"""

import subprocess
import sys
import logging
import argparse
from typing import List
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent

# ── Pipeline assignment (2026-02 audit) ──
#
# Custom scraper (non-LLM, one script per ticker): use output/custom_scraper_TICKER_*.csv in consolidation
CUSTOM_SCRAPER_TICKERS = ["BCIC", "BCSF"]
# Map ticker -> script path (relative to REPO_ROOT); add new tickers here when adding a new scraper script
CUSTOM_SCRAPER_SCRIPTS = {
    "BCIC": "scripts/run_custom_scraper_bcic.py",
    "BCSF": "scripts/run_custom_scraper_bcsf.py",
}

# XBRL pipeline: Grade A — cost ≥ 60% AND rate ≥ 40% from XBRL (excludes CUSTOM_SCRAPER_TICKERS)
XBRL_TICKERS = [
    "ARCC", "BBDC", "BXSL", "CCAP", "CGBD",
    "CSWC", "FDUS", "FSK", "GAIN", "GBDC", "GLAD", "GSBD",
    "HRZN", "KBDC", "LIEN", "MAIN", "MRCC", "MSDL",
    "MSIF", "NCDL", "NMFC", "OCSL", "OFS",
    "PFLT", "PNNT", "PSBD", "PSEC", "RAND", "SAR", "SCM",
    "SLRC", "TCPC", "TRIN", "TSLX", "WHF",
]

# LLM (html_soi_parser) pipeline: tickers with functional HTML SOI tables
# Grade B: CION (rate=12%), HTGC (rate=2%), OBDC (rate=0%), TPVG (rate=12%)
LLM_TICKERS = [
    "CION", "HTGC",
    "OBDC", "TPVG",
]

# DSPy pipeline: tickers where html_soi_parser produces empty/broken output.
# html_soi_parser can't handle their filing table format; DSPy Gemini extracts correctly.
DSPY_TICKERS = [
    "EQS",   # No XBRL SOI, html_soi_parser produces 6-8 rows (incomplete)
    "GECC",  # XBRL/HTML outputs contain many malformed/placeholder rows; DSPy extraction is materially cleaner
    "ICMB",  # html_soi_parser produces 1-2 rows; table format not recognized
    "MFIC",  # html_soi_parser: 100% blank investment_type (type embedded in company name as comma suffix)
    "OXSQ",  # html_soi_parser returns 0 rows (COMPANY/INVESTMENT header not recognized)
    "PFX",   # html_soi_parser: 100% blank investment_type + column mapping wrong
    # "RAND" moved to XBRL_TICKERS — has XBRL SOI data; correct scale (whole-dollar filings, XBRL handles correctly)
    "RWAY",  # html_soi_parser produces 77% garbled company names (flat-path row format); DSPy: 0% garbled, 100% rate coverage
    "SSSS",  # No XBRL SOI, html_soi_parser produces ≤1 row per filing
]

# Defunct / merged tickers (removed from pipeline):
# ORCC → renamed to OBDC (Blue Owl Capital, 2023)
# PTMN → merged into BCIC (BCP Investment Corp, 2025)
# SUNS → merged into SLRC (SLR Investment Corp, 2022)
# NEWT → converted to a bank (Newtek Bank) in 2023; only 6 JV rows remain, not useful

ALL_BDCS = sorted(set(XBRL_TICKERS + LLM_TICKERS + DSPY_TICKERS + CUSTOM_SCRAPER_TICKERS))


def run(cmd: List[str], cwd: Path = REPO_ROOT) -> bool:
    """Run a command; return True on success, False on failure."""
    logger.info("Running: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, cwd=cwd)
        return True
    except subprocess.CalledProcessError as e:
        logger.error("Command failed (exit %s): %s", e.returncode, " ".join(cmd))
        return False


def extract_xbrl(ticker: str, years_back: int, force: bool) -> bool:
    """Extract investments via XBRL pipeline."""
    cmd = [
        sys.executable,
        str(REPO_ROOT / "src/extraction/xbrl_investment_extractor.py"),
        "--ticker", ticker,
        "--years-back", str(years_back),
        "--output-dir", str(REPO_ROOT / "output"),
    ]
    if force:
        cmd.append("--force")
    return run(cmd)


def extract_html(ticker: str, years_back: int, force: bool) -> bool:
    """Extract investments via custom HTML SOI parser (no LLM)."""
    cmd = [
        sys.executable,
        str(REPO_ROOT / "src/extraction/html_soi_parser.py"),
        "--ticker", ticker,
        "--years-back", str(years_back),
        "--output-dir", str(REPO_ROOT / "output"),
        "--data-dir", str(REPO_ROOT / "data"),
    ]
    if force:
        cmd.append("--force")
    return run(cmd)


def extract_dspy(ticker: str, years_back: int, force: bool) -> bool:
    """Extract investments via DSPy+Gemini pipeline (for tickers html_soi_parser can't handle)."""
    from datetime import date, timedelta
    start = (date.today() - timedelta(days=365 * years_back)).isoformat()
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts/run_dspy_scraper.py"),
        "--ticker", ticker,
        "--start", start,
        "--output-dir", str(REPO_ROOT / "output"),
    ]
    if force:
        cmd.append("--force")
    return run(cmd)


def extract_financials(ticker: str, years_back: int, force: bool) -> bool:
    """Extract financial statements (balance sheet / income statement) via XBRL."""
    cmd = [
        sys.executable,
        str(REPO_ROOT / "src/extraction/financial_statements_extractor.py"),
        "--ticker", ticker,
        "--years-back", str(years_back),
        "--filing-type", "10-Q",
    ]
    if force:
        cmd.append("--force")
    return run(cmd)


def main():
    parser = argparse.ArgumentParser(description="Process all BDCs for the frontend")
    parser.add_argument('--tickers', nargs='+', help='Specific tickers to process (default: all)')
    parser.add_argument('--years-back', type=int, default=1, help='Number of years back to process')
    parser.add_argument('--force', action='store_true', help='Re-process even if output CSV exists')
    parser.add_argument('--pipeline', choices=['auto', 'xbrl', 'llm', 'dspy'], default='auto',
                        help='Force a specific extraction pipeline (default: auto-route per ticker)')
    parser.add_argument('--skip-investments', action='store_true', help='Skip investment extraction')
    parser.add_argument('--skip-financials', action='store_true', help='Skip financial statement extraction')
    parser.add_argument('--skip-consolidation', action='store_true', help='Skip consolidation step')
    parser.add_argument('--skip-company-resolution', action='store_true', help='Skip company ID resolution')
    parser.add_argument('--skip-profiles', action='store_true', help='Skip company profile build')

    args = parser.parse_args()

    tickers = args.tickers if args.tickers else ALL_BDCS

    # Partition tickers into XBRL vs LLM vs DSPy vs custom scraper
    xbrl_set = set(XBRL_TICKERS)
    dspy_set = set(DSPY_TICKERS)
    custom_set = set(CUSTOM_SCRAPER_TICKERS)
    if args.pipeline == 'xbrl':
        xbrl_batch = tickers
        llm_batch = []
        dspy_batch = []
        custom_batch = []
    elif args.pipeline == 'llm':
        xbrl_batch = []
        llm_batch = tickers
        dspy_batch = []
        custom_batch = []
    elif args.pipeline == 'dspy':
        xbrl_batch = []
        llm_batch = []
        dspy_batch = tickers
        custom_batch = []
    else:
        custom_batch = [t for t in tickers if t in custom_set]
        xbrl_batch = [t for t in tickers if t in xbrl_set]
        dspy_batch = [t for t in tickers if t in dspy_set and t not in xbrl_set and t not in custom_set]
        llm_batch = [t for t in tickers if t not in xbrl_set and t not in dspy_set and t not in custom_set]

    logger.info(
        f"Processing {len(tickers)} BDCs: {len(xbrl_batch)} XBRL, "
        f"{len(llm_batch)} LLM, {len(dspy_batch)} DSPy, {len(custom_batch)} custom scraper"
    )

    # ── 1. Investment extraction ──
    if not args.skip_investments:
        if custom_batch:
            logger.info("=== Custom scraper: %s ===", ", ".join(custom_batch))
            for ticker in custom_batch:
                script = CUSTOM_SCRAPER_SCRIPTS.get(ticker)
                if not script:
                    logger.warning("No custom scraper script for %s, skipping", ticker)
                    continue
                cmd = [sys.executable, str(REPO_ROOT / script),
                       "--years-back", str(args.years_back), "--output-dir", str(REPO_ROOT / "output")]
                if args.force:
                    cmd.append("--force")
                run(cmd)

        if xbrl_batch:
            logger.info("=== XBRL extraction: %s ===", ", ".join(xbrl_batch))
            for ticker in xbrl_batch:
                logger.info(f"--- XBRL: {ticker} ---")
                extract_xbrl(ticker, args.years_back, args.force)

        if llm_batch:
            logger.info("=== HTML extraction: %s ===", ", ".join(llm_batch))
            for ticker in llm_batch:
                logger.info(f"--- HTML: {ticker} ---")
                extract_html(ticker, args.years_back, args.force)

        if dspy_batch:
            logger.info("=== DSPy extraction: %s ===", ", ".join(dspy_batch))
            for ticker in dspy_batch:
                logger.info(f"--- DSPy: {ticker} ---")
                extract_dspy(ticker, args.years_back, args.force)

    # ── 2. Financial statement extraction ──
    if not args.skip_financials:
        logger.info("=== Financial statements ===")
        for ticker in tickers:
            logger.info(f"--- Financials: {ticker} ---")
            extract_financials(ticker, args.years_back, args.force)

    # ── 3. Post-process extracted data ──
    if not args.skip_investments:
        logger.info("Post-processing extracted investment data...")
        run([sys.executable, str(REPO_ROOT / "src/processing/post_process_extraction.py"),
             "--directory", "output"])

    # ── 4. Consolidate and move to frontend ──
    if not args.skip_consolidation:
        logger.info("Consolidating investments...")
        run([sys.executable, str(REPO_ROOT / "src/consolidation/consolidate_investments.py")])

        if not args.skip_company_resolution:
            logger.info("Resolving company names...")
            run([sys.executable, str(REPO_ROOT / "src/company_resolution/resolve_companies.py")])

            if not args.skip_profiles:
                logger.info("Building company profiles...")
                run([sys.executable, str(REPO_ROOT / "src/company_resolution/build_profiles.py"),
                     "--no-llm"])

        logger.info("Consolidating financial statements...")
        run([sys.executable, str(REPO_ROOT / "src/consolidation/consolidate_financial_statements.py")])

    logger.info("Done!")


if __name__ == "__main__":
    main()
