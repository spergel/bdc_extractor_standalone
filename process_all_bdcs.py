#!/usr/bin/env python3
"""
Process all BDC tickers for the frontend.
This script runs both the investment table scraper and the financial statement extractor
 for all BDCs in our list, then consolidates the results.
"""

import subprocess
import logging
import argparse
from typing import List

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Full list of BDCs from the frontend data folder
ALL_BDCS = [
    "ARCC", "BBDC", "BCSF", "BXSL", "CCAP", "CGBD", "CION", "CSWC", 
    "FDUS", "FSK", "GAIN", "GBDC", "GECC", "GLAD", "GSBD", "HRZN", 
    "HTGC", "MAIN", "MFIC", "MRCC", "MSDL", "NCDL", "NEWT", "NMFC", 
    "OBDC", "OCSI", "OCSL", "OFS", "ORCC", "PFLT", "PNNT", "PSEC", 
    "PTMN", "RWAY", "SAR", "SLRC", "SUNS", "TCPC", "TPVG", "TRIN", 
    "TSLX", "WHF"
]

def run_command(command: List[str]):
    """Run a command and log output."""
    logger.info(f"Running: {' '.join(command)}")
    try:
        subprocess.run(command, check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Process all BDCs for the frontend")
    parser.add_argument('--tickers', nargs='+', help='Specific tickers to process (optional)')
    parser.add_argument('--years-back', type=int, default=1, help='Number of years back to process')
    parser.add_argument('--skip-investments', action='store_true', help='Skip investment table extraction')
    parser.add_argument('--skip-financials', action='store_true', help='Skip financial statement extraction')
    parser.add_argument('--skip-consolidation', action='store_true', help='Skip consolidation step')
    
    args = parser.parse_args()
    
    tickers = args.tickers if args.tickers else ALL_BDCS
    
    logger.info(f"Starting processing for {len(tickers)} BDC(s)...")
    
    for ticker in tickers:
        logger.info(f"--- Processing {ticker} ---")
        
        # 1. Extract Investments (10-Q)
        if not args.skip_investments:
            logger.info(f"Extracting investments for {ticker}...")
            # We'll use a simplified version of the loop in process_mrcc_historical.py
            # For simplicity in this script, we'll just call the scraper for the last year
            run_command([
                "python", "llm_table_scraper.py", 
                "--ticker", ticker, 
                "--years-back", str(args.years_back),
                "--llm-provider", "gemini"
            ])
            
        # 2. Extract Financials (10-Q)
        if not args.skip_financials:
            logger.info(f"Extracting financials for {ticker}...")
            run_command([
                "python", "financial_statements_extractor.py",
                "--ticker", ticker,
                "--years-back", str(args.years_back),
                "--filing-type", "10-Q"
            ])

    # 3. Consolidate and move to frontend
    if not args.skip_consolidation:
        logger.info("Consolidating all extracted data...")
        
        # Financial statements consolidation
        run_command([
            "python", "scrape_and_consolidate_financials.py",
            "--consolidate-only"
        ])
        
        # Investment consolidation
        run_command([
            "python", "consolidate_investments.py"
        ])

    logger.info("Done!")

if __name__ == "__main__":
    main()

