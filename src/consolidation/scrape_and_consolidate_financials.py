#!/usr/bin/env python3
"""
Complete workflow: Scrape financial statements and consolidate for frontend.

This script:
1. Scrapes financial statements from SEC filings (using financial_statements_extractor.py)
2. Consolidates individual files into master CSV files (using consolidate_financial_statements.py)
3. Places consolidated files in frontend/public/data/ for use by the frontend

Usage:
    # Scrape and consolidate for a single ticker
    python scrape_and_consolidate_financials.py --ticker MRCC --years-back 1
    
    # Scrape and consolidate for all tickers in a list
    python scrape_and_consolidate_financials.py --tickers MRCC ARCC MAIN --years-back 1
    
    # Just consolidate existing files (no scraping)
    python scrape_and_consolidate_financials.py --consolidate-only
"""

import os
import sys
import logging
import argparse
from pathlib import Path
from typing import List, Optional

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import our modules
from extraction.financial_statements_extractor import FinancialStatementsExtractor
from consolidation.consolidate_financial_statements import FinancialStatementsConsolidator

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def scrape_financials(ticker: str, filing_type: str = "10-Q",
                     years_back: int = 1) -> bool:
    """
    Scrape financial statements for a ticker.
    
    Args:
        ticker: Company ticker symbol
        filing_type: Type of filing (10-Q, 10-K)
        years_back: Number of years to look back
        
    Returns:
        True if successful, False otherwise
    """
    try:
        extractor = FinancialStatementsExtractor()
        results = extractor.process_historical_filings(
            ticker=ticker,
            filing_type=filing_type,
            years_back=years_back
        )
        
        total_files = sum(len(paths) for paths in results.values())
        if total_files > 0:
            logger.info(f"✅ Scraped {total_files} financial statement files for {ticker}")
            return True
        else:
            logger.warning(f"⚠️  No financial data extracted for {ticker}")
            return False
    
    except Exception as e:
        logger.error(f"❌ Error scraping financials for {ticker}: {e}")
        return False


def consolidate_all_statements(ticker_filter: Optional[str] = None) -> bool:
    """
    Consolidate all financial statement files.
    
    Args:
        ticker_filter: Optional ticker to filter by
        
    Returns:
        True if successful, False otherwise
    """
    try:
        consolidator = FinancialStatementsConsolidator()
        results = consolidator.consolidate_all(ticker_filter=ticker_filter)
        
        if results:
            logger.info(f"✅ Consolidated {len(results)} statement types")
            return True
        else:
            logger.warning("⚠️  No files to consolidate")
            return False
    
    except Exception as e:
        logger.error(f"❌ Error consolidating statements: {e}")
        return False


def main():
    """Main CLI interface."""
    parser = argparse.ArgumentParser(
        description="Scrape financial statements and consolidate for frontend"
    )
    parser.add_argument(
        '--ticker',
        help='Single ticker to process (e.g., MRCC)'
    )
    parser.add_argument(
        '--tickers',
        nargs='+',
        help='Multiple tickers to process (e.g., MRCC ARCC MAIN)'
    )
    parser.add_argument(
        '--filing-type',
        default='10-Q',
        choices=['10-Q', '10-K'],
        help='Type of SEC filing to process'
    )
    parser.add_argument(
        '--years-back',
        type=int,
        default=1,
        help='Number of years to look back for historical filings'
    )
    parser.add_argument(
        '--consolidate-only',
        action='store_true',
        help='Only consolidate existing files, do not scrape'
    )
    parser.add_argument(
        '--skip-consolidate',
        action='store_true',
        help='Only scrape, do not consolidate'
    )
    
    args = parser.parse_args()
    
    # Determine which tickers to process
    tickers: List[str] = []
    if args.ticker:
        tickers = [args.ticker]
    elif args.tickers:
        tickers = args.tickers
    elif not args.consolidate_only:
        logger.error("Must specify --ticker or --tickers, or use --consolidate-only")
        sys.exit(1)
    
    success = True
    
    # Step 1: Scrape financial statements (if not consolidate-only)
    if not args.consolidate_only and tickers:
        logger.info(f"Step 1: Scraping financial statements for {len(tickers)} ticker(s)...")
        for ticker in tickers:
            if not scrape_financials(
                ticker=ticker,
                filing_type=args.filing_type,
                years_back=args.years_back
            ):
                success = False
    else:
        logger.info("Skipping scraping step (--consolidate-only mode)")
    
    # Step 2: Consolidate files (if not skip-consolidate)
    if not args.skip_consolidate:
        logger.info("Step 2: Consolidating financial statement files...")
        ticker_filter = tickers[0] if len(tickers) == 1 else None
        if not consolidate_all_statements(ticker_filter=ticker_filter):
            success = False
    else:
        logger.info("Skipping consolidation step (--skip-consolidate mode)")
    
    # Summary
    if success:
        print("\n✅ Workflow completed successfully!")
        print(f"   Consolidated files are in: frontend/public/data/")
        print(f"   - balance_sheets.csv")
        print(f"   - income_statements.csv")
    else:
        print("\n⚠️  Workflow completed with warnings/errors")
        sys.exit(1)


if __name__ == "__main__":
    main()






