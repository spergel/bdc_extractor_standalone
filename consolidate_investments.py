#!/usr/bin/env python3
"""
Consolidate individual investment CSV files into per-ticker files for the frontend.
"""

import os
import csv
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Set
from collections import defaultdict

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def consolidate_investments(output_dir: str = "frontend/public/data/investments"):
    """
    Consolidate all *_investments_*.csv files from output/ into per-ticker files.
    """
    output_path = Path(output_dir)
    source_dir = Path("output")
    
    # Get all investment CSVs
    investment_files = list(source_dir.glob("*_investments_*.csv"))
    if not investment_files:
        logger.warning("No investment files found in output/")
        return
    
    logger.info(f"Found {len(investment_files)} investment files to consolidate.")
    
    # Group files by ticker
    ticker_files = defaultdict(list)
    for file_path in investment_files:
        parts = file_path.stem.split('_')
        if len(parts) < 3:
            continue
        ticker = parts[0]
        ticker_files[ticker].append(file_path)
    
    # Ensure directory exists
    output_path.mkdir(parents=True, exist_ok=True)
    
    for ticker, files in ticker_files.items():
        logger.info(f"Consolidating {len(files)} files for ticker: {ticker}")
        
        all_ticker_data = []
        headers = []
        seen_rows = set()
        
        # Sort files by date (contained in filename) to ensure consistent ordering
        files.sort()
        
        for file_path in files:
            parts = file_path.stem.split('_')
            filing_date = parts[2]
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    if not headers:
                        headers = reader.fieldnames + ['ticker', 'filing_date']
                    
                    for row in reader:
                        row['ticker'] = ticker
                        row['filing_date'] = filing_date
                        
                        # Create a unique key for deduplication
                        # Using all fields except filing_date might be too aggressive if we want to track changes over time
                        # But user mentioned wanting it to be easier to load, so let's keep all for now
                        # Actually, we want to keep data for different filing dates.
                        row_key = tuple(row.get(h, '') for f, h in enumerate(headers))
                        
                        if row_key not in seen_rows:
                            seen_rows.add(row_key)
                            all_ticker_data.append(row)
                            
            except Exception as e:
                logger.error(f"Error reading {file_path}: {e}")
        
        if all_ticker_data:
            ticker_output_file = output_path / f"{ticker}.csv"
            logger.info(f"Writing {len(all_ticker_data)} rows to {ticker_output_file}")
            
            try:
                with open(ticker_output_file, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=headers)
                    writer.writeheader()
                    writer.writerows(all_ticker_data)
            except Exception as e:
                logger.error(f"Error writing to {ticker_output_file}: {e}")

    logger.info("Successfully consolidated investments by ticker.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Consolidate investment CSVs by ticker")
    parser.add_argument('--output-dir', default='frontend/public/data/investments', help='Output directory for per-ticker CSV files')
    args = parser.parse_args()
    
    consolidate_investments(args.output_dir)
