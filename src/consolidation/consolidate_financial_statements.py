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
from datetime import datetime

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
        self.overview_fieldnames = [
            'ticker',
            'filing_date',
            'period_end',
            'period_type',
            'total_assets',
            'total_liabilities',
            'total_equity',
            'total_debt',
            'debt_to_equity',
            'debt_to_assets',
            'asset_coverage',
            'shares_outstanding',
            'share_change',
            'share_change_pct',
            'gross_issuance_proceeds',
            'gross_buybacks',
            'net_equity_financing',
            'shares_issued',
            'shares_repurchase',
            'share_based_comp_expense',
            'stock_issued_for_comp',
            'net_income',
            'nii_proxy',
            'nii_per_share',
            'cash_equivalents',
            'unfunded_commitments',
            'balance_check_delta',
            'balance_check_ok',
        ]

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
            # TICKER_YYYY-MM-DD_balance_sheet.csv or TICKER_YYYY-MM-DD_10-K_balance_sheet.csv
            pattern = r'^([A-Z0-9]+)_(\d{4}-\d{2}-\d{2})(?:_10-K)?_(balance_sheet|income_statement)$'
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

    @staticmethod
    def _to_float(value: str) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        neg = text.startswith('(') and text.endswith(')')
        cleaned = text.replace(',', '').replace('$', '').replace('(', '').replace(')', '')
        try:
            val = float(cleaned)
            return -val if neg else val
        except ValueError:
            return None

    @staticmethod
    def _safe_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
        if numerator is None or denominator is None or denominator == 0:
            return None
        return numerator / denominator

    def _metric_key_from_row(self, row: Dict) -> str:
        metric_key = (row.get('metric_key') or '').strip().lower()
        if metric_key:
            return metric_key

        concept = (row.get('concept') or '').lower().replace(':', '').replace('-', '').replace('_', '')
        fallback_map = {
            'total_assets': ['assets'],
            'total_liabilities': ['liabilities'],
            'total_equity': [
                'stockholdersequity',
                'stockholdersequityincludingportionattributabletononcontrollinginterest',
                'membersequity',
            ],
            'total_debt': ['longtermdebt', 'shorttermborrowings', 'debtinstrumentcarryingamount', 'unsecureddebt', 'creditfacility'],
            'shares_outstanding': ['sharesoutstanding'],
            'gross_issuance_proceeds': ['proceedsfromissuanceofcommonstock', 'proceedsfromstockoptionsexercised'],
            'shares_issued': ['commonstocksharesissued'],
            'gross_buybacks': ['paymentsforrepurchaseofcommonstock', 'treasurystockvalueacquiredcostmethod'],
            'shares_repurchase': ['commonstockrepurchased', 'sharesrepurchased'],
            'share_based_comp_expense': ['sharebasedcompensationexpense', 'sharebasedcompensation'],
            'stock_issued_for_comp': ['stockissuedduringperiodvaluesharebasedcompensation'],
            'net_income': ['netincomeloss'],
            'nii_proxy': ['interestanddividendincomeoperating'],
            'cash_equivalents': ['cashandcashequivalentsatcarryingvalue'],
            'unfunded_commitments': ['investmentunfundedcommitment', 'unfundedcommitment'],
        }
        for key, aliases in fallback_map.items():
            if concept in aliases:
                return key
        return ''

    @staticmethod
    def _row_period_end(row: Dict) -> str:
        return (row.get('instant_date') or row.get('end_date') or row.get('filing_date') or '').strip()

    @staticmethod
    def _row_period_type(row: Dict) -> str:
        return 'instant' if (row.get('instant_date') or '').strip() else 'duration'

    def build_overview_rows(self, ticker: str, rows: List[Dict]) -> List[Dict]:
        period_data = defaultdict(lambda: defaultdict(list))
        filing_dates = {}
        period_types = {}

        for row in rows:
            period_end = self._row_period_end(row)
            if not period_end:
                continue
            metric_key = self._metric_key_from_row(row)
            if not metric_key:
                continue
            value = self._to_float(row.get('value', ''))
            if value is None:
                continue

            period_data[period_end][metric_key].append(value)
            filing_dates[period_end] = max(filing_dates.get(period_end, ''), (row.get('filing_date') or '').strip())
            row_period_type = self._row_period_type(row)
            if period_end not in period_types:
                period_types[period_end] = row_period_type
            elif row_period_type == 'instant':
                period_types[period_end] = 'instant'

        def pick_value(values: List[float], prefer_abs_max: bool = False) -> Optional[float]:
            if not values:
                return None
            if prefer_abs_max:
                return max(values, key=lambda v: abs(v))
            return values[-1]

        overview_rows = []
        prev_shares = None
        for period_end in sorted(period_data.keys()):
            metrics = period_data[period_end]
            row = {
                'ticker': ticker,
                'filing_date': filing_dates.get(period_end, ''),
                'period_end': period_end,
                'period_type': period_types.get(period_end, ''),
            }

            row['total_assets'] = pick_value(metrics.get('total_assets', []), prefer_abs_max=True)
            row['total_liabilities'] = pick_value(metrics.get('total_liabilities', []), prefer_abs_max=True)
            row['total_equity'] = pick_value(metrics.get('total_equity', []), prefer_abs_max=True)
            row['total_debt'] = pick_value(metrics.get('total_debt', []), prefer_abs_max=True)
            if row['total_debt'] is None:
                # Fallback for filers that only expose aggregate liabilities cleanly.
                row['total_debt'] = row['total_liabilities']
            row['shares_outstanding'] = pick_value(metrics.get('shares_outstanding', []), prefer_abs_max=True)
            row['gross_issuance_proceeds'] = sum(metrics.get('gross_issuance_proceeds', [])) or None
            row['gross_buybacks'] = sum(abs(v) for v in metrics.get('gross_buybacks', [])) or None
            row['shares_issued'] = sum(metrics.get('shares_issued', [])) or None
            row['shares_repurchase'] = sum(abs(v) for v in metrics.get('shares_repurchase', [])) or None
            row['share_based_comp_expense'] = sum(metrics.get('share_based_comp_expense', [])) or None
            row['stock_issued_for_comp'] = sum(metrics.get('stock_issued_for_comp', [])) or None
            row['net_income'] = sum(metrics.get('net_income', [])) or None
            row['nii_proxy'] = sum(metrics.get('nii_proxy', [])) or None
            row['cash_equivalents'] = pick_value(metrics.get('cash_equivalents', []), prefer_abs_max=True)
            row['unfunded_commitments'] = pick_value(metrics.get('unfunded_commitments', []), prefer_abs_max=True)

            row['debt_to_equity'] = self._safe_ratio(row['total_debt'], row['total_equity'])
            row['debt_to_assets'] = self._safe_ratio(row['total_debt'], row['total_assets'])
            row['asset_coverage'] = self._safe_ratio(row['total_assets'], row['total_debt'])
            row['net_equity_financing'] = (
                (row['gross_issuance_proceeds'] or 0.0) - (row['gross_buybacks'] or 0.0)
                if row['gross_issuance_proceeds'] is not None or row['gross_buybacks'] is not None else None
            )
            row['nii_per_share'] = self._safe_ratio(row['nii_proxy'], row['shares_outstanding'])

            share_change = None
            share_change_pct = None
            if row['shares_outstanding'] is not None and prev_shares not in (None, 0):
                share_change = row['shares_outstanding'] - prev_shares
                share_change_pct = self._safe_ratio(share_change, prev_shares)
            prev_shares = row['shares_outstanding'] if row['shares_outstanding'] is not None else prev_shares
            row['share_change'] = share_change
            row['share_change_pct'] = share_change_pct

            balance_delta = None
            balance_ok = ''
            if row['total_assets'] is not None and row['total_liabilities'] is not None and row['total_equity'] is not None:
                balance_delta = row['total_assets'] - (row['total_liabilities'] + row['total_equity'])
                tolerance = max(1.0, abs(row['total_assets']) * 0.03)
                balance_ok = 'True' if abs(balance_delta) <= tolerance else 'False'
            row['balance_check_delta'] = balance_delta
            row['balance_check_ok'] = balance_ok

            for key in self.overview_fieldnames:
                if key not in row:
                    row[key] = ''
                elif isinstance(row[key], float):
                    row[key] = f"{row[key]:.6f}"
                elif row[key] is None:
                    row[key] = ''
            overview_rows.append(row)

        return overview_rows

    def write_overview_csv(self, ticker: str, rows: List[Dict]) -> Optional[Path]:
        if not rows:
            return None
        output_path = self.output_dir / f"{ticker}_overview_metrics.csv"
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=self.overview_fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"Wrote {len(rows)} overview metric rows to {output_path}")
        return output_path

    def build_quality_rows(self, ticker: str, overview_rows: List[Dict]) -> List[Dict]:
        quality_rows = []
        prior_shares = None
        for row in overview_rows:
            shares = self._to_float(row.get('shares_outstanding', ''))
            share_change_pct = self._to_float(row.get('share_change_pct', ''))
            abrupt_share_change = (
                share_change_pct is not None and abs(share_change_pct) > 0.25
            )
            quality_rows.append({
                'ticker': ticker,
                'filing_date': row.get('filing_date', ''),
                'period_end': row.get('period_end', ''),
                'balance_check_ok': row.get('balance_check_ok', ''),
                'balance_check_delta': row.get('balance_check_delta', ''),
                'shares_outstanding': row.get('shares_outstanding', ''),
                'share_change_pct': row.get('share_change_pct', ''),
                'abrupt_share_change_flag': 'True' if abrupt_share_change else 'False',
            })
            prior_shares = shares if shares is not None else prior_shares
        return quality_rows

    def write_quality_csv(self, ticker: str, rows: List[Dict]) -> Optional[Path]:
        if not rows:
            return None
        output_path = self.output_dir / f"{ticker}_overview_quality_checks.csv"
        fieldnames = [
            'ticker', 'filing_date', 'period_end', 'balance_check_ok', 'balance_check_delta',
            'shares_outstanding', 'share_change_pct', 'abrupt_share_change_flag'
        ]
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"Wrote {len(rows)} overview quality rows to {output_path}")
        return output_path

    def consolidate_all(self, ticker_filter: str = None) -> Dict[str, Path]:
        """Consolidate all financial statement files by ticker."""
        logger.info(f"Consolidating financial statements from {self.source_dir}")
        data = self.find_statement_files(ticker_filter=ticker_filter)
        
        results = {}
        for ticker, types in data.items():
            ticker_rows = []
            for stmt_type, file_paths in types.items():
                path = self.consolidate_and_write(ticker, stmt_type, file_paths)
                if path:
                    results[f"{ticker}_{stmt_type}"] = path
                    ticker_rows.extend(self.read_statement_file(path))
            if ticker_rows:
                overview_rows = self.build_overview_rows(ticker, ticker_rows)
                overview_path = self.write_overview_csv(ticker, overview_rows)
                if overview_path:
                    results[f"{ticker}_overview_metrics"] = overview_path
                quality_rows = self.build_quality_rows(ticker, overview_rows)
                quality_path = self.write_quality_csv(ticker, quality_rows)
                if quality_path:
                    results[f"{ticker}_overview_quality_checks"] = quality_path
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
        print("Successfully consolidated financial statements by ticker.")
    except Exception as e:
        logger.error(f"Error: {e}")
        exit(1)


if __name__ == "__main__":
    main()
