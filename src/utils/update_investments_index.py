#!/usr/bin/env python3
"""
Generate investments_index.json from the consolidated CSV files.
"""

import csv
import json
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def update_investments_index(data_dir: str = "frontend/public/data"):
    """
    Generate investments_index.json from consolidated CSV files.
    """
    data_path = Path(data_dir)
    investments_dir = data_path / "investments"
    
    if not investments_dir.exists():
        logger.error(f"Investments directory not found: {investments_dir}")
        return
    
    # Get all ticker CSV files
    ticker_files = list(investments_dir.glob("*.csv"))
    if not ticker_files:
        logger.warning("No investment CSV files found")
        return
    
    logger.info(f"Found {len(ticker_files)} ticker files to index")
    
    index_data = {
        "generated_at": datetime.now().isoformat(),
        "total_tickers": 0,
        "total_rows": 0,
        "tickers": {}
    }
    
    for ticker_file in sorted(ticker_files):
        ticker = ticker_file.stem
        logger.info(f"Processing {ticker}...")
        
        periods = set()
        row_count = 0
        
        try:
            with open(ticker_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row_count += 1
                    filing_date = row.get('filing_date', '')
                    if filing_date:
                        periods.add(filing_date)
            
            # Get file size in MB
            file_size_mb = ticker_file.stat().st_size / (1024 * 1024)
            
            # Sort periods chronologically
            sorted_periods = sorted(list(periods))
            
            index_data["tickers"][ticker] = {
                "periods": sorted_periods,
                "latest": sorted_periods[-1] if sorted_periods else None,
                "row_count": row_count,
                "file_size_mb": round(file_size_mb, 2)
            }
            
            index_data["total_rows"] += row_count
            
        except Exception as e:
            logger.error(f"Error processing {ticker_file}: {e}")
    
    index_data["total_tickers"] = len(index_data["tickers"])
    
    # Write index file
    index_file = data_path / "investments_index.json"
    logger.info(f"Writing index to {index_file}")
    
    with open(index_file, 'w', encoding='utf-8') as f:
        json.dump(index_data, f, indent=2)
    
    logger.info(f"Successfully indexed {index_data['total_tickers']} tickers with {index_data['total_rows']} total rows")

if __name__ == "__main__":
    update_investments_index()
