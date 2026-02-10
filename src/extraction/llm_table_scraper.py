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
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, date
from pathlib import Path
import re
import requests
from bs4 import BeautifulSoup
import openai
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
                 openai_api_key: Optional[str] = None,
                 gemini_api_key: Optional[str] = None,
                 llm_provider: str = "openai",
                 data_dir: str = "data",
                 output_dir: str = "output",
                 debug_dir: str = "debug_tables"):
        """
        Initialize the LLM table scraper.

        Args:
            openai_api_key: OpenAI API key (will check env var if not provided)
            gemini_api_key: Gemini API key (will check env var if not provided)
            llm_provider: Which LLM to use ('openai' or 'gemini')
            data_dir: Directory for SEC API client data
            output_dir: Directory to save CSV outputs
            debug_dir: Directory to save debug information
        """
        # Load environment variables from .env file if available
        if DOTENV_AVAILABLE:
            load_dotenv()

        # Set up LLM provider and API keys
        self.llm_provider = llm_provider.lower()
        if self.llm_provider not in ['openai', 'gemini']:
            raise ValueError("llm_provider must be 'openai' or 'gemini'")

        # Set up OpenAI
        if self.llm_provider == 'openai':
            self.openai_api_key = openai_api_key or os.getenv('OPENAI_API_KEY')
            if not self.openai_api_key:
                raise ValueError("OpenAI API key required for OpenAI provider. Set OPENAI_API_KEY env var or pass directly.")
            openai.api_key = self.openai_api_key
            logger.info("Using OpenAI GPT-4 for LLM processing")

        # Set up Gemini
        elif self.llm_provider == 'gemini':
            if not GEMINI_AVAILABLE:
                raise ImportError("google-generativeai package not installed. Install with: pip install google-generativeai")

            self.gemini_api_key = gemini_api_key or os.getenv('GOOGLE_API_KEY')
            if not self.gemini_api_key:
                raise ValueError("Gemini API key required for Gemini provider. Set GOOGLE_API_KEY env var or pass directly.")

            genai.configure(api_key=self.gemini_api_key)
            self.gemini_model = genai.GenerativeModel('gemini-2.5-flash-lite')
            logger.info("Using Gemini 2.5 Flash Lite for LLM processing")

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
                response = requests.get(doc.url, headers=self.sec_client.headers)
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

    def _is_year_end_table(self, table_html: str, table_text: str, filing_date: str) -> bool:
        """
        Check if a table is from year-end (previous year) rather than current quarter.
        
        Args:
            table_html: HTML of the table
            table_text: Text content of the table
            filing_date: Current filing date (YYYY-MM-DD)
            
        Returns:
            True if this appears to be a year-end table
        """
        from datetime import datetime
        
        # Parse filing date to get year and quarter
        try:
            filing_dt = datetime.strptime(filing_date, '%Y-%m-%d')
            filing_year = filing_dt.year
            filing_month = filing_dt.month
            
            # For 10-Q filings, we want current quarter data, not year-end
            # Year-end tables typically reference "December 31" of previous year
            text_lower = table_text.lower()
            html_lower = table_html.lower()
            combined_text = text_lower + ' ' + html_lower
            
            # Look for year-end indicators
            year_end_patterns = [
                f'december 31, {filing_year - 1}',  # Previous year end
                f'december 31,{filing_year - 1}',
                f'as of december 31, {filing_year - 1}',
                f'as of december 31,{filing_year - 1}',
                f'at december 31, {filing_year - 1}',
                f'year ended december 31, {filing_year - 1}',
                f'fiscal year ended {filing_year - 1}',
            ]
            
            # Also check for "as of" dates that are clearly year-end
            # Look for dates like "12/31/YYYY" where YYYY is previous year
            import re
            date_pattern = re.search(r'(?:as of|at|as of|date:)\s*(\d{1,2}[/-]\d{1,2}[/-](\d{4}))', combined_text, re.IGNORECASE)
            if date_pattern:
                date_year = int(date_pattern.group(2))
                if date_year < filing_year:
                    logger.debug(f"Table appears to be from previous year ({date_year})")
                    return True
            
            # Check for explicit year-end references
            for pattern in year_end_patterns:
                if pattern in combined_text:
                    logger.info(f"Detected year-end table: {pattern}")
                    return True
            
            # Check if table has "comparative" or "prior period" language suggesting it's historical
            historical_indicators = [
                'prior period',
                'prior year',
                'comparative',
                'previous year',
                f'year ended {filing_year - 1}',
            ]
            
            for indicator in historical_indicators:
                if indicator in combined_text:
                    logger.debug(f"Table has historical indicator: {indicator}")
                    # But don't exclude if it's clearly the current period table
                    # Only exclude if it's explicitly labeled as prior/comparative
                    if any(phrase in combined_text for phrase in ['prior period', 'prior year', 'previous year']):
                        return True
            
        except Exception as e:
            logger.debug(f"Error checking year-end status: {e}")
            # If we can't determine, err on the side of including it
            return False
        
        return False

    def find_investment_schedule_tables(self, tables: List[Tuple[str, str, int, str]],
                                       filing_result: Any) -> List[Tuple[str, str, int, str]]:
        """
        Find tables that are directly under "Schedule of Investments" headers.
        Uses HTML structure to find tables that appear after schedule headers.
        Checks ALL HTML documents (main + exhibits) to avoid missing tables.

        Args:
            tables: List of (html, text, table_num, table_id) tuples
            filing_result: The FilingResult object
            
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
                    response = requests.get(doc.url, headers=self.sec_client.headers)
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
                    # Check if this section is year-end by looking at the header
                    section_is_year_end = False
                    filing_year = int(filing_date.split('-')[0]) if filing_date else 2025
                    
                    # Check the schedule header that started this section
                    if header_idx < len(all_elements):
                        header_element = all_elements[header_idx]
                        header_text = header_element.get_text(strip=True).lower()
                        # Check if header mentions December 31 of previous year
                        if 'december 31' in header_text or 'dec 31' in header_text:
                            import re
                            year_match = re.search(r'(?:december 31|dec 31)[,\s]+(\d{4})', header_text, re.IGNORECASE)
                            if year_match:
                                header_year = int(year_match.group(1))
                                if header_year < filing_year:
                                    section_is_year_end = True
                                    logger.info(f"Detected year-end section: header mentions December 31, {header_year}, skipping all tables in this section")
                                    # Skip this entire section
                                    continue
                    
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
                                                    # Double-check with year-end filter
                                                    if not self._is_year_end_table(html, text, filing_date):
                                                        investment_tables.append((html, text, table_num, table_id))
                                                        logger.info(f"Added investment schedule table {table_num} from {doc.filename} ({table_id})")
                                                    else:
                                                        logger.info(f"Skipping year-end table {table_num} from {doc.filename} ({table_id})")
                                                    processed_table_indices.add(table_num)
                                                    break  # Found match, move to next table
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
        return self._fallback_table_detection(tables, filing_date)
    
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
    
    def _fallback_table_detection(self, tables, filing_date: str = None):
        """Fallback: simple content-based detection with expanded criteria."""
        investment_tables = []
        for html, text, table_num, table_id in tables:
            text_lower = text.lower()
            
            # Use the same expanded criteria as the main detection
            if self._is_investment_table(text_lower):
                # Filter out year-end tables if filing_date is provided
                if filing_date and self._is_year_end_table(html, text, filing_date):
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

    def _clean_table_html(self, table_html: str) -> str:
        """
        Aggressively clean and simplify HTML table for better LLM parsing.

        Args:
            table_html: Raw HTML table

        Returns:
            Cleaned markdown table
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(table_html, 'html.parser')
        table = soup.find('table')

        if not table:
            return table_html

        # Remove all styling and complex elements
        for element in table.find_all(style=True):
            del element['style']

        # Remove inline XBRL tags and complex markup
        for element in table.find_all(['ix:nonfraction', 'ix:nonnumeric', 'span', 'div', 'font', 'sup', 'sub', 'br']):
            # Keep the text content but remove the tags
            element.replace_with(element.get_text())

        # Remove all attributes except basic ones
        for tag in table.find_all(True):
            # Keep only essential attributes
            attrs_to_keep = {}
            for attr, value in tag.attrs.items():
                if attr in ['colspan', 'rowspan']:  # Keep for table structure
                    attrs_to_keep[attr] = value
            tag.attrs = attrs_to_keep

        # Extract clean text from all cells
        rows = []
        for tr in table.find_all('tr'):
            row_data = []
            for cell in tr.find_all(['th', 'td']):
                # Get clean text
                text = cell.get_text(strip=True)

                # Clean up common SEC formatting
                text = text.replace('\u00a0', ' ')  # Non-breaking spaces
                text = text.replace('\u2014', '-')  # Em dashes
                text = text.replace('\u2013', '-')  # En dashes
                text = text.replace('\u2019', "'")  # Smart quotes
                text = text.replace('\u201c', '"')  # Smart quotes
                text = text.replace('\u201d', '"')  # Smart quotes
                text = text.replace('—', '-')      # Em dash
                text = text.replace('–', '-')      # En dash

                # Remove footnotes like (1), (2), etc. if they're just references
                text = re.sub(r'\s*\(\d+\)\s*$', '', text)

                # Normalize whitespace
                text = ' '.join(text.split())

                row_data.append(text)

            if row_data and any(cell.strip() for cell in row_data):  # Skip completely empty rows
                rows.append(row_data)

        # Convert to clean markdown table
        if len(rows) >= 2:  # Need at least header + 1 data row
            markdown_lines = []

            # First row is headers
            headers = rows[0]
            header_line = '| ' + ' | '.join(headers) + ' |'
            separator_line = '| ' + ' | '.join(['---'] * len(headers)) + ' |'

            markdown_lines.append(header_line)
            markdown_lines.append(separator_line)

            # Add data rows (skip headers, limit to reasonable number)
            for row_data in rows[1:30]:  # Limit rows to avoid token limits
                # Pad or truncate to match header count
                while len(row_data) < len(headers):
                    row_data.append('')
                row_data = row_data[:len(headers)]

                # Clean the data
                cleaned_row = []
                for cell in row_data:
                    # Remove common prefixes/suffixes that aren't data
                    cell = cell.strip()
                    cell = re.sub(r'^[-\s]*$', '', cell)  # Empty cells
                    cleaned_row.append(cell)

                row_line = '| ' + ' | '.join(cleaned_row) + ' |'
                markdown_lines.append(row_line)

            return '\n'.join(markdown_lines)

        return "Could not extract clean table data"

    def parse_table_with_llm(self, table_html: str, table_text: str, table_number: int,
                           filing_info: Dict[str, Any], previous_table_csv: Optional[str] = None) -> Optional[TableExtractionResult]:
        """
        Use LLM to parse an HTML table into structured CSV data.

        Args:
            table_html: Raw HTML of the table
            table_text: Plain text content of the table
            table_number: Sequential number of the table
            filing_info: Information about the filing (ticker, date, etc.)

        Returns:
            TableExtractionResult if successful, None otherwise
        """
        # Clean the table HTML before sending to LLM
        cleaned_table = self._clean_table_html(table_html)
        prompt = self._build_llm_prompt(cleaned_table, table_text, table_number, filing_info, previous_table_csv)

        try:
            if self.llm_provider == 'openai':
                response = openai.ChatCompletion.create(
                    model="gpt-4",  # Use GPT-4 for better table parsing
                    messages=[
                        {"role": "system", "content": "You are an expert financial analyst specializing in SEC filing data extraction. You extract structured data from complex financial tables with high accuracy."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,  # Low temperature for consistent parsing
                    max_tokens=4000
                )
                llm_response = response.choices[0].message.content.strip()

            elif self.llm_provider == 'gemini':
                # Configure Gemini with appropriate settings for table parsing
                generation_config = genai.types.GenerationConfig(
                    temperature=0.1,  # Low temperature for consistent parsing
                    max_output_tokens=4000,
                    top_p=0.8,
                    top_k=40
                )

                # Add timeout to prevent hanging
                import signal
                
                # Create the prompt for Gemini - with separate cash and PIK rates
                gemini_prompt = f"""You are an expert financial analyst extracting investment data from a CLEANED SEC filing table. The table has been preprocessed to remove formatting artifacts and is presented in a clear markdown format.

CRITICAL INSTRUCTIONS:
1. This is a markdown table with clean data - analyze each column carefully
2. Each row represents one investment in a portfolio company
3. Extract ALL financial data available - don't skip columns
4. SEPARATE cash rates and PIK rates into different columns
5. Look for monetary amounts ($), percentages (%), dates, and rates
6. VERY IMPORTANT: DO NOT USE COMMAS INSIDE ANY FIELD VALUES. If the source table has commas inside text (for example in company names like "Douglas Holdings, Inc."), REPLACE THOSE COMMAS WITH SPACES (e.g., "Douglas Holdings Inc"). The ONLY commas in your response should be the separators between the 16 CSV columns.

REQUIRED OUTPUT COLUMNS (in this exact order):
company_name,investment_type,industry,cash_rate,pik_rate,reference_rate,spread,acquisition_date,maturity_date,principal_amount,amortized_cost,fair_value,percent_of_net_assets,cost,commitment_limit,undrawn_commitment

EXTRACTION RULES (WITH STANDARDIZATION):
- company_name: Portfolio company name (may include "(fka ...)" or other qualifiers)
- investment_type: Use ONLY these standard values: "First Lien", "Revolver", "Delayed Draw", "Second Lien", "Subordinated Debt", "Unsecured Debt", "Common Equity", "Preferred Equity", "Partnership Interest", "Warrants", "Unfunded Commitment", "Structured Note", "Money Market Fund", "Other Debt", "Other"
  Examples: "First lien senior secured loan" → "First Lien", "Revolving credit facility" → "Revolver", "Common stock" → "Common Equity"
- industry: Use ONLY standard categories: "Software", "Healthcare Services", "Pharmaceuticals & Biotechnology", "Financial Services", "Insurance", "Manufacturing", "Business Services", etc.
  Examples: "Healthcare Providers & Services" → "Healthcare Services", "High Tech Industries" → "Software", "Banking" → "Financial Services"
- cash_rate: Cash interest rate only (e.g., "8.5%", "SOFR + 5.0%") - extract from combined rates like "8.5% Cash/ 2.0% PIK"
- pik_rate: PIK interest rate only (e.g., "2.0%") - extract from combined rates, leave blank if no PIK
- reference_rate: Use ONLY: "SOFR", "SOFR (Q)", "LIBOR", "Euribor", "Prime", "SONIA", "CDOR", "BKBM", "N/A"
  Examples: "SF" or "SF+" → "SOFR", "L" or "3 Month LIBOR" → "LIBOR (Q)", "P" or "Base Rate" → "Prime", "n/a" → "N/A"
- spread: Spread over reference rate (e.g., "5.0%", "4.75%")
- maturity_date: YYYY-MM-DD format
- principal_amount: Loan principal (numbers only, no $ signs)
- amortized_cost: Book value (numbers only)
- fair_value: Market value (numbers only)
- percent_of_net_assets: Percentage (numbers only, no % signs)

RATE PARSING EXAMPLES:
- "8.5%" → cash_rate: "8.5%", pik_rate: ""
- "8.5% PIK" → cash_rate: "", pik_rate: "8.5%"
- "8.5% Cash/ 2.0% PIK" → cash_rate: "8.5%", pik_rate: "2.0%"
- "SOFR + 5.0%" → cash_rate: "SOFR + 5.0%", pik_rate: ""

CSV FORMATTING:
- One investment per row
- NEVER output a comma inside any field value. If a value would normally contain a comma, replace it with a space instead.
- Leave empty fields blank
- Do not add extra columns or change order

{prompt}

QUALITY CHECK: Most investments should have principal_amount, amortized_cost, and fair_value filled in. Parse rates carefully - separate cash and PIK components."""

                # Add timeout wrapper to prevent hanging
                try:
                    import time
                    start_time = time.time()
                    timeout_seconds = 120  # 2 minute timeout per table
                    
                    response = self.gemini_model.generate_content(
                        gemini_prompt,
                        generation_config=generation_config
                    )
                    
                    elapsed = time.time() - start_time
                    logger.info(f"LLM call completed in {elapsed:.1f} seconds")
                    
                    llm_response = response.text.strip()
                except Exception as timeout_error:
                    logger.error(f"LLM call timed out or failed for table {table_number}: {timeout_error}")
                    raise

            # Parse the LLM response
            csv_content, confidence_score, metadata = self._parse_llm_response(llm_response)

            if csv_content:
                return TableExtractionResult(
                    csv_content=csv_content,
                    table_number=table_number,
                    confidence_score=confidence_score,
                    metadata=metadata
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
            filing_info: Filing metadata

        Returns:
            Complete LLM prompt
        """
        # Truncate very large tables for the prompt
        if len(table_html) > 10000:
            table_html = table_html[:10000] + "...[truncated]"

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
- investment_type MUST be specific (e.g., "Senior Secured Loan", "First Lien Senior Secured", "Common Equity", "Preferred Equity") - NOT generic terms like "Loan" or "Equity"
- If you see a row that is ONLY an industry name (like "High Tech Industries") or ONLY an investment type category, SKIP IT - it's a header, not data

EXAMPLE OF CORRECT FORMAT:
company_name,investment_type,industry,cash_rate,pik_rate,reference_rate,spread,acquisition_date,maturity_date,principal_amount,amortized_cost,fair_value,percent_of_net_assets,cost,commitment_limit,undrawn_commitment
ABC Company LLC,Senior Secured Loan,Healthcare,8.5%,,SOFR,5.0%,2023-01-15,2028-01-15,5000,4950,5000,2.5,,,,
XYZ Corp,Common Equity,Technology,,,n/a,n/a,2022-06-01,,,1000,1200,0.6,1000,,
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
            logger.warning("No CSV headers found in LLM response")
            return None, 0.0, {}

        # Extract CSV content
        csv_lines = lines[csv_start:]

        # Validate CSV structure
        if len(csv_lines) < 2:  # Need at least header + 1 data row
            logger.warning("CSV content too short")
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

        csv_content = '\n'.join(valid_rows)

        # Calculate confidence based on data quality
        confidence = self._calculate_confidence_score(csv_content)

        metadata = {
            'total_rows': len(valid_rows) - 1,  # Subtract header
            'columns': expected_columns,
            'parsing_timestamp': datetime.now().isoformat()
        }

        return csv_content, confidence, metadata

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
        
        # Filter out rows with generic investment types like "Loan" or "Equity"
        # These should be more specific like "Senior Secured Loan" or "Common Equity"
        investment_type_lower = investment_type.lower().strip()
        generic_types = ['loan', 'equity', 'debt']
        
        # Check if investment_type is too generic (just one word matching generic types)
        is_generic = (
            investment_type_lower in generic_types or
            (len(investment_type.split()) == 1 and investment_type_lower in generic_types)
        )
        
        # ALWAYS filter out generic investment types - they should be more specific
        if is_generic:
            logger.debug(f"Filtered out row with generic investment_type '{investment_type}' - should be more specific (e.g., 'Senior Secured Loan', 'Common Equity')")
            return False
        
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
                fair_value = cols[11].strip().strip('"')
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
            has_fair_value = len(fair_value) > 0 and fair_value != ''

            row_quality = (has_company + has_investment_type + has_fair_value) / 3
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
                                   skip_existing: bool = True) -> List[str]:
        """
        Process multiple historical filings for a ticker.

        Args:
            ticker: Company ticker symbol
            filing_type: Type of filing (10-Q, 10-K, etc.)
            years_back: Number of years to look back
            skip_existing: Skip filings that already have output CSVs

        Returns:
            List of paths to generated CSV files
        """
        logger.info(f"Processing historical {filing_type} filings for {ticker} (last {years_back} years)")

        # Get historical filings
        if filing_type == "10-Q":
            filings = self.sec_client.get_historical_10q_filings(ticker, years_back=years_back)
        else:
            logger.warning(f"Historical filing lookup not fully implemented for {filing_type}, using latest")
            return [self.process_filing(ticker, filing_type)]

        results = []
        skipped = 0
        for f in filings:
            filing_date = f['date']

            # Check if output already exists
            if skip_existing:
                existing_file = self.output_dir / f"{ticker}_investments_{filing_date}.csv"
                if existing_file.exists() and existing_file.stat().st_size > 100:
                    logger.info(f"SKIPPING {ticker} {filing_date} - already exists ({existing_file.stat().st_size:,} bytes)")
                    results.append(str(existing_file))
                    skipped += 1
                    continue

            logger.info(f"Processing filing from {filing_date} ({f['accession']})")
            csv_path = self.process_filing(ticker, filing_type, index_url=f['index_url'])
            if csv_path:
                results.append(csv_path)

        if skipped > 0:
            logger.info(f"Skipped {skipped} already-processed filings for {ticker}")

        return results

    def process_filing(self, ticker: str, filing_type: str = "10-Q",
                      year: Optional[int] = None, quarter: Optional[str] = None,
                      index_url: Optional[str] = None) -> Optional[str]:
        """
        Process a single SEC filing to extract investment tables.

        Args:
            ticker: Company ticker symbol
            filing_type: Type of filing (10-Q, 10-K, etc.)
            year: Year to fetch filing for (ignored if index_url is provided)
            quarter: Quarter for 10-Q filings (Q1, Q2, Q3, Q4)
            index_url: Optional specific index URL to process

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
                    year=year
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

            # Process each table - limit to top confidence tables to avoid API limits
            all_csv_data = []
            processed_tables = 0
            max_tables_to_process = 10  # Limit to avoid excessive API calls

            # First pass: collect confidence scores
            # Initialize confidence_scores for fallback use
            confidence_scores = {}

            # Use the new investment schedule detection method
            investment_tables = self.find_investment_schedule_tables(
                tables, 
                filing_result
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

            logger.info(f"Processing {len(tables_to_process)} investment schedule tables")
            
            # Filter out year-end tables before processing
            current_quarter_tables = []
            for html, text, num, table_id in tables_to_process:
                if not self._is_year_end_table(html, text, filing_result.filing_date):
                    current_quarter_tables.append((html, text, num, table_id))
                else:
                    logger.info(f"Filtering out year-end table {num} ({table_id})")
            
            if not current_quarter_tables:
                logger.warning("All tables were filtered out as year-end tables. Processing all tables anyway.")
                current_quarter_tables = tables_to_process
            else:
                logger.info(f"After filtering year-end tables: {len(current_quarter_tables)} tables remain")
            
            for html, text, num, table_id in current_quarter_tables:
                text_len = len(text)
                company_indicators = ['llc', 'inc.', 'corp', 'ltd.', 'lp', 'llp']
                company_count = sum(1 for indicator in company_indicators if indicator in text.lower())
                logger.info(f"  Table {num}: {text_len} chars, {company_count} company indicators")

            # Process ALL relevant tables - no limit
            logger.info(f"Processing all {len(current_quarter_tables)} current quarter tables")

            total_tables = len(current_quarter_tables)
            previous_table_csv = None  # Track previous table's CSV for context
            
            for idx, (table_html, table_text, table_num, table_id) in enumerate(current_quarter_tables, 1):
                # All tables in current_quarter_tables are already identified as investment tables
                logger.info(f"Processing investment table {idx}/{total_tables} (table {table_num + 1})")

                # Save debug information
                self._save_debug_info(table_html, table_text, table_num, filing_result, table_id)

                # Parse table with LLM
                filing_info = {
                    'ticker': ticker,
                    'filing_type': filing_type,
                    'filing_date': filing_result.filing_date,
                    'accession_number': filing_result.accession_number
                }

                conf_score = confidence_scores.get(table_num, 0.5)  # Default to 0.5 if not in fallback scores
                logger.info(f"Attempting to parse table {idx}/{total_tables} (table {table_num + 1}, confidence: {conf_score:.2f})")
                
                try:
                    # Pass previous table CSV for context (industry/investment_type may span tables)
                    result = self.parse_table_with_llm(table_html, table_text, table_num, filing_info, previous_table_csv)
                except Exception as e:
                    logger.error(f"Error parsing table {table_num + 1}: {e}")
                    logger.info(f"Skipping table {table_num + 1} due to error, continuing with next table")
                    continue

                if result:
                    logger.info(f"LLM result for table {table_num + 1}: {result.confidence_score:.2f} confidence, {len(result.csv_content.strip().split('\n')) - 1} rows")
                else:
                    logger.warning(f"No LLM result for table {table_num + 1}")

                if result and (result.confidence_score > 0.2 or len(result.csv_content.strip().split('\n')) > 2):
                    # Parse CSV and add to collection
                    csv_rows = result.csv_content.strip().split('\n')
                    if len(csv_rows) > 1:  # Has header + data
                        # Skip header for all but first table
                        data_rows = csv_rows[1:] if processed_tables > 0 else csv_rows
                        all_csv_data.extend(data_rows)
                        processed_tables += 1
                        
                        # Update previous_table_csv for next iteration (use full CSV including header)
                        previous_table_csv = result.csv_content

                        logger.info(f"Successfully processed table {table_num + 1} with {len(data_rows)} rows")
                else:
                    logger.warning(f"Failed to parse table {table_num + 1} with LLM")

            if not all_csv_data:
                logger.warning("No valid investment data extracted")
                return None

            # Deduplicate rows before saving
            deduplicated_data = self._deduplicate_csv_rows(all_csv_data)
            logger.info(f"Deduplicated {len(all_csv_data)} rows to {len(deduplicated_data)} unique rows")

            # Combine all data and save
            final_csv = '\n'.join(deduplicated_data)
            output_path = self._save_csv_output(final_csv, ticker, filing_result.filing_date)

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

    def _deduplicate_csv_rows(self, csv_rows: List[str]) -> List[str]:
        """
        Remove duplicate rows from CSV data.
        Two rows are considered duplicates if they have the same:
        - company_name
        - investment_type
        - acquisition_date (if present)
        - principal_amount (if present)
        
        If duplicates are found, keeps the row with more complete data.
        
        Args:
            csv_rows: List of CSV row strings (first row should be header)
            
        Returns:
            Deduplicated list of CSV rows
        """
        if not csv_rows:
            return []
        
        import csv
        from io import StringIO
        
        # Parse all rows
        parsed_rows = []
        header_row = None
        
        for i, row in enumerate(csv_rows):
            # Parse CSV row - handle quoted fields properly
            reader = csv.reader(StringIO(row))
            try:
                cols = list(reader)[0]
            except:
                continue
            
            if i == 0:
                # First row is header
                header_row = row
                continue
            
            if len(cols) < 3:  # Skip malformed rows
                continue
            
            expected_columns = 16
            # Handle case where company name with comma was split (e.g., "Douglas Holdings, Inc." -> ["Douglas Holdings", " Inc."])
            # This happens when the row doesn't have quotes around fields with commas
            # If cols[1] looks like part of a company name (ends with "Inc.", "LLC", etc.), merge it with cols[0]
            if len(cols) > 1 and len(cols) > expected_columns:
                col1 = cols[1].strip()
                # Check if col1 is part of company name (ends with company suffix)
                if col1 and any(col1.endswith(suffix) for suffix in ['.', 'Inc.', 'LLC', 'Corp.', 'LP', 'Ltd.', 'Company']):
                    # Merge cols[0] and cols[1] into company name, shift everything else
                    cols[0] = f"{cols[0]}, {col1}"
                    cols = [cols[0]] + cols[2:]  # Remove the merged col1
            
            # Ensure we have at least 16 columns (pad if needed)
            expected_columns = 16
            while len(cols) < expected_columns:
                cols.append('')
            
            parsed_rows.append((cols, row))
        
        # Create a dictionary to track unique rows
        # Key: (company_name, investment_type, acquisition_date, principal_amount, maturity_date)
        # We ignore: industry, cash_rate, pik_rate, spread, fair_value (these can vary between tables)
        seen = {}
        
        for cols, original_row in parsed_rows:
            # Extract key fields (normalize company name to handle variations)
            company_name = cols[0].strip().lower() if len(cols) > 0 else ""
            # Remove common variations like "(Delayed Draw)", "(Revolver)" for matching
            # Also normalize whitespace and punctuation
            company_base = company_name.split('(')[0].strip()
            company_base = ' '.join(company_base.split())  # Normalize whitespace
            
            investment_type = cols[1].strip().lower() if len(cols) > 1 else ""
            # Normalize investment type (remove extra spaces, normalize case)
            investment_type = ' '.join(investment_type.split())
            
            acquisition_date = cols[7].strip() if len(cols) > 7 else ""
            principal_amount = cols[9].strip() if len(cols) > 9 else ""
            maturity_date = cols[8].strip() if len(cols) > 8 else ""
            
            # Normalize amounts - remove commas, spaces, and normalize empty strings
            if principal_amount:
                principal_amount = principal_amount.replace(',', '').replace(' ', '')
            if not principal_amount:
                principal_amount = ""
            
            # Create unique key - use base company name and ignore industry, rates, spread, fair_value
            # This catches cases where same investment appears in different industry sections
            # with slightly different values (e.g., different spread, rates, or fair_value)
            # Only use company, investment type, dates, and principal amount for matching
            key = (company_base, investment_type, acquisition_date, principal_amount, maturity_date)
            
            # Debug: Log duplicate keys for Douglas Holdings
            if 'douglas holdings' in company_base and acquisition_date == '2024-08-27' and principal_amount == '2500':
                logger.debug(f"Douglas Holdings key: {key}, company_base: '{company_base}', investment_type: '{investment_type}'")
            
            # Count non-empty fields to prefer more complete rows
            non_empty_count = sum(1 for col in cols if col.strip())
            
            if key not in seen:
                seen[key] = (non_empty_count, original_row)
            else:
                # Duplicate found - keep the row with more complete data
                existing_count, existing_row = seen[key]
                logger.debug(f"Duplicate key found: {key}, keeping row with {max(non_empty_count, existing_count)} non-empty fields")
                if non_empty_count > existing_count:
                    seen[key] = (non_empty_count, original_row)
                elif non_empty_count == existing_count:
                    # If same completeness, prefer the one with industry data (more likely to be from main table)
                    existing_has_industry = len(existing_row.split(',')) > 2 and existing_row.split(',')[2].strip()
                    current_has_industry = len(cols) > 2 and cols[2].strip()
                    if current_has_industry and not existing_has_industry:
                        seen[key] = (non_empty_count, original_row)
        
        # Reconstruct CSV with header + unique rows
        # Use csv.writer to ensure proper quoting of fields with commas
        result = [header_row] if header_row else []
        
        for _, row in seen.values():
            # Parse the row to get columns
            reader = csv.reader(StringIO(row))
            try:
                cols = list(reader)[0]
                # Write using csv.writer to ensure proper quoting
                # Create a fresh StringIO for each row to avoid issues
                output = StringIO()
                writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator='')
                writer.writerow(cols)
                result.append(output.getvalue())
            except Exception as e:
                # Fallback: use original row if parsing fails
                logger.warning(f"Failed to re-quote row in deduplication: {e}")
                result.append(row)
        
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
    parser = argparse.ArgumentParser(description="LLM-powered SEC filing table scraper")
    parser.add_argument('--ticker', required=True, help='Company ticker symbol')
    parser.add_argument('--filing-type', default='10-Q', choices=['10-Q', '10-K', '8-K', '424B'],
                       help='Type of SEC filing to process')
    parser.add_argument('--year', type=int, help='Year to fetch filing for (default: latest)')
    parser.add_argument('--quarter', choices=['Q1', 'Q2', 'Q3', 'Q4'],
                       help='Quarter for 10-Q filings')
    parser.add_argument('--llm-provider', default='gemini', choices=['openai', 'gemini'],
                       help='LLM provider to use (default: gemini)')
    parser.add_argument('--output-dir', default='output', help='Output directory for CSV files')
    parser.add_argument('--debug-dir', default='debug_tables', help='Debug output directory')
    parser.add_argument('--openai-key', help='OpenAI API key (or set OPENAI_API_KEY env var)')
    parser.add_argument('--google-key', help='Google Gemini API key (or set GOOGLE_API_KEY env var)')
    parser.add_argument('--years-back', type=int, default=0, help='Number of years to look back for historical filings')
    parser.add_argument('--force', action='store_true', help='Re-process filings even if output CSV already exists')

    args = parser.parse_args()

    # Validate arguments
    if args.filing_type == '10-Q' and args.quarter and not args.year:
        parser.error("--year is required when specifying --quarter")

    try:
        # Initialize scraper
        scraper = LLMTableScraper(
            openai_api_key=args.openai_key,
            gemini_api_key=args.google_key,
            llm_provider=args.llm_provider,
            output_dir=args.output_dir,
            debug_dir=args.debug_dir
        )

        # Process the filing
        if args.years_back > 0:
            results = scraper.process_historical_filings(
                ticker=args.ticker,
                filing_type=args.filing_type,
                years_back=args.years_back,
                skip_existing=not args.force
            )
            
            if results:
                print(f"✅ Successfully processed {len(results)} filings for {args.ticker}")
                for res in results:
                    print(f"📄 CSV saved to: {res}")
            else:
                print(f"❌ No investment data found for {args.ticker} in the last {args.years_back} years")
                exit(1)
        else:
            result = scraper.process_filing(
                ticker=args.ticker,
                filing_type=args.filing_type,
                year=args.year
            )

            if result:
                print(f"✅ Successfully processed {args.ticker} {args.filing_type}")
                print(f"📄 CSV saved to: {result}")
            else:
                print(f"❌ No investment data found for {args.ticker} {args.filing_type}")
                exit(1)

    except Exception as e:
        logger.error(f"Error: {e}")
        exit(1)

if __name__ == "__main__":
    main()
