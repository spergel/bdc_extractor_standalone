#!/usr/bin/env python3
"""
Copy investment CSV files to frontend organized by ticker and period.
Each period gets its own file for faster loading.
"""

import os
import csv
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def deploy_investments_by_period(
    source_dir: str = "output",
    output_dir: str = "frontend/public/data/investments"
):
    """
    Copy individual investment CSV files to frontend organized by ticker/period.
    """
    source_path = Path(source_dir)
    output_path = Path(output_dir)
    
    # Get all investment CSVs
    investment_files = list(source_path.glob("*_investments_*.csv"))
    if not investment_files:
        logger.warning("No investment files found in output/")
        return
    
    logger.info(f"Found {len(investment_files)} investment files to deploy.")
    
    # Group files by ticker
    ticker_files = defaultdict(list)
    for file_path in investment_files:
        parts = file_path.stem.split('_')
        if len(parts) < 3:
            continue
        ticker = parts[0]
        filing_date = parts[2]
        ticker_files[ticker].append((filing_date, file_path))
    
    # Create index data
    index_data = {
        "generated_at": datetime.now().isoformat(),
        "total_tickers": 0,
        "total_periods": 0,
        "tickers": {}
    }
    
    for ticker, files in sorted(ticker_files.items()):
        logger.info(f"Deploying {len(files)} periods for ticker: {ticker}")
        
        # Create ticker directory
        ticker_dir = output_path / ticker
        ticker_dir.mkdir(parents=True, exist_ok=True)
        
        periods_info = []
        
        for filing_date, file_path in sorted(files):
            # Copy file to ticker/period.csv
            dest_file = ticker_dir / f"{filing_date}.csv"
            shutil.copy2(file_path, dest_file)
            
            # Get file stats
            file_size_mb = dest_file.stat().st_size / (1024 * 1024)
            
            # Count rows
            try:
                with open(dest_file, 'r', encoding='utf-8') as f:
                    row_count = sum(1 for _ in csv.DictReader(f))
            except Exception as e:
                logger.error(f"Error counting rows in {dest_file}: {e}")
                row_count = 0
            
            periods_info.append({
                "period": filing_date,
                "row_count": row_count,
                "file_size_mb": round(file_size_mb, 3),
                "file": f"{ticker}/{filing_date}.csv"
            })
            
            logger.info(f"  ✓ {ticker}/{filing_date}.csv ({row_count} rows, {file_size_mb:.2f}MB)")
        
        # Sort periods chronologically
        sorted_periods = sorted(periods_info, key=lambda x: x["period"])
        
        index_data["tickers"][ticker] = {
            "periods": [p["period"] for p in sorted_periods],
            "latest": sorted_periods[-1]["period"] if sorted_periods else None,
            "period_details": sorted_periods
        }
        
        index_data["total_periods"] += len(sorted_periods)
    
    index_data["total_tickers"] = len(index_data["tickers"])
    
    # Write index file
    index_file = output_path.parent / "investments_index.json"
    logger.info(f"Writing index to {index_file}")
    
    with open(index_file, 'w', encoding='utf-8') as f:
        json.dump(index_data, f, indent=2)
    
    logger.info(f"✓ Successfully deployed {index_data['total_tickers']} tickers with {index_data['total_periods']} total periods")
    logger.info(f"  Data size optimized: each period loads independently")

if __name__ == "__main__":
    deploy_investments_by_period()
