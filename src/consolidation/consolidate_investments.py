#!/usr/bin/env python3
"""
Consolidate individual investment CSV files into per-ticker files for the frontend.
Also writes investments_index.json so the frontend can discover tickers and periods.
"""

import csv
import json
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Set, Optional
from collections import defaultdict
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def consolidate_investments(output_dir: str = "frontend/public/data/investments", source_dir: Optional[Path] = None):
    """
    Consolidate all *_investments_*.csv files from output/ into per-ticker files.
    """
    output_path = Path(output_dir)
    if source_dir is None:
        _repo_root = Path(__file__).resolve().parent.parent.parent
        source_dir = _repo_root / "output"
    
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

            # Also write per-period files for the frontend
            ticker_dir = output_path / ticker
            ticker_dir.mkdir(parents=True, exist_ok=True)

            # Group rows by filing_date
            period_rows = defaultdict(list)
            for row in all_ticker_data:
                period_rows[row.get('filing_date', '')].append(row)

            for period, rows in period_rows.items():
                if not period:
                    continue
                period_file = ticker_dir / f"{period}.csv"
                try:
                    with open(period_file, 'w', encoding='utf-8', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=headers)
                        writer.writeheader()
                        writer.writerows(rows)
                except Exception as e:
                    logger.error(f"Error writing to {period_file}: {e}")

            logger.info(f"Wrote {len(period_rows)} period files to {ticker_dir}")

    # Write investments_index.json so the frontend can list tickers and periods
    _write_investments_index(output_path)

    logger.info("Successfully consolidated investments by ticker.")


def _write_investments_index(investments_dir: Path) -> None:
    """Scan investments_dir for TICKER/period.csv and write investments_index.json in the parent (data) dir."""
    index_path = investments_dir.parent / "investments_index.json"
    tickers: Dict[str, Dict] = {}
    total_rows = 0

    for ticker_dir in sorted(investments_dir.iterdir()):
        if not ticker_dir.is_dir():
            continue
        ticker = ticker_dir.name
        period_files = sorted(f.stem for f in ticker_dir.glob("*.csv") if not f.stem.endswith(".BEFORE"))
        if not period_files:
            continue
        periods = [p for p in period_files if p.replace("-", "").isdigit() or (len(p) == 10 and p[4] == "-" and p[7] == "-")]
        if not periods:
            periods = period_files
        row_count = 0
        size_bytes = 0
        for period in periods:
            p_path = ticker_dir / f"{period}.csv"
            if p_path.exists():
                with open(p_path, "r", encoding="utf-8") as f:
                    row_count += sum(1 for _ in f) - 1  # subtract header
                size_bytes += p_path.stat().st_size
        total_rows += row_count
        tickers[ticker] = {
            "periods": periods,
            "latest": periods[-1] if periods else None,
            "row_count": row_count,
            "file_size_mb": round(size_bytes / (1024 * 1024), 2),
        }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_tickers": len(tickers),
        "total_rows": total_rows,
        "tickers": tickers,
    }
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    logger.info(f"Wrote {index_path} with {len(tickers)} tickers.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Consolidate investment CSVs by ticker")
    parser.add_argument('--output-dir', default='frontend/public/data/investments', help='Output directory for per-ticker CSV files')
    args = parser.parse_args()
    
    consolidate_investments(args.output_dir)
