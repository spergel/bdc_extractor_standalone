#!/usr/bin/env python3
"""
Consolidate individual financial statement CSV files into per-ticker files for the frontend.
"""

import os
import sys
import logging
import argparse
from pathlib import Path
import csv
from typing import List, Dict, Set, Optional
from collections import defaultdict

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class FinancialStatementsConsolidator:
    """Consolidate individual financial statement files into per-ticker CSV files."""

    def __init__(self,
                 source_dir: str = "output/financials",
                 output_dir: str = "frontend/public/data/financials"):
        """
        Initialize the consolidator.

        Args:
            source_dir: Directory containing individual statement files
            output_dir: Directory to write consolidated CSV files
        """
        self.source_dir = Path(source_dir)
        self.output_dir = Path(output_dir)
        
        self.source_dir.mkdir(exist_ok=True, parents=True)
        self.output_dir.mkdir(exist_ok=True, parents=True)

    def find_statement_files(self, ticker_filter: str = None) -> Dict[str, Dict[str, List[Path]]]:
        """
        Find all financial statement files and group them by ticker and type.
        
        Returns:
            Dict: { ticker: { 'balance_sheet': [paths], 'income_statement': [paths] } }
        """
        import re
        data = defaultdict(lambda: defaultdict(list))
        
        if not self.source_dir.exists():
            logger.warning(f"Source directory {self.source_dir} does not exist")
            return data
        
        for file_path in self.source_dir.glob("*.csv"):
            filename = file_path.name
            if '_' not in filename: continue
            
            name_without_ext = filename.replace('.csv', '')
            pattern = r'^([A-Z0-9]+)_(\d{4}-\d{2}-\d{2})_(.+)$'
            match = re.match(pattern, name_without_ext)
            
            if not match: continue
            
            ticker = match.group(1)
            statement_type = match.group(3)
            
            if ticker_filter and ticker.upper() != ticker_filter.upper():
                continue
            
            if statement_type in ['balance_sheet', 'income_statement']:
                data[ticker][statement_type].append(file_path)
        
        # Sort files by date
        for ticker in data:
            for stmt_type in data[ticker]:
                data[ticker][stmt_type].sort()
        
        return data

    def read_statement_file(self, file_path: Path) -> List[Dict]:
        """Read a single statement CSV file."""
        rows = []
        try:
            csv.field_size_limit(min(2**31 - 1, sys.maxsize))
            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)
        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")
        return rows

    def consolidate_and_write(self, ticker: str, statement_type: str, file_paths: List[Path]) -> Optional[Path]:
        """Consolidate multiple files for a ticker/type and write to output."""
        all_rows = []
        seen_rows = set()
        
        for file_path in file_paths:
            rows = self.read_statement_file(file_path)
            for row in rows:
                dedup_key = (
                    row.get('ticker', ''),
                    row.get('filing_date', ''),
                    row.get('concept', ''),
                    row.get('context_key', ''),
                    row.get('value', '')
                )
                if dedup_key not in seen_rows:
                    seen_rows.add(dedup_key)
                    all_rows.append(row)
        
        if not all_rows: return None

        output_path = self.output_dir / f"{ticker}_{statement_type}.csv"
        
        # Determine fieldnames
        fieldnames = set()
        for row in all_rows: fieldnames.update(row.keys())
        
        standard_fieldnames = [
            'ticker', 'filing_date', 'statement_label', 'statement_type',
            'line_item', 'concept', 'value', 'context_key', 'start_date',
            'end_date', 'instant_date', 'duration_days', 'level',
            'is_abstract', 'preferred_label', 'order_index'
        ]
        ordered_fieldnames = [f for f in standard_fieldnames if f in fieldnames]
        extra_fieldnames = sorted(fieldnames - set(standard_fieldnames))
        final_fieldnames = ordered_fieldnames + extra_fieldnames
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=final_fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)
        
        logger.info(f"Wrote {len(all_rows)} rows to {output_path}")
        return output_path

    def consolidate_all(self, ticker_filter: str = None) -> Dict[str, Path]:
        """Consolidate all financial statement files by ticker."""
        logger.info(f"Consolidating financial statements from {self.source_dir}")
        data = self.find_statement_files(ticker_filter=ticker_filter)
        
        results = {}
        for ticker, types in data.items():
            for stmt_type, file_paths in types.items():
                path = self.consolidate_and_write(ticker, stmt_type, file_paths)
                if path:
                    results[f"{ticker}_{stmt_type}"] = path
        return results


def main():
    """Main CLI interface."""
    parser = argparse.ArgumentParser(description="Consolidate financial statements by ticker")
    parser.add_argument('--ticker', help='Only consolidate files for this ticker')
    parser.add_argument('--source-dir', default='output/financials', help='Source directory')
    parser.add_argument('--output-dir', default='frontend/public/data/financials', help='Output directory')
    
    args = parser.parse_args()
    
    try:
        consolidator = FinancialStatementsConsolidator(
            source_dir=args.source_dir,
            output_dir=args.output_dir
        )
        consolidator.consolidate_all(ticker_filter=args.ticker)
        print("✅ Successfully consolidated financial statements by ticker.")
    except Exception as e:
        logger.error(f"Error: {e}")
        exit(1)


if __name__ == "__main__":
    main()
