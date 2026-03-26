#!/usr/bin/env python3
"""
Extract historical 3-statement financials (Balance Sheet, Income Statement, Cash Flow)
from SEC 10-Q and 10-K filings using XBRL data.

Usage:
    python financial_statements_extractor.py --ticker MRCC --years-back 1
    python financial_statements_extractor.py --ticker MRCC --filing-type 10-Q --year 2025
"""

import os
import logging
import argparse
import io
from typing import List, Dict, Any, Optional
from datetime import datetime, date
from pathlib import Path
import re
import requests
import csv
from collections import defaultdict
import xml.etree.ElementTree as ET

from sec_api_client import SECAPIClient

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class FinancialStatementsExtractor:
    """
    Extract standard financial statements (Balance Sheet, Income Statement)
    from SEC filings using XBRL data.
    Only extracts standard line items, excluding detailed breakdowns.
    """

    def __init__(self,
                 data_dir: str = "data",
                 output_dir: str = "output/financials"):
        """
        Initialize the financial statements extractor.

        Args:
            data_dir: Directory for SEC API client data
            output_dir: Directory to save CSV outputs
        """
        self.data_dir = Path(data_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        
        self.data_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
        
        self.sec_client = SECAPIClient(data_dir=str(self.data_dir))
        
        # XBRL concept prefixes for different statement types
        self.balance_sheet_concepts = [
            'us-gaap:Assets',
            'us-gaap:Liabilities',
            'us-gaap:StockholdersEquity',
            'us-gaap:InvestmentOwnedAtFairValue',
            'us-gaap:CashAndCashEquivalentsAtCarryingValue',
        ]
        
        self.income_statement_concepts = [
            'us-gaap:Revenues',
            'us-gaap:OperatingExpenses',
            'us-gaap:NetIncomeLoss',
            'us-gaap:InterestAndDividendIncomeOperating',
            'us-gaap:InterestExpense',
        ]
        self.metric_concept_aliases = {
            'total_assets': ['assets'],
            'total_liabilities': ['liabilities'],
            'total_equity': [
                'stockholdersequity',
                'stockholdersequityincludingportionattributabletononcontrollinginterest',
                'membersequity',
                'partnerscapital',
            ],
            'total_debt': [
                'longtermdebt',
                'shorttermborrowings',
                'debtinstrumentcarryingamount',
                'unsecureddebt',
                'creditfacility',
                'lineofcredit',
            ],
            'shares_outstanding': [
                'commonstocksharesoutstanding',
                'commonsharesoutstanding',
                'sharesoutstanding',
            ],
            'gross_issuance_proceeds': [
                'proceedsfromissuanceofcommonstock',
                'proceedsfromstockoptionsexercised',
            ],
            'shares_issued': ['commonstocksharesissued'],
            'gross_buybacks': [
                'paymentsforrepurchaseofcommonstock',
                'treasurystockvalueacquiredcostmethod',
            ],
            'shares_repurchase': ['commonstockrepurchased', 'sharesrepurchased'],
            'share_based_comp_expense': ['sharebasedcompensationexpense', 'sharebasedcompensation'],
            'stock_issued_for_comp': ['stockissuedduringperiodvaluesharebasedcompensation'],
            'net_income': ['netincomeloss'],
            'total_revenue': ['revenues'],
            'operating_expense': ['operatingexpenses', 'operatingexpense'],
            'nii_proxy': ['interestanddividendincomeoperating'],
            'interest_expense': ['interestexpense'],
            'cash_equivalents': ['cashandcashequivalentsatcarryingvalue'],
            'unfunded_commitments': ['investmentunfundedcommitment', 'unfundedcommitment'],
        }

    @staticmethod
    def _form_filename_suffix(form: str) -> str:
        """Use _10-K in filenames for annual reports; 10-Q stays unsuffixed (backward compatible)."""
        f = (form or '10-Q').strip().upper().replace(' ', '')
        if f in ('10-K', '10-K/A', '10-KA'):
            return '_10-K'
        return ''

    def find_xbrl_instance_document(self, filing_result) -> Optional[str]:
        """
        Find the XBRL instance document URL from a filing.
        
        Args:
            filing_result: FilingResult object with documents
            
        Returns:
            URL to XBRL instance document, or None if not found
        """
        if not filing_result.documents:
            return None
        
        # Look for XBRL instance documents
        # Common patterns: *_htm.xml (most common), *_xbrl.htm, *-xbrl.htm, *instance*.xml
        for doc in filing_result.documents:
            filename_lower = doc.filename.lower()
            
            # Skip schema and linkbase files
            if any(skip in filename_lower for skip in [
                'schema',
                'cal.xml',
                'def.xml',
                'lab.xml',
                'pre.xml',
                'calculation',
                'definition',
                'label',
                'presentation'
            ]):
                continue
            
            # Check for XBRL instance document patterns
            if any(pattern in filename_lower for pattern in [
                '_htm.xml',  # Most common: mrcc-20250930_htm.xml
                'xbrl.htm',
                'xbrl.html',
                'instance.xml',
                'xbrl.xml',
                '.xml'  # Fallback: any XML file that's not a schema
            ]):
                # Make sure it's not a schema file (double check)
                if 'schema' not in filename_lower:
                    logger.info(f"Found XBRL instance document: {doc.filename}")
                    return doc.url
        
        return None

    def _local_tag(self, tag: str) -> str:
        """Strip XML namespace from tag to get local name (e.g. 'context', 'Assets')."""
        if not tag:
            return ''
        return tag.split('}')[-1] if '}' in tag else tag

    def parse_xbrl_document(self, xbrl_url: str) -> Dict[str, Any]:
        """
        Parse XBRL instance document using streaming iterparse to avoid loading
        the entire multi-MB file into memory (which was slow and could hang).
        """
        try:
            response = requests.get(xbrl_url, headers=self.sec_client.headers, timeout=120)
            response.raise_for_status()
            content = response.content
            logger.info(f"Parsing XBRL document ({len(content) / (1024*1024):.2f} MB) with streaming parser...")

            contexts = {}
            facts = []
            # Skip root and other non-data tags when collecting facts
            skip_tags = {'context', 'unit', 'schemaref', 'linkbaseref', 'roleref', 'arcroleref'}

            for event, elem in ET.iterparse(io.BytesIO(content), events=('end',)):
                local = self._local_tag(elem.tag).lower()
                if local == 'context':
                    cid = elem.get('id', '')
                    if cid:
                        period_info = {}
                        for child in elem:
                            c_local = self._local_tag(child.tag).lower()
                            if c_local == 'period':
                                for sub in child:
                                    s_local = self._local_tag(sub.tag).lower()
                                    text = (sub.text or '').strip()
                                    if s_local == 'instant':
                                        period_info['instant'] = text
                                    elif s_local == 'startdate':
                                        period_info['start'] = text
                                    elif s_local == 'enddate':
                                        period_info['end'] = text
                                break
                        contexts[cid] = period_info
                elif local not in skip_tags:
                    context_ref = elem.get('contextRef', '')
                    if context_ref:
                        value_text = (elem.text or '').strip()
                        concept = self._local_tag(elem.tag)
                        if value_text and concept:
                            facts.append({
                                'concept': concept,
                                'label': elem.get('label', '') or concept,
                                'value': value_text,
                                'contextRef': context_ref,
                                'unitRef': elem.get('unitRef', ''),
                                'context': contexts.get(context_ref, {})
                            })
                # Free memory: clear children of processed elements (iterparse keeps tree by default)
                elem.clear()

            # Filter for standard line items only (exclude detailed breakdowns)
            def is_standard_line_item(fact: Dict) -> bool:
                """
                Filter to keep only standard financial statement line items.
                Excludes detailed breakdowns by industry, investment type, etc.
                """
                concept = fact.get('concept', '').lower()
                label = fact.get('label', '').lower()
                
                # Exclude supplement/breakdown items (these are detailed industry/investment type breakdowns)
                if 'supplement' in concept:
                    return False
                if 'member' in concept:
                    return False
                
                # Exclude items with multiple dashes indicating breakdowns
                # Standard items typically have simpler names
                # But allow one dash (e.g., "Cash and cash equivalents - restricted")
                if label.count(' - ') > 1:
                    return False
                
                # Exclude very long labels (likely detailed breakdowns)
                if len(label) > 150:
                    return False
                
                # Exclude percentage/ratio items (these are not standard line items)
                if 'percent' in label or 'percentage' in label:
                    return False
                if 'percent' in concept or 'percentage' in concept:
                    return False
                
                # Keep all other items that match our balance sheet/income statement patterns
                # The categorization logic below will determine which statement they belong to
                return True

            def metric_key_for_concept(concept_name: str) -> str:
                normalized = concept_name.lower().replace(':', '').replace('-', '').replace('_', '')
                for metric_key, aliases in self.metric_concept_aliases.items():
                    if normalized in aliases:
                        return metric_key
                return ''
            
            # Categorize facts by statement type (only standard items)
            balance_sheet = []
            income_statement = []
            
            for fact in facts:
                # Skip non-standard items
                if not is_standard_line_item(fact):
                    continue
                
                concept = fact['concept'].lower()
                
                # Categorize based on concept name patterns
                if any(bs_concept in concept for bs_concept in [
                    'asset', 'liabilit', 'equity', 'investmentowned',
                    'cashandcashequivalents', 'debt', 'stockholders'
                ]):
                    fact['metric_key'] = metric_key_for_concept(fact.get('concept', ''))
                    balance_sheet.append(fact)
                elif any(is_concept in concept for is_concept in [
                    'revenue', 'income', 'expense', 'netincomeloss',
                    'interestanddividend', 'operatingexpense'
                ]):
                    fact['metric_key'] = metric_key_for_concept(fact.get('concept', ''))
                    income_statement.append(fact)
            
            return {
                'balance_sheet': balance_sheet,
                'income_statement': income_statement,
                'cash_flow': [],  # No longer extracting cash flow
                'contexts': contexts
            }
            
        except Exception as e:
            logger.error(f"Error parsing XBRL document: {e}")
            return {
                'balance_sheet': [],
                'income_statement': [],
                'cash_flow': [],  # No longer extracting cash flow
                'contexts': {}
            }

    def save_financial_statement_csv(self, data: List[Dict], statement_type: str,
                                     ticker: str, filing_date: str,
                                     form: str = '10-Q') -> Optional[Path]:
        """
        Save financial statement data to CSV.
        
        Args:
            data: List of fact dictionaries
            statement_type: 'balance_sheet' or 'income_statement'
            ticker: Company ticker
            filing_date: Filing date (YYYY-MM-DD)
            form: SEC form (10-Q, 10-K, etc.) — affects filename for 10-K
            
        Returns:
            Path to saved CSV file, or None if no data
        """
        if not data:
            logger.warning(f"No {statement_type} data to save")
            return None
        
        suffix = self._form_filename_suffix(form)
        filename = f"{ticker}_{filing_date}{suffix}_{statement_type}.csv"
        output_path = self.output_dir / filename
        
        # Prepare CSV rows
        rows = []
        for fact in data:
            context = fact.get('context', {})
            row = {
                'ticker': ticker,
                'filing_date': filing_date,
                'statement_label': statement_type,
                'statement_type': statement_type,
                'line_item': fact.get('label', ''),
                'concept': fact.get('concept', ''),
                'value': fact.get('value', ''),
                'context_key': fact.get('contextRef', ''),
                'start_date': context.get('start', ''),
                'end_date': context.get('end', ''),
                'instant_date': context.get('instant', ''),
                'duration_days': '',
                'level': '',
                'is_abstract': 'False',
                'preferred_label': fact.get('label', ''),
                'order_index': '',
                'metric_key': fact.get('metric_key', ''),
            }
            
            # Calculate duration if we have start and end dates
            if row['start_date'] and row['end_date']:
                try:
                    start = datetime.strptime(row['start_date'], '%Y-%m-%d')
                    end = datetime.strptime(row['end_date'], '%Y-%m-%d')
                    row['duration_days'] = str((end - start).days)
                except:
                    pass
            
            rows.append(row)
        
        # Write CSV
        if rows:
            fieldnames = ['ticker', 'filing_date', 'statement_label', 'statement_type',
                         'line_item', 'concept', 'value', 'context_key', 'start_date',
                         'end_date', 'instant_date', 'duration_days', 'level',
                         'is_abstract', 'preferred_label', 'order_index', 'metric_key']
            
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            
            logger.info(f"Saved {len(rows)} {statement_type} rows to {output_path}")
            return output_path
        
        return None

    def process_filing(self, ticker: str, filing_type: str = "10-Q",
                      year: Optional[int] = None) -> Dict[str, Optional[Path]]:
        """
        Process a single SEC filing to extract financial statements.
        
        Args:
            ticker: Company ticker symbol
            filing_type: Type of filing (10-Q, 10-K, etc.)
            year: Year to fetch filing for
            
        Returns:
            Dictionary mapping statement types to file paths
        """
        logger.info(f"Processing {ticker} {filing_type} for {year or 'latest'}")
        
        try:
            # Get the filing index URL
            index_url = self.sec_client.get_filing_index_url(
                ticker=ticker,
                filing_type=filing_type,
                year=year
            )
            
            if not index_url:
                logger.warning(f"No {filing_type} filing found for {ticker}")
                return {}
            
            # Optimization: Only download XML and main HTM files first to find XBRL
            # This is MUCH faster than downloading every document including large TXT files
            filing_result = self.sec_client.fetch_filing_by_index_url(
                index_url=index_url,
                ticker=ticker,
                filing_type=filing_type,
                save_to_file=False,
                document_types=['.xml', '.htm', '.html']
            )
            
            if not filing_result:
                logger.warning(f"Could not fetch filing documents from {index_url}")
                return {}
            
            # Find XBRL instance document
            xbrl_url = self.find_xbrl_instance_document(filing_result)
            if not xbrl_url:
                logger.warning(f"No XBRL instance document found in filing")
                return {}
            
            # Parse XBRL document
            xbrl_data = self.parse_xbrl_document(xbrl_url)
            
            # Save each statement type
            results = {}
            
            if xbrl_data['balance_sheet']:
                bs_path = self.save_financial_statement_csv(
                    xbrl_data['balance_sheet'],
                    'balance_sheet',
                    ticker,
                    filing_result.filing_date,
                    form=filing_type,
                )
                results['balance_sheet'] = bs_path
            
            if xbrl_data['income_statement']:
                is_path = self.save_financial_statement_csv(
                    xbrl_data['income_statement'],
                    'income_statement',
                    ticker,
                    filing_result.filing_date,
                    form=filing_type,
                )
                results['income_statement'] = is_path
            
            # No longer extracting cash flow statements
            
            return results
            
        except Exception as e:
            logger.error(f"Error processing filing for {ticker}: {e}")
            return {}

    def process_historical_filings(self, ticker: str, filing_type: str = "10-Q",
                                   years_back: int = 1,
                                   skip_existing: bool = True) -> Dict[str, List[Path]]:
        """
        Process multiple historical filings.

        Args:
            ticker: Company ticker symbol
            filing_type: 10-Q, 10-K, or both (10-Q and 10-K combined)
            years_back: Number of years to look back
            skip_existing: Skip filings that already have output CSVs

        Returns:
            Dictionary mapping statement types to lists of file paths
        """
        logger.info(f"Processing historical {filing_type} filings for {ticker} (last {years_back} years)")
        logger.info(f"Per-filing outputs: {self.output_dir} (skip_existing={skip_existing})")

        # Get historical filings
        if filing_type == "both":
            filings_q = self.sec_client.get_historical_10q_filings(ticker, years_back=years_back)
            filings_k = self.sec_client.get_historical_10k_filings(ticker, years_back=years_back)
            filings = list(filings_q) + list(filings_k)
            seen_urls: set = set()
            deduped = []
            for fi in sorted(filings, key=lambda x: x.get('date', '')):
                url = fi.get('index_url', '')
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                deduped.append(fi)
            filings = deduped
            logger.info(f"Combined 10-Q+10-K: {len(filings)} unique filings for {ticker}")
        elif filing_type == "10-Q":
            filings = self.sec_client.get_historical_10q_filings(ticker, years_back=years_back)
        elif filing_type == "10-K":
            filings = self.sec_client.get_historical_10k_filings(ticker, years_back=years_back)
        else:
            logger.warning(f"Unknown filing_type {filing_type!r}; use 10-Q, 10-K, or both")
            filings = []

        # Optional: skip entire ticker if consolidated outputs already exist (e.g. from prior run)
        if skip_existing and filings:
            consolidated_dir = Path("frontend/public/data/financials").resolve()
            if consolidated_dir.exists():
                bs_cons = consolidated_dir / f"{ticker}_balance_sheet.csv"
                is_cons = consolidated_dir / f"{ticker}_income_statement.csv"
                if (bs_cons.exists() and bs_cons.stat().st_size > 100 and
                    is_cons.exists() and is_cons.stat().st_size > 100):
                    logger.info(f"SKIPPING {ticker} financials - consolidated files already exist in {consolidated_dir}")
                    return {
                        'balance_sheet': [bs_cons],
                        'income_statement': [is_cons]
                    }

        results = {
            'balance_sheet': [],
            'income_statement': []
        }

        skipped = 0
        for filing_info in filings:
            filing_date = filing_info['date']
            if filing_type == 'both':
                form = filing_info.get('form') or '10-Q'
            elif filing_type in ('10-Q', '10-K'):
                form = filing_info.get('form') or filing_type
            else:
                form = '10-Q'

            suffix = self._form_filename_suffix(form)

            # Check if per-filing output already exists (both balance_sheet and income_statement)
            if skip_existing:
                bs_file = self.output_dir / f"{ticker}_{filing_date}{suffix}_balance_sheet.csv"
                is_file = self.output_dir / f"{ticker}_{filing_date}{suffix}_income_statement.csv"
                if (bs_file.exists() and bs_file.stat().st_size > 100 and
                    is_file.exists() and is_file.stat().st_size > 100):
                    logger.info(f"SKIPPING {ticker} {filing_date}{suffix or ''} financials - already exists ({bs_file.name})")
                    results['balance_sheet'].append(bs_file)
                    results['income_statement'].append(is_file)
                    skipped += 1
                    continue

            logger.info(f"Processing {form} filing dated {filing_date}")
            
            try:
                # Fetch the specific filing (only XML/HTML - skip large exhibits)
                filing_result = self.sec_client.fetch_filing_by_index_url(
                    index_url=filing_info['index_url'],
                    ticker=ticker,
                    filing_type=form,
                    save_to_file=False,
                    document_types=['.xml', '.htm', '.html']
                )
                
                if not filing_result:
                    logger.warning(f"Skipping {filing_date}: could not fetch filing")
                    continue
                
                # Find and parse XBRL
                xbrl_url = self.find_xbrl_instance_document(filing_result)
                if not xbrl_url:
                    logger.warning(f"Skipping {filing_date}: no XBRL instance document found in filing")
                    continue
                
                xbrl_data = self.parse_xbrl_document(xbrl_url)
                if not xbrl_data.get('balance_sheet') and not xbrl_data.get('income_statement'):
                    logger.warning(f"Skipping {filing_date}: XBRL parse returned no balance sheet or income statement data")
                    continue
                
                # Save statements
                if xbrl_data['balance_sheet']:
                    bs_path = self.save_financial_statement_csv(
                        xbrl_data['balance_sheet'],
                        'balance_sheet',
                        ticker,
                        filing_date,
                        form=form,
                    )
                    if bs_path:
                        results['balance_sheet'].append(bs_path)
                
                if xbrl_data['income_statement']:
                    is_path = self.save_financial_statement_csv(
                        xbrl_data['income_statement'],
                        'income_statement',
                        ticker,
                        filing_date,
                        form=form,
                    )
                    if is_path:
                        results['income_statement'].append(is_path)
                
            except Exception as e:
                logger.warning(f"Skipping {filing_date}: {e}")

        if skipped > 0:
            logger.info(f"Skipped {skipped} already-processed filings for {ticker}")

        return results


def main():
    """Main CLI interface."""
    parser = argparse.ArgumentParser(description="Extract financial statements from SEC filings")
    parser.add_argument('--ticker', required=True, help='Company ticker symbol')
    parser.add_argument('--filing-type', default='10-Q', choices=['10-Q', '10-K', 'both'],
                       help='SEC form: 10-Q, 10-K, or both (quarterlies + annuals)')
    parser.add_argument('--year', type=int, help='Year to fetch filing for (default: latest)')
    parser.add_argument('--years-back', type=int, default=1,
                       help='Number of years to look back for historical filings')
    parser.add_argument('--output-dir', default='output/financials',
                       help='Output directory for CSV files')
    parser.add_argument('--force', action='store_true',
                       help='Re-process filings even if output CSV already exists')

    args = parser.parse_args()
    
    try:
        extractor = FinancialStatementsExtractor(
            output_dir=args.output_dir
        )
        
        if args.year:
            # Process single filing
            results = extractor.process_filing(
                ticker=args.ticker,
                filing_type=args.filing_type,
                year=args.year
            )
            
            if results:
                print(f"Successfully processed {args.ticker} {args.filing_type}")
                for stmt_type, path in results.items():
                    if path:
                        print(f"  - {stmt_type}: {path}")
            else:
                print(f"No financial data extracted for {args.ticker} {args.filing_type}")
        else:
            # Process historical filings
            results = extractor.process_historical_filings(
                ticker=args.ticker,
                filing_type=args.filing_type,
                years_back=args.years_back,
                skip_existing=not args.force
            )
            
            total_files = sum(len(paths) for paths in results.values())
            if total_files > 0:
                print(f"Successfully processed {total_files} financial statement files")
                for stmt_type, paths in results.items():
                    if paths:
                        print(f"  - {stmt_type}: {len(paths)} files")
            else:
                print(f"No financial data extracted for {args.ticker}")
    
    except Exception as e:
        logger.error(f"Error: {e}")
        exit(1)


if __name__ == "__main__":
    main()

