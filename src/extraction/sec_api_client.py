#!/usr/bin/env python3
"""
SEC API Client - A generic client for fetching data from the SEC EDGAR API

This module provides a clean interface for accessing SEC filings and company data
from the EDGAR database. It supports various filing types, company lookups,
and text extraction with proper rate limiting and error handling.
"""

import os
import logging
import time
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
from datetime import date, datetime, timedelta
import re
import json
from dataclasses import dataclass
from urllib.parse import urljoin

# Default path for local company tickers cache (repo root / data / company_tickers.json)
def _default_company_tickers_path() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data" / "company_tickers.json"

logger = logging.getLogger(__name__)

# Request timeout in seconds - prevents indefinite hangs on slow SEC responses
REQUEST_TIMEOUT = 90

# Retry configuration for transient SEC errors (503, timeouts, connection drops)
_RETRY_STATUS_CODES = {429, 503, 504}
_RETRY_ATTEMPTS = 4          # total attempts (initial + 3 retries)
_RETRY_BACKOFF_BASE = 5.0    # seconds; doubles each retry: 5, 10, 20


def _sec_get(url: str, headers: dict, timeout: int = REQUEST_TIMEOUT) -> requests.Response:
    """
    GET a URL with automatic retry on transient SEC errors (503/429/504/timeout).
    Raises the last exception if all retries are exhausted.
    """
    last_exc: Exception = RuntimeError('No attempt made')
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code in _RETRY_STATUS_CODES:
                wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning('SEC returned %s for %s; retrying in %.0fs (attempt %d/%d)',
                               resp.status_code, url, wait, attempt + 1, _RETRY_ATTEMPTS)
                time.sleep(wait)
                last_exc = requests.exceptions.HTTPError(
                    f'{resp.status_code} Server Error', response=resp)
                continue
            return resp
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as exc:
            wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
            logger.warning('SEC request failed (%s) for %s; retrying in %.0fs (attempt %d/%d)',
                           type(exc).__name__, url, wait, attempt + 1, _RETRY_ATTEMPTS)
            time.sleep(wait)
            last_exc = exc
    raise last_exc

@dataclass
class FilingDocument:
    """Represents a document within an SEC filing."""
    url: str
    filename: str
    exhibit_type: Optional[str] = None
    description: Optional[str] = None

@dataclass
class FilingResult:
    """Represents the result of fetching an SEC filing."""
    ticker: str
    filing_type: str
    filing_date: str
    accession_number: str
    text: str
    file_path: Optional[str] = None
    documents: List[FilingDocument] = None
    metadata: Dict[str, Any] = None
    text_map: Dict[str, str] = None  # Added: map of filename to text content
    period_end_date: Optional[str] = None  # Added: report/period end date (e.g., "2025-06-30")


def _is_main_filing_document(doc: FilingDocument, filing_type: str) -> bool:
    """Return True if doc is the main filing (e.g. 10-Q/10-K), not an exhibit .htm."""
    fn = doc.filename.lower()
    # Exhibit files are typically named ex231.htm, ex311.htm, ex99.htm, etc.
    if re.search(r'ex\d{2,}', fn):
        return False
    # Main document has exhibit_type matching the form (10-Q, 10-K, etc.)
    et = (doc.exhibit_type or "").strip().upper()
    main_forms = ['10-Q', '10-K', '10-Q/A', '10-K/A', '8-K', '20-F', '40-F']
    if et in main_forms:
        return True
    # If filename has no exhibit pattern, treat as main (e.g. cswc-20251231.htm)
    return True


class SECAPIClient:
    """
    A generic client for fetching data from the SEC EDGAR API.
    
    This client provides methods to:
    - Look up company information by ticker or CIK
    - Fetch SEC filings of various types
    - Extract and clean text from filings
    - Download multiple filings with date filtering
    """
    
    # Common filing types
    FILING_TYPES = {
        '10-K': 'Annual Report',
        '10-Q': 'Quarterly Report', 
        '8-K': 'Current Report',
        '424B': 'Prospectus',
        'S-1': 'Registration Statement',
        'S-3': 'Registration Statement',
        'DEF 14A': 'Proxy Statement',
        '13F-HR': 'Institutional Investment Manager Holdings'
    }
    
    # Manual overrides for tickers missing from SEC feed or recently renamed
    COMPANY_TICKERS_OVERRIDES = {
        'LRFC': {
            'ticker': 'LRFC',
            'cik_str': 1278752,
            'title': 'Logan Ridge Finance Corp',
        },
    }

    def __init__(self,
                 data_dir: str = "data",
                 user_agent: str = None,
                 rate_limit_delay: float = 0.1,
                 company_tickers_path: Optional[Union[str, Path]] = None):
        """
        Initialize the SEC API client.

        Args:
            data_dir: Directory to store downloaded filings
            user_agent: Custom user agent string (required by SEC)
            rate_limit_delay: Delay between requests in seconds
            company_tickers_path: Path to local company_tickers.json. If set, load from
                here first; if file missing, download from SEC and save here. Default:
                repo data/company_tickers.json. Set to False to disable local cache.
        """
        self.data_dir = data_dir
        self.rate_limit_delay = rate_limit_delay
        os.makedirs(data_dir, exist_ok=True)

        if company_tickers_path is False:
            self._company_tickers_path = None
        else:
            self._company_tickers_path = Path(company_tickers_path) if company_tickers_path else _default_company_tickers_path()

        # Set user agent - SEC requires this
        if user_agent is None:
            user_agent = "SEC-API-Client/1.0 (your-email@domain.com)"
        self.headers = {'User-Agent': user_agent}

        # Load company data (local first, then download if needed)
        self._company_tickers = self._load_company_tickers()

    def _company_data_to_ticker_map(self, company_data: Dict[str, Any]) -> Dict[str, Any]:
        """Build ticker -> info map from SEC-style company_data (keys 0,1,2... or cik keys)."""
        ticker_map: Dict[str, Any] = {}
        for _key, data in company_data.items():
            if isinstance(data, dict) and data.get('ticker'):
                ticker_map[data['ticker']] = data
        for ticker, info in self.COMPANY_TICKERS_OVERRIDES.items():
            ticker_map[ticker] = info
        return ticker_map

    def _load_company_tickers(self) -> Dict[str, Any]:
        """Load company ticker -> CIK mapping. Prefer local file; download and cache if missing."""
        manual_overrides = self.COMPANY_TICKERS_OVERRIDES

        # 1) Try local file first (if we have a cache path)
        if self._company_tickers_path and self._company_tickers_path.exists():
            try:
                with open(self._company_tickers_path, 'r', encoding='utf-8') as f:
                    company_data = json.load(f)
                ticker_map = self._company_data_to_ticker_map(company_data)
                if ticker_map:
                    logger.info(f"Loaded {len(ticker_map)} companies from local {self._company_tickers_path}")
                    return ticker_map
            except Exception as e:
                logger.warning(f"Could not load local company tickers from {self._company_tickers_path}: {e}")

        # 2) Download from SEC
        url = "https://www.sec.gov/files/company_tickers.json"
        logger.info(f"Loading company tickers from {url}")
        try:
            response = requests.get(url, headers=self.headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            company_data = response.json()

            ticker_map = self._company_data_to_ticker_map(company_data)

            # Save to local cache for next time
            if self._company_tickers_path and ticker_map:
                self._company_tickers_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._company_tickers_path, 'w', encoding='utf-8') as f:
                    json.dump(company_data, f, indent=0)
                logger.info(f"Saved company tickers to {self._company_tickers_path}")

            logger.info(f"Loaded {len(ticker_map)} companies (with overrides)")
            return ticker_map

        except Exception as e:
            logger.error(f"Failed to load company tickers: {e}")
            return {}

    def _dynamic_cik_lookup(self, ticker: str) -> Optional[str]:
        """
        Dynamically look up CIK for a ticker by searching SEC EDGAR.
        Fallback method when ticker is not in the pre-loaded mapping.
        """
        search_url = f"https://www.sec.gov/cgi-bin/browse-edgar?company={ticker}&owner=exclude&action=getcompany&Find=Search&output=atom"
        
        try:
            logger.info(f"Attempting dynamic CIK lookup for {ticker}")
            response = requests.get(search_url, headers=self.headers)
            response.raise_for_status()
            
            # Try XML parsing first
            soup = BeautifulSoup(response.content, 'xml')
            cik_element = soup.find('CIK')
            if cik_element and cik_element.text:
                cik = cik_element.text.strip().zfill(10)
                logger.info(f"Dynamic lookup found CIK {cik} for ticker {ticker}")
                self._update_company_cache(ticker, cik)
                return cik
                
            # Fallback to regex
            match = re.search(r'/Archives/edgar/data/(\d{10})/', response.text)
            if match:
                cik = match.group(1).zfill(10)
                logger.info(f"Dynamic regex lookup found CIK {cik} for ticker {ticker}")
                self._update_company_cache(ticker, cik)
                return cik

            logger.warning(f"Dynamic CIK lookup failed for ticker: {ticker}")
            return None
            
        except Exception as e:
            logger.error(f"Error during dynamic CIK lookup for {ticker}: {e}")
            return None

    def _update_company_cache(self, ticker: str, cik: str):
        """Update the in-memory company cache with new ticker-CIK mapping."""
        if ticker.upper() not in self._company_tickers:
            self._company_tickers[ticker.upper()] = {
                'cik_str': int(cik),
                'ticker': ticker.upper(),
                'title': ''
            }

    def get_cik(self, ticker: str) -> Optional[str]:
        """
        Get the CIK for a given ticker symbol.
        
        Args:
            ticker: Company ticker symbol
            
        Returns:
            CIK as 10-digit string, or None if not found
        """
        logger.info(f"Looking up CIK for ticker: {ticker}")
        
        # Try cached data first
        company_info = self._company_tickers.get(ticker.upper())
        if company_info:
            cik = str(company_info['cik_str']).zfill(10)
            logger.info(f"Found CIK {cik} for ticker {ticker} in cache")
            return cik
        
        # Try dynamic lookup
        logger.warning(f"CIK not found in cache for ticker: {ticker}. Attempting dynamic lookup.")
        return self._dynamic_cik_lookup(ticker)

    def get_company_info(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get comprehensive company information.
        
        Args:
            ticker: Company ticker symbol
            
        Returns:
            Dictionary with company information, or None if not found
        """
        cik = self.get_cik(ticker)
        if not cik:
            return None
            
        return {
            'ticker': ticker.upper(),
            'cik': cik,
            'cik_int': int(cik),
            'title': self._company_tickers.get(ticker.upper(), {}).get('title', ''),
            'sic': self._company_tickers.get(ticker.upper(), {}).get('sic', ''),
            'sicDescription': self._company_tickers.get(ticker.upper(), {}).get('sicDescription', '')
        }

    def get_filing_index_url(self, ticker: str, filing_type: str, cik: Optional[str] = None,
                            year: Optional[int] = None, quarter: Optional[str] = None,
                            min_date: Optional[str] = None) -> Optional[str]:
        """
        Get the URL for a filing of a given type, optionally filtered by date/quarter.

        Args:
            ticker: Company ticker symbol
            filing_type: Type of filing (e.g., "10-K", "10-Q")
            cik: Optional CIK number to use directly
            year: Optional year to filter by
            quarter: For 10-Q: Q1 (Mar), Q2 (Jun), Q3 (Sep). Requires year.
            min_date: Optional minimum date in YYYY-MM-DD format

        Returns:
            URL to the filing index page, or None if not found
        """
        if cik is None:
            cik = self.get_cik(ticker)
        if not cik:
            return None

        # 10-Q quarter-end months
        quarter_months = {'Q1': 3, 'Q2': 6, 'Q3': 9}

        try:
            submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            response = requests.get(submissions_url, headers=self.headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            submissions = response.json()

            recent_filings = submissions['filings']['recent']

            def find_matching_filings(year_filter: Optional[int], quarter_filter: Optional[str],
                                     min_date_filter: Optional[str]):
                matches = []
                min_date_obj = None
                if min_date_filter:
                    try:
                        min_date_obj = datetime.strptime(min_date_filter, '%Y-%m-%d').date()
                    except ValueError:
                        min_date_filter = None
                        min_date_obj = None

                target_month = quarter_months.get(quarter_filter) if quarter_filter else None

                for i, form in enumerate(recent_filings['form']):
                    if form != filing_type:
                        continue

                    report_date_str = recent_filings.get('reportDate', [None])[i]
                    if not report_date_str:
                        continue

                    try:
                        report_date = datetime.strptime(report_date_str, '%Y-%m-%d').date()
                    except (ValueError, TypeError):
                        continue

                    if min_date_obj and report_date < min_date_obj:
                        continue
                    if year_filter is not None and report_date.year != year_filter:
                        continue
                    if target_month is not None and report_date.month != target_month:
                        continue

                    matches.append((i, report_date))
                return matches

            matching_filings = find_matching_filings(year, quarter, min_date)
            if not matching_filings and year is not None:
                logger.info(f"No {filing_type} found for {ticker} in {year}" +
                           (f" {quarter}" if quarter else "") + "; retrying without year filter.")
                matching_filings = find_matching_filings(None, None, min_date)
            
            if not matching_filings:
                year_msg = f" for year {year}" if year else ""
                min_date_msg = f" after {min_date}" if min_date else ""
                logger.warning(f"No {filing_type} found for {ticker} (CIK: {cik}){year_msg}{min_date_msg}")
                return None
            
            # Sort by date (most recent first) and return the first one
            matching_filings.sort(key=lambda x: x[1] if x[1] else date.min, reverse=True)
            i, report_date = matching_filings[0]
            
            accession_number = recent_filings['accessionNumber'][i]
            accession_number_no_hyphens = accession_number.replace('-', '')
            
            filing_index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_number_no_hyphens}/{accession_number}-index.html"
            date_str = report_date.strftime('%Y-%m-%d') if report_date else 'unknown'
            logger.info(f"Found {filing_type} index for {ticker} (period end: {date_str}): {filing_index_url}")
            
            # Store the period end date in metadata for use in year-end filtering
            if not hasattr(self, '_cached_period_date'):
                self._cached_period_date = {}
            self._cached_period_date[filing_index_url] = date_str
            
            return filing_index_url
            
        except Exception as e:
            logger.error(f"Error fetching filing URL for {ticker}: {e}")
            return None

    def get_latest_filing_date(self, ticker: str, filing_types: List[str], 
                              cik: Optional[str] = None) -> Optional[date]:
        """
        Get the date of the most recent filing of the specified types.
        
        Args:
            ticker: Company ticker symbol
            filing_types: List of filing types to check (e.g., ["10-Q", "10-K"])
            cik: Optional CIK number to use directly
            
        Returns:
            Date of the most recent filing, or None if not found
        """
        if cik is None:
            cik = self.get_cik(ticker)
        if not cik:
            return None

        try:
            submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            response = requests.get(submissions_url, headers=self.headers)
            response.raise_for_status()
            submissions = response.json()

            recent_filings = submissions['filings']['recent']
            
            latest_date = None
            
            for i, form in enumerate(recent_filings['form']):
                if form in filing_types:
                    report_date_str = recent_filings.get('reportDate', [None])[i]
                    if not report_date_str:
                        continue
                    
                    try:
                        report_date = datetime.strptime(report_date_str, '%Y-%m-%d').date()
                        if latest_date is None or report_date > latest_date:
                            latest_date = report_date
                    except (ValueError, TypeError):
                        continue
            
            return latest_date
            
        except Exception as e:
            logger.error(f"Error fetching latest filing date for {ticker}: {e}")
            return None

    def get_documents_from_index(self, index_url: str) -> List[FilingDocument]:
        """
        Parse a filing's index page to get all document URLs and metadata.
        Prioritize documents likely to contain securities information.
        
        Args:
            index_url: URL to the filing index page
            
        Returns:
            List of FilingDocument objects, sorted by priority
        """
        if not index_url:
            return []
            
        try:
            response = _sec_get(index_url, headers=self.headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            documents = []
            base_url = "https://www.sec.gov"
            
            for row in soup.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) < 3:
                    continue
                    
                # Link is typically in the 3rd cell (index 2)
                link = cells[2].find('a')
                if not link or not link.get('href'):
                    continue
                    
                doc_path = link.get('href')
                
                # Skip XBRL viewer links - we want raw documents
                if 'ix?doc=' in doc_path:
                    doc_path = doc_path.replace('/ix?doc=', '')
                    
                if doc_path.startswith('/'):
                    doc_url = urljoin(base_url, doc_path)
                    filename = doc_url.split('/')[-1]
                    
                    # Skip image files and other non-essential documents
                    if self._should_skip_document(filename):
                        continue
                    
                    # Try to get exhibit type and description
                    exhibit_type = None
                    description = None
                    
                    if len(cells) > 3:
                        exhibit_type = cells[3].get_text(strip=True)
                    if len(cells) > 4:
                        description = cells[4].get_text(strip=True)
                        
                    # Fallback: check first two cells for exhibit type
                    if not exhibit_type:
                        for idx in [0, 1]:
                            if idx < len(cells):
                                text = cells[idx].get_text(strip=True)
                                if text.startswith('EX-'):
                                    exhibit_type = text
                                    break
                    
                    documents.append(FilingDocument(
                        url=doc_url,
                        filename=filename,
                        exhibit_type=exhibit_type,
                        description=description
                    ))
            
            # Prioritize documents likely to contain securities information
            def doc_priority(doc):
                """Return priority score (lower = higher priority)"""
                filename = doc.filename.lower()
                description = (doc.description or "").lower()
                exhibit = (doc.exhibit_type or "").lower()
                
                # Main document gets highest priority
                # Check for main form types in filename or exhibit type
                if exhibit in ['10-k', '10-q', '8-k', '20-f', '40-f', '10-k/a', '10-q/a']:
                    return 0
                
                if any(keyword in filename for keyword in ['s-3.htm', 's-1.htm', '424b5.htm', 'prospectus']):
                    return 1
                
                # Securities-related exhibits
                if any(keyword in description for keyword in ['prospectus', 'indenture', 'certificate of designation', 'securities']):
                    return 2
                    
                # Other exhibits with potential securities info
                if exhibit.startswith('ex-') and any(num in exhibit for num in ['4.', '3.', '10.']):
                    return 3
                    
                # Other documents
                return 4
            
            # Sort by priority
            documents.sort(key=doc_priority)
            return documents
            
        except Exception as e:
            logger.error(f"Could not parse document URLs from index {index_url}: {e}")
            return []

    def _extract_xbrl_text(self, xml_soup) -> str:
        """Extract meaningful text content from XBRL XML files with better structure preservation."""
        if not xml_soup:
            return ""

        extracted_text = []
        structured_data = {}

        # First pass: collect structured data by context
        contexts = xml_soup.find_all(['context', 'xbrli:context'])
        context_map = {}
        for context in contexts:
            context_id = context.get('id', '')
            if context_id:
                # Extract entity and period information from context
                entity_info = context.find(['entity', 'xbrli:entity'])
                period_info = context.find(['period', 'xbrli:period'])
                if entity_info and period_info:
                    context_map[context_id] = {
                        'entity': entity_info.get_text(strip=True),
                        'period': period_info.get_text(strip=True)
                    }

        # Second pass: extract facts with context
        for element in xml_soup.find_all():
            # Skip schema elements and metadata
            if element.name and element.name.startswith(('xs:', 'xsd:', 'link:', 'xbrli:', 'xbrldi:')):
                continue

            # Special handling for DEI (Document and Entity Information) elements
            # These contain important security descriptions and identifiers
            is_dei_element = element.name and element.name.startswith('dei:')
            if is_dei_element:
                # DEI elements often contain security descriptions with dividend rates
                text_content = element.get_text(strip=True)
                if text_content and len(text_content) > 5:  # DEI content is usually descriptive
                    extracted_text.append(f"{element.name}: {text_content}")
                continue

            # Get text content
            text_content = element.get_text(strip=True)
            if not text_content or len(text_content) <= 1:
                continue

            element_name = element.name or ""

            # Get context reference
            context_ref = element.get('contextRef', '')
            unit_ref = element.get('unitRef', '')

            # Format structured data
            if any(keyword in element_name.lower() for keyword in [
                'preferred', 'stock', 'series', 'dividend', 'rate', 'share',
                'outstanding', 'par', 'value', 'cumulative', 'callable', 'redemption',
                'depositary', 'perpetual', 'liquidation'
            ]):
                # Financial data - format with context
                formatted = f"{element_name}"
                if context_ref and context_ref in context_map:
                    ctx = context_map[context_ref]
                    formatted += f" [{ctx['period']}]"
                if unit_ref:
                    formatted += f" ({unit_ref})"
                formatted += f": {text_content}"

                # Group by category for better extraction
                category = 'other'
                if 'preferred' in element_name.lower() and 'share' in element_name.lower():
                    category = 'preferred_shares'
                elif 'dividend' in element_name.lower():
                    category = 'dividends'
                elif 'outstanding' in element_name.lower():
                    category = 'outstanding'
                elif 'rate' in element_name.lower():
                    category = 'rates'

                if category not in structured_data:
                    structured_data[category] = []
                structured_data[category].append(formatted)

                extracted_text.append(formatted)

            elif 'us-gaap:' in element_name or 'rily:' in element_name:
                # XBRL taxonomy elements - include for context
                formatted = f"{element_name}: {text_content}"
                extracted_text.append(formatted)

        # Add structured sections for better parsing
        for category, items in structured_data.items():
            if items:
                extracted_text.append(f"\n--- {category.upper()} ---")
                extracted_text.extend(items)

        # Join with newlines to preserve some structure
        combined_text = '\n'.join(extracted_text)

        # Clean the combined text
        return self.clean_text(combined_text)

    def clean_text(self, text: str) -> str:
        """Clean and normalize text content."""
        if not text:
            return ""

        # Handle encoding issues
        if isinstance(text, bytes):
            try:
                text = text.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    text = text.decode('latin-1')
                except UnicodeDecodeError:
                    text = text.decode('utf-8', errors='replace')

        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)

        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)

        # Remove special characters that cause issues
        text = text.replace('\x00', '')  # Remove null bytes
        text = text.replace('\ufffd', '')  # Remove replacement characters

        return text.strip()

    def fetch_filing(self, ticker: str, filing_type: str = "10-K", 
                    cik: Optional[str] = None, save_to_file: bool = True) -> Optional[FilingResult]:
        """
        Fetch the latest SEC filing for a company.
        
        Args:
            ticker: Company ticker symbol
            filing_type: Type of filing to fetch
            cik: Optional CIK number to use directly
            save_to_file: Whether to save the filing text to a file
            
        Returns:
            FilingResult object with filing data, or None if not found
        """
        index_url = self.get_filing_index_url(ticker, filing_type, cik=cik)
        if not index_url:
            return None
            
        documents = self.get_documents_from_index(index_url)
        if not documents:
            logger.warning(f"No documents found for filing {index_url}")
            return None
            
        full_text = ""
        accession_number = index_url.split('/')[-1].replace('-index.html', '')
        
        try:
            for doc in documents:
                try:
                    logger.info(f"Fetching document: {doc.filename}")
                    response = _sec_get(doc.url, headers=self.headers)
                    response.raise_for_status()

                    # Add document separator
                    label = doc.filename
                    if doc.exhibit_type:
                        label = f"{doc.exhibit_type} ({doc.filename})"
                    full_text += f"\n\n--- DOCUMENT: {label} ---\n\n"
                    
                    # Handle different content types
                    content_type = response.headers.get('content-type', '').lower()
                    filename = doc.filename.lower()

                    # Check if this is an XBRL file (contains structured financial data)
                    is_xbrl_file = (
                        'xml' in content_type or filename.endswith('.xml')
                    ) and (
                        # XBRL instance documents typically have date patterns like 20240930
                        re.search(r'\d{8}', filename) and
                        ('htm' in filename or 'xbrl' in filename or '_' in filename)
                    )

                    if is_xbrl_file:
                        # Process XBRL XML files - they contain valuable financial data
                        try:
                            # Parse as XML and extract text content
                            if response.encoding is None:
                                response.encoding = 'utf-8'

                            xml_content = response.text
                            # Parse XML and extract all text content
                            xml_soup = BeautifulSoup(xml_content, 'xml')
                            # Extract text from all elements, preserving structure
                            xbrl_text = self._extract_xbrl_text(xml_soup)
                            full_text += xbrl_text

                        except Exception as e:
                            logger.warning(f"Failed to process XBRL file {doc.filename}: {e}")
                            # Fallback: try to extract raw text
                            try:
                                raw_text = self.clean_text(xml_content)
                                if raw_text.strip():
                                    full_text += raw_text
                            except Exception as e2:
                                logger.warning(f"Failed to extract raw text from XBRL file {doc.filename}: {e2}")
                                continue

                    elif 'xml' in content_type or filename.endswith('.xml'):
                        # Skip non-XBRL XML files (schema files, etc.) that may cause issues
                        continue
                    elif 'image' in content_type or any(doc.filename.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                        # Skip image files
                        continue
                    else:
                        # Process text/HTML content with better encoding handling
                        try:
                            # Try to detect encoding
                            if response.encoding is None:
                                response.encoding = 'utf-8'

                            text_content = response.text

                            # Parse and clean text
                            soup = BeautifulSoup(text_content, 'html.parser')
                            cleaned_text = self.clean_text(soup.get_text())
                            full_text += cleaned_text

                        except UnicodeDecodeError:
                            # Fallback to binary content with manual encoding
                            try:
                                text_content = response.content.decode('utf-8', errors='ignore')
                                soup = BeautifulSoup(text_content, 'html.parser')
                                cleaned_text = self.clean_text(soup.get_text())
                                full_text += cleaned_text
                            except Exception as e:
                                logger.warning(f"Failed to decode document {doc.filename}: {e}")
                                continue
                    
                except Exception as e:
                    logger.warning(f"Failed to download document {doc.url}: {e}")
                    continue
                    
            if not full_text.strip():
                logger.error(f"Failed to extract any text from filing {index_url}")
                return None
                
            # Extract filing date from index page
            filing_date = date.today().isoformat()  # Default fallback
            try:
                # Fetch the index page to extract filing date
                index_response = _sec_get(index_url, headers=self.headers)
                index_response.raise_for_status()
                index_soup = BeautifulSoup(index_response.content, 'html.parser')
                
                # Look for filing date in the index page
                # SEC index pages typically have the filing date in a table or specific format
                # Common patterns: "Filing Date:", "Date Filed:", or in a table cell
                index_text = index_soup.get_text()
                
                # Try to find date patterns (YYYY-MM-DD or MM/DD/YYYY)
                from datetime import datetime
                
                # Look for "Filing Date" or "Date Filed" followed by a date
                date_patterns = [
                    r'(?:Filing Date|Date Filed)[:\s]+(\d{4}-\d{2}-\d{2})',
                    r'(?:Filing Date|Date Filed)[:\s]+(\d{1,2}/\d{1,2}/\d{4})',
                    r'<td[^>]*>Filing Date</td>\s*<td[^>]*>(\d{4}-\d{2}-\d{2})',
                    r'<td[^>]*>Filing Date</td>\s*<td[^>]*>(\d{1,2}/\d{1,2}/\d{4})',
                ]
                
                for pattern in date_patterns:
                    match = re.search(pattern, index_text, re.IGNORECASE)
                    if match:
                        date_str = match.group(1)
                        # Try to parse and normalize to YYYY-MM-DD
                        try:
                            if '/' in date_str:
                                # MM/DD/YYYY format
                                parsed_date = datetime.strptime(date_str, '%m/%d/%Y')
                            else:
                                # Already YYYY-MM-DD
                                parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
                            filing_date = parsed_date.date().isoformat()
                            logger.info(f"Extracted filing date from index page: {filing_date}")
                            break
                        except ValueError:
                            continue
                
                # If not found in text, try looking in table cells
                if filing_date == date.today().isoformat():
                    for td in index_soup.find_all('td'):
                        td_text = td.get_text(strip=True)
                        if 'filing date' in td_text.lower() or 'date filed' in td_text.lower():
                            # Check next sibling or parent row for date
                            parent_row = td.find_parent('tr')
                            if parent_row:
                                for cell in parent_row.find_all('td'):
                                    cell_text = cell.get_text(strip=True)
                                    # Check if it looks like a date
                                    if re.match(r'\d{4}-\d{2}-\d{2}', cell_text):
                                        filing_date = cell_text
                                        logger.info(f"Extracted filing date from table: {filing_date}")
                                        break
                                    elif re.match(r'\d{1,2}/\d{1,2}/\d{4}', cell_text):
                                        try:
                                            parsed_date = datetime.strptime(cell_text, '%m/%d/%Y')
                                            filing_date = parsed_date.date().isoformat()
                                            logger.info(f"Extracted filing date from table: {filing_date}")
                                            break
                                        except ValueError:
                                            continue
            except Exception as e:
                logger.warning(f"Could not extract filing date from index page: {e}, using today's date as fallback")
                
            file_path = None
            
            if save_to_file:
                # Sanitize filing_type for filename (replace "/" with "_")
                safe_filing_type = filing_type.replace("/", "_")
                file_path = os.path.join(self.data_dir, f"{ticker}_{safe_filing_type}_{accession_number}.txt")
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(full_text)
                    
            # Get period end date from cache if available
            period_end = getattr(self, '_cached_period_date', {}).get(index_url)
            
            return FilingResult(
                ticker=ticker,
                filing_type=filing_type,
                filing_date=filing_date,
                accession_number=accession_number,
                text=full_text,
                file_path=file_path,
                documents=documents,
                metadata={'index_url': index_url},
                text_map={},  # Placeholder for generic fetch
                period_end_date=period_end
            )
        except Exception as e:
            logger.error(f"Unexpected error during filing fetch for {ticker}: {e}")
            return None

    def get_filing_text(self, ticker: str, filing_type: str = "10-K", 
                       cik: Optional[str] = None) -> Optional[str]:
        """
        Get the text content of the latest SEC filing.
        
        Args:
            ticker: Company ticker symbol
            filing_type: Type of filing to fetch
            cik: Optional CIK number to use directly
        
        Returns:
            Filing text content, or None if not found
        """
        result = self.fetch_filing(ticker, filing_type, cik=cik, save_to_file=False)
        return result.text if result else None

    def get_filing_with_fallback(self, ticker: str, primary_filing_type: str = "10-K", 
                                fallback_filing_types: List[str] = None, 
                                cik: Optional[str] = None) -> Optional[FilingResult]:
        """
        Get filing with fallback to other filing types if primary not found.
        
        Args:
            ticker: Company ticker symbol
            primary_filing_type: Primary filing type to look for
            fallback_filing_types: List of fallback filing types
            cik: Optional CIK number to use directly
        
        Returns:
            FilingResult object, or None if not found
        """
        if fallback_filing_types is None:
            fallback_filing_types = ["424B", "8-K"]
        
        # Try primary filing type first
        result = self.fetch_filing(ticker, primary_filing_type, cik=cik)
        if result:
            result.metadata = result.metadata or {}
            result.metadata['source_type'] = 'primary'
            return result
        
        # Try fallback filing types
        for fallback_type in fallback_filing_types:
            result = self.fetch_filing(ticker, fallback_type, cik=cik)
            if result:
                result.metadata = result.metadata or {}
                result.metadata['source_type'] = 'fallback'
                result.metadata['fallback_type'] = fallback_type
                return result
        
        return None

    def get_multiple_filing_types(self, ticker: str, 
                                filing_types: List[str] = None) -> Dict[str, Optional[str]]:
        """
        Get multiple filing types for a company.
        
        Args:
            ticker: Company ticker symbol
            filing_types: List of filing types to fetch
        
        Returns:
            Dictionary mapping filing type to text content
        """
        if filing_types is None:
            filing_types = ["10-K", "424B", "8-K"]
        
        results = {}
        for filing_type in filing_types:
            text = self.get_filing_text(ticker, filing_type)
            results[filing_type] = text
        
        return results
    
    def get_all_424b_filings(self, ticker: str, max_filings: int = 100,
                            filing_variants: List[str] = None) -> List[Dict]:
        """
        Get all 424B filings (all variants) for a company.
        
        Args:
            ticker: Company ticker symbol
            max_filings: Maximum number of filings to return
            filing_variants: List of 424B variants to include (default: all)
        
        Returns:
            List of dicts with filing metadata:
            - form: Filing type (e.g., "424B5", "424B2")
            - date: Filing date (YYYY-MM-DD)
            - accession: Accession number
            - description: Primary document filename
            - url: URL to filing viewer
            - index_url: URL to filing index page (for fetching content)
        """
        if filing_variants is None:
            # Include all common 424B variants
            filing_variants = ['424B', '424B1', '424B2', '424B3', '424B4', '424B5', '424B7']
        
        cik = self.get_cik(ticker)
        if not cik:
            logger.warning(f"Could not find CIK for {ticker}")
            return []
        
        try:
            submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            response = requests.get(submissions_url, headers=self.headers)
            response.raise_for_status()
            submissions = response.json()
            
            recent_filings = submissions['filings']['recent']
            
            # Find all 424B filings
            filings_424b = []
            for i, form in enumerate(recent_filings['form']):
                if form in filing_variants:
                    accession = recent_filings['accessionNumber'][i]
                    accession_no_hyphens = accession.replace('-', '')
                    
                    filing_info = {
                        'form': form,
                        'date': recent_filings['filingDate'][i],
                        'accession': accession,
                        'description': recent_filings['primaryDocument'][i],
                        'url': f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_hyphens}/{recent_filings['primaryDocument'][i]}",
                        'index_url': f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_hyphens}/{accession}-index.html"
                    }
                    filings_424b.append(filing_info)
                    
                    if len(filings_424b) >= max_filings:
                        break
            
            logger.info(f"Found {len(filings_424b)} 424B filings for {ticker}")
            return filings_424b
            
        except Exception as e:
            logger.error(f"Error fetching 424B filings for {ticker}: {e}")
            return []
    
    def get_filing_by_accession(self, ticker: str, accession: str, filing_type: str) -> Optional[str]:
        """
        Get a specific filing by its accession number.
        
        Args:
            ticker: Company ticker symbol
            accession: Accession number (e.g., "0001104659-23-028831")
            filing_type: Type of filing (e.g., "424B5")
        
        Returns:
            Filing text content, or None if not found
        """
        cik = self.get_cik(ticker)
        if not cik:
            return None
        
        try:
            # Build the index URL
            accession_no_hyphens = accession.replace('-', '')
            index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_hyphens}/{accession}-index.html"
            
            logger.debug(f"Fetching filing by accession: {accession} from {index_url}")
            
            # Use existing method to fetch by index URL
            result = self.fetch_filing_by_index_url(index_url, ticker, filing_type, save_to_file=False)
            
            return result.text if result else None
            
        except Exception as e:
            logger.error(f"Error fetching filing by accession {accession}: {e}")
            return None

    def get_historical_10q_filings(self, ticker: str, years_back: int = 5,
                                   start_date: Optional[str] = None,
                                   end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all historical 10-Q filings for a ticker within a date range.
        
        Args:
            ticker: Company ticker symbol
            years_back: Number of years to look back (used if start_date/end_date not provided)
            start_date: Start date in YYYY-MM-DD format (optional)
            end_date: End date in YYYY-MM-DD format (optional, defaults to today)
            
        Returns:
            List of dicts with filing metadata:
            - form: Filing type (e.g., "10-Q")
            - date: Filing date (YYYY-MM-DD)
            - accession: Accession number
            - description: Primary document filename
            - index_url: URL to filing index page
            - period_end_date: Period end date if available
        """
        cik = self.get_cik(ticker)
        if not cik:
            logger.warning(f"Could not find CIK for {ticker}")
            return []
        
        try:
            submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            response = requests.get(submissions_url, headers=self.headers)
            response.raise_for_status()
            submissions = response.json()
            
            recent_filings = submissions['filings']['recent']
            
            # Set date range
            if end_date:
                end_datetime = datetime.strptime(end_date, '%Y-%m-%d')
            else:
                end_datetime = datetime.now()
                
            if start_date:
                start_datetime = datetime.strptime(start_date, '%Y-%m-%d')
            else:
                start_datetime = end_datetime - timedelta(days=years_back * 365)
            
            # Find all 10-Q filings
            filings_10q = []
            for i, form in enumerate(recent_filings['form']):
                if form == '10-Q':
                    filing_date_str = recent_filings['filingDate'][i]
                    filing_date = datetime.strptime(filing_date_str, '%Y-%m-%d')
                    
                    # Check if within date range
                    if filing_date < start_datetime or filing_date > end_datetime:
                        continue
                    
                    accession = recent_filings['accessionNumber'][i]
                    accession_no_hyphens = accession.replace('-', '')
                    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_hyphens}/{accession}-index.html"
                    report_date_str = recent_filings.get('reportDate', [None])[i]
                    period_end_date = report_date_str if report_date_str else None
                    if not hasattr(self, '_cached_period_date'):
                        self._cached_period_date = {}
                    if period_end_date:
                        self._cached_period_date[index_url] = period_end_date
                    
                    filing_info = {
                        'form': form,
                        'date': filing_date_str,
                        'accession': accession,
                        'description': recent_filings['primaryDocument'][i],
                        'index_url': index_url,
                        'period_end_date': period_end_date,
                    }
                    filings_10q.append(filing_info)
            
            # Check for older filings in prior submissions
            if 'filings' in submissions and 'files' in submissions['filings']:
                for file_info in submissions['filings']['files']:
                    if file_info.get('name') and file_info['name'].startswith('CIK'):
                        # This indicates there are more filings in separate files
                        # We'd need to fetch those files individually, but for now
                        # we'll rely on the 'recent' filings which typically cover 2+ years
                        pass
            
            logger.info(f"Found {len(filings_10q)} 10-Q filings for {ticker} between {start_datetime.date()} and {end_datetime.date()}")
            return filings_10q
            
        except Exception as e:
            logger.error(f"Error fetching historical 10-Q filings for {ticker}: {e}")
            return []

    def get_historical_10k_filings(self, ticker: str, years_back: int = 5,
                                   start_date: Optional[str] = None,
                                   end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all historical 10-K filings for a ticker within a date range.
        10-K = annual report (year-end / Q4 snapshot). Filed in Feb–Mar after year end.
        """
        cik = self.get_cik(ticker)
        if not cik:
            logger.warning(f"Could not find CIK for {ticker}")
            return []
        try:
            submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            response = requests.get(submissions_url, headers=self.headers)
            response.raise_for_status()
            submissions = response.json()
            recent_filings = submissions['filings']['recent']
            if end_date:
                end_datetime = datetime.strptime(end_date, '%Y-%m-%d')
            else:
                end_datetime = datetime.now()
            if start_date:
                start_datetime = datetime.strptime(start_date, '%Y-%m-%d')
            else:
                start_datetime = end_datetime - timedelta(days=years_back * 365)
            filings_10k = []
            for i, form in enumerate(recent_filings['form']):
                if form == '10-K':
                    filing_date_str = recent_filings['filingDate'][i]
                    filing_date = datetime.strptime(filing_date_str, '%Y-%m-%d')
                    if filing_date < start_datetime or filing_date > end_datetime:
                        continue
                    accession = recent_filings['accessionNumber'][i]
                    accession_no_hyphens = accession.replace('-', '')
                    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_hyphens}/{accession}-index.html"
                    report_date_str = recent_filings.get('reportDate', [None])[i]
                    period_end_date = report_date_str if report_date_str else None
                    if not hasattr(self, '_cached_period_date'):
                        self._cached_period_date = {}
                    if period_end_date:
                        self._cached_period_date[index_url] = period_end_date
                    filing_info = {
                        'form': form,
                        'date': filing_date_str,
                        'accession': accession,
                        'description': recent_filings['primaryDocument'][i],
                        'index_url': index_url,
                        'period_end_date': period_end_date,
                    }
                    filings_10k.append(filing_info)
            logger.info(f"Found {len(filings_10k)} 10-K filings for {ticker} between {start_datetime.date()} and {end_datetime.date()}")
            return filings_10k
        except Exception as e:
            logger.error(f"Error fetching historical 10-K filings for {ticker}: {e}")
            return []

    def download_filings_by_date_range(self, ticker: str, filing_types: List[str],
                                     months_back: int = 3,
                                     max_results: Optional[int] = None) -> List[str]:
        """
        Download all filings of specified types for a ticker from the last N months.
        
        Args:
            ticker: Company ticker symbol
            filing_types: List of filing types to download
            months_back: Number of months to look back
            max_results: Optional cap on number of filings to download (per call)
        
        Returns:
            List of file paths for downloaded filings
        """
        cik = self.get_cik(ticker)
        if not cik:
            logger.error(f"CIK not found for ticker: {ticker}")
            return []
            
        submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        
        try:
            response = requests.get(submissions_url, headers=self.headers)
            response.raise_for_status()
            submissions = response.json()
            
            recent_filings = submissions['filings']['recent']
            accession_numbers = recent_filings['accessionNumber']
            forms = recent_filings['form']
            filing_dates = recent_filings['filingDate']
            
            # Calculate cutoff date
            cutoff_date = datetime.now() - timedelta(days=months_back * 30)
            downloaded_files = []
            seen_accessions = set()
            count = 0
            
            for i, form in enumerate(forms):
                if form not in filing_types:
                    continue
                    
                filing_date = datetime.strptime(filing_dates[i], '%Y-%m-%d')
                if filing_date < cutoff_date:
                    continue
                    
                accession_number = accession_numbers[i]
                if accession_number in seen_accessions:
                    continue
                seen_accessions.add(accession_number)
                
                # Build index URL and fetch this specific filing
                try:
                    accession_folder = accession_number.replace('-', '')
                    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_folder}/{accession_number}-index.html"
                    result = self.fetch_filing_by_index_url(index_url=index_url, ticker=ticker, filing_type=form, save_to_file=True)
                    if result and result.file_path:
                        downloaded_files.append(result.file_path)
                        count += 1
                        if max_results is not None and count >= max_results:
                            break
                except Exception as e:
                    logger.warning(f"Failed to fetch filing by accession {accession_number}: {e}")
                    
            return downloaded_files
            
        except Exception as e:
            logger.error(f"Error downloading filings for {ticker}: {e}")
            return [] 

    def fetch_filing_by_index_url(self, index_url: str, ticker: str, filing_type: str,
                                  save_to_file: bool = True,
                                  document_types: Optional[List[str]] = None,
                                  main_document_only: bool = False) -> Optional[FilingResult]:
        """
        Fetch a filing given a specific index URL, avoiding extra lookups.
        
        Args:
            index_url: URL to the filing index page
            ticker: Company ticker symbol
            filing_type: Type of filing
            save_to_file: Whether to save to local storage
            document_types: Optional list of extensions to include (e.g. ['.htm', '.xml'])
            main_document_only: If True, fetch only the main filing document (exclude exhibit .htm files).
        """
        try:
            documents = self.get_documents_from_index(index_url)
            if not documents:
                logger.warning(f"No documents found for filing {index_url}")
                return None
            
            # Filter documents if types specified
            if document_types:
                filtered_docs = []
                for doc in documents:
                    if any(doc.filename.lower().endswith(ext) for ext in document_types):
                        filtered_docs.append(doc)
                documents = filtered_docs
                if not documents:
                    logger.warning(f"No documents matching {document_types} found in {index_url}")
                    return None

            # Optionally keep only the main filing document (exclude exhibit .htm files)
            if main_document_only and documents:
                main_docs = [
                    d for d in documents
                    if _is_main_filing_document(d, filing_type)
                ]
                if main_docs:
                    documents = main_docs
                    logger.info(f"Using main document only: {[d.filename for d in documents]}")
                else:
                    logger.warning("No main document found; using first document")

            full_text = ""
            text_map = {}
            for doc in documents:
                try:
                    logger.info(f"Fetching document: {doc.filename}")
                    response = _sec_get(doc.url, headers=self.headers)
                    response.raise_for_status()

                    # Store original text content
                    doc_text = response.text
                    text_map[doc.filename] = doc_text
                    
                    label = doc.filename
                    if doc.exhibit_type:
                        label = f"{doc.exhibit_type} ({doc.filename})"
                    full_text += f"\n\n--- DOCUMENT: {label} ---\n\n"
                    
                    content_type = response.headers.get('content-type', '').lower()
                    filename = doc.filename.lower()

                    # Check if this is an XBRL file (contains structured financial data)
                    is_xbrl_file = (
                        'xml' in content_type or filename.endswith('.xml')
                    ) and (
                        # XBRL instance documents typically have date patterns like 20240930
                        re.search(r'\d{8}', filename) and
                        ('htm' in filename or 'xbrl' in filename or '_' in filename)
                    )

                    if is_xbrl_file:
                        # Process XBRL XML files - they contain valuable financial data
                        try:
                            # Parse as XML and extract text content
                            if response.encoding is None:
                                response.encoding = 'utf-8'

                            xml_content = response.text
                            # Parse XML and extract all text content
                            xml_soup = BeautifulSoup(xml_content, 'xml')
                            # Extract text from all elements, preserving structure
                            xbrl_text = self._extract_xbrl_text(xml_soup)
                            full_text += xbrl_text

                        except Exception as e:
                            logger.warning(f"Failed to process XBRL file {doc.filename}: {e}")
                            # Fallback: try to extract raw text
                            try:
                                raw_text = self.clean_text(xml_content)
                                if raw_text.strip():
                                    full_text += raw_text
                            except Exception as e2:
                                logger.warning(f"Failed to extract raw text from XBRL file {doc.filename}: {e2}")
                                continue

                    elif 'xml' in content_type or filename.endswith('.xml'):
                        # Skip non-XBRL XML files (schema files, etc.) that may cause issues
                        continue
                    elif 'image' in content_type or any(doc.filename.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                        # Skip image files
                        continue
                    else:
                        if response.encoding is None:
                            # Try to detect encoding
                            response.encoding = response.apparent_encoding or 'utf-8'
                        
                        text_content = response.text
                        soup = BeautifulSoup(text_content, 'html.parser')
                        cleaned_text = self.clean_text(soup.get_text())
                        full_text += cleaned_text
                except Exception as e:
                    logger.warning(f"Failed to download document {doc.url}: {e}")
                    continue
            
            if not full_text.strip():
                logger.error(f"Failed to extract any text from filing {index_url}")
                return None
            accession_number = index_url.split('/')[-1].replace('-index.html', '')

            # Extract filing date from the index page so we store by actual filing date,
            # not the day of scraping.
            filing_date = date.today().isoformat()  # Default fallback
            try:
                index_response = _sec_get(index_url, headers=self.headers)
                index_response.raise_for_status()
                index_soup = BeautifulSoup(index_response.content, 'html.parser')

                index_text = index_soup.get_text()

                from datetime import datetime

                date_patterns = [
                    r'(?:Filing Date|Date Filed)[:\s]+(\d{4}-\d{2}-\d{2})',
                    r'(?:Filing Date|Date Filed)[:\s]+(\d{1,2}/\d{1,2}/\d{4})',
                    r'<td[^>]*>Filing Date</td>\s*<td[^>]*>(\d{4}-\d{2}-\d{2})',
                    r'<td[^>]*>Filing Date</td>\s*<td[^>]*>(\d{1,2}/\d{1,2}/\d{4})',
                ]

                for pattern in date_patterns:
                    match = re.search(pattern, index_text, re.IGNORECASE)
                    if match:
                        date_str = match.group(1)
                        try:
                            if '/' in date_str:
                                parsed_date = datetime.strptime(date_str, '%m/%d/%Y')
                            else:
                                parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
                            filing_date = parsed_date.date().isoformat()
                            logger.info(f"Extracted filing date from index page (by URL): {filing_date}")
                            break
                        except ValueError:
                            continue

                # If not found in text, try table cells
                if filing_date == date.today().isoformat():
                    for td in index_soup.find_all('td'):
                        td_text = td.get_text(strip=True)
                        if 'filing date' in td_text.lower() or 'date filed' in td_text.lower():
                            parent_row = td.find_parent('tr')
                            if parent_row:
                                for cell in parent_row.find_all('td'):
                                    cell_text = cell.get_text(strip=True)
                                    if re.match(r'\d{4}-\d{2}-\d{2}', cell_text):
                                        filing_date = cell_text
                                        logger.info(f"Extracted filing date from table (by URL): {filing_date}")
                                        break
                                    elif re.match(r'\d{1,2}/\d{1,2}/\d{4}', cell_text):
                                        try:
                                            parsed_date = datetime.strptime(cell_text, '%m/%d/%Y')
                                            filing_date = parsed_date.date().isoformat()
                                            logger.info(f"Extracted filing date from table (by URL): {filing_date}")
                                            break
                                        except ValueError:
                                            continue
            except Exception as e:
                logger.warning(f"Could not extract filing date from index page (by URL): {e}, using today's date as fallback")

            file_path = None
            if save_to_file:
                safe_filing_type = filing_type.replace("/", "_")
                file_path = os.path.join(self.data_dir, f"{ticker}_{safe_filing_type}_{accession_number}.txt")
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(full_text)

            # Get period end date from cache if available
            period_end = getattr(self, '_cached_period_date', {}).get(index_url)
            
            return FilingResult(
                ticker=ticker,
                filing_type=filing_type,
                filing_date=filing_date,
                accession_number=accession_number,
                text=full_text,
                file_path=file_path,
                documents=documents,
                metadata={'index_url': index_url},
                text_map=text_map,
                period_end_date=period_end
            )
        except Exception as e:
            logger.error(f"Unexpected error during filing fetch by URL for {ticker}: {e}")
            return None
    def search_filings_for_text(self, ticker: str, search_text: str,
                               filing_types: List[str] = None) -> Optional[FilingResult]:
        """
        Search through filings for specific text content.
        
        Args:
            ticker: Company ticker symbol
            search_text: Text to search for
            filing_types: List of filing types to search through
            
        Returns:
            FilingResult of the first filing containing the search text, or None
        """
        if filing_types is None:
            filing_types = ["424B", "8-K", "10-K"]
        
        for filing_type in filing_types:
            result = self.fetch_filing(ticker, filing_type)
            if result and search_text.lower() in result.text.lower():
                result.metadata = result.metadata or {}
                result.metadata['search_text'] = search_text
                result.metadata['matched_filing_type'] = filing_type
                return result
        
        return None

    def get_available_filing_types(self) -> Dict[str, str]:
        """
        Get a dictionary of available filing types and their descriptions.
        
        Returns:
            Dictionary mapping filing type codes to descriptions
        """
        return self.FILING_TYPES.copy()

    def _should_skip_document(self, filename: str) -> bool:
        """
        Determine if a document should be skipped based on its filename.
        Skip images and non-essential files while keeping text documents.
        """
        filename_lower = filename.lower()
        
        # Skip all image files
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.tiff', '.webp']
        if any(filename_lower.endswith(ext) for ext in image_extensions):
            return True
        
        # Skip specific image file patterns that show up in SEC filings
        image_patterns = ['logo', 'image_', 'graphic', 'projecttimber', 'bin1', 'bin2', 'bin3', 'bin4', 'bin5']
        if any(pattern in filename_lower for pattern in image_patterns):
            return True
        
        # Skip some XBRL schema files (but keep main XBRL data)
        xbrl_skip_patterns = ['_cal.xml', '_def.xml', '_lab.xml', '_pre.xml', '.xsd']
        if any(pattern in filename_lower for pattern in xbrl_skip_patterns):
            return True
        
        # Skip archive and binary files
        archive_extensions = ['.zip', '.rar', '.tar', '.gz']
        if any(filename_lower.endswith(ext) for ext in archive_extensions):
            return True
        
        # Skip office document files (we want HTML/TXT for text extraction)
        office_extensions = ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']
        if any(filename_lower.endswith(ext) for ext in office_extensions):
            return True
        
        return False

    def download_all_exhibits_for_filing(self, ticker: str, index_url: str) -> list:
        """
        Download all non-image, non-XML exhibits for a given filing index URL.
        Returns a list of file paths for the downloaded exhibits.
        """
        import requests
        from urllib.parse import urljoin
        import os
        exhibit_paths = []
        documents = self.get_documents_from_index(index_url)
        if not documents:
            logger.warning(f"No documents found for filing {index_url}")
            return []
        for doc in documents:
            # Only download .htm or .txt, skip XML/images
            if doc.filename.endswith('.xml') or any(doc.filename.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                continue
            try:
                response = _sec_get(doc.url, headers=self.headers)
                response.raise_for_status()
                # Save to temp_filings with a clear filename
                ext = '.htm' if doc.filename.endswith('.htm') else '.txt'
                safe_name = f"{ticker}_{doc.filename}"
                file_path = os.path.join('temp_filings', safe_name)
                with open(file_path, 'w', encoding='utf-8', errors='ignore') as f:
                    f.write(response.text)
                logger.info(f"Downloaded exhibit: {file_path}")
                exhibit_paths.append(file_path)
            except Exception as e:
                logger.warning(f"Failed to download exhibit {doc.filename}: {e}")
        return exhibit_paths

# Convenience function for quick access
def create_sec_client(data_dir: str = "data", user_agent: str = None) -> SECAPIClient:
    """
    Create a new SEC API client instance.
    
    Args:
        data_dir: Directory to store downloaded filings
        user_agent: Custom user agent string
        
    Returns:
        Configured SECAPIClient instance
    """
    return SECAPIClient(data_dir=data_dir, user_agent=user_agent) 