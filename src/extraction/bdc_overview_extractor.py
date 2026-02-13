#!/usr/bin/env python3
"""
Extract BDC overview metrics from SEC 10-Q and 10-K filings.

Extracts key metrics for BDC dashboard:
- NAV/share
- Debt/equity ratio
- NOI (Net Operating Income) / EPS per share
- Portfolio composition (% senior debt, sub debt, equity, other)
- Market cap, shares outstanding
- Originations/repayments
- Realized/unrealized gains
- Non-accruals
- Leverage ratio

Usage:
    python bdc_overview_extractor.py --ticker MRCC --filing-type 10-Q --year 2025 --quarter Q3
    python bdc_overview_extractor.py --ticker MRCC --years-back 2
"""

import os
import logging
import argparse
from typing import List, Dict, Any, Optional
from datetime import datetime, date
from pathlib import Path
import re
import requests
from bs4 import BeautifulSoup
import csv
import json

from sec_api_client import SECAPIClient

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class BDCOverviewExtractor:
    """
    Extract BDC-specific overview metrics from SEC filings using XBRL data.
    """

    def __init__(self,
                 data_dir: str = "data",
                 output_dir: str = "output/bdc_overview"):
        """
        Initialize the BDC overview extractor.

        Args:
            data_dir: Directory for SEC API client data
            output_dir: Directory to save CSV outputs
        """
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        
        self.data_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
        
        self.sec_client = SECAPIClient(data_dir=str(self.data_dir))
        
        # XBRL concepts for BDC metrics
        self.bdc_xbrl_concepts = {
            # Balance Sheet
            'net_assets': ['us-gaap:StockholdersEquity', 'us-gaap:NetAssets'],
            'total_assets': ['us-gaap:Assets'],
            'total_liabilities': ['us-gaap:Liabilities'],
            'debt': ['us-gaap:DebtCurrent', 'us-gaap:LongTermDebt', 'us-gaap:Debt', 'us-gaap:LongTermDebtAndCapitalLeaseObligations'],
            'short_term_debt': ['us-gaap:ShortTermBorrowings', 'us-gaap:DebtCurrent'],
            'long_term_debt': ['us-gaap:LongTermDebt', 'us-gaap:LongTermDebtNoncurrent'],
            'investments_at_cost': ['us-gaap:InvestmentOwnedAtCost'],
            'investments_at_fair_value': ['us-gaap:InvestmentOwnedAtFairValue', 'us-gaap:Investments'],
            'shares_outstanding': ['us-gaap:CommonStockSharesOutstanding', 'us-gaap:SharesOutstanding'],
            'non_accrual_investments_cost': ['us-gaap:FinancingReceivableNonaccrualStatus'],
            'non_accrual_investments_fv': ['us-gaap:FinancingReceivableNonaccrualStatusFairValue'],
            
            # Portfolio Composition (from investment schedules or notes)
            'senior_debt_fv': ['invest:SeniorSecuredDebtInvestmentsFairValue'],
            'subordinated_debt_fv': ['invest:SubordinatedDebtInvestmentsFairValue'],
            'equity_investments_fv': ['invest:EquitySecuritiesFairValue', 'us-gaap:EquitySecuritiesFvNi'],
            
            # Income Statement
            'net_investment_income': ['us-gaap:NetInvestmentIncome', 'us-gaap:InvestmentIncomeNet'],
            'net_income': ['us-gaap:NetIncomeLoss'],
            'realized_gains': ['us-gaap:RealizedInvestmentGainsLosses', 'us-gaap:GainLossOnInvestments'],
            'unrealized_gains': ['us-gaap:UnrealizedGainLossOnInvestments', 'us-gaap:UnrealizedGainLossOnInvestmentSecurities'],
            'total_investment_income': ['us-gaap:InvestmentIncomeInterestAndDividend', 'us-gaap:InterestAndDividendIncomeOperating'],
            'interest_expense': ['us-gaap:InterestExpense'],
            'total_expenses': ['us-gaap:OperatingExpenses', 'us-gaap:CostsAndExpenses'],
            
            # Cash Flow / Activity
            'purchases_of_investments': ['us-gaap:PaymentsToAcquireInvestments', 'us-gaap:PaymentsToAcquireLoansReceivable'],
            'proceeds_from_sales': ['us-gaap:ProceedsFromSaleOfInvestments', 'us-gaap:ProceedsFromSaleAndCollectionOfLoansReceivable'],
            'principal_repayments': ['us-gaap:ProceedsFromCollectionOfLoansReceivable'],
            
            # Per Share
            'nav_per_share': ['us-gaap:NetAssetValuePerShare'],
            'earnings_per_share': ['us-gaap:EarningsPerShareBasic'],
            'net_investment_income_per_share': ['us-gaap:NetInvestmentIncomePerShare'],
            'dividends_declared_per_share': ['us-gaap:CommonStockDividendsPerShareDeclared'],
        }

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
        for doc in filing_result.documents:
            filename_lower = doc.filename.lower()
            
            # Skip schema and linkbase files
            if any(skip in filename_lower for skip in [
                'schema', 'cal.xml', 'def.xml', 'lab.xml', 'pre.xml'
            ]):
                continue
            
            # Check for XBRL instance document patterns
            if any(pattern in filename_lower for pattern in [
                '_htm.xml',  # Most common
                'xbrl.htm',
                'xbrl.html',
                'instance.xml',
                '.xml'
            ]):
                if 'schema' not in filename_lower:
                    logger.info(f"Found XBRL instance document: {doc.filename}")
                    return doc.url
        
        return None

    def parse_xbrl_for_bdc_metrics(self, xbrl_url: str) -> Dict[str, Any]:
        """
        Parse XBRL instance document to extract BDC-specific metrics.
        Also looks for BDC-specific extension taxonomy elements.
        
        Args:
            xbrl_url: URL to XBRL instance document
            
        Returns:
            Dictionary with BDC overview metrics
        """
        try:
            response = requests.get(xbrl_url, headers=self.sec_client.headers, timeout=90)
            response.raise_for_status()
            
            # Parse XML
            soup = BeautifulSoup(response.content, 'xml')
            
            # Also look for company-specific extension elements (mrcc:, invest:, etc.)
            # Many BDCs have custom tags for portfolio composition and non-accruals
            logger.info("Scanning for BDC-specific extension taxonomy elements...")
            
            # Extract contexts (periods)
            contexts = {}
            for context in soup.find_all(['context', 'xbrli:context']):
                context_id = context.get('id', '')
                if not context_id:
                    continue
                
                # Extract period information
                period_elem = context.find(['period', 'xbrli:period'])
                if period_elem:
                    instant_elem = period_elem.find(['instant', 'xbrli:instant'])
                    start_elem = period_elem.find(['startDate', 'xbrli:startDate'])
                    end_elem = period_elem.find(['endDate', 'xbrli:endDate'])
                    
                    if instant_elem:
                        contexts[context_id] = {
                            'type': 'instant',
                            'date': instant_elem.text.strip()
                        }
                    elif start_elem and end_elem:
                        contexts[context_id] = {
                            'type': 'duration',
                            'start_date': start_elem.text.strip(),
                            'end_date': end_elem.text.strip()
                        }
            
            # Extract all facts
            metrics = {}
            extension_facts = []  # Store custom/extension facts for analysis
            
            # Find all XBRL facts
            for tag in soup.find_all():
                # Skip non-fact elements
                if not tag.get('contextRef'):
                    continue
                
                tag_name = tag.name
                concept = None
                
                # Handle different namespace prefixes
                if ':' in tag_name:
                    concept = tag_name
                    namespace = tag_name.split(':')[0]
                    
                    # Track custom/extension taxonomies (not us-gaap or dei)
                    if namespace not in ['us-gaap', 'dei', 'xbrli']:
                        tag_lower = tag_name.lower()
                        
                        # Look for BDC-specific patterns in extension taxonomy
                        if any(keyword in tag_lower for keyword in [
                            'nonaccrual', 'non-accrual', 'portfolio', 'composition',
                            'seniordebt', 'subordinated', 'equity', 'origination',
                            'repayment', 'realized', 'unrealized'
                        ]):
                            extension_facts.append({
                                'tag': tag_name,
                                'value': tag.text.strip(),
                                'context_ref': tag.get('contextRef')
                            })
                else:
                    # Try to find concept in common namespaces
                    for ns in ['us-gaap', 'dei', 'invest']:
                        potential_concept = f"{ns}:{tag_name}"
                        if any(potential_concept.lower() in concepts_list[0].lower() 
                              for concepts_list in self.bdc_xbrl_concepts.values()
                              if concepts_list):
                            concept = potential_concept
                            break
                    
                    if not concept:
                        concept = tag_name
                
                context_ref = tag.get('contextRef')
                context = contexts.get(context_ref, {})
                
                value = tag.text.strip()
                
                # Store metric
                for metric_name, concept_list in self.bdc_xbrl_concepts.items():
                    if any(concept.lower() == c.lower() for c in concept_list):
                        if metric_name not in metrics:
                            metrics[metric_name] = []
                        
                        metrics[metric_name].append({
                            'concept': concept,
                            'value': value,
                            'context': context,
                            'context_ref': context_ref
                        })
            
            # Log extension facts found
            if extension_facts:
                logger.info(f"Found {len(extension_facts)} extension taxonomy facts (BDC-specific)")
                for fact in extension_facts[:10]:  # Log first 10
                    logger.debug(f"  - {fact['tag']}: {fact['value'][:50] if len(fact['value']) > 50 else fact['value']}")
            
            return {
                'metrics': metrics,
                'contexts': contexts,
                'extension_facts': extension_facts  # Include for further analysis
            }
            
        except Exception as e:
            logger.error(f"Error parsing XBRL document: {e}")
            return {
                'metrics': {},
                'contexts': {}
            }

    def extract_latest_metrics(self, xbrl_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract the most recent values for each metric.
        For income statement items, extracts BOTH YTD and quarterly values.
        
        Args:
            xbrl_data: Parsed XBRL data
            
        Returns:
            Dictionary with latest metric values
        """
        metrics = xbrl_data.get('metrics', {})
        
        result = {}
        
        # Income statement items that should also show quarterly values
        quarterly_items = {
            'realized_gains', 'unrealized_gains', 'net_investment_income', 
            'net_income', 'purchases_of_investments', 'proceeds_from_sales',
            'principal_repayments', 'total_investment_income', 'interest_expense'
        }
        
        for metric_name, facts in metrics.items():
            if not facts:
                continue
            
            # Find the most recent instant or end_date
            latest_fact = None
            latest_date = None
            latest_ytd_fact = None
            latest_quarterly_fact = None
            
            for fact in facts:
                context = fact.get('context', {})
                
                if context.get('type') == 'instant':
                    date_str = context.get('date')
                elif context.get('type') == 'duration':
                    date_str = context.get('end_date')
                    
                    # For duration contexts, check if it's quarterly (3 months) vs YTD
                    if metric_name in quarterly_items:
                        try:
                            start = datetime.strptime(context.get('start_date', ''), '%Y-%m-%d')
                            end = datetime.strptime(context.get('end_date', ''), '%Y-%m-%d')
                            duration_days = (end - start).days
                            
                            # Quarterly: ~90 days, YTD: 180-270 days
                            if duration_days <= 100:  # Quarterly
                                if not latest_quarterly_fact or end.date() > latest_date:
                                    latest_quarterly_fact = fact
                            else:  # YTD
                                if not latest_ytd_fact or end.date() > latest_date:
                                    latest_ytd_fact = fact
                        except:
                            pass
                else:
                    continue
                
                if not date_str:
                    continue
                
                try:
                    date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                    
                    if latest_date is None or date_obj > latest_date:
                        latest_date = date_obj
                        latest_fact = fact
                except:
                    continue
            
            # Store latest (YTD or instant value)
            if latest_fact:
                try:
                    value = float(latest_fact['value'].replace(',', ''))
                    result[metric_name] = {
                        'value': value,
                        'date': latest_date.isoformat() if latest_date else None,
                        'concept': latest_fact.get('concept')
                    }
                except ValueError:
                    # Keep as string if not numeric
                    result[metric_name] = {
                        'value': latest_fact['value'],
                        'date': latest_date.isoformat() if latest_date else None,
                        'concept': latest_fact.get('concept')
                    }
            
            # Also store quarterly value if available
            if latest_quarterly_fact and metric_name in quarterly_items:
                try:
                    value = float(latest_quarterly_fact['value'].replace(',', ''))
                    result[f"{metric_name}_quarterly"] = {
                        'value': value,
                        'date': latest_quarterly_fact.get('context', {}).get('end_date'),
                        'concept': latest_quarterly_fact.get('concept')
                    }
                except ValueError:
                    pass
        
        return result

    def calculate_derived_metrics(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate derived metrics from extracted values.
        
        Args:
            metrics: Dictionary of extracted metrics
            
        Returns:
            Dictionary with derived metrics added
        """
        derived = {}
        
        # NAV per share (if not already in XBRL)
        if 'nav_per_share' not in metrics:
            net_assets = metrics.get('net_assets', {}).get('value')
            shares = metrics.get('shares_outstanding', {}).get('value')
            
            if net_assets and shares:
                derived['nav_per_share_calculated'] = net_assets / shares
        
        # Total Debt (combine short + long term if debt not directly available)
        debt = metrics.get('debt', {}).get('value')
        if not debt:
            short_debt = metrics.get('short_term_debt', {}).get('value', 0)
            long_debt = metrics.get('long_term_debt', {}).get('value', 0)
            if short_debt or long_debt:
                debt = short_debt + long_debt
                derived['total_debt_calculated'] = debt
        
        # Debt/Equity ratio
        equity = metrics.get('net_assets', {}).get('value')
        
        if debt and equity:
            derived['debt_to_equity_ratio'] = round(debt / equity, 2)
        
        # Leverage ratio (Total Assets / Equity)
        total_assets = metrics.get('total_assets', {}).get('value')
        if total_assets and equity:
            derived['leverage_ratio'] = round(total_assets / equity, 2)
        
        # Unrealized appreciation/depreciation
        fv = metrics.get('investments_at_fair_value', {}).get('value')
        cost = metrics.get('investments_at_cost', {}).get('value')
        
        if fv and cost:
            derived['unrealized_appreciation'] = fv - cost
            derived['unrealized_appreciation_pct'] = round((fv - cost) / cost * 100, 2) if cost else 0
        
        # Originations (purchases during period)
        purchases = metrics.get('purchases_of_investments', {}).get('value')
        if purchases:
            # Purchases are usually negative in cash flow, make positive
            derived['originations'] = abs(purchases)
        
        # Repayments (proceeds from sales + collections)
        proceeds = metrics.get('proceeds_from_sales', {}).get('value', 0)
        principal = metrics.get('principal_repayments', {}).get('value', 0)
        if proceeds or principal:
            derived['repayments'] = proceeds + principal
        
        # Net investment activity
        if purchases and (proceeds or principal):
            derived['net_investment_activity'] = (proceeds + principal) - abs(purchases)
        
        # Non-accrual percentages
        non_accrual_cost = metrics.get('non_accrual_investments_cost', {}).get('value')
        non_accrual_fv = metrics.get('non_accrual_investments_fv', {}).get('value')
        
        if non_accrual_cost and cost:
            derived['non_accrual_pct_cost'] = round(non_accrual_cost / cost * 100, 2)
        
        if non_accrual_fv and fv:
            derived['non_accrual_pct_fv'] = round(non_accrual_fv / fv * 100, 2)
        
        # Portfolio Composition (% of total FV)
        if fv:
            senior_debt = metrics.get('senior_debt_fv', {}).get('value', 0)
            sub_debt = metrics.get('subordinated_debt_fv', {}).get('value', 0)
            equity = metrics.get('equity_investments_fv', {}).get('value', 0)
            
            if senior_debt:
                derived['pct_senior_debt'] = round(senior_debt / fv * 100, 1)
            if sub_debt:
                derived['pct_subordinated_debt'] = round(sub_debt / fv * 100, 1)
            if equity:
                derived['pct_equity'] = round(equity / fv * 100, 1)
            
            # Calculate "Other" as remainder
            total_classified = senior_debt + sub_debt + equity
            if total_classified > 0 and total_classified < fv:
                derived['pct_other'] = round((fv - total_classified) / fv * 100, 1)
        
        return derived

    def save_overview_metrics(self, ticker: str, filing_date: str, 
                             metrics: Dict[str, Any], derived: Dict[str, Any]) -> Path:
        """
        Save BDC overview metrics to CSV.
        
        Args:
            ticker: Company ticker
            filing_date: Filing date (YYYY-MM-DD)
            metrics: Extracted metrics
            derived: Derived metrics
            
        Returns:
            Path to saved CSV file
        """
        filename = f"{ticker}_{filing_date}_overview.csv"
        output_path = self.output_dir / filename
        
        # Flatten metrics for CSV
        rows = []
        
        # Add extracted metrics
        for metric_name, data in metrics.items():
            if isinstance(data, dict) and 'value' in data:
                rows.append({
                    'ticker': ticker,
                    'filing_date': filing_date,
                    'metric_name': metric_name,
                    'value': data['value'],
                    'date': data.get('date', filing_date),
                    'concept': data.get('concept', ''),
                    'type': 'extracted'
                })
        
        # Add derived metrics
        for metric_name, value in derived.items():
            rows.append({
                'ticker': ticker,
                'filing_date': filing_date,
                'metric_name': metric_name,
                'value': value,
                'date': filing_date,
                'concept': '',
                'type': 'calculated'
            })
        
        # Write to CSV
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=['ticker', 'filing_date', 'metric_name', 
                                                       'value', 'date', 'concept', 'type'])
                writer.writeheader()
                writer.writerows(rows)
        
        logger.info(f"Saved {len(rows)} metrics to {output_path}")
        return output_path

    def extract_portfolio_composition_from_holdings(self, holdings_csv_path: Path) -> Dict[str, Any]:
        """
        Calculate portfolio composition from the investment holdings CSV.
        
        Args:
            holdings_csv_path: Path to the holdings CSV file
            
        Returns:
            Dictionary with portfolio composition percentages
        """
        try:
            with open(holdings_csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            
            if not rows:
                return {}
            
            # Calculate by investment type
            senior_debt_fv = 0
            sub_debt_fv = 0
            equity_fv = 0
            other_fv = 0
            total_fv = 0
            
            for row in rows:
                fv_str = row.get('fair_value', '').strip().replace(',', '')
                inv_type = row.get('investment_type', '').strip().lower()
                
                try:
                    fv = float(fv_str) if fv_str else 0
                except ValueError:
                    continue
                
                total_fv += fv
                
                # Classify by investment type
                if any(t in inv_type for t in ['first lien', 'senior secured', 'senior debt']):
                    senior_debt_fv += fv
                elif any(t in inv_type for t in ['second lien', 'subordinated', 'mezzanine']):
                    sub_debt_fv += fv
                elif any(t in inv_type for t in ['equity', 'common', 'preferred', 'warrant']):
                    equity_fv += fv
                else:
                    other_fv += fv
            
            if total_fv > 0:
                return {
                    'pct_senior_debt': round(senior_debt_fv / total_fv * 100, 1),
                    'pct_subordinated_debt': round(sub_debt_fv / total_fv * 100, 1),
                    'pct_equity': round(equity_fv / total_fv * 100, 1),
                    'pct_other': round(other_fv / total_fv * 100, 1),
                    'total_fair_value_from_holdings': total_fv
                }
            
            return {}
            
        except Exception as e:
            logger.warning(f"Could not calculate portfolio composition from holdings: {e}")
            return {}

    def extract_activity_metrics_from_html(self, filing_result) -> Dict[str, Any]:
        """
        Extract originations, repayments, and quarterly gains from HTML filing text.
        Looks for these in MD&A, Statement of Changes, or portfolio activity notes.
        
        Args:
            filing_result: FilingResult object with documents
            
        Returns:
            Dictionary with activity metrics
        """
        activity_metrics = {}
        
        try:
            # Get the main HTML document
            main_doc = None
            for doc in filing_result.documents:
                if doc.filename.endswith('.htm') and 'ex' not in doc.filename.lower():
                    main_doc = doc
                    break
            
            if not main_doc:
                return {}
            
            # Fetch and parse HTML content
            response = requests.get(main_doc.url, headers=self.sec_client.headers, timeout=90)
            response.raise_for_status()
            text = response.text.lower()
            
            # Pattern 1: Look for originations and repayments in text
            # Common patterns: "originations of $X million", "repayments of $X million"
            originations_patterns = [
                r'originations?\s+(?:of|during[^$]*)\$?\s*([\d,]+\.?\d*)\s*million',
                r'new\s+investments?\s+(?:of|totaling)\s*\$?\s*([\d,]+\.?\d*)\s*million',
                r'purchases?\s+of\s+investments?\s*\$?\s*([\d,]+\.?\d*)\s*million'
            ]
            
            repayments_patterns = [
                r'repayments?\s+(?:of|totaling)\s*\$?\s*([\d,]+\.?\d*)\s*million',
                r'sales?\s+and\s+repayments?\s+(?:of|totaling)\s*\$?\s*([\d,]+\.?\d*)\s*million',
                r'proceeds?\s+from\s+(?:sales?|repayments?)\s+(?:of|totaling)\s*\$?\s*([\d,]+\.?\d*)\s*million'
            ]
            
            # Search for originations
            for pattern in originations_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    try:
                        # Take the first match, convert to actual value (in thousands)
                        value_millions = float(matches[0].replace(',', ''))
                        activity_metrics['originations'] = value_millions * 1000  # Convert to thousands
                        logger.info(f"Found originations in text: ${value_millions}M")
                        break
                    except:
                        pass
            
            # Search for repayments
            for pattern in repayments_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    try:
                        value_millions = float(matches[0].replace(',', ''))
                        activity_metrics['repayments'] = value_millions * 1000  # Convert to thousands
                        logger.info(f"Found repayments in text: ${value_millions}M")
                        break
                    except:
                        pass
            
            # Pattern 2: Look for realized and unrealized gains/losses for the quarter
            # "net realized gains of $X", "net unrealized gains of $X"
            realized_patterns = [
                r'net\s+realized\s+(?:gains?|losses?)\s+(?:of|totaling)\s*[\(\$]?\s*([\-\d,]+\.?\d*)\s*(?:million|\))',
                r'realized\s+(?:gains?|losses?)\s+(?:of)?\s*[\(\$]?\s*([\-\d,]+\.?\d*)\s*(?:million|\))'
            ]
            
            unrealized_patterns = [
                r'net\s+unrealized\s+(?:gains?|losses?|appreciation)\s+(?:of|totaling)\s*[\(\$]?\s*([\-\d,]+\.?\d*)\s*(?:million|\))',
                r'unrealized\s+(?:gains?|losses?|appreciation)\s+(?:of)?\s*[\(\$]?\s*([\-\d,]+\.?\d*)\s*(?:million|\))'
            ]
            
            # Search for realized gains (quarterly)
            for pattern in realized_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    try:
                        value_millions = float(matches[0].replace(',', '').replace('(', '-').replace(')', ''))
                        activity_metrics['realized_gains_quarterly'] = value_millions * 1000
                        logger.info(f"Found quarterly realized gains in text: ${value_millions}M")
                        break
                    except:
                        pass
            
            # Search for unrealized gains (quarterly)
            for pattern in unrealized_patterns:
                matches = re.findall(pattern, text, re.IGNORECASE)
                if matches:
                    try:
                        value_millions = float(matches[0].replace(',', '').replace('(', '-').replace(')', ''))
                        activity_metrics['unrealized_gains_quarterly'] = value_millions * 1000
                        logger.info(f"Found quarterly unrealized gains in text: ${value_millions}M")
                        break
                    except:
                        pass
            
            return activity_metrics
            
        except Exception as e:
            logger.warning(f"Could not extract activity metrics from HTML: {e}")
            return {}

    def extract_filing_overview(self, ticker: str, filing_type: str = "10-Q",
                                year: Optional[int] = None, quarter: Optional[str] = None) -> Optional[Path]:
        """
        Extract overview metrics for a specific filing.
        
        Args:
            ticker: Company ticker symbol
            filing_type: Type of filing (10-Q or 10-K)
            year: Filing year
            quarter: Quarter (Q1, Q2, Q3, Q4) - only for 10-Q
            
        Returns:
            Path to saved CSV file, or None if extraction failed
        """
        try:
            # Get the filing index URL
            index_url = self.sec_client.get_filing_index_url(
                ticker=ticker,
                filing_type=filing_type,
                year=year,
                quarter=quarter
            )
            
            if not index_url:
                logger.error(f"Could not find {filing_type} filing for {ticker}")
                return None
            
            # Fetch the filing
            filing_result = self.sec_client.fetch_filing_by_index_url(
                index_url=index_url,
                ticker=ticker,
                filing_type=filing_type,
                save_to_file=False,
                document_types=['.xml', '.htm', '.html']
            )
            
            if not filing_result:
                logger.error(f"Could not fetch filing for {ticker}")
                return None
            
            filing_date = filing_result.filing_date
            
            # Find and parse XBRL
            xbrl_url = self.find_xbrl_instance_document(filing_result)
            if not xbrl_url:
                logger.warning(f"No XBRL instance document found in filing")
                return None
            
            xbrl_data = self.parse_xbrl_for_bdc_metrics(xbrl_url)
            
            # Extract latest metrics
            metrics = self.extract_latest_metrics(xbrl_data)
            
            if not metrics:
                logger.warning(f"No metrics extracted from XBRL")
                return None
            
            # Calculate derived metrics
            derived = self.calculate_derived_metrics(metrics)
            
            # Try to extract portfolio composition from holdings CSV if available
            holdings_path = Path(f"output/{ticker}_investments_{filing_date}.csv")
            if holdings_path.exists():
                logger.info(f"Found holdings CSV, calculating portfolio composition...")
                portfolio_comp = self.extract_portfolio_composition_from_holdings(holdings_path)
                if portfolio_comp:
                    derived.update(portfolio_comp)
                    logger.info(f"Added portfolio composition from holdings")
            
            # Extract activity metrics from HTML (originations, repayments, quarterly gains)
            logger.info(f"Parsing HTML for activity metrics (originations, repayments, quarterly gains)...")
            activity_metrics = self.extract_activity_metrics_from_html(filing_result)
            if activity_metrics:
                derived.update(activity_metrics)
                logger.info(f"Added {len(activity_metrics)} activity metrics from HTML parsing")
            
            # Save to CSV
            output_path = self.save_overview_metrics(ticker, filing_date, metrics, derived)
            
            logger.info(f"Successfully extracted overview metrics for {ticker} {filing_date}")
            return output_path
            
        except Exception as e:
            logger.error(f"Error extracting overview metrics: {e}")
            return None


def main():
    parser = argparse.ArgumentParser(
        description='Extract BDC overview metrics from SEC filings'
    )
    parser.add_argument('--ticker', required=True, help='Company ticker symbol')
    parser.add_argument('--filing-type', default='10-Q', choices=['10-Q', '10-K'],
                       help='Type of filing to extract')
    parser.add_argument('--year', type=int, help='Filing year')
    parser.add_argument('--quarter', choices=['Q1', 'Q2', 'Q3', 'Q4'],
                       help='Quarter (only for 10-Q filings)')
    parser.add_argument('--output-dir', default='output/bdc_overview',
                       help='Output directory for CSV files')
    
    args = parser.parse_args()
    
    try:
        extractor = BDCOverviewExtractor(output_dir=args.output_dir)
        
        output_path = extractor.extract_filing_overview(
            ticker=args.ticker,
            filing_type=args.filing_type,
            year=args.year,
            quarter=args.quarter
        )
        
        if output_path:
            logger.info(f"✅ Overview metrics saved to: {output_path}")
        else:
            logger.error("❌ Failed to extract overview metrics")
            exit(1)
            
    except Exception as e:
        logger.error(f"Error: {e}")
        exit(1)


if __name__ == '__main__':
    main()
