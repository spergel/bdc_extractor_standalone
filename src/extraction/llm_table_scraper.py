#!/usr/bin/env python3
"""
LLM Table Scraper for SEC 10-Q Filings

This script automatically extracts schedule of investments from SEC 10-Q filings
using LLM-powered table parsing to create CSV files for the frontend.

Usage:
    python llm_table_scraper.py --ticker MRCC --filing-type 10-Q --year 2025
    python llm_table_scraper.py --ticker ARCC --quarter Q3 --year 2025
"""

import os
import logging
import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, date
import math
from pathlib import Path
import re
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass

# Optional Gemini import
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# Optional dotenv import
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

from sec_api_client import SECAPIClient

# Import modular components
from data_cleaning import deduplicate_csv_rows, filter_equity_types, filter_llm_artifact_rows, remove_empty_header_rows
from table_detection import (
    fallback_table_detection,
    is_year_end_table,
    select_current_quarter_tables,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class TableExtractionResult:
    """Result of LLM table extraction."""
    csv_content: str
    table_number: int
    confidence_score: float
    metadata: Dict[str, Any]

class LLMTableScraper:
    """
    LLM-powered scraper for SEC filing tables.

    Extracts and parses investment schedule tables from SEC filings using
    advanced language models to convert HTML tables to structured CSV data.
    """

    def __init__(self,
                 gemini_api_key: Optional[str] = None,
                 data_dir: str = "data",
                 output_dir: str = "output",
                 debug_dir: str = "debug_tables",
                 use_llm: bool = True):
        """
        Initialize the LLM table scraper.

        Args:
            gemini_api_key: Gemini API key (will check env var if not provided)
            data_dir: Directory for SEC API client data
            output_dir: Directory to save CSV outputs
            debug_dir: Directory to save debug information
            use_llm: If False, skip Gemini setup (for custom scrapers that only use table parsing).
        """
        # Load environment variables from .env file if available
        if DOTENV_AVAILABLE:
            load_dotenv()

        self.use_llm = use_llm
        self.gemini_api_key = None
        self.gemini_model = None

        if use_llm:
            if not GEMINI_AVAILABLE:
                raise ImportError("google-generativeai package not installed. Install with: pip install google-generativeai")
            self.gemini_api_key = gemini_api_key or os.getenv('GOOGLE_API_KEY')
            if not self.gemini_api_key:
                raise ValueError("Gemini API key required. Set GOOGLE_API_KEY env var or pass directly.")
            genai.configure(api_key=self.gemini_api_key)
            self.gemini_model = genai.GenerativeModel('gemini-3-flash-preview')
            logger.info("Using Gemini 3 Flash for LLM processing")
        else:
            logger.info("LLM disabled (use_llm=False) - table parsing only")

        # Set up directories
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.debug_dir = Path(debug_dir)

        self.data_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
        self.debug_dir.mkdir(exist_ok=True)

        # Initialize SEC client
        self.sec_client = SECAPIClient(data_dir=str(self.data_dir))

        # Investment-related keywords to identify relevant tables
        self.investment_keywords = [
            'schedule of investments',
            'investments',
            'portfolio companies',
            'loan portfolio',
            'debt securities',
            'equity securities',
            'portfolio of investments',
            'investment portfolio'
        ]

    def extract_tables_from_filing_documents(self, filing_result: Any) -> List[Tuple[str, str, int, str]]:
        """
        Extract HTML tables from individual filing documents, not the combined text.

        Args:
            filing_result: FilingResult object with documents

        Returns:
            List of tuples: (table_html, table_text, table_number, document_filename)
        """
        all_tables = []

        if not filing_result.documents:
            logger.warning("No documents found in filing result")
            return all_tables

        for doc in filing_result.documents:
            # Skip non-HTML documents and XML files
            if not (doc.filename.endswith('.htm') or doc.filename.endswith('.html')):
                continue

            # Skip XBRL and other XML files
            if doc.filename.endswith('.xml') or 'xml' in doc.filename.lower():
                continue

            try:
                logger.info(f"Processing document for tables: {doc.filename}")

                # Fetch the document content
                response = requests.get(doc.url, headers=self.sec_client.headers, timeout=90)
                response.raise_for_status()

                content_type = response.headers.get('content-type', '').lower()
                if 'html' not in content_type and 'xml' not in content_type:
                    continue

                # Parse HTML and extract tables
                soup = BeautifulSoup(response.content, 'html.parser')
                doc_tables = soup.find_all('table')

                logger.info(f"Found {len(doc_tables)} tables in {doc.filename}")

                for i, table in enumerate(doc_tables):
                    # Get the raw HTML
                    table_html = str(table)

                    # Extract text content for analysis
                    table_text = table.get_text(separator=' ', strip=True)

                    # Skip very small tables (likely not investment tables)
                    if len(table_text.strip()) < 100:
                        continue

                    table_id = f"{doc.filename}_table_{i}"
                    all_tables.append((table_html, table_text, len(all_tables), table_id))

            except Exception as e:
                logger.warning(f"Failed to process document {doc.filename}: {e}")
                continue

        logger.info(f"Extracted {len(all_tables)} total tables from all documents")
        return all_tables

    def _is_year_end_table(self, table_html: str, table_text: str, filing_date: str,
                           context_before: str = "", filing_type: str = "10-Q", 
                           period_end_date: str = None) -> bool:
        """
        Check if this table is ONLY prior year-end (so we should skip it).
        We want ONLY current quarter's holdings; filings often have two blocks:
        (1) current quarter tables, (2) prior year-end comparative tables.
        Return True only when we're sure this table is in block (2).

        - If the table mentions the CURRENT PERIOD date anywhere → KEEP (return False).
        - If it only has December 31 of a PRIOR year (no current period) → SKIP (return True).
        - 10-K: we want year-end data, so never skip (return False).
        """
        # For 10-K filings, we WANT year-end data - don't filter anything
        if filing_type == "10-K":
            return False
            
        from datetime import datetime
        import re

        try:
            # Parse the period end date (the actual date this filing covers)
            if period_end_date:
                period_dt = datetime.strptime(period_end_date, '%Y-%m-%d')
                period_year = period_dt.year
                period_month = period_dt.month
                period_day = period_dt.day
            else:
                filing_dt = datetime.strptime(filing_date, '%Y-%m-%d')
                period_year = filing_dt.year
                period_month = filing_dt.month
                period_day = filing_dt.day

            # Combine table content with context (header often has the date)
            text_lower = (table_text + ' ' + (context_before or "")).lower()
            html_lower = table_html.lower()
            combined_text = text_lower + ' ' + html_lower
            combined_text = re.sub(r'[\xa0\u2013\u2014\u00a0]', ' ', combined_text)

            # Use a LARGE window so we see both current and prior-year dates in comparative tables.
            # Q4 tables often have "December 31, 2025" and "December 31, 2024" - we must keep them.
            search_text = combined_text[:2500]

            month_names = {
                1: 'january', 2: 'february', 3: 'march', 4: 'april',
                5: 'may', 6: 'june', 7: 'july', 8: 'august',
                9: 'september', 10: 'october', 11: 'november', 12: 'december'
            }
            period_month_name = month_names.get(period_month, '')

            # 1) CURRENT PERIOD: if we see the filing's period date anywhere, this is current-quarter → KEEP
            current_period_patterns = [
                rf'{re.escape(period_month_name)}\s+{period_day}[,\s]+{period_year}',
                rf'{period_month_name[:3]}[.]?\s+{period_day}[,\s]+{period_year}',
                rf'\b{period_month}/{period_day}/{period_year}\b',
                rf'\b{period_month:02d}/{period_day:02d}/{period_year}\b',
                rf'\b{period_year}-{period_month:02d}-{period_day:02d}\b',
                rf'as\s+of\s+{re.escape(period_month_name)}\s+{period_day}[,\s]+{period_year}',
                rf'at\s+{re.escape(period_month_name)}\s+{period_day}[,\s]+{period_year}',
            ]
            for pattern in current_period_patterns:
                if re.search(pattern, search_text, re.I):
                    return False  # current period → keep

            # 2) PRIOR YEAR-END: only skip if we see Dec 31 of a prior year AND we did not see current period above
            dec31_match = re.search(r'(?:december|dec\.?)\s+31[,\s]+(\d{4})', search_text, re.I)
            if dec31_match:
                year_in_table = int(dec31_match.group(1))
                if year_in_table < period_year:
                    logger.info(f"Detected prior year-end table: December 31, {year_in_table} (no current period date in table)")
                    return True

            dec31_numeric = re.search(r'\b12[/-]31[/-](\d{4})\b', search_text)
            if dec31_numeric:
                year_in_table = int(dec31_numeric.group(1))
                if year_in_table < period_year:
                    logger.info(f"Detected prior year-end table: 12/31/{year_in_table} (no current period date in table)")
                    return True

            # Unclear → keep (don't skip)
            return False

        except Exception as e:
            logger.debug(f"Error checking year-end status: {e}")
            return False

    def _detect_unit_scale(self, tables: List[Tuple[str, str, int, str]],
                           filing_result: Any) -> str:
        """
        Detect the unit scale of dollar amounts from table context.
        SEC schedule of investments often state "amounts in thousands" or "in millions".

        Args:
            tables: List of (table_html, table_text, table_num, table_id)
            filing_result: FilingResult with text_map (doc filename -> HTML content)

        Returns:
            "thousands", "millions", or "units" (raw/unstated)
        """
        search_text_parts = []

        # Add all table HTML and text
        for table_html, table_text, _, _ in tables:
            search_text_parts.append(table_html.lower())
            search_text_parts.append(table_text.lower())

        # Add document context (schedule headers often state the unit before tables)
        if hasattr(filing_result, 'text_map') and filing_result.text_map:
            for doc_text in filing_result.text_map.values():
                if doc_text:
                    # Get a window of text - schedule headers are usually near the top or before tables
                    search_text_parts.append(doc_text[:15000].lower())

        search_text = ' '.join(search_text_parts)

        # Patterns that indicate thousands
        thousands_patterns = [
            r'in thousands',
            r'\(in thousands\)',
            r'amounts in thousands',
            r'\(\$ in thousands\)',
            r'\$ in thousands',
            r'\(\d+\s*\)\s*in thousands',  # (000s)
            r'\(000\'?s?\)',
            r'\(\s*000\s*\)',
            r'thousands of dollars',
            r'000s',
            r'\(000\)',
        ]

        # Patterns that indicate millions
        millions_patterns = [
            r'in millions',
            r'\(in millions\)',
            r'amounts in millions',
            r'\(\$ in millions\)',
            r'\$ in millions',
            r'millions of dollars',
        ]

        for pattern in thousands_patterns:
            if re.search(pattern, search_text, re.IGNORECASE):
                logger.info(f"Detected unit scale: thousands (matched: {pattern})")
                return "thousands"

        for pattern in millions_patterns:
            if re.search(pattern, search_text, re.IGNORECASE):
                logger.info(f"Detected unit scale: millions (matched: {pattern})")
                return "millions"

        logger.info("Detected unit scale: units (no explicit scale found, assuming raw amounts)")
        return "units"

    def find_investment_schedule_tables(self, tables: List[Tuple[str, str, int, str]],
                                       filing_result: Any, filing_type: str = "10-Q",
                                       period_end_date: str = None) -> List[Tuple[str, str, int, str]]:
        """Find tables under Schedule of Investments headers; fallback to content-based detection."""
        return self._find_investment_schedule_tables_impl(
            tables, filing_result, filing_type, period_end_date
        )
    
    def _find_investment_schedule_tables_impl(self, tables: List[Tuple[str, str, int, str]],
                                       filing_result: Any, filing_type: str = "10-Q",
                                       period_end_date: str = None) -> List[Tuple[str, str, int, str]]:
        """
        Find tables that are directly under "Schedule of Investments" headers.
        Uses HTML structure to find tables that appear after schedule headers.
        Checks ALL HTML documents (main + exhibits) to avoid missing tables.

        Args:
            tables: List of (html, text, table_num, table_id) tuples
            filing_result: The FilingResult object
            filing_type: Type of filing (10-Q, 10-K, etc.) - affects year-end filtering
            
        Returns:
            List of investment schedule tables
        """
        from bs4 import BeautifulSoup
        import requests
        
        filing_date = filing_result.filing_date
        filing_documents = filing_result.documents if hasattr(filing_result, 'documents') else None
        
        investment_tables = []
        processed_table_indices = set()
        
        if not filing_documents:
            logger.warning("No filing documents provided, using fallback detection")
            return self._fallback_table_detection(tables, filing_date)
        
        # Find ALL HTML documents (main + exhibits) - don't skip exhibits!
        html_docs = []
        for doc in filing_documents:
            if (doc.filename.endswith('.htm') or doc.filename.endswith('.html')) and not doc.filename.endswith('.xml'):
                html_docs.append(doc)
                logger.info(f"Will check document for schedule headers: {doc.filename}")
        
        if not html_docs:
            logger.warning("No HTML documents found for precise table detection")
            return self._fallback_table_detection(tables, filing_date)
        
        # Process each HTML document to find schedule headers and tables
        for doc in html_docs:
            try:
                logger.info(f"Processing document for schedule headers: {doc.filename}")
                
                # Check if we already have the text for this document in filing_result
                doc_text = ""
                if hasattr(filing_result, 'text_map') and doc.filename in filing_result.text_map:
                    # Use pre-fetched text if available
                    doc_text = filing_result.text_map[doc.filename]
                else:
                    # Fallback to fetching
                    response = requests.get(doc.url, headers=self.sec_client.headers, timeout=90)
                    response.raise_for_status()
                    doc_text = response.text
                
                soup = BeautifulSoup(doc_text, 'html.parser')
                
                # Find all "Schedule of Investments" headers (expanded search)
                schedule_headers = []
                for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div', 'span']):
                    text = tag.get_text(strip=True).lower()
                    if any(keyword in text for keyword in [
                        'schedule of investments',
                        'consolidated schedule of investments',
                        'investment schedule',
                        'schedule of portfolio investments',
                        'condensed schedule of investments'
                    ]):
                        schedule_headers.append(tag)
                        logger.info(f"Found schedule header in {doc.filename}: {tag.get_text(strip=True)[:100]}")
                
                if not schedule_headers:
                    logger.debug(f"No schedule headers found in {doc.filename}, continuing to next document")
                    continue
                
                # Find all tables in this document
                all_html_tables = soup.find_all('table')
                logger.info(f"Found {len(all_html_tables)} tables in {doc.filename}")
                
                # Get all elements in document order (include more element types)
                all_elements = list(soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'table', 'div', 'span']))
                
                # Find ALL schedule headers (not just first)
                schedule_header_positions = []
                for i, element in enumerate(all_elements):
                    text = element.get_text(strip=True).lower()
                    if any(keyword in text for keyword in [
                        'schedule of investments',
                        'consolidated schedule of investments',
                        'schedule of portfolio investments'
                    ]):
                        schedule_header_positions.append(i)
                        logger.info(f"Found schedule header at position {i} in {doc.filename}: {element.get_text(strip=True)[:100]}")
                
                if not schedule_header_positions:
                    logger.debug(f"Could not find schedule header in document order for {doc.filename}")
                    continue
                
                # Process each schedule section in this document
                for header_idx in schedule_header_positions:
                    # Check if this section is year-end by looking at the header + nearby elements
                    filing_year = int(filing_date.split('-')[0]) if filing_date else 2025
                    filing_month = int(filing_date.split('-')[1]) if filing_date else 9
                    filing_day = int(filing_date.split('-')[2]) if filing_date else 30

                    # Check header and next 5 elements (date often in sibling or nearby paragraph)
                    import re
                    section_text_parts = []
                    for k in range(min(5, len(all_elements) - header_idx)):
                        el = all_elements[header_idx + k]
                        section_text_parts.append(el.get_text(strip=True))
                    section_text = ' '.join(section_text_parts).lower()
                    
                    # Normalize text: replace special characters with spaces
                    section_text = re.sub(r'[\xa0\u2013\u2014\u00a0]', ' ', section_text)

                    # DISABLED: Section-level year-end filtering (too aggressive, causes false positives)
                    # Instead, we rely on table-level year-end transition detection which is more accurate
                    #
                    # # Simple year-end section detection: only skip if "December 31" of prior year appears
                    # # Don't try to be too clever - let table-level filtering handle the rest
                    # 
                    # # Check for December 31 of prior year in section header
                    # dec31_match = re.search(r'december\s+31[^\d]*(\d{4})', section_text, re.I)
                    # if dec31_match:
                    #     header_year = int(dec31_match.group(1))
                    #     if header_year < filing_year:
                    #         # Also check if current year appears - if so, it's comparative
                    #         if str(filing_year) not in section_text:
                    #             logger.info(f"Detected year-end ONLY section: December 31, {header_year}, skipping section")
                    #             continue
                    
                    # Find all tables that come after this schedule header
                    in_schedule_section = False
                    consecutive_non_investment_tables = 0
                    max_consecutive_non_investment = 3  # Allow up to 3 non-investment tables before stopping
                    
                    for i in range(header_idx, len(all_elements)):
                        element = all_elements[i]
                        
                        if i == header_idx:
                            in_schedule_section = True
                            continue
                        
                        if in_schedule_section:
                            # Check for stop markers (less aggressive - only stop on major section breaks)
                            if element.name in ['h1', 'h2', 'h3']:
                                text = element.get_text(strip=True).lower()
                                if self._is_stop_marker(text):
                                    # Only stop if it's a major section header (h1, h2, h3)
                                    logger.info(f"Found major stop marker in {doc.filename}: {element.get_text(strip=True)[:100]}, stopping this section")
                                    break
                            
                            # If it's a table, check if it's an investment table
                            if element.name == 'table':
                                table_text = element.get_text(separator=' ', strip=True).lower()
                                
                                # Expanded criteria: look for investment-related content
                                is_investment_table = self._is_investment_table(table_text)
                                
                                if is_investment_table:
                                    consecutive_non_investment_tables = 0  # Reset counter

                                    # Preceding elements often contain date (e.g. "Schedule - December 31, 2024")
                                    context_before = ' '.join(
                                        all_elements[j].get_text(strip=True)
                                        for j in range(max(0, i - 3), i)
                                    )

                                    # Find the corresponding table in our tables list
                                    table_html = str(element)
                                    # Quick check: extract first company name from table to match
                                    table_first_text = table_text[:300]  # First 300 chars should be enough

                                    for html, text, table_num, table_id in tables:
                                        # Match tables from this document (not just main doc)
                                        if table_id.startswith(doc.filename):
                                            # Fast match: check if first part of text matches (avoid slow HTML parsing)
                                            text_first = text.lower()[:300]
                                            if table_first_text[:100] in text_first or text_first[:100] in table_first_text:
                                                if table_num not in processed_table_indices:
                                                    # Double-check with year-end filter (include caption context)
                                                    if not self._is_year_end_table(html, text, filing_date, context_before, filing_type, period_end_date):
                                                        investment_tables.append((html, text, table_num, table_id))
                                                        logger.info(f"Added investment schedule table {table_num} from {doc.filename} ({table_id})")
                                                    else:
                                                        logger.info(f"Skipping year-end table {table_num} from {doc.filename} ({table_id})")
                                                    processed_table_indices.add(table_num)
                                                break  # Found match, move to next element
                                else:
                                    consecutive_non_investment_tables += 1
                                    # If we hit too many non-investment tables in a row, we might have left the section
                                    if consecutive_non_investment_tables >= max_consecutive_non_investment:
                                        # But don't break - continue to next schedule header
                                        logger.debug(f"Hit {consecutive_non_investment_tables} non-investment tables, but continuing")
            
            except Exception as e:
                logger.warning(f"Error processing document {doc.filename} for table detection: {e}, continuing to next document")
                continue
        
        # If we found tables, return them
        if investment_tables:
            logger.info(f"Found {len(investment_tables)} tables in investment schedule section(s) across all documents")
            return investment_tables
        
        # Fallback to content-based detection if no headers found
        logger.info("No schedule headers found in any document, using fallback detection")
        return self._fallback_table_detection(tables, filing_date, filing_type, period_end_date)
    
    def _is_investment_table(self, table_text: str) -> bool:
        """
        Determine if a table is an investment table based on content.
        Uses expanded criteria to catch more tables.
        
        Args:
            table_text: Lowercase text content of the table
            
        Returns:
            True if this appears to be an investment table
        """
        # Primary indicators (any of these strongly suggests investment table)
        primary_indicators = [
            'portfolio company',
            'company name',
            'portfolio company name',
            'investment company',
            'issuer name'
        ]
        
        # Secondary indicators (need multiple)
        secondary_indicators = [
            'principal',
            'fair value',
            'amortized cost',
            'interest rate',
            'maturity',
            'acquisition date',
            'senior secured',
            'first lien',
            'common equity',
            'preferred equity',
            'spread',
            'reference rate',
            'sofr',
            'libor'
        ]
        
        # Check for primary indicators
        has_primary = any(indicator in table_text for indicator in primary_indicators)
        
        # Check for secondary indicators (need at least 2)
        secondary_count = sum(1 for indicator in secondary_indicators if indicator in table_text)
        has_secondary = secondary_count >= 2
        
        # Also check for financial data patterns
        has_financial_data = (
            ('$' in table_text or 'percent' in table_text or '%' in table_text) and
            (any(term in table_text for term in ['llc', 'inc.', 'corp', 'ltd', 'lp', 'holdings', 'acquisition']))
        )
        
        # Table is investment-related if it has primary indicator OR (secondary + financial data)
        is_investment = has_primary or (has_secondary and has_financial_data)
        
        if is_investment:
            logger.debug(f"Table identified as investment table (primary: {has_primary}, secondary: {secondary_count}, financial: {has_financial_data})")
        
        return is_investment
    
    
    def _tables_match(self, html1, html2):
        """Check if two table HTMLs are similar enough to be the same table."""
        # Fast comparison: extract first few rows of text without full parsing
        import re
        # Extract text from first few table rows using regex (much faster than BeautifulSoup)
        def extract_first_text(html, max_chars=200):
            # Remove HTML tags and get first N characters
            text = re.sub(r'<[^>]+>', ' ', html)
            text = ' '.join(text.split())[:max_chars]
            return text.lower()
        
        text1 = extract_first_text(html1)
        text2 = extract_first_text(html2)
        
        # If first 200 chars are very similar, likely the same table
        return text1 == text2 or (len(text1) > 50 and text1[:50] == text2[:50])
    
    def _is_stop_marker(self, text):
        """Check if text indicates we've reached the end of investment schedule.
        Made less aggressive to avoid stopping too early."""
        # Only stop on major section headers, not minor notes
        stop_markers = [
            'consolidated statements of operations',
            'consolidated statement of operations',
            'consolidated balance sheets',
            'consolidated balance sheet',
            'consolidated statements of cash flows',
            'consolidated statement of cash flows',
            'management\'s discussion and analysis',
            'md&a',
            # Only stop on "Note 1" if it's clearly a major section
            # (we'll be more lenient with other notes)
        ]
        text_lower = text.lower().strip()
        
        # Only match if it's a clear major section header
        # Don't stop on generic "Note" references
        for marker in stop_markers:
            if marker in text_lower:
                # Make sure it's not just a footnote reference
                if not (text_lower.startswith('(') and text_lower.endswith(')')):
                    return True
        
        return False
    
    def _fallback_table_detection(self, tables, filing_date: str = None, filing_type: str = "10-Q", period_end_date: str = None):
        """Fallback: simple content-based detection with expanded criteria."""
        investment_tables = []
        for html, text, table_num, table_id in tables:
            text_lower = text.lower()
            
            # Use the same expanded criteria as the main detection
            if self._is_investment_table(text_lower):
                # Filter out year-end tables if filing_date is provided (only for 10-Q)
                if filing_date and self._is_year_end_table(html, text, filing_date, "", filing_type, period_end_date):
                    logger.info(f"Fallback: Skipping year-end table {table_num}")
                    continue
                investment_tables.append((html, text, table_num, table_id))
                logger.info(f"Fallback: Added table {table_num} with investment content")
        
        return investment_tables

    def extract_table_context(self, html_content: str, table_index: int) -> str:
        """
        Extract context around a table to help with classification.

        Args:
            html_content: Full HTML content
            table_index: Index of the table to get context for

        Returns:
            Context text around the table
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        tables = soup.find_all('table')

        if table_index >= len(tables):
            return ""

        table = tables[table_index]

        # Get text from preceding elements (headers, paragraphs)
        context_parts = []

        # Look for preceding header elements
        current = table.previous_sibling
        context_chars = 0
        max_chars = 1000

        while current and context_chars < max_chars:
            if current.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p']:
                text = current.get_text(strip=True)
                if text:
                    context_parts.insert(0, text)
                    context_chars += len(text)
            elif current.name == 'table':
                # Stop at previous table
                break
            current = current.previous_sibling

        return ' '.join(context_parts)

    def parse_table_to_rows(self, table_html: str) -> Optional[List[List[str]]]:
        """
        Parse HTML table into a grid of cell strings (header + data rows).
        Same logic as _clean_table_html but returns rows for use by custom scrapers.

        Args:
            table_html: Raw HTML table

        Returns:
            List of rows, each row a list of cell strings; first row is header.
            None if table could not be parsed.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(table_html, 'html.parser')
        table = soup.find('table')

        if not table:
            return None

        for element in table.find_all(style=True):
            del element['style']
        for element in table.find_all(['ix:nonfraction', 'ix:nonnumeric', 'span', 'div', 'font', 'sup', 'sub', 'br']):
            element.replace_with(element.get_text())
        for tag in table.find_all(True):
            attrs_to_keep = {a: v for a, v in tag.attrs.items() if a in ['colspan', 'rowspan']}
            tag.attrs = attrs_to_keep

        rows = []
        for tr in table.find_all('tr'):
            row_data = []
            for cell in tr.find_all(['th', 'td']):
                text = cell.get_text(strip=True)
                text = text.replace('\u00a0', ' ')
                text = text.replace('\u2014', '-').replace('\u2013', '-')
                text = text.replace('\u2019', "'").replace('\u201c', '"').replace('\u201d', '"')
                text = text.replace('—', '-').replace('–', '-')
                text = re.sub(r'\s*\(\d+\)\s*$', '', text)
                text = ' '.join(text.split())
                row_data.append(text)
                colspan = int(cell.get('colspan', 1))
                if colspan > 1:
                    row_data.extend([''] * (colspan - 1))
            if row_data and any(cell.strip() for cell in row_data):
                rows.append(row_data)

        if len(rows) >= 2:
            rows = self._collapse_spacer_columns(rows)
        return rows if (rows and len(rows) >= 2) else None

    def _clean_table_html(self, table_html: str) -> str:
        """
        Aggressively clean and simplify HTML table for better LLM parsing.

        Args:
            table_html: Raw HTML table

        Returns:
            Cleaned markdown table
        """
        rows = self.parse_table_to_rows(table_html)
        if not rows or len(rows) < 2:
            return "Could not extract clean table data"

        headers = rows[0]
        markdown_lines = [
            '| ' + ' | '.join(headers) + ' |',
            '| ' + ' | '.join(['---'] * len(headers)) + ' |',
        ]
        for row_data in rows[1:]:
            while len(row_data) < len(headers):
                row_data.append('')
            row_data = row_data[:len(headers)]
            cleaned_row = [re.sub(r'^[-\s]*$', '', cell.strip()) for cell in row_data]
            markdown_lines.append('| ' + ' | '.join(cleaned_row) + ' |')
        return '\n'.join(markdown_lines)

    def _collapse_spacer_columns(self, rows: List[List[str]]) -> List[List[str]]:
        """
        Remove spacer columns and merge value+unit columns from SEC table data.

        SEC HTML tables often use many empty "spacer" columns for visual alignment,
        and split values from their units (e.g., "5.61" | "%" in separate columns).
        This collapses the table to only meaningful columns.

        Args:
            rows: List of row data (first row is header)

        Returns:
            Cleaned rows with spacer columns removed and units merged
        """
        if not rows or len(rows) < 2:
            return rows

        # Normalize row lengths to the max
        max_cols = max(len(r) for r in rows)
        for r in rows:
            while len(r) < max_cols:
                r.append('')

        # Step 1: Identify spacer columns (empty in >80% of data rows)
        # Never collapse columns that look like rate/interest/spread/PIK (often mostly empty for equity rows)
        data_rows = rows[1:]  # Skip header
        num_data = len(data_rows)
        if num_data == 0:
            return rows

        RATE_HEADER_KEYWORDS = (
            "rate", "spread", "pik", "interest", "reference", "margin", "coupon", "index"
        )

        spacer_cols = set()
        for col_idx in range(max_cols):
            header = (rows[0][col_idx] or "").lower()
            if any(kw in header for kw in RATE_HEADER_KEYWORDS):
                continue  # Keep this column even if mostly empty
            empty_count = sum(1 for r in data_rows if not r[col_idx].strip())
            if empty_count / num_data > 0.80:
                spacer_cols.add(col_idx)

        # Remove spacer columns from all rows
        keep_cols = [i for i in range(max_cols) if i not in spacer_cols]
        rows = [[r[i] for i in keep_cols] for r in rows]

        if not rows or not rows[0]:
            return rows

        # Step 2: Merge value+unit columns
        # Scan for columns that are frequently just a unit symbol (%, $)
        data_rows = rows[1:]
        num_cols = len(rows[0])
        unit_cols = set()

        for col_idx in range(num_cols):
            col_values = [r[col_idx].strip() for r in data_rows if r[col_idx].strip()]
            if not col_values:
                continue
            unit_count = sum(1 for v in col_values if v in ('%', '$'))
            if len(col_values) > 0 and unit_count / len(col_values) > 0.5:
                unit_cols.add(col_idx)

        if unit_cols:
            # Merge unit columns with their adjacent value column
            merged_rows = []
            for row in rows:
                new_row = []
                skip_next = False
                for col_idx in range(num_cols):
                    if skip_next:
                        skip_next = False
                        continue

                    cell = row[col_idx].strip()

                    # Check if next column is a unit column — merge into this cell
                    if col_idx + 1 < num_cols and (col_idx + 1) in unit_cols:
                        unit = row[col_idx + 1].strip()
                        if unit == '$':
                            new_row.append(f"${cell}" if cell else cell)
                        elif unit == '%':
                            new_row.append(f"{cell}%" if cell else cell)
                        else:
                            new_row.append(cell)
                        skip_next = True
                    # Check if THIS column is a unit column and previous wasn't handled
                    elif col_idx in unit_cols:
                        # Unit column with no preceding value — already merged or standalone
                        # Check if previous column exists and wasn't a value that got merged
                        if new_row:
                            prev = new_row[-1]
                            if cell == '$':
                                new_row[-1] = f"${prev}" if prev else ''
                            elif cell == '%':
                                new_row[-1] = f"{prev}%" if prev else ''
                            else:
                                new_row.append(cell)
                        else:
                            new_row.append(cell)
                    else:
                        new_row.append(cell)

                merged_rows.append(new_row)
            rows = merged_rows

        if rows:
            removed = max_cols - len(rows[0])
            if removed > 0:
                logger.info(f"Collapsed table from {max_cols} to {len(rows[0])} columns (removed {len(spacer_cols)} spacer cols, merged {len(unit_cols)} unit cols)")

        return rows

    # Max rows per LLM call - tables larger than this are chunked
    ROWS_PER_CHUNK = 100

    def _chunk_markdown_table(self, markdown_table: str) -> List[str]:
        """
        Split a large markdown table into chunks, each with the header.
        Returns a list of markdown table strings.
        """
        lines = markdown_table.split('\n')
        if len(lines) <= 2:
            return [markdown_table]

        header_lines = lines[:2]  # header + separator
        data_lines = lines[2:]

        if len(data_lines) <= self.ROWS_PER_CHUNK:
            return [markdown_table]

        chunks = []
        for i in range(0, len(data_lines), self.ROWS_PER_CHUNK):
            chunk_data = data_lines[i:i + self.ROWS_PER_CHUNK]
            chunk_md = '\n'.join(header_lines + chunk_data)
            chunks.append(chunk_md)

        logger.info(f"Split table with {len(data_lines)} rows into {len(chunks)} chunks of ~{self.ROWS_PER_CHUNK} rows")
        return chunks

    def _call_gemini(self, prompt: str, table_number: int) -> str:
        """Make a single Gemini API call and return the response text."""
        import time
        
        logger.debug(f"🤖 Sending to LLM: {len(prompt)} chars prompt for table {table_number}")

        generation_config = genai.types.GenerationConfig(
            temperature=0.1,
            max_output_tokens=16000,
            top_p=0.8,
            top_k=40
        )

        start_time = time.time()

        response = self.gemini_model.generate_content(
            prompt,
            generation_config=generation_config
        )

        elapsed = time.time() - start_time
        logger.info(f"LLM call completed in {elapsed:.1f} seconds")
        
        response_text = response.text.strip()
        
        logger.debug(f"🤖 LLM returned: {len(response_text)} chars")
        if len(response_text) < 500:
            logger.warning(f"⚠️  Short LLM response ({len(response_text)} chars): {response_text}")
        else:
            logger.debug(f"   First 300 chars: {response_text[:300]}")

        return response_text

    def parse_table_with_llm(self, table_html: str, table_text: str, table_number: int,
                           filing_info: Dict[str, Any], previous_table_csv: Optional[str] = None) -> Optional[TableExtractionResult]:
        """
        Use LLM to parse an HTML table into structured CSV data.
        Large tables are automatically chunked into multiple LLM calls.

        Args:
            table_html: Raw HTML of the table
            table_text: Plain text content of the table
            table_number: Sequential number of the table
            filing_info: Information about the filing (ticker, date, etc.)

        Returns:
            TableExtractionResult if successful, None otherwise
        """
        # Log table details for debugging
        logger.info(f"📊 Table {table_number}: {len(table_html)} chars HTML, {len(table_text)} chars text")
        logger.debug(f"   First 300 chars of text: {table_text[:300]}")
        logger.debug(f"   Table has {table_html.count('<tr>')} rows (HTML <tr> count)")
        
        # Clean the table HTML before sending to LLM
        cleaned_table = self._clean_table_html(table_html)

        # Split into chunks if table is large
        chunks = self._chunk_markdown_table(cleaned_table)

        try:
            all_csv_rows = []  # Collects all parsed CSV rows across chunks
            chunk_count = len(chunks)

            for chunk_idx, chunk_md in enumerate(chunks):
                if chunk_count > 1:
                    logger.info(f"Processing chunk {chunk_idx + 1}/{chunk_count} for table {table_number}")

                prompt = self._build_llm_prompt(chunk_md, table_text, table_number, filing_info, previous_table_csv)

                try:
                    llm_response = self._call_gemini(prompt, table_number)
                except Exception as e:
                    logger.error(f"LLM call failed for table {table_number} chunk {chunk_idx + 1}: {e}")
                    continue

                csv_content, confidence_score, metadata = self._parse_llm_response(llm_response)

                if csv_content:
                    csv_lines = csv_content.strip().split('\n')
                    if chunk_idx == 0:
                        # First chunk: include header + data
                        all_csv_rows.extend(csv_lines)
                    else:
                        # Subsequent chunks: skip header row
                        all_csv_rows.extend(csv_lines[1:])

            if not all_csv_rows:
                return None

            combined_csv = '\n'.join(all_csv_rows)
            confidence_score = self._calculate_confidence_score(combined_csv)
            total_data_rows = len(all_csv_rows) - 1  # minus header

            return TableExtractionResult(
                csv_content=combined_csv,
                table_number=table_number,
                confidence_score=confidence_score,
                metadata={'total_rows': total_data_rows, 'chunks': chunk_count}
            )

        except Exception as e:
            logger.error(f"Error calling LLM for table {table_number}: {e}")

        return None

    def _build_llm_prompt(self, table_html: str, table_text: str, table_number: int,
                         filing_info: Dict[str, Any], previous_table_csv: Optional[str] = None) -> str:
        """
        Build the LLM prompt for table parsing.

        Args:
            table_html: Raw HTML table
            table_text: Plain text table content
            table_number: Table number
            filing_info: Filing metadata (may include 'unit_scale': "thousands"|"millions"|"units")
            previous_table_csv: CSV from previous table for context

        Returns:
            Complete LLM prompt
        """
        # Truncate very large tables for the prompt (raised limit for chunked processing)
        if len(table_html) > 50000:
            table_html = table_html[:50000] + "...[truncated]"

        # Build unit scale instruction
        unit_instruction = ""
        scale = filing_info.get('unit_scale', 'units')
        if scale == "thousands":
            unit_instruction = """
IMPORTANT: This table reports dollar amounts IN THOUSANDS. Extract the numbers exactly as shown in the table. Do NOT multiply or divide. The raw numbers from the table are already in the correct unit (thousands).
"""
        elif scale == "millions":
            unit_instruction = """
IMPORTANT: This table reports dollar amounts IN MILLIONS. Extract the numbers exactly as shown in the table. Do NOT multiply or divide. The raw numbers from the table are already in the correct unit (millions).
"""
        else:
            unit_instruction = """
IMPORTANT: Extract dollar amounts exactly as shown in the table. Do NOT multiply or divide. The raw numbers from the table are already in the correct unit.
"""

        # Scale consistency check
        scale_check = """
CRITICAL SCALE CHECK: If percent_of_net_assets for any single holding exceeds 15%, double-check that you read the correct column. BDC portfolios are diversified - single positions rarely exceed 10% of net assets.
"""

        # Build previous table context if available
        previous_context = ""
        if previous_table_csv:
            # Get last few rows from previous table to show context (industry, investment_type patterns)
            prev_lines = previous_table_csv.strip().split('\n')
            if len(prev_lines) > 1:
                # Show header + last 5 rows for context
                context_lines = prev_lines[:1] + prev_lines[-5:] if len(prev_lines) > 6 else prev_lines
                previous_context = f"""
PREVIOUS TABLE CONTEXT (for reference - industry and investment_type may span across tables):
{chr(10).join(context_lines)}

NOTE: If you see industry categories or investment types in the previous table, they may continue in this table.
"""
        
        prompt = f"""Extract investment data from this SEC filing table. The table has been cleaned and converted to markdown format.
{unit_instruction}
{scale_check}
OUTPUT: CSV with EXACTLY 16 columns in this exact order:
company_name,investment_type,industry,cash_rate,pik_rate,reference_rate,spread,acquisition_date,maturity_date,principal_amount,amortized_cost,fair_value,percent_of_net_assets,cost,commitment_limit,undrawn_commitment

CRITICAL: Every row MUST have exactly 16 comma-separated values. If a field is empty, use an empty string (nothing between commas).
CRITICAL NO-COMMA RULE: DO NOT USE COMMAS INSIDE ANY FIELD VALUES. If the original table has commas inside text (for example, in company names like "Douglas Holdings, Inc." or "Kar Wash Holdings, LLC"), REPLACE THOSE COMMAS WITH SPACES (e.g., "Douglas Holdings Inc", "Kar Wash Holdings LLC"). The ONLY commas in your entire response should be the separators between the 16 columns.

COLUMN DEFINITIONS:
1. company_name: Portfolio company name. KEEP IT CONCISE. Remove unnecessary legal suffixes if redundant (e.g., "ABC Company, LLC" -> "ABC Company"). If the name is very long with multiple parentheticals, keep only the primary name. REQUIRED.
2. investment_type: Loan/Equity type. Use STANDARDIZED values ONLY: "First Lien", "Revolver", "Delayed Draw", "Second Lien", "Subordinated Debt", "Unsecured Debt", "Common Equity", "Preferred Equity", "Partnership Interest", "Warrants", "Unfunded Commitment", "Structured Note", "Money Market Fund", "Other Debt", or "Other". DO NOT use verbose descriptions like "First lien senior secured loan due 2028" - use ONLY "First Lien".
3. industry: Business sector. Use STANDARDIZED values ONLY: "Software", "Healthcare Services", "Pharmaceuticals & Biotechnology", "Medical Devices & Equipment", "Healthcare Technology", "Financial Services", "Insurance", "Real Estate", "Business Services", "Manufacturing", "Industrial Services", "Aerospace & Defense", "Automotive", "Construction & Engineering", "Chemicals", "Metals & Mining", "Media & Entertainment", "Marketing & Advertising", "Education", "Legal Services", "Consumer Products", "Food & Beverage", "Retail", "Apparel & Textiles", "Hotels & Leisure", "Transportation & Logistics", "Airlines", "Oil & Gas", "Energy & Utilities", "Energy Services", "Packaging", "Environmental Services", "Telecommunications", "Technology Hardware", "Internet & Media", "Investment Vehicles", "Diversified", "Agriculture", or "Other".
4. cash_rate: Cash interest rate only (e.g., "8.5%", "10.25%") - extract from combined rates like "8.5% Cash/ 2.0% PIK", leave blank if no cash rate
5. pik_rate: PIK interest rate only (e.g., "2.0%", "1.5%") - extract from combined rates, leave blank if no PIK
6. reference_rate: Base rate. Use STANDARDIZED values ONLY: "SOFR", "SOFR (Q)", "SOFR (M)", "SOFR (S)", "LIBOR", "LIBOR (Q)", "LIBOR (M)", "Euribor", "Prime", "Prime (Q)", "SONIA", "CDOR", "BKBM", or "N/A". Convert variations like "SF", "SF+", "L", "P" to standard names ("SOFR", "LIBOR", "Prime"). Preserve period indicators in parentheses if present (Q=Quarterly, M=Monthly, S=Semi-annual).
7. spread: Spread over reference rate (e.g., "5.0%", "4.75%", "6.25%")
8. acquisition_date: Date in YYYY-MM-DD format, leave blank if not available
9. maturity_date: Date in YYYY-MM-DD format, leave blank if not available
10. principal_amount: Loan principal amount (numbers only, no $ signs, no commas)
11. amortized_cost: Book value (numbers only, no $ signs, no commas)
12. fair_value: Market value (numbers only, no $ signs, no commas)
13. percent_of_net_assets: Percentage (numbers only, no % signs, e.g., "1.2" not "1.2%")
14. cost: Cost basis (numbers only)
15. commitment_limit: Commitment limit (numbers only)
16. undrawn_commitment: Undrawn commitment (numbers only)

EXCLUDE THESE ROWS:
- Headers and subheaders (rows that are just column names)
- Industry category headers (rows that are ONLY industry names like "High Tech Industries", "Healthcare", "Automotive" without a company name)
- Investment type category headers (rows that are ONLY investment types like "Senior Secured Loans", "Common Equity" without a company name)
- Total/summary rows (containing words like "Total", "Aggregate", "Summary")
- Footnotes and references
- Empty rows or rows with no company name
- Rows that are just dates or numbers without company context

ONLY INCLUDE: Individual investment positions with actual company names (must have LLC, Inc., Corp, LP, etc. or be a recognizable company name)
CRITICAL: If a row only contains an industry name (like "High Tech Industries") or investment type (like "Senior Secured Loans") without a company name, DO NOT include it as a data row.
Leave empty fields as empty strings (nothing between commas, e.g., ",," not ", ,")
CRITICAL: Output ONLY raw CSV data - NO markdown code blocks (no ``` markers), NO explanations, NO text before or after the CSV. Start directly with the header row "company_name,investment_type,..."

TABLE CONTEXT:
- Filing: {filing_info.get('ticker', 'Unknown')} {filing_info.get('filing_type', 'Unknown')} for {filing_info.get('filing_date', 'Unknown')}
- Table {table_number + 1} extracted from SEC filing
{previous_context}
CLEANED TABLE (Markdown format):
{table_html}

INSTRUCTIONS:
1. Analyze the markdown table structure and identify which columns contain which data types
2. Map the data to the 16 required CSV columns in the exact order specified above
3. Clean and normalize the data:
   - Remove $ signs and commas from numbers
   - Convert dates to YYYY-MM-DD format
   - Remove % signs from percentages (keep just the number)
   - Split combined rates like "8.5% Cash/ 2.0% PIK" into cash_rate="8.5%" and pik_rate="2.0%"
4. Only include actual investment rows - skip headers, totals, and footnotes
5. If a field is missing, leave it as an empty string (nothing between commas)
6. Output pure CSV data only - start with the header row, then data rows
7. If industry or investment_type appears in header rows above the data, apply it to all rows below until a new category appears

FILL-DOWN RULES (critical for multi-row positions):
- DO fill down company_name and industry (and investment_type when from a section header): When the source table has a blank cell in the company/sector column for a continuation row (same issuer, multiple tranches or lines), copy the value from the row above so every output row has a company name and industry.
- DO NOT fill down rates or numbers: cash_rate, pik_rate, reference_rate, spread, principal_amount, amortized_cost, fair_value, cost, percent_of_net_assets, dates, commitment_limit, undrawn_commitment must come ONLY from that row in the source table. If the source cell is blank or the value is for a different tranche, leave the field blank or use only the value that appears in that row. Never copy a rate or numeric value from a previous row into the current row.

CRITICAL FORMATTING REQUIREMENTS:
- Every row MUST have exactly 16 comma-separated values - NO MORE, NO LESS
- Do NOT add extra columns or values
- Do NOT use quotes around empty fields (use ,, not ,"",)
- DO NOT OUTPUT COMMAS INSIDE FIELD VALUES. If a value would normally contain a comma, replace it with a space so that parsers never see internal commas.
- Do NOT add trailing commas
- Do NOT add extra commas between fields
- Each row should look like: company_name,investment_type,industry,cash_rate,pik_rate,reference_rate,spread,acquisition_date,maturity_date,principal_amount,amortized_cost,fair_value,percent_of_net_assets,cost,commitment_limit,undrawn_commitment
- If a row has extra values at the end, remove them - only keep the first 16 values

DATA QUALITY REQUIREMENTS:
- company_name MUST be an actual company name (contains LLC, Inc., Corp, LP, etc. or is a recognizable business name)
- investment_type MUST use standardized values (e.g., "First Lien", "Second Lien", "Common Equity", "Preferred Equity") - NOT generic terms like "Loan" or "Equity"
- If you see a row that is ONLY an industry name (like "High Tech Industries") or ONLY an investment type category, SKIP IT - it's a header, not data

EXAMPLE OF CORRECT FORMAT:
company_name,investment_type,industry,cash_rate,pik_rate,reference_rate,spread,acquisition_date,maturity_date,principal_amount,amortized_cost,fair_value,percent_of_net_assets,cost,commitment_limit,undrawn_commitment
ABC Company LLC,First Lien,Healthcare Services,8.5%,,SOFR,5.0%,2023-01-15,2028-01-15,5000,4950,5000,2.5,,,,
XYZ Corp,Common Equity,Software,,,N/A,N/A,2022-06-01,,,1000,1200,0.6,1000,,
"""

        return prompt

    def _parse_llm_response(self, response: str) -> Tuple[Optional[str], float, Dict[str, Any]]:
        """
        Parse the LLM response to extract CSV content and metadata.

        Args:
            response: Raw LLM response

        Returns:
            Tuple of (csv_content, confidence_score, metadata)
        """
        # Strip markdown code blocks if present
        response = response.strip()
        if response.startswith('```'):
            # Remove opening code block marker
            lines = response.split('\n')
            if lines[0].strip().startswith('```'):
                lines = lines[1:]
            # Remove closing code block marker if present
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            response = '\n'.join(lines)
        
        # Look for CSV content in the response
        lines = response.strip().split('\n')

        # Find the start of CSV data (should start with headers)
        csv_start = -1
        for i, line in enumerate(lines):
            if line.strip().startswith('company_name,'):
                csv_start = i
                break

        if csv_start == -1:
            logger.warning("❌ No CSV headers found in LLM response")
            logger.warning(f"   Response length: {len(response)} chars")
            logger.warning(f"   First 500 chars: {response[:500]}")
            logger.warning(f"   Last 500 chars: {response[-500:]}")
            return None, 0.0, {}

        # Extract CSV content
        csv_lines = lines[csv_start:]

        # Validate CSV structure
        if len(csv_lines) < 2:  # Need at least header + 1 data row
            logger.warning("❌ CSV content too short")
            logger.warning(f"   Only {len(csv_lines)} CSV lines found (need at least 2)")
            logger.warning(f"   Response length: {len(response)} chars")
            logger.warning(f"   Full response:\n{response}")
            return None, 0.0, {}

        # Parse header to get expected column count (should be 16)
        import csv
        from io import StringIO
        
        expected_columns = 16  # Fixed: we always expect 16 columns
        valid_rows = []
        header_row = None
        
        # Use proper CSV parsing to handle quoted fields
        # IMPORTANT: If a line doesn't have quotes and has commas in company names,
        # csv.reader will split incorrectly. We need to detect and fix this.
        for i, line in enumerate(csv_lines):
            try:
                # Parse the line as CSV to handle quoted fields properly
                reader = csv.reader(StringIO(line))
                cols = list(reader)[0]
                
                # First line should be header
                if i == 0:
                    header_row = line
                    # Validate header has correct columns
                    if len(cols) != expected_columns:
                        logger.warning(f"Header has {len(cols)} columns, expected {expected_columns}. Attempting to repair...")
                        # Try to fix header if close
                        if len(cols) > expected_columns:
                            cols = cols[:expected_columns]
                        elif len(cols) < expected_columns:
                            cols.extend([''] * (expected_columns - len(cols)))
                        header_row = ','.join(cols)
                    valid_rows.append(header_row)
                    continue
                
                # Check if parsing resulted in too many columns (likely due to unquoted commas in company name)
                # If we have more than expected_columns, try to merge split company name
                if len(cols) > 16 and i > 0:  # Skip header, expected_columns is 16
                    # Try to merge split company name (cols[0] and cols[1] if cols[1] ends with company suffix)
                    if len(cols) > 1:
                        col1 = cols[1].strip()
                        if col1 and any(col1.endswith(suffix) for suffix in ['.', 'Inc.', 'LLC', 'Corp.', 'LP', 'Ltd.', 'Company', 'Ltd']):
                            # Merge cols[0] and cols[1] into company name
                            cols[0] = f"{cols[0]}, {col1}"
                            cols = [cols[0]] + cols[2:]
                
                # Repair data rows with wrong column counts
                if len(cols) != expected_columns:
                    cols = self._repair_csv_row(cols, expected_columns, line)
                
                if len(cols) == expected_columns:
                    # Validate that this is not just an industry header or invalid row
                    if self._is_valid_data_row(cols):
                        # Reconstruct the row properly - csv.writer with QUOTE_MINIMAL will quote fields with commas
                        output = StringIO()
                        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator='')
                        writer.writerow(cols)
                        repaired_line = output.getvalue()
                        valid_rows.append(repaired_line)
                    else:
                        logger.debug(f"Filtered out invalid row (likely industry header): {cols[0] if cols else 'empty'}")
                else:
                    logger.warning(f"Could not repair row with {len(cols)} columns: {line[:100]}...")
                    
            except Exception as e:
                logger.warning(f"Error parsing CSV line: {e}, line: {line[:100]}...")
                continue

        if len(valid_rows) < 2:
            logger.warning("No valid CSV rows after validation")
            return None, 0.0, {}

        # Post-extraction validation: fix obviously wrong numeric values
        valid_rows = self._validate_numeric_fields(valid_rows)

        csv_content = '\n'.join(valid_rows)

        # Calculate confidence based on data quality
        confidence = self._calculate_confidence_score(csv_content)

        metadata = {
            'total_rows': len(valid_rows) - 1,  # Subtract header
            'columns': expected_columns,
            'parsing_timestamp': datetime.now().isoformat()
        }

        return csv_content, confidence, metadata

    def _validate_numeric_fields(self, rows: List[str]) -> List[str]:
        """
        Post-extraction validation to catch obviously wrong values.
        Fixes or clears fields that are clearly mis-parsed (e.g., dollar amounts
        in the percent_of_net_assets column).

        Column indices (0-based):
          9=principal_amount, 10=amortized_cost, 11=fair_value,
          12=percent_of_net_assets, 13=cost
        """
        import csv
        from io import StringIO

        if not rows:
            return rows

        validated = [rows[0]]  # Keep header
        fixes_count = 0

        for row_str in rows[1:]:
            try:
                reader = csv.reader(StringIO(row_str))
                cols = list(reader)[0]
            except Exception:
                validated.append(row_str)
                continue

            if len(cols) < 16:
                validated.append(row_str)
                continue

            fixed = False

            # Validate percent_of_net_assets (col 12)
            # Should be a small number, typically -5 to 30. Values > 50 or < -10
            # are almost certainly dollar amounts leaked from adjacent columns.
            pct_str = cols[12].strip()
            if pct_str:
                try:
                    pct_val = float(pct_str.replace(',', '').replace('%', ''))
                    if abs(pct_val) > 50:
                        logger.debug(f"Clearing bad percent_of_net_assets={pct_val} for {cols[0]}")
                        cols[12] = ''
                        fixed = True
                except (ValueError, TypeError):
                    cols[12] = ''
                    fixed = True

            # Validate fair_value (col 11) - should not be negative
            fv_str = cols[11].strip()
            if fv_str:
                try:
                    fv_val = float(fv_str.replace(',', ''))
                    if fv_val < -1000:
                        logger.debug(f"Clearing suspicious negative fair_value={fv_val} for {cols[0]}")
                        cols[11] = ''
                        fixed = True
                except (ValueError, TypeError):
                    pass

            # Validate amortized_cost (col 10) - should not be wildly negative
            ac_str = cols[10].strip()
            if ac_str:
                try:
                    ac_val = float(ac_str.replace(',', ''))
                    if ac_val < -1000:
                        logger.debug(f"Clearing suspicious negative amortized_cost={ac_val} for {cols[0]}")
                        cols[10] = ''
                        fixed = True
                except (ValueError, TypeError):
                    pass

            if fixed:
                fixes_count += 1
                output = StringIO()
                writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator='')
                writer.writerow(cols)
                validated.append(output.getvalue())
            else:
                validated.append(row_str)

        if fixes_count > 0:
            logger.info(f"Post-extraction validation: fixed {fixes_count} rows with bad numeric values")

        return validated

    def _is_valid_data_row(self, cols: list) -> bool:
        """
        Check if a row is a valid data row (not just an industry header or invalid entry).
        
        Args:
            cols: List of column values
            
        Returns:
            True if valid data row, False if should be filtered out
        """
        if not cols or len(cols) < 3:
            return False
        
        # Handle misaligned columns due to commas in company names
        # If cols[1] looks like part of a company name (ends with "Inc.", "LLC", contains parentheses, etc.), 
        # then the actual investment_type is likely in cols[2]
        company_name = cols[0].strip().strip('"').strip()
        investment_type = ""
        
        # Check if cols[1] is part of company name (misaligned due to comma in name)
        if len(cols) > 1:
            col1 = cols[1].strip().strip('"').strip()
            # Check if col1 looks like part of a company name:
            # - Ends with company suffix (Inc., LLC, Corp., etc.)
            # - Contains parentheses (e.g., "(Class A units)", "(Delayed Draw)")
            # - Starts with space and company suffix
            is_part_of_company_name = False
            if col1:
                if any(col1.endswith(suffix) for suffix in ['.', 'Inc.', 'LLC', 'Corp.', 'LP', 'Ltd.', 'Company', ')']):
                    is_part_of_company_name = True
                elif '(' in col1 or col1.startswith(' '):
                    # Contains parentheses or starts with space (likely continuation of company name)
                    is_part_of_company_name = True
                elif any(suffix in col1 for suffix in ['Inc', 'LLC', 'Corp', 'LP', 'Ltd']):
                    # Contains company suffix anywhere
                    is_part_of_company_name = True
            
            if is_part_of_company_name and len(cols) > 2:
                # Company name has comma, investment_type is in cols[2]
                investment_type = cols[2].strip().strip('"').strip()
                # Reconstruct full company name
                company_name = f"{company_name}, {col1}"
            else:
                # Normal case: investment_type is in cols[1]
                investment_type = col1
        
        # Filter out rows that are just industry headers
        # Industry headers typically don't have company suffixes and are often just category names
        industry_only_patterns = [
            'industries', 'industry', 'sector', 'category', 'group',
            'healthcare', 'technology', 'automotive', 'services', 'media',
            'consumer goods', 'capital equipment', 'construction', 'retail'
        ]
        
        # Check if company_name looks like just an industry header
        company_lower = company_name.lower()
        if any(pattern in company_lower for pattern in industry_only_patterns):
            # If it doesn't have a company suffix and no other data, it's likely a header
            has_company_suffix = any(suffix in company_name for suffix in ['LLC', 'Inc', 'Corp', 'LP', 'Ltd', 'Company', 'Holdings', 'Group'])
            has_other_data = any(cols[i].strip() for i in range(3, min(12, len(cols))))  # Check for financial data
            
            if not has_company_suffix and not has_other_data:
                return False
        
        # Warn about generic investment types like "Loan" or "Equity" but keep the row
        # Some data with a generic type is better than no data
        investment_type_lower = investment_type.lower().strip()
        generic_types = ['loan', 'equity', 'debt']

        is_generic = (
            investment_type_lower in generic_types or
            (len(investment_type.split()) == 1 and investment_type_lower in generic_types)
        )

        if is_generic:
            logger.warning(f"Row has generic investment_type '{investment_type}' for '{company_name}' - ideally should be more specific (e.g., 'First Lien', 'Common Equity')")
        
        # Must have a company name
        if len(company_name) < 3:
            return False
        
        # Filter out common header patterns
        if company_name.upper() in ['COMPANY NAME', 'PORTFOLIO COMPANY', 'COMPANY', 'INVESTMENT', 'TOTAL', 'SUMMARY']:
            return False
        
        # Filter out markdown code block markers
        if company_name.strip() == '```' or company_name.strip().startswith('```'):
            return False
        
        return True
    
    def _repair_csv_row(self, cols: list, expected_columns: int, original_line: str) -> list:
        """
        Attempt to repair a CSV row with incorrect column count.
        
        Args:
            cols: Parsed columns from the row
            expected_columns: Expected number of columns (16)
            original_line: Original line for logging
            
        Returns:
            Repaired list of columns
        """
        if len(cols) == expected_columns:
            return cols
        
        # If too many columns, try to remove trailing columns
        if len(cols) > expected_columns:
            # First, try to remove trailing empty columns
            while len(cols) > expected_columns and cols[-1].strip() == '':
                cols.pop()
            
            # If still too many, truncate to expected (remove extra values at the end)
            if len(cols) > expected_columns:
                logger.debug(f"Truncating row from {len(cols)} to {expected_columns} columns (removed: {cols[expected_columns:]})")
                cols = cols[:expected_columns]
        
        # If too few columns, pad with empty strings
        if len(cols) < expected_columns:
            missing = expected_columns - len(cols)
            logger.debug(f"Padding row from {len(cols)} to {expected_columns} columns (adding {missing} empty fields)")
            cols.extend([''] * missing)
        
        return cols
    
    def _get_table_context(self, html_content: str, table_index: int) -> str:
        """
        Extract context around a table for better classification.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_content, 'html.parser')
        tables = soup.find_all('table')

        if table_index >= len(tables):
            return ""

        table = tables[table_index]

        # Get text from preceding elements (headers, paragraphs)
        context_parts = []
        current = table.previous_sibling
        context_chars = 0
        max_chars = 1000

        while current and context_chars < max_chars:
            if current.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'p']:
                text = current.get_text(strip=True)
                if text:
                    context_parts.insert(0, text)
                    context_chars += len(text)
            elif current.name == 'table':
                break
            current = current.previous_sibling

        return ' '.join(context_parts)

    def _calculate_confidence_score(self, csv_content: str) -> float:
        """
        Calculate a confidence score for the extracted CSV data.

        Args:
            csv_content: The CSV content string

        Returns:
            Confidence score between 0 and 1
        """
        lines = csv_content.strip().split('\n')
        if len(lines) < 2:
            return 0.0

        total_rows = len(lines) - 1  # Subtract header
        if total_rows == 0:
            return 0.0

        # Check data quality indicators
        quality_indicators = []

        import csv
        from io import StringIO
        
        for line in lines[1:]:  # Skip header
            try:
                reader = csv.reader(StringIO(line))
                cols = next(reader)
                if len(cols) != 16:  # Should have 16 columns
                    continue

                # Check for meaningful data in key columns
                company_name = cols[0].strip().strip('"')
                investment_type = cols[1].strip().strip('"')
                # Numeric fields: principal_amount(9), amortized_cost(10), fair_value(11), cost(13)
                principal_amount = cols[9].strip().strip('"')
                amortized_cost = cols[10].strip().strip('"')
                fair_value = cols[11].strip().strip('"')
                cost = cols[13].strip().strip('"')
            except Exception:
                continue

            # Company name should not be empty and not look like a header or industry category
            has_company = (
                len(company_name) > 3 and
                not company_name.upper().startswith(('COMPANY', 'PORTFOLIO', 'TOTAL')) and
                # Check if it's actually a company (has LLC, Inc, Corp, etc.) or has financial data
                (any(suffix in company_name for suffix in ['LLC', 'Inc', 'Corp', 'LP', 'Ltd', 'Company', 'Holdings']) or
                 any(cols[i].strip() for i in range(9, min(12, len(cols)))))  # Has financial data
            )
            # Investment type should be specific, not generic
            has_investment_type = (
                len(investment_type) > 3 and
                investment_type.lower() not in ['loan', 'equity', 'debt']  # Must be more specific
            )
            # Check if ANY numeric field is populated (revolvers/unfunded may lack fair_value)
            has_numeric = any(v for v in [principal_amount, amortized_cost, fair_value, cost])

            row_quality = (has_company + has_investment_type + has_numeric) / 3
            quality_indicators.append(row_quality)

        if not quality_indicators:
            return 0.0

        avg_quality = sum(quality_indicators) / len(quality_indicators)

        # Bonus for having multiple rows
        volume_bonus = min(1.0, total_rows / 10)  # Cap at 10 rows

        confidence = (avg_quality * 0.8) + (volume_bonus * 0.2)

        return min(1.0, confidence)

    def process_historical_filings(self, ticker: str, filing_type: str = "10-Q",
                                   years_back: int = 1,
                                   skip_existing: bool = True,
                                   debt_only: bool = False) -> List[str]:
        """
        Process multiple historical filings for a ticker.

        Args:
            ticker: Company ticker symbol
            filing_type: Type of filing (10-Q, 10-K, etc.)
            years_back: Number of years to look back
            skip_existing: Skip filings that already have output CSVs
            debt_only: If True, filter out equity investments

        Returns:
            List of paths to generated CSV files
        """
        logger.info(f"Processing historical 10-Q + 10-K filings for {ticker} (last {years_back} years)")

        # Get historical 10-Q (Q1, Q2, Q3) and 10-K (Q4 / year-end) so we have 4 periods per year
        filings_10q = self.sec_client.get_historical_10q_filings(ticker, years_back=years_back)
        filings_10k = self.sec_client.get_historical_10k_filings(ticker, years_back=years_back)
        # Combine; each item has 'form' so we process 10-K with filing_type 10-K (keeps year-end tables)
        filings = list(filings_10q) + list(filings_10k)
        filings.sort(key=lambda f: f['date'])

        if not filings:
            return []

        # Group filings by year (one worker per year; all quarters within year processed by that worker)
        by_year: Dict[int, List[Dict[str, Any]]] = {}
        for f in filings:
            year = int(f['date'][:4])
            by_year.setdefault(year, []).append(f)
        # Sort quarters within each year by date
        for year in by_year:
            by_year[year].sort(key=lambda x: x['date'])
        years_sorted = sorted(by_year.keys())

        def process_one_year(year: int) -> List[str]:
            year_results = []
            for f in by_year[year]:
                filing_date = f['date']
                if skip_existing:
                    existing_file = self.output_dir / f"{ticker}_investments_{filing_date}.csv"
                    if existing_file.exists() and existing_file.stat().st_size > 100:
                        logger.info(f"SKIPPING {ticker} {filing_date} - already exists ({existing_file.stat().st_size:,} bytes)")
                        year_results.append(str(existing_file))
                        continue
                logger.info(f"[{year}] Processing {f.get('form', '10-Q')} from {filing_date} ({f['accession']})")
                csv_path = self.process_filing(
                    ticker, f.get('form', '10-Q'), index_url=f['index_url'], debt_only=debt_only
                )
                if csv_path:
                    year_results.append(csv_path)
            return year_results

        # One worker per year; all years in parallel
        num_workers = len(years_sorted)
        logger.info(f"Running {num_workers} year-workers in parallel (years {years_sorted[0]}-{years_sorted[-1]})")
        results = []
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            future_to_year = {executor.submit(process_one_year, y): y for y in years_sorted}
            for future in as_completed(future_to_year):
                y = future_to_year[future]
                try:
                    year_results = future.result()
                    results.extend(year_results)
                except Exception as e:
                    logger.exception(f"Year {y} failed: {e}")
        # Sort by filing date so output order is chronological
        results.sort(key=lambda p: Path(p).stem.replace(f"{ticker}_investments_", ""))
        return results

    def process_filing(self, ticker: str, filing_type: str = "10-Q",
                      year: Optional[int] = None, quarter: Optional[str] = None,
                      index_url: Optional[str] = None,
                      max_tables: Optional[int] = None,
                      workers: Optional[int] = None,
                      debt_only: bool = False,
                      detect_only: bool = False,
                      detect_to_file: Optional[str] = None) -> Optional[str]:
        """
        Process a single SEC filing to extract investment tables.

        Args:
            ticker: Company ticker symbol
            filing_type: Type of filing (10-Q, 10-K, etc.)
            year: Year to fetch filing for (ignored if index_url is provided)
            quarter: Quarter for 10-Q filings (Q1, Q2, Q3, Q4)
            index_url: Optional specific index URL to process
            debt_only: If True, filter out equity investments (Common/Preferred Equity, Warrants)
            detect_only: If True, run detection only (no LLM calls, no CSV output);
                         logs which tables are selected as current-quarter.
            detect_to_file: If set, run detection only and write selected table texts to a
                         single file with LLM-friendly structure (file header + [TABLE N] blocks
                         with table_id, table_num, length, then content). Use "" for default path
                         (output_dir/{ticker}_detect_{filing_date}.txt), or a path string.

        Returns:
            Path to generated CSV file, or None if no data extracted
        """
        logger.info(f"Processing {ticker} {filing_type} for {year or 'latest'}")

        try:
            # Get the filing index URL if not provided
            if not index_url:
                index_url = self.sec_client.get_filing_index_url(
                    ticker=ticker,
                    filing_type=filing_type,
                    year=year,
                    quarter=quarter
                )

            if not index_url:
                logger.warning(f"No {filing_type} filing found for {ticker}")
                return None

            # Extract accession number from URL
            accession_match = re.search(r'/(\d{10}-\d{2}-\d{6})', index_url)
            accession_number = accession_match.group(1) if accession_match else "unknown"

            # Fetch the actual filing content using the index URL
            # Optimization: Only download HTM files for table scraping
            filing_result = self.sec_client.fetch_filing_by_index_url(
                index_url=index_url,
                ticker=ticker,
                filing_type=filing_type,
                save_to_file=False,
                document_types=['.htm', '.html']
            )

            if not filing_result:
                logger.warning(f"Could not fetch filing content from {index_url}")
                return None

            logger.info(f"Retrieved {filing_type} filing: {accession_number}")

            # Extract tables from individual filing documents
            tables = self.extract_tables_from_filing_documents(filing_result)

            if not tables:
                logger.warning("No tables found in filing documents")
                return None

            # Process each table - collect rows with (row_str, table_idx, table_size) for dedup
            all_csv_data = []  # List of (row_str, table_idx, table_size)
            processed_tables = 0
            max_tables_to_process = max_tables  # None = process all tables

            # First pass: collect confidence scores
            # Initialize confidence_scores for fallback use
            confidence_scores = {}

            # Get period end date from filing result
            period_end_date = getattr(filing_result, 'period_end_date', None)
            if period_end_date:
                logger.info(f"📅 Period end date: {period_end_date}")
            else:
                logger.warning("No period end date available, using filing date as fallback")
            
            # Use the SIMPLE filtering method (avoids duplicate matching issues from HTML re-scanning)
            investment_tables = fallback_table_detection(
                tables,
                filing_result.filing_date,
                filing_type,
                period_end_date,
            )

            if not investment_tables:
                logger.warning("No investment schedule tables found using the new detection method")
                # Fallback to simple confidence-based method
                for table_html, table_text, table_num, table_id in tables:
                    # Get context for better classification
                    context = self._get_table_context(filing_result.text, table_num)

                    # Simple confidence calculation
                    text_lower = table_text.lower()
                    confidence = 0.0
                    if 'portfolio company' in text_lower:
                        confidence += 0.3
                    if 'principal' in text_lower and 'fair value' in text_lower:
                        confidence += 0.3
                    if 'interest rate' in text_lower:
                        confidence += 0.2
                    if any(term in text_lower for term in ['senior secured', 'first lien', 'common equity']):
                        confidence += 0.2

                    confidence_scores[table_num] = confidence

                investment_tables = [
                    (html, text, num, table_id)
                    for html, text, num, table_id in tables
                    if confidence_scores.get(num, 0) >= 0.4
                ]
                logger.info(f"Falling back to confidence-based method: found {len(investment_tables)} tables")

            if not investment_tables:
                logger.info("No investment tables found at all")
                return None

            # Sort by table size (larger tables likely have more investments)
            def table_sort_key(table_info):
                html, text, num, table_id = table_info
                text_length = len(text)

                # Prioritize larger tables
                size_score = min(1.0, text_length / 5000)  # Normalize to 0-1 scale

                # Boost for tables with many company indicators
                company_indicators = ['llc', 'inc.', 'corp', 'ltd.', 'lp', 'llp']
                company_count = sum(1 for indicator in company_indicators if indicator in text.lower())
                company_score = min(1.0, company_count / 10)  # Normalize

                return size_score + company_score

            sorted_tables = sorted(investment_tables, key=table_sort_key, reverse=True)
            tables_to_process = sorted_tables  # Process all found investment tables
            if max_tables_to_process and len(tables_to_process) > max_tables_to_process:
                tables_to_process = tables_to_process[:max_tables_to_process]
                logger.info(f"Limiting to {max_tables_to_process} tables for trial run")

            logger.info(
                f"Found {len(tables_to_process)} investment schedule tables, "
                "scanning for year-end transition..."
            )

            current_quarter_tables, _ = select_current_quarter_tables(
                tables_to_process,
                filing_result.filing_date,
                filing_type,
                period_end_date,
            )

            # If we're only debugging detection (or writing detect-to-file), log and optionally write, then stop
            if detect_only or detect_to_file is not None:
                logger.info("DETECT-ONLY mode: selected %d current-quarter tables", len(current_quarter_tables))
                for html, text, num, table_id in current_quarter_tables:
                    text_len = len(text)
                    snippet = text[:160].replace("\n", " ").replace("\r", " ")
                    logger.info(
                        "  [DETECT-ONLY] table_num=%s id=%s len=%s snippet=%r",
                        num,
                        table_id,
                        text_len,
                        snippet,
                    )
                if detect_to_file is not None:
                    out_path = (
                        Path(detect_to_file)
                        if detect_to_file
                        else self.output_dir / f"{ticker}_detect_{filing_result.filing_date}.txt"
                    )
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    period_end = getattr(filing_result, "period_end_date", None) or ""
                    lines = [
                        f"# ticker={ticker} filing_type={filing_type} filing_date={filing_result.filing_date} period_end={period_end} num_tables={len(current_quarter_tables)}",
                        "",
                    ]
                    for idx, (_, text, table_num, table_id) in enumerate(current_quarter_tables, 1):
                        lines.append(f"[TABLE {idx}]")
                        lines.append(f"table_id={table_id}")
                        lines.append(f"table_num={table_num}")
                        lines.append(f"length={len(text)}")
                        lines.append("")
                        lines.append(text.strip())
                        lines.append("")
                    out_path.write_text("\n".join(lines), encoding="utf-8")
                    logger.info("Wrote %d table(s) to %s (structured for LLM)", len(current_quarter_tables), out_path)
                    return str(out_path)
                # No CSV path to return in detect-only mode
                return None

            for html, text, num, table_id in current_quarter_tables:
                text_len = len(text)
                company_indicators = ['llc', 'inc.', 'corp', 'ltd.', 'lp', 'llp']
                company_count = sum(1 for indicator in company_indicators if indicator in text.lower())
                logger.info(f"  Table {num}: {text_len} chars, {company_count} company indicators")

            # Process tables in parallel batches of 5 per worker
            logger.info(f"Processing {len(current_quarter_tables)} current quarter tables in parallel batches")

            # Detect unit scale once per filing (thousands/millions/units)
            unit_scale = self._detect_unit_scale(current_quarter_tables, filing_result)

            filing_info = {
                'ticker': ticker,
                'filing_type': filing_type,
                'filing_date': filing_result.filing_date,
                'accession_number': filing_result.accession_number,
                'unit_scale': unit_scale
            }

            # NEW: Batch tables in groups of 5 for parallel processing
            tables_per_worker = 5  # Fixed batch size - each worker gets 5 tables
            num_tables = len(current_quarter_tables)
            num_chunks = max(1, math.ceil(num_tables / tables_per_worker))
            
            # If user specified workers, use that, otherwise use number of chunks needed
            if workers:
                num_workers = min(workers, num_chunks)  # Don't use more workers than chunks
            else:
                num_workers = min(num_chunks, 8)  # Auto: use up to 8 workers for parallelization

            logger.info(f"📊 Batching {num_tables} tables: {tables_per_worker} tables/worker × {num_chunks} chunks")
            if num_workers > 1:
                logger.info(f"🚀 Using {num_workers} parallel workers for faster processing")

            def process_chunk(chunk_idx: int) -> List[Tuple[str, int, int]]:
                """Process a chunk of tables sequentially. Returns [(row_str, table_idx, table_size), ...]"""
                start = chunk_idx * tables_per_worker
                end = min(start + tables_per_worker, len(current_quarter_tables))
                chunk_tables = current_quarter_tables[start:end]
                chunk_results = []
                prev_csv = None
                is_first_chunk = (chunk_idx == 0)
                for i, (table_html, table_text, table_num, table_id) in enumerate(chunk_tables):
                    global_idx = start + i
                    logger.info(f"Worker {chunk_idx + 1}: Processing table {global_idx + 1}/{len(current_quarter_tables)} (table {table_num + 1})")
                    self._save_debug_info(table_html, table_text, table_num, filing_result, table_id)
                    try:
                        result = self.parse_table_with_llm(
                            table_html, table_text, table_num, filing_info, prev_csv)
                    except Exception as e:
                        logger.error(f"Error parsing table {table_num + 1}: {e}")
                        continue
                    if result and (result.confidence_score > 0.2 or len(result.csv_content.strip().split('\n')) > 2):
                        csv_rows = result.csv_content.strip().split('\n')
                        table_size = len(table_text)
                        if len(csv_rows) > 1:
                            include_header = is_first_chunk and i == 0
                            data_rows = csv_rows if include_header else csv_rows[1:]
                            for row in data_rows:
                                chunk_results.append((row, global_idx, table_size))
                            prev_csv = result.csv_content
                return chunk_results

            if num_workers > 1 and num_chunks > 1:
                with ThreadPoolExecutor(max_workers=min(num_workers, num_chunks)) as executor:
                    futures = {executor.submit(process_chunk, i): i for i in range(num_chunks)}
                    for future in as_completed(futures):
                        chunk_idx = futures[future]
                        try:
                            chunk_data = future.result()
                            all_csv_data.extend(chunk_data)
                        except Exception as e:
                            logger.error(f"Worker {chunk_idx} failed: {e}")
                processed_tables = len(set(r[1] for r in all_csv_data))
            else:
                chunk_data = process_chunk(0)
                all_csv_data.extend(chunk_data)
                processed_tables = len(set(r[1] for r in all_csv_data))

            if not all_csv_data:
                logger.warning("No valid investment data extracted")
                return None

            # Ensure header row is first (workers may complete in any order)
            header_pattern = 'company_name,investment_type,industry,'
            header_row = next((r for r in all_csv_data if r[0].strip().startswith('company_name')), None)
            if header_row:
                all_csv_data = [header_row] + [r for r in all_csv_data if r != header_row]

            # Deduplicate rows before saving (all_csv_data is list of (row_str, table_idx, table_size))
            deduplicated_data = self._deduplicate_csv_rows(all_csv_data)
            logger.info(f"Deduplicated {len(all_csv_data)} rows to {len(deduplicated_data)} unique rows")

            # Validate and correct scale consistency (percent_of_net_assets, etc.)
            deduplicated_data, scale_meta = self._validate_scale_consistency(deduplicated_data)

            # Remove empty header rows (company name only, no financials)
            deduplicated_data = self._remove_empty_header_rows(deduplicated_data)

            # Remove LLM artifact rows (* *Wait, Undrawn = ..., backtick/pipe junk)
            deduplicated_data = filter_llm_artifact_rows(deduplicated_data)

            # Filter equity types if debt_only mode is enabled
            deduplicated_data = self._filter_equity_types(deduplicated_data, debt_only=debt_only)

            # Combine all data and save
            final_csv = '\n'.join(deduplicated_data)
            output_path = self._save_csv_output(final_csv, ticker, filing_result.filing_date)

            # Generate validation report
            self._generate_validation_report(
                ticker=ticker,
                filing_date=filing_result.filing_date,
                unit_scale=unit_scale,
                raw_row_count=len(all_csv_data),
                dedup_row_count=len(deduplicated_data),
                final_csv_rows=deduplicated_data,
                scale_meta=scale_meta,
                output_path=str(output_path)
            )

            logger.info(f"Successfully processed {processed_tables} tables, saved {len(deduplicated_data)} rows to {output_path}")
            return str(output_path)

        except Exception as e:
            logger.error(f"Error processing filing for {ticker}: {e}")
            return None

    def _save_debug_info(self, table_html: str, table_text: str, table_number: int,
                        filing_result: Any, table_id: str):
        """
        Save debug information for a table.

        Args:
            table_html: Raw HTML table
            table_text: Plain text table
            table_number: Table number
            filing_result: Filing result object
        """
        debug_filename = f"{filing_result.ticker}_{filing_result.filing_date}_{table_id}"

        # Save HTML
        html_path = self.debug_dir / f"{debug_filename}.html"
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(table_html)

        # Save text
        text_path = self.debug_dir / f"{debug_filename}.txt"
        with open(text_path, 'w', encoding='utf-8') as f:
            f.write(table_text)

        # Save full prompt for debugging
        prompt_path = self.debug_dir / f"{debug_filename}_FULL_PROMPT.txt"
        filing_info = {
            'ticker': filing_result.ticker,
            'filing_type': filing_result.filing_type,
            'filing_date': filing_result.filing_date,
            'accession_number': filing_result.accession_number
        }
        # For debug, we don't have previous CSV, so pass None
        cleaned_table = self._clean_table_html(table_html)
        prompt = self._build_llm_prompt(cleaned_table, table_text, table_number, filing_info, None)

        with open(prompt_path, 'w', encoding='utf-8') as f:
            f.write(prompt)

    def _validate_scale_consistency(self, csv_rows: List[str]) -> Tuple[List[str], Dict[str, Any]]:
        """
        Validate scale consistency and auto-correct if percent_of_net_assets are off by 1000x.
        - Sum of percent_of_net_assets should be roughly 50-150%
        - Single holding > 15% gets a warning
        - If scale appears off by 1000x, divide percent values to correct

        Args:
            csv_rows: List of CSV row strings (first row is header)

        Returns:
            Possibly corrected list of CSV row strings
        """
        import csv
        from io import StringIO

        report_meta = {"scale_correction": 1.0, "original_total_pct": 0.0, "holdings_over_15pct": []}
        if len(csv_rows) < 2:
            return csv_rows, report_meta

        header_row = csv_rows[0]
        parsed = []
        for row_str in csv_rows[1:]:
            try:
                reader = csv.reader(StringIO(row_str))
                cols = list(reader)[0]
                while len(cols) < 16:
                    cols.append('')
                pct_str = cols[12].strip().replace(',', '').replace(' ', '') if len(cols) > 12 else ''
                fv_str = cols[11].strip().replace(',', '').replace(' ', '') if len(cols) > 11 else ''
                try:
                    pct = float(pct_str) if pct_str else 0.0
                    fv = float(fv_str) if fv_str else 0.0
                except ValueError:
                    pct, fv = 0.0, 0.0
                parsed.append((cols, row_str, pct, fv))
            except Exception:
                parsed.append((None, row_str, 0.0, 0.0))

        total_pct = sum(p[2] for p in parsed)
        total_fair_value = sum(p[3] for p in parsed)
        report_meta["original_total_pct"] = total_pct
        report_meta["total_fair_value"] = total_fair_value

        # Warn if any single holding > 15%
        for cols, _, pct, _ in parsed:
            if cols and pct > 15.0:
                company = cols[0][:50] if cols else "?"
                report_meta["holdings_over_15pct"].append({"company": company, "percent": pct})
                logger.warning(
                    f"Scale check: single holding '{company}' has percent_of_net_assets={pct}% "
                    f"- verify column mapping. BDC portfolios rarely have positions > 10%."
                )

        # If sum is 500+%, likely extracted as raw (e.g. 4.5 shown as 4500)
        scale_correction = 1.0
        if total_pct > 500.0:
            scale_correction = 1000.0
            report_meta["scale_correction"] = scale_correction
            logger.info(
                f"Scale correction: sum of percent_of_net_assets={total_pct:.0f}% suggests wrong scale. "
                f"Dividing percent values by 1000."
            )
        elif total_pct > 150.0 and total_pct < 500.0:
            scale_correction = 100.0
            report_meta["scale_correction"] = scale_correction
            logger.info(
                f"Scale correction: sum of percent_of_net_assets={total_pct:.0f}% suggests 100x scale error. "
                f"Dividing percent values by 100."
            )

        if scale_correction != 1.0:
            result = [header_row]
            for cols, _, pct, _ in parsed:
                if cols:
                    if pct > 0:
                        corrected_pct = pct / scale_correction
                        cols[12] = str(int(corrected_pct)) if corrected_pct == int(corrected_pct) else str(round(corrected_pct, 4))
                    output = StringIO()
                    w = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator='')
                    w.writerow(cols[:16])
                    result.append(output.getvalue())
            return result, report_meta

        return csv_rows, report_meta

    def _generate_validation_report(self, ticker: str, filing_date: str, unit_scale: str,
                                    raw_row_count: int, dedup_row_count: int,
                                    final_csv_rows: List[str], scale_meta: Dict[str, Any],
                                    output_path: str) -> str:
        """
        Generate a post-validation report and save to file.

        Returns:
            Path to the saved report file
        """
        import csv
        from io import StringIO

        report_lines = [
            f"# Validation Report: {ticker} {filing_date}",
            "",
            "## Extraction Summary",
            f"- **Unit scale detected:** {unit_scale}",
            f"- **Raw rows extracted:** {raw_row_count}",
            f"- **Rows after deduplication:** {dedup_row_count}",
            f"- **Output CSV:** {output_path}",
            "",
        ]

        if scale_meta.get("scale_correction", 1.0) != 1.0:
            report_lines.extend([
                "## Scale Correction Applied",
                f"- **Original sum of percent_of_net_assets:** {scale_meta.get('original_total_pct', 0):.1f}%",
                f"- **Correction factor:** 1/{scale_meta['scale_correction']}",
                "",
            ])

        report_lines.extend([
            "## Final Data Summary",
        ])

        if len(final_csv_rows) < 2:
            report_lines.append("- No data rows to summarize.")
        else:
            parsed = []
            for row_str in final_csv_rows[1:]:
                try:
                    reader = csv.reader(StringIO(row_str))
                    cols = list(reader)[0]
                    while len(cols) < 16:
                        cols.append('')
                    pct = float(cols[12].replace(',', '')) if len(cols) > 12 and cols[12].strip() else 0.0
                    fv = float(cols[11].replace(',', '')) if len(cols) > 11 and cols[11].strip() else 0.0
                    parsed.append((cols[0], cols[1], pct, fv))
                except (ValueError, IndexError):
                    pass

            total_pct = sum(p[2] for p in parsed)
            total_fv = sum(p[3] for p in parsed)
            unique_companies = len(set(p[0].lower() for p in parsed))

            report_lines.extend([
                f"- **Total holdings:** {len(parsed)}",
                f"- **Unique companies:** {unique_companies}",
                f"- **Sum of percent_of_net_assets:** {total_pct:.1f}%",
                f"- **Total fair value (raw):** {total_fv:,.0f}",
                "",
            ])

            # Holdings > 15% (from final data, after scale correction)
            high_holdings = [(p[0], p[2]) for p in parsed if p[2] > 15.0]
            if high_holdings:
                report_lines.extend([
                    "## ⚠️ Holdings Exceeding 15% of Net Assets",
                    "",
                ])
                for company, pct in high_holdings:
                    report_lines.append(f"- {company[:50]}: {pct:.1f}%")
                report_lines.append("")

            # Top 10 by percent
            sorted_by_pct = sorted(parsed, key=lambda x: x[2], reverse=True)[:10]
            report_lines.extend([
                "## Top 10 Holdings by % of Net Assets",
                "",
                "| Company | Investment Type | % of Net Assets |",
                "|---------|-----------------|-----------------|",
            ])
            for company, inv_type, pct, _ in sorted_by_pct:
                report_lines.append(f"| {company[:40]} | {inv_type[:20]} | {pct:.1f}% |")

        report_content = '\n'.join(report_lines)
        report_path = self.output_dir / f"{ticker}_{filing_date}_validation_report.md"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_content)
        logger.info(f"Validation report saved to {report_path}")
        return str(report_path)

    def _parse_maturity_date(self, maturity_str: str) -> Optional[date]:
        """Parse maturity date string to date for comparison. Returns None if unparseable."""
        if not maturity_str or not maturity_str.strip():
            return None
        s = maturity_str.strip()
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    def _deduplicate_csv_rows(self, csv_rows: List[Any]) -> List[str]:
        """Delegate to data_cleaning.deduplicate_csv_rows."""
        return deduplicate_csv_rows(csv_rows)
    
    def _OLD_deduplicate_csv_rows_DEPRECATED(self, csv_rows: List[Any]) -> List[str]:
        """
        OLD IMPLEMENTATION - NOW IN data_cleaning/deduplicator.py
        Remove duplicate rows from CSV data.
        Duplicates are identified by: company_base + investment_type + principal (within 1%).
        When the same position appears with different maturity dates (e.g. 10-Q current vs prior
        year-end), prefers the row with the LATER maturity_date (current period).

        Args:
            csv_rows: List of (row_str, table_idx, table_size) or List[str] for legacy
        """
        import csv
        from io import StringIO

        if not csv_rows:
            return []

        # Normalize input: convert to [(row_str, table_idx, table_size), ...]
        rows_with_meta = []
        if isinstance(csv_rows[0], tuple):
            for item in csv_rows:
                row_str = item[0] if len(item) > 0 else ""
                table_idx = item[1] if len(item) > 1 else -1
                table_size = item[2] if len(item) > 2 else 0
                rows_with_meta.append((row_str, table_idx, table_size))
        else:
            for row_str in csv_rows:
                rows_with_meta.append((row_str, -1, 0))

        parsed_rows = []
        header_row = None

        for i, (row_str, table_idx, table_size) in enumerate(rows_with_meta):
            reader = csv.reader(StringIO(row_str))
            try:
                cols = list(reader)[0]
            except Exception:
                continue

            if i == 0:
                header_row = row_str
                continue

            if len(cols) < 3:
                continue

            expected_columns = 16
            if len(cols) > 1 and len(cols) > expected_columns:
                col1 = cols[1].strip()
                if col1 and any(col1.endswith(s) for s in ['.', 'Inc.', 'LLC', 'Corp.', 'LP', 'Ltd.', 'Company']):
                    cols[0] = f"{cols[0]}, {col1}"
                    cols = [cols[0]] + cols[2:]

            while len(cols) < expected_columns:
                cols.append('')

            company_base = (cols[0].strip().lower().split('(')[0] if cols else "").strip()
            company_base = ' '.join(company_base.split())
            investment_type = ' '.join((cols[1].strip().lower() if len(cols) > 1 else "").split())
            maturity_date = cols[8].strip() if len(cols) > 8 else ""
            principal_str = (cols[9].strip() if len(cols) > 9 else "").replace(',', '').replace(' ', '')
            try:
                principal_val = float(principal_str) if principal_str else None
            except ValueError:
                principal_val = None

            non_empty_count = sum(1 for c in cols if c.strip())
            maturity_dt = self._parse_maturity_date(maturity_date)
            parsed_rows.append((company_base, investment_type, maturity_date, maturity_dt, principal_val,
                               non_empty_count, table_size, row_str, cols))

        def principal_match(a: Optional[float], b: Optional[float]) -> bool:
            if a is None and b is None:
                return True
            if a is None or b is None:
                return False
            denom = max(abs(a), abs(b), 1.0)
            return abs(a - b) / denom <= 0.01

        # Group by (company_base, investment_type). Within each group, cluster by principal (1%).
        # When same position appears with different maturity dates (10-Q current vs prior year-end),
        # keep only the row with the LATER maturity_date.
        grouped: Dict[Tuple[str, str], List[Tuple]] = {}
        for company_base, inv_type, maturity, maturity_dt, principal, non_empty, table_size, row_str, cols in parsed_rows:
            key = (company_base, inv_type)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append((principal, maturity_dt, non_empty, table_size, row_str, cols))

        result_rows: List[Tuple] = []
        for key, entries in grouped.items():
            # For each row i, find all rows j with matching principal (potential duplicates).
            # Keep only the best: prefer later maturity_date, then non_empty, then table_size.
            keep_mask = [True] * len(entries)
            for i in range(len(entries)):
                if not keep_mask[i]:
                    continue
                principal_i, maturity_dt_i, non_empty_i, size_i, _, _ = entries[i]
                for j in range(len(entries)):
                    if i == j or not keep_mask[j]:
                        continue
                    principal_j, maturity_dt_j, non_empty_j, size_j, _, _ = entries[j]
                    if not principal_match(principal_i, principal_j):
                        continue
                    # Same position: keep the one with later maturity
                    if maturity_dt_i is not None and maturity_dt_j is not None:
                        if maturity_dt_j > maturity_dt_i:
                            keep_mask[i] = False
                            break
                        elif maturity_dt_j < maturity_dt_i:
                            keep_mask[j] = False
                            continue
                    elif maturity_dt_i is None and maturity_dt_j is not None:
                        keep_mask[i] = False
                        break
                    elif maturity_dt_i is not None and maturity_dt_j is None:
                        keep_mask[j] = False
                        continue
                    else:
                        if non_empty_j > non_empty_i or (non_empty_j == non_empty_i and size_j > size_i):
                            keep_mask[i] = False
                            break
                        elif non_empty_j < non_empty_i or (non_empty_j == non_empty_i and size_j < size_i):
                            keep_mask[j] = False
                            continue

            for idx, keep in enumerate(keep_mask):
                if keep:
                    _, _, _, _, row_str, cols = entries[idx]
                    result_rows.append((row_str, cols))

        result = [header_row] if header_row else []
        for row_str, cols in result_rows:
            output = StringIO()
            try:
                w = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator='')
                w.writerow(cols)
                result.append(output.getvalue())
            except Exception as e:
                logger.warning(f"Failed to re-quote row in deduplication: {e}")
                result.append(row_str)

        return result

    def _remove_empty_header_rows(self, csv_rows: List[str]) -> List[str]:
        """Delegate to data_cleaning.remove_empty_header_rows."""
        return remove_empty_header_rows(csv_rows)
    
    def _OLD_remove_empty_header_rows_DEPRECATED(self, csv_rows: List[str]) -> List[str]:
        """
        OLD IMPLEMENTATION - NOW IN data_cleaning/filters.py
        Remove rows that have a company name but no financial data, when
        the same company name appears on other rows that DO have data.
        These are SEC table sub-headers (company name before listing tranches).

        Args:
            csv_rows: List of CSV row strings (first row is header)

        Returns:
            Filtered list with empty header rows removed
        """
        import csv
        from io import StringIO

        if len(csv_rows) < 2:
            return csv_rows

        # Parse all rows
        parsed = []
        for row_str in csv_rows[1:]:  # Skip header
            try:
                reader = csv.reader(StringIO(row_str))
                cols = list(reader)[0]
                if len(cols) >= 16:
                    parsed.append((row_str, cols))
            except Exception:
                parsed.append((row_str, None))

        # Identify rows with no financial data
        # Financial columns: principal(9), amortized_cost(10), fair_value(11), cost(13)
        empty_rows = []
        populated_names = set()

        for row_str, cols in parsed:
            if cols is None:
                continue
            has_financials = any(
                cols[i].strip() for i in [9, 10, 11, 13] if i < len(cols)
            )
            name = cols[0].strip().lower()
            if has_financials and name:
                populated_names.add(name)

        # Filter: remove rows with no financials whose name appears on populated rows
        result = [csv_rows[0]]  # Keep header
        removed = 0
        for row_str, cols in parsed:
            if cols is None:
                result.append(row_str)
                continue
            has_financials = any(
                cols[i].strip() for i in [9, 10, 11, 13] if i < len(cols)
            )
            name = cols[0].strip().lower()
            if not has_financials and name in populated_names:
                removed += 1
                logger.debug(f"Removed empty header row for '{cols[0]}'")
                continue
            result.append(row_str)

        if removed > 0:
            logger.info(f"Removed {removed} empty header-only rows (company name duplicated on rows with data)")

        return result

    def _filter_equity_types(self, csv_rows: List[str], debt_only: bool = False) -> List[str]:
        """Delegate to data_cleaning.filter_equity_types."""
        return filter_equity_types(csv_rows, debt_only)
    
    def _OLD_filter_equity_types_DEPRECATED(self, csv_rows: List[str], debt_only: bool = False) -> List[str]:
        """
        OLD IMPLEMENTATION - NOW IN data_cleaning/filters.py
        Filter out equity investment types if debt_only is True.
        
        Args:
            csv_rows: List of CSV row strings (first row is header)
            debt_only: If True, exclude equity types (Common Equity, Preferred Equity, Warrant, etc.)
        
        Returns:
            Filtered list of CSV row strings
        """
        if not debt_only or not csv_rows:
            return csv_rows
        
        import csv
        from io import StringIO
        
        # Parse header
        header_row = csv_rows[0]
        reader = csv.reader(StringIO(header_row))
        header = list(reader)[0]
        
        # Find investment_type column index
        investment_type_idx = None
        for i, col in enumerate(header):
            if col.lower() in ['investment_type', 'investment type', 'type']:
                investment_type_idx = i
                break
        
        if investment_type_idx is None:
            logger.warning("Could not find investment_type column for equity filtering, returning all rows")
            return csv_rows
        
        # Equity types to exclude
        equity_keywords = [
            'common equity',
            'preferred equity', 
            'warrant',
            'equity interest',
            'membership interest',
            'common stock',
            'preferred stock',
        ]
        
        result = [header_row]  # Keep header
        filtered_count = 0
        
        for row_str in csv_rows[1:]:
            try:
                reader = csv.reader(StringIO(row_str))
                cols = list(reader)[0]
                
                if len(cols) > investment_type_idx:
                    investment_type = cols[investment_type_idx].lower().strip()
                    
                    # Check if this is an equity type
                    is_equity = any(keyword in investment_type for keyword in equity_keywords)
                    
                    if not is_equity:
                        result.append(row_str)
                    else:
                        filtered_count += 1
                        logger.debug(f"Filtered out equity investment: {investment_type}")
                else:
                    # Row has fewer columns than expected, keep it to avoid errors
                    result.append(row_str)
            except Exception as e:
                logger.warning(f"Error parsing row for equity filtering: {e}, keeping row")
                result.append(row_str)
        
        if filtered_count > 0:
            logger.info(f"Filtered out {filtered_count} equity investments (debt-only mode)")
        
        return result

    def _save_csv_output(self, csv_content: str, ticker: str, filing_date: str) -> Path:
        """
        Save the final CSV output.

        Args:
            csv_content: CSV content string
            ticker: Company ticker
            filing_date: Filing date

        Returns:
            Path to saved file
        """
        # Clean filing date for filename
        clean_date = filing_date.replace('-', '-') if filing_date else 'unknown'
        filename = f"{ticker}_investments_{clean_date}.csv"
        output_path = self.output_dir / filename

        # Ensure clean overwrite (remove existing file first)
        if output_path.exists():
            output_path.unlink()

        # Re-write CSV with proper quoting to ensure fields with commas are quoted
        import csv
        from io import StringIO
        
        lines = csv_content.strip().split('\n')
        output_lines = []
        
        for line in lines:
            # Parse the line - csv.reader handles unquoted fields with commas correctly
            # by looking ahead to match the expected number of columns
            reader = csv.reader(StringIO(line))
            try:
                cols = list(reader)[0]
                # Write with QUOTE_MINIMAL which will quote fields containing commas
                output = StringIO()
                writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator='')
                writer.writerow(cols)
                written_line = output.getvalue()
                output_lines.append(written_line)
            except Exception as e:
                # Fallback: try to manually quote fields with commas
                # Split by comma but be smart about it
                parts = line.split(',')
                if len(parts) > 16:
                    # Likely has unquoted commas in company name
                    # Try to reconstruct: first field might be split
                    logger.warning(f"Failed to parse CSV line properly: {e}, trying manual fix")
                    output_lines.append(line)
                else:
                    output_lines.append(line)

        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            f.write('\n'.join(output_lines))

        logger.info(f"Saved CSV output to {output_path}")
        return output_path

    def process_multiple_filings(self, tickers: List[str], filing_type: str = "10-Q",
                               years_back: int = 1) -> Dict[str, Optional[str]]:
        """
        Process multiple filings for different companies.

        Args:
            tickers: List of ticker symbols
            filing_type: Type of filing to process
            years_back: How many years back to look for filings

        Returns:
            Dictionary mapping tickers to CSV file paths
        """
        results = {}

        for ticker in tickers:
            try:
                # Try to get recent filings
                for year_offset in range(years_back + 1):
                    year = datetime.now().year - year_offset

                    result = self.process_filing(ticker, filing_type, year=year)
                    if result:
                        results[ticker] = result
                        break
                else:
                    logger.warning(f"No recent {filing_type} filings found for {ticker}")
                    results[ticker] = None

            except Exception as e:
                logger.error(f"Error processing {ticker}: {e}")
                results[ticker] = None

        return results

def main():
    """Main CLI interface."""
    # Default paths to project root so consolidation and SEC cache stay in one place
    _script_dir = Path(__file__).resolve().parent
    _repo_root = _script_dir.parent.parent
    _project_output = _repo_root / "output"
    _project_data = _repo_root / "data"
    parser = argparse.ArgumentParser(description="LLM-powered SEC filing table scraper")
    parser.add_argument('--ticker', required=True, help='Company ticker symbol')
    parser.add_argument('--filing-type', default='10-Q', choices=['10-Q', '10-K', '8-K', '424B'],
                       help='Type of SEC filing to process')
    parser.add_argument('--year', type=int, help='Year to fetch filing for (default: latest)')
    parser.add_argument('--quarter', choices=['Q1', 'Q2', 'Q3', 'Q4'],
                       help='Quarter for 10-Q filings')
    _project_debug = _repo_root / "debug_tables"
    parser.add_argument('--output-dir', default=str(_project_output), help='Output directory for CSV files')
    parser.add_argument('--data-dir', default=str(_project_data), help='SEC download cache (default: repo root data/)')
    parser.add_argument('--debug-dir', default=str(_project_debug), help='Debug output directory (default: repo root debug_tables/)')
    parser.add_argument('--google-key', help='Google Gemini API key (or set GOOGLE_API_KEY env var)')
    parser.add_argument('--years-back', type=int, default=0, help='Number of years to look back for historical filings')
    parser.add_argument('--force', action='store_true', help='Re-process filings even if output CSV already exists')
    parser.add_argument('--max-tables', type=int, default=None, help='Max tables to process (for trial runs)')
    parser.add_argument('--workers', type=int, default=None, help='Parallel workers (e.g., 8 for 39 tables = ~5 tables/worker)')
    parser.add_argument('--debt-only', action='store_true', help='Filter out equity investments (Common/Preferred Equity, Warrants)')
    parser.add_argument('--detect-only', action='store_true',
                       help='Run detection only (no LLM), log selected tables')
    parser.add_argument('--detect-to-file', nargs='?', const='', default=None, metavar='PATH',
                       help='Run detection only and write selected table texts to one file (LLM-friendly structure). '
                            'Optional PATH; if omitted, writes to output_dir/{ticker}_detect_{filing_date}.txt')

    args = parser.parse_args()

    # Validate arguments
    if args.filing_type == '10-Q' and args.quarter and not args.year:
        parser.error("--year is required when specifying --quarter")

    try:
        # Initialize scraper
        scraper = LLMTableScraper(
            gemini_api_key=args.google_key,
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            debug_dir=args.debug_dir
        )

        # Process the filing
        if args.years_back > 0:
            if args.detect_only or args.detect_to_file is not None:
                logger.error("--detect-only and --detect-to-file are only supported for a single filing (years-back=0). "
                             "Use --year/--quarter and years-back=0 for detection debugging.")
                exit(1)

            results = scraper.process_historical_filings(
                ticker=args.ticker,
                filing_type=args.filing_type,
                years_back=args.years_back,
                skip_existing=not args.force,
                debt_only=args.debt_only
            )
            
            if results:
                print(f"Successfully processed {len(results)} filings for {args.ticker}")
                for res in results:
                    print(f"- CSV saved to: {res}")
            else:
                print(f"No investment data found for {args.ticker} in the last {args.years_back} years")
                exit(1)
        else:
            result = scraper.process_filing(
                ticker=args.ticker,
                filing_type=args.filing_type,
                year=args.year,
                quarter=args.quarter,
                max_tables=args.max_tables,
                workers=args.workers,
                debt_only=args.debt_only,
                detect_only=args.detect_only,
                detect_to_file=args.detect_to_file,
            )

            if args.detect_only or args.detect_to_file is not None:
                print(f"Detection-only run completed for {args.ticker} {args.filing_type}")
                if result:
                    print(f"Table text file written (structured): {result}")
            else:
                if result:
                    print(f"Successfully processed {args.ticker} {args.filing_type}")
                    print(f"- CSV saved to: {result}")
                else:
                    print(f"No investment data found for {args.ticker} {args.filing_type}")
                    exit(1)

    except Exception as e:
        logger.error(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    main()
