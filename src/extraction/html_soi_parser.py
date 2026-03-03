#!/usr/bin/env python3
"""
Custom BeautifulSoup-based HTML Schedule of Investments Parser.

Replaces the LLM (Gemini) table scraper for BDC tickers that lack
sufficient XBRL rate/industry data (Grade B/C/F). Parses HTML tables
directly from SEC filings — no LLM required.

Supported table formats:
  'explicit_industry' — has an Industry/Sector column per row (e.g. FSK, CION, HTGC)
  'section_industry'  — section-header rows provide industry context (e.g. OBDC, EQS)

Usage (CLI-compatible with llm_table_scraper.py):
    python html_soi_parser.py --ticker FSK --years-back 1
    python html_soi_parser.py --ticker OBDC --years-back 2 --force
"""

import csv
import logging
import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests
from bs4 import BeautifulSoup, Tag

# ── Path setup for src/extraction imports ───────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent
sys.path.insert(0, str(_SCRIPT_DIR))

from sec_api_client import SECAPIClient
from table_detection import (
    fallback_table_detection,
    is_investment_table,
    is_year_end_table,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# Standard output columns (matches XBRL extractor + consolidation pipeline)
CSV_COLUMNS = [
    'company_name', 'investment_type', 'industry', 'cash_rate', 'pik_rate',
    'reference_rate', 'spread', 'floor_rate', 'acquisition_date', 'maturity_date',
    'principal_amount', 'amortized_cost', 'fair_value', 'percent_of_net_assets',
    'cost', 'commitment_limit', 'undrawn_commitment', 'rate_cap', 'shares',
]

# Reference rate identifier tokens (lowercase for matching)
REFERENCE_RATE_TOKENS = frozenset({
    'sofr', 'sf', '1m sofr', '3m sofr', '6m sofr', '1msofr', '3msofr',
    'libor', 'l', '1ml', '3ml', '6ml', '1m libor', '3m libor', '6m libor',
    'prime', 'p', 'euribor', 'e', 'sonia', 'ff', 'fed funds', 'bsby', 'ameribor',
    'corra', 'saron', 'aonia', 'sa', 'sr', 's', 'stibor', 'cdor', 'bbsw', 'hibor', 'tibor',
    'tonar', 'nzocr', 'jibar',
})

# Currency codes that appear as money separators in some BDC filings
_CURRENCY_CODES = frozenset({'usd', 'eur', 'gbp', 'sek', 'nok', 'dkk', 'chf', 'aud',
                              'cad', 'jpy', 'nzd', 'hkd', 'sgd', 'mxn', 'brl'})

# Section header text patterns → investment_type label
SECTION_TYPE_PATTERNS = [
    (r'first\s+lien|senior\s+secured.*first', 'First Lien'),
    (r'second\s+lien|senior\s+secured.*second', 'Second Lien'),
    (r'senior\s+secured', 'Senior Secured'),
    (r'senior\s+unsecured', 'Senior Unsecured'),
    (r'subordinated|sub(?:ordinate)?d?\s+debt', 'Subordinated'),
    (r'mezzanine', 'Mezzanine'),
    (r'preferred\s+equity|preferred\s+stock', 'Preferred Equity'),
    (r'common\s+equity|common\s+stock', 'Common Equity'),
    (r'warrant', 'Warrant'),
    (r'\bclo\b|structured\s+product', 'CLO/Structured'),
    (r'\bequity\b', 'Equity'),
    (r'revolver|revolving', 'Revolver'),
    (r'term\s+loan|term\s+b|delayed\s+draw', 'Term Loan'),
    (r'growth\s+capital|growth\s+loan', 'Term Loan'),
]

# Column header patterns → field name (order matters: more specific first)
HEADER_FIELD_MAP = [
    (r'portfolio\s+company|company\s+name|issuer', 'company_name'),
    (r'footnote|foot\s+note', 'footnotes'),
    (r'industry\s+classification|gics|sub[\s\-]?industry|industry|sector', 'industry'),
    # "Investments (1)(19)" — the portfolio-company column in BXSL/MAIN/GAIN/GLAD style
    # filings uses "Investments" as the column header. Must come before the general
    # investment_type pattern to avoid misclassifying it.
    (r'^investments?\s*(?:\(\d+\)\s*)*$', 'company_name'),
    (r'investment(?!\s+company)', 'investment_type'),
    (r'rate|interest', 'rate'),
    (r'floor', 'floor'),
    (r'maturity', 'maturity'),
    (r'principal|par\s*/?\s*unit|par\s+amount', 'principal'),
    (r'amortized\s+cost', 'amortized_cost'),
    (r'fair\s+value', 'fair_value'),
    (r'%\s+of\s+net|percent.*net\s+asset', 'pct_net_assets'),
    (r'shares?|units?', 'shares'),
    (r'\bcompany\b|\bissuer\b', 'company_name'),  # fallback for 'Company' only header
    (r'^\s*value\s*$|^\s*value\s*\(', 'fair_value'),   # HTGC uses "Value" not "Fair Value"
    (r'^\s*cost\s*$|^\s*cost\s*\(', 'amortized_cost'),  # HTGC/CION use "Cost" not "Amortized Cost"
]


# ── Text helpers ─────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Normalize whitespace and replace unicode specials."""
    if not text:
        return ''
    text = text.replace('\u00a0', ' ').replace('\xa0', ' ')
    text = text.replace('\u2014', '-').replace('\u2013', '-')
    text = text.replace('\u2019', "'").replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('—', '-').replace('–', '-')
    return ' '.join(text.split()).strip()


def _cell_text(cell: Tag) -> str:
    return _clean_text(cell.get_text(separator=' ', strip=True))


def _parse_number(text: str) -> Optional[float]:
    """Parse '1,234.5' or '(123.4)' → float. Returns None if not parseable."""
    if not text:
        return None
    t = text.strip().replace(',', '').replace('$', '').replace(' ', '')
    # Handle parenthetical negatives: (123) → -123
    m = re.match(r'^\(([0-9.]+)\)$', t)
    if m:
        t = '-' + m.group(1)
    try:
        return float(t)
    except (ValueError, TypeError):
        return None


def _fmt_num(value: float) -> str:
    """Format a number for CSV: integer if whole number, else preserve decimals."""
    if value == int(value):
        return str(int(value))
    return str(value)


_MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

def _normalize_maturity(text: str) -> str:
    """
    Normalize maturity date strings to YYYY-MM-DD.
    Handles:
      MM/DD/YYYY or M/D/YYYY → YYYY-MM-DD
      MM/YYYY or M/YYYY      → YYYY-MM-01
      MM-YYYY                → YYYY-MM-01
      YYYY-MM-DD             → unchanged
      Month YYYY             → YYYY-MM-01
      Month DD, YYYY         → YYYY-MM-DD
    Returns original text if format not recognized.
    """
    if not text:
        return text
    t = text.strip()
    # MM/DD/YYYY or M/D/YYYY
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', t)
    if m:
        return f'{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}'
    # MM/YYYY or M/YYYY
    m = re.match(r'^(\d{1,2})/(\d{4})$', t)
    if m:
        return f'{m.group(2)}-{int(m.group(1)):02d}-01'
    # MM-YYYY
    m = re.match(r'^(\d{1,2})-(\d{4})$', t)
    if m:
        return f'{m.group(2)}-{int(m.group(1)):02d}-01'
    # ISO date already
    if re.match(r'^\d{4}-\d{2}-\d{2}$', t):
        return t
    # Month DD, YYYY (e.g. "January 15, 2028")
    m = re.match(
        r'^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*[\s,]+(\d{1,2})[\s,]+(\d{4})$',
        t, re.I,
    )
    if m:
        mo = _MONTH_MAP.get(m.group(1).lower()[:3], 0)
        if mo:
            return f'{m.group(3)}-{mo:02d}-{int(m.group(2)):02d}'
    # Month YYYY (e.g. "January 2028" or "Jan 2028")
    m = re.match(
        r'^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*[\s\-](\d{4})$',
        t, re.I,
    )
    if m:
        mo = _MONTH_MAP.get(m.group(1).lower()[:3], 0)
        if mo:
            return f'{m.group(2)}-{mo:02d}-01'
    return t


def _parse_percentage(text: str) -> Optional[str]:
    """
    Normalize a percentage string to e.g. '6.0%'.
    Handles: '6.0 %', '6.0%', '0.060', '(3.5 % PIK)' → just the number + %.
    Returns None if it doesn't look like a percentage.
    """
    if not text:
        return None
    # Extract number from "N.N %" or "N.N%" (possibly with extra text)
    m = re.search(r'([\d.]+)\s*%', text)
    if m:
        return m.group(1) + '%'
    # Plain decimal that looks like a rate (0–30 range)
    try:
        v = float(text.strip().replace(',', ''))
        if 0 <= v <= 30:
            return f'{v}%'
    except (ValueError, TypeError):
        pass
    return None


def _normalize_ref_text(text: str) -> str:
    """Normalize reference rate text: '3-month SOFR' → '3m sofr', etc."""
    t = text.strip().lower()
    # "3-month SOFR" / "3 month SOFR" → "3m sofr"
    t = re.sub(r'(\d+)[\s-]month\s+', r'\1m ', t)
    # Strip trailing qualifiers
    t = re.sub(r'\s+(rate|index|benchmark|floor)$', '', t)
    return t.strip()


def _is_ref_rate_token(text: str) -> bool:
    """Return True if text is a reference rate identifier like SOFR, SF, L, PRIME."""
    t = text.strip().lower()
    if t in REFERENCE_RATE_TOKENS:
        return True
    # Normalize then check: "3-month SOFR" → "3m sofr"
    t_norm = _normalize_ref_text(t)
    if t_norm in REFERENCE_RATE_TOKENS:
        return True
    # Handle multi-word variants like "1M SOFR", "3M LIBOR"
    if re.match(r'^\d+m?\s*(sofr|libor|l)$', t) or re.match(r'^\d+m?\s*(sofr|libor|l)$', t_norm):
        return True
    return False


def _infer_investment_type_from_section(text: str) -> str:
    """Try to extract an investment type label from a section header string."""
    text_lower = text.lower()
    for pattern, label in SECTION_TYPE_PATTERNS:
        if re.search(pattern, text_lower):
            return label
    return ''


def _is_total_row(cells: List[str]) -> bool:
    """Return True if this row looks like a subtotal/total row (not a real investment)."""
    if not cells:
        return False
    first = cells[0].lower().strip()
    return re.match(r'^(total|sub[ -]?total|net\s+assets)', first) is not None


# ── Main parser class ────────────────────────────────────────────────────────

class HTMLSOIParser:
    """
    BeautifulSoup-based HTML Schedule of Investments parser.
    Extracts investment data directly from HTML without LLM assistance.
    """

    def __init__(self, data_dir: str = 'data', output_dir: str = 'output'):
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sec_client = SECAPIClient(data_dir=str(self.data_dir))

    # ── Public API ───────────────────────────────────────────────────────────

    def process_ticker(self, ticker: str, years_back: int = 1, force: bool = False,
                       filing_type: str = '10-Q') -> List[Path]:
        """
        Process all recent filings for a ticker.
        Returns list of output CSV paths created.
        """
        logger.info('=== HTMLSOIParser: %s (years_back=%d) ===', ticker, years_back)

        if filing_type == '10-K':
            filings = list(self.sec_client.get_historical_10k_filings(ticker, years_back=years_back))
        else:
            filings = list(self.sec_client.get_historical_10q_filings(ticker, years_back=years_back))
            if not filings:
                logger.info('No 10-Q found; trying 10-K')
                filings = list(self.sec_client.get_historical_10k_filings(ticker, years_back=years_back))

        if not filings:
            logger.warning('No filings found for %s', ticker)
            return []

        logger.info('Found %d filing(s) for %s', len(filings), ticker)
        results = []
        for filing in filings:
            path = self.process_filing(ticker, filing, force=force)
            if path:
                results.append(path)
        return results

    def process_filing(self, ticker: str, filing: Dict, force: bool = False) -> Optional[Path]:
        """
        Process a single filing dict (from SEC client get_historical_* methods).
        Returns output CSV path, or None on failure.
        """
        period_end = filing.get('period_end_date') or filing.get('date')
        filing_date = filing.get('date', period_end)
        filing_type = filing.get('form', '10-Q')

        output_name = f'{ticker}_investments_{filing_date}.csv'
        output_path = self.output_dir / output_name

        if output_path.exists() and not force:
            logger.info('Skipping %s %s — output already exists', ticker, period_end)
            return output_path

        logger.info('Processing %s %s (%s)', ticker, period_end, filing_type)

        try:
            filing_result = self.sec_client.fetch_filing_by_index_url(
                index_url=filing['index_url'],
                ticker=ticker,
                filing_type=filing_type,
                save_to_file=False,
                document_types=['.htm', '.html'],
                main_document_only=True,
            )
            if not filing_result:
                logger.error('Could not fetch filing for %s %s', ticker, period_end)
                return None

            all_tables = self._extract_html_tables(filing_result)
            if not all_tables:
                logger.warning('No HTML tables found in %s %s', ticker, period_end)
                return None

            soi_tables = fallback_table_detection(
                all_tables,
                filing_date=filing_date,
                filing_type=filing_type,
                period_end_date=period_end,
            )
            if not soi_tables:
                logger.warning('No SOI tables found in %s %s', ticker, period_end)
                return None

            logger.info('Found %d SOI table(s) for %s %s', len(soi_tables), ticker, period_end)

            unit_scale = self._detect_unit_scale(all_tables, filing_result)

            all_rows: List[Dict] = []
            for table_html, _table_text, table_num, _table_id in soi_tables:
                soup = BeautifulSoup(table_html, 'html.parser')
                table_tag = soup.find('table')
                if not table_tag:
                    continue
                rows = self._parse_table(table_tag, ticker, period_end)
                logger.info('Table %d: extracted %d data rows', table_num, len(rows))
                all_rows.extend(rows)

            if not all_rows:
                logger.warning('No rows extracted for %s %s', ticker, period_end)
                return None

            # Apply unit scale to get "thousands" storage unit
            if unit_scale == 'millions':
                all_rows = self._scale_monetary(all_rows, 1000)
            elif unit_scale == 'units':
                # Raw dollars → thousands
                all_rows = self._scale_monetary(all_rows, 0.001)
            # 'thousands' → no scaling needed

            self._write_csv(all_rows, output_path, ticker, period_end)
            logger.info('Wrote %d rows to %s', len(all_rows), output_path)
            return output_path

        except Exception as exc:
            logger.error('Error processing %s %s: %s', ticker, period_end, exc, exc_info=True)
            return None

    # ── Table extraction ─────────────────────────────────────────────────────

    def _extract_html_tables(self, filing_result: Any) -> List[Tuple]:
        """Extract all HTML tables from filing documents."""
        all_tables = []
        self._doc_texts: List[str] = []  # raw doc HTML for unit-scale search
        if not hasattr(filing_result, 'documents') or not filing_result.documents:
            return all_tables

        for doc in filing_result.documents:
            fn = doc.filename.lower()
            if not (fn.endswith('.htm') or fn.endswith('.html')):
                continue
            if fn.endswith('.xml'):
                continue

            try:
                resp = requests.get(doc.url, headers=self.sec_client.headers, timeout=90)
                resp.raise_for_status()
                raw_html = resp.text
                self._doc_texts.append(raw_html)
                soup = BeautifulSoup(resp.content, 'html.parser')
                tables = soup.find_all('table')
                logger.info('%s: %d tables', doc.filename, len(tables))

                for i, table in enumerate(tables):
                    table_html = str(table)
                    row_lines = []
                    for tr in table.find_all('tr'):
                        cells = [c.get_text(separator=' ', strip=True)
                                 for c in tr.find_all(['td', 'th'])]
                        if cells:
                            row_lines.append('\t'.join(cells))
                    table_text = '\n'.join(row_lines)
                    if len(table_text.strip()) < 50:
                        continue
                    table_id = f'{doc.filename}_t{i}'
                    all_tables.append((table_html, table_text, len(all_tables), table_id))

            except Exception as exc:
                logger.warning('Failed to process %s: %s', doc.filename, exc)

        logger.info('Total tables extracted: %d', len(all_tables))
        return all_tables

    def _detect_unit_scale(self, tables: List[Tuple], filing_result: Any) -> str:
        """
        Detect whether amounts are in 'millions', 'thousands', or 'units' (raw dollars).
        Searches table HTML/text, filing text, and full document HTML.
        """
        parts = []
        for html, text, _, _ in tables:
            parts.append(html[:5000].lower())
            parts.append(text[:2000].lower())
        if hasattr(filing_result, 'text') and filing_result.text:
            parts.append(filing_result.text[:30000].lower())
        # Also search full raw document HTML (unit scale may appear deep in the file)
        for doc_html in getattr(self, '_doc_texts', []):
            parts.append(doc_html.lower())

        combined = ' '.join(parts)

        thousands_pats = [
            r'in\s+thousands', r'\(in\s+thousands\)', r'\(\$\s*in\s+thousands\)',
            r'\$\s+in\s+thousands', r'\(000\'?s?\)', r'\(\s*000\s*\)',
            r'amounts?\s+in\s+thousands', r'\(amounts?\s+in\s+thousands\)',
        ]
        millions_pats = [
            r'in\s+millions', r'\(in\s+millions\)', r'\(\$\s*in\s+millions\)',
            r'\$\s+in\s+millions', r'amounts?\s+in\s+millions',
        ]

        for pat in thousands_pats:
            if re.search(pat, combined, re.I):
                logger.info('Unit scale detected: thousands')
                return 'thousands'
        for pat in millions_pats:
            if re.search(pat, combined, re.I):
                logger.info('Unit scale detected: millions')
                return 'millions'

        logger.info('Unit scale: thousands (default assumption)')
        return 'thousands'

    # ── Table parsing ────────────────────────────────────────────────────────

    def _parse_table(self, table: Tag, ticker: str, filing_date: str) -> List[Dict]:
        """
        Parse an SOI HTML table. Returns list of row dicts.
        Auto-detects format (explicit_industry vs section_industry).
        """
        all_trs = list(table.find_all('tr'))
        if not all_trs:
            return []

        # Find the header row (may merge second row for industry column, e.g. ARCC)
        header_idx, col_map, fmt, header_rows = self._find_header_row(all_trs)
        if header_idx < 0:
            logger.warning('No recognisable header row found in table')
            return []

        logger.debug('Format: %s | header row %d | header_rows %d | col_map: %s', fmt, header_idx, header_rows, col_map)

        rows_out: List[Dict] = []
        # Allow cross-table carry when called from extract_soi_enrichment
        current_investment_type = getattr(self, '_carry_investment_type', '')
        current_industry = getattr(self, '_carry_industry', '')

        for tr in all_trs[header_idx + header_rows:]:
            raw_cells = tr.find_all(['td', 'th'])
            if not raw_cells:
                continue

            # Expand colspans into a flat cell list
            cells = []
            for cell in raw_cells:
                txt = _cell_text(cell)
                colspan = int(cell.get('colspan', 1))
                cells.append(txt)
                cells.extend([''] * (colspan - 1))

            if not any(c.strip() for c in cells):
                continue

            # Detect section-header row: few non-empty cells or clearly labeled
            n_nonempty = sum(1 for c in cells if c.strip())
            is_section = (
                n_nonempty <= 2 and len(cells) > 3
                or (len(raw_cells) == 1 and int(raw_cells[0].get('colspan', 1)) > 3)
            )

            if is_section:
                header_text = next((c for c in cells if c.strip()), '')
                inv_type = _infer_investment_type_from_section(header_text)
                if inv_type:
                    current_investment_type = inv_type
                # In section_industry format, section headers that aren't type headers
                # are industry names — but skip affiliation/class/unit-scale headers
                if fmt == 'section_industry' and not inv_type:
                    txt = header_text.strip()
                    if txt and not re.match(
                        r'^(total|sub.?total|net\s+asset'
                        r'|non.?controlled|controlled[\s,/]'
                        r'|non.?affiliate|affiliate'
                        r'|debt\s+invest|equity\s+invest'
                        r'|\$\s*in\s+|in\s+(?:million|thousand)'
                        r'|\(\$)',
                        txt, re.I
                    ):
                        current_industry = txt
                continue

            # Skip obvious total rows
            if _is_total_row(cells):
                continue

            # Extract data row
            row = self._extract_row(cells, col_map, fmt)
            if row and row.get('company_name'):
                if not row.get('investment_type'):
                    row['investment_type'] = current_investment_type
                if fmt == 'section_industry' and not row.get('industry'):
                    row['industry'] = current_industry
                row['ticker'] = ticker
                row['filing_date'] = filing_date
                rows_out.append(row)

        # Persist carry state for cross-table context (used by extract_soi_enrichment)
        if hasattr(self, '_carry_industry'):
            self._carry_industry = current_industry
        if hasattr(self, '_carry_investment_type'):
            self._carry_investment_type = current_investment_type

        return rows_out

    def _find_header_row(self, trs: List[Tag]) -> Tuple[int, Dict, str, int]:
        """
        Scan rows for the column header row.
        Returns (row_index, col_map, format_type, header_rows).
        header_rows: 1 or 2 when two-row header was merged (e.g. ARCC).
        """
        for idx, tr in enumerate(trs):
            cells = tr.find_all(['td', 'th'])
            col = 0
            col_map: Dict[str, Any] = {}
            first_visible_unmatched: Optional[int] = None
            header_rows = 1

            for cell in cells:
                txt = _cell_text(cell).lower().strip()
                colspan = int(cell.get('colspan', 1))
                is_hidden = 'display:none' in (cell.get('style') or '')

                # Match against header field patterns
                matched = False
                if not is_hidden:
                    for pattern, field in HEADER_FIELD_MAP:
                        if re.search(pattern, txt, re.I):
                            # Don't let 'company' match 'investment company' for company_name
                            if field == 'company_name' and 'investment company' in txt:
                                continue
                            # Rate/interest: track number of columns it spans
                            if field == 'rate':
                                col_map['rate_start'] = col
                                col_map['rate_cols'] = colspan
                            else:
                                # Only set if not already found (first match wins)
                                if field not in col_map:
                                    col_map[field] = col
                            matched = True
                            break

                    # Track first visible, non-matched column for company_name inference
                    if not matched and first_visible_unmatched is None:
                        first_visible_unmatched = col

                col += colspan

            col_map['_total_cols'] = col

            # Must have at least company_name to be a valid header
            if 'company_name' not in col_map:
                # Some BDCs (e.g. GBDC) omit the "Portfolio Company" column header
                # label entirely. If we detected ≥3 financial columns, the first
                # visible unmatched column is almost certainly the company name.
                financial_keys = {k for k in col_map
                                  if k not in ('_total_cols', 'rate_start', 'rate_cols')}
                if len(financial_keys) >= 3 and first_visible_unmatched is not None:
                    col_map['company_name'] = first_visible_unmatched
                else:
                    continue

            # Two-row header (e.g. ARCC): if this row has no industry, try next row
            header_rows = 1
            if 'industry' not in col_map and idx + 1 < len(trs):
                next_tr = trs[idx + 1]
                next_cells = next_tr.find_all(['td', 'th'])
                col2 = 0
                for cell in next_cells:
                    txt = _cell_text(cell).lower().strip()
                    colspan = int(cell.get('colspan', 1))
                    if not txt:
                        col2 += colspan
                        continue
                    for pattern, field in HEADER_FIELD_MAP:
                        if re.search(pattern, txt, re.I):
                            if field == 'company_name' and 'investment company' in txt:
                                continue
                            if field not in col_map:
                                col_map[field] = col2
                            break
                    col2 += colspan
                if col2 > col_map.get('_total_cols', 0):
                    col_map['_total_cols'] = col2
                if 'industry' in col_map:
                    header_rows = 2  # skip second row when iterating data

            # Determine format
            fmt = 'explicit_industry' if 'industry' in col_map else 'section_industry'
            return idx, col_map, fmt, header_rows

        return -1, {}, 'unknown', 1

    def _extract_row(self, cells: List[str], col_map: Dict, fmt: str) -> Optional[Dict]:
        """
        Extract a data row dict from a flat list of cell strings.
        """
        row: Dict[str, str] = {}

        # ── Company name ─────────────────────────────────────────────────────
        company_col = col_map.get('company_name', 0)
        name = cells[company_col].strip() if company_col < len(cells) else ''
        # Filter out footnote-only "names" like "(a)" or "(1)(2)"
        if not name or re.match(r'^[\(\)\w,\s]+$', name) and re.match(r'^\(', name):
            return None
        # Filter out rows where company_name is clearly a section header word
        if re.match(r'^(total|sub.?total|net\s+asset|debt\s+invest|equity\s+invest)',
                    name, re.I):
            return None
        row['company_name'] = name

        # ── Investment type (OBDC-style 'Investment' column) ─────────────────
        if fmt == 'section_industry' and 'investment_type' in col_map:
            inv_col = col_map['investment_type']
            if inv_col < len(cells):
                raw_inv = cells[inv_col].strip()
                inv_type = _infer_investment_type_from_section(raw_inv)
                if inv_type:
                    row['investment_type'] = inv_type

        # ── Industry (explicit column) ────────────────────────────────────────
        if fmt == 'explicit_industry' and 'industry' in col_map:
            ind_col = col_map['industry']
            if ind_col < len(cells):
                row['industry'] = cells[ind_col].strip()

        # ── Determine start of "rate area" ───────────────────────────────────
        rate_start = col_map.get('rate_start', -1)
        if rate_start < 0:
            # Fallback: rate area starts after the last non-rate fixed column
            last_fixed_col = max(
                col_map.get('industry', col_map.get('investment_type', company_col)),
                company_col,
            )
            rate_start = last_fixed_col

        # ── Maturity column position ─────────────────────────────────────────
        mat_col = col_map.get('maturity', -1)
        floor_col = col_map.get('floor', -1)

        # ── Parse rate area → maturity ────────────────────────────────────────
        if mat_col >= 0 and mat_col > rate_start:
            # Normal layout: Rate ... Maturity ... Money (FSK, CION, OBDC)
            rate_area_cells = cells[rate_start:mat_col]
            maturity_text = cells[mat_col].strip() if mat_col < len(cells) else ''
        elif mat_col >= 0 and mat_col <= rate_start:
            # Unusual layout: Maturity before Rate (HTGC)
            maturity_text = cells[mat_col].strip() if mat_col < len(cells) else ''
            # Rate area ends at the first numeric/monetary column after rate_start
            money_end = min(
                (c for c in [col_map.get('principal', 9999),
                              col_map.get('amortized_cost', 9999),
                              col_map.get('fair_value', 9999)]
                 if c > rate_start),
                default=len(cells)
            )
            rate_area_cells = cells[rate_start:money_end]
        else:
            # No maturity column in col_map — auto-detect
            rate_area_cells, maturity_text, mat_col = self._split_at_maturity(cells, rate_start)

        rate_data = self._parse_rate_area(rate_area_cells)
        row.update(rate_data)

        if maturity_text:
            row['maturity_date'] = _normalize_maturity(maturity_text)

        # ── Floor rate (explicit column) ──────────────────────────────────────
        if floor_col >= 0 and floor_col < len(cells):
            pct = _parse_percentage(cells[floor_col])
            if pct:
                row['floor_rate'] = pct

        # ── Monetary values: principal, amortized_cost, fair_value ───────────
        money_start = (mat_col + 1) if mat_col >= 0 else rate_start
        self._extract_monetary(cells, col_map, money_start, row)

        # ── Percent of net assets ─────────────────────────────────────────────
        pct_col = col_map.get('pct_net_assets', -1)
        if pct_col >= 0 and pct_col < len(cells):
            pct_text = cells[pct_col].strip()
            if pct_text:
                row['percent_of_net_assets'] = pct_text.replace('%', '').strip()

        # cost mirrors amortized_cost
        if row.get('amortized_cost') and not row.get('cost'):
            row['cost'] = row['amortized_cost']

        return row

    # ── Rate parsing ─────────────────────────────────────────────────────────

    def _split_at_maturity(self, cells: List[str], start: int
                           ) -> Tuple[List[str], str, int]:
        """
        Scan cells[start:] to find the first maturity-like cell.
        Returns (rate_cells, maturity_text, maturity_col_index).
        """
        mat_patterns = [
            r'^\d{1,2}/\d{1,2}/\d{4}$', # MM/DD/YYYY or M/D/YYYY
            r'^\d{1,2}/\d{4}$',          # MM/YYYY
            r'^\d{1,2}-\d{4}$',          # MM-YYYY
            r'^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*[\s\-]\d{4}$',
            r'^\d{4}-\d{2}-\d{2}$',      # ISO date
            r'^n/?a$',                    # N/A for equity
        ]
        for i, c in enumerate(cells[start:], start=start):
            txt = c.strip()
            if not txt:
                continue
            for pat in mat_patterns:
                if re.match(pat, txt, re.I):
                    return cells[start:i], _normalize_maturity(txt), i
        # No maturity found — return all remaining as rate area
        return cells[start:], '', len(cells)

    def _parse_single_cell_rate(self, text: str) -> Optional[Dict[str, str]]:
        """
        Try to parse a single-cell combined rate string.

        Strategy 1 (split on +/-): handles "Prime + 2.85%, Floor rate 1.00%"
          and "3-month SOFR + 8.01%, Floor 1.00%", "Prime - 0.55%"
        Strategy 2 (basis-point): handles "S+ 1200, 1.00% SOFR Floor"
        Strategy 3 (pct + PIK): handles "SOFR + 5.00% / 3.5% PIK"

        Returns None if no recognized pattern matches.
        """
        t = text.strip()

        # Pre-processing: if text looks like "Loan Type (rate info ...)", extract the
        # rate info from inside the parentheses.  Handles TPVG-style cells like:
        #   "Growth Capital Loan (Prime + 3.50 % interest rate, 11.25 % floor, ...)"
        # Only do this when the part before the parenthesis is NOT itself a reference
        # rate token (so we don't mangle things like "SOFR (Floor 1%)").
        paren_m = re.search(r'\(([^)]*\d[^)]*(?:%|sofr|libor|prime|floor|spread)[^)]*)\)', t, re.I)
        if paren_m:
            prefix = t[:paren_m.start()].strip()
            if not _is_ref_rate_token(prefix):
                t = paren_m.group(1).strip()

        # Strategy 1: split on " + " or " - " (with surrounding spaces)
        # This cleanly separates the reference rate from the numeric spread.
        for sign_pat, sign in [(r'\s+\+\s+', +1), (r'\s+-\s+', -1)]:
            m = re.search(sign_pat, t)
            if not m:
                continue
            ref_candidate = t[:m.start()].strip()
            rest = t[m.end():].strip()
            if not _is_ref_rate_token(ref_candidate):
                continue
            spread_m = re.match(r'^([\d.]+)\s*%', rest)
            if not spread_m:
                continue
            spread_raw = spread_m.group(1)
            result: Dict[str, str] = {}
            result['reference_rate'] = _normalize_ref_text(ref_candidate).upper()
            result['spread'] = (f'-{spread_raw}%' if sign < 0 else f'{spread_raw}%')
            # Optional floor: "Floor rate N.NN %" or "N.NN % floor" (either order)
            floor_m = re.search(
                r'[Ff]loor\s*(?:rate)?\s*([\d.]+)\s*%'
                r'|([\d.]+)\s*%\s*(?:floor|minimum)',
                rest, re.I,
            )
            if floor_m:
                fv = floor_m.group(1) or floor_m.group(2)
                if fv:
                    result['floor_rate'] = fv + '%'
            # Optional PIK
            pik_m = re.search(r'([\d.]+)\s*%\s*pik', rest, re.I)
            if pik_m and float(pik_m.group(1)) != 0:
                result['pik_rate'] = pik_m.group(1) + '%'
            return result

        # Strategy 2: "REF+ NNN [, Floor%]" (basis-point, no space around +)
        # e.g. "S+ 1200 , 1.00 % SOFR Floor"
        m2 = re.match(
            r'^([A-Za-z][A-Za-z0-9]{0,7})\s*\+\s*(\d{2,5})'     # ref+ bps
            r'(?:[,;\s]+([\d.]+)\s*%[^/]*?(?:floor|flo))?'       # optional floor
            r'(?:[,;\s/]+([\d.]+)\s*%\s*pik)?',                  # optional PIK
            t, re.I
        )
        if m2 and _is_ref_rate_token(m2.group(1)):
            result = {}
            result['reference_rate'] = m2.group(1).upper()
            bps = int(m2.group(2))
            result['spread'] = f'{bps / 100}%'
            if m2.group(3):
                result['floor_rate'] = m2.group(3) + '%'
            if m2.group(4) and float(m2.group(4)) != 0:
                result['pik_rate'] = m2.group(4) + '%'
            return result

        # Strategy 3: "RREF + N.NN% [/ M.MM% PIK]" (percentage spread, no spaces)
        # e.g. "SOFR + 5.00% / 3.5% PIK"
        m3 = re.match(
            r'^([A-Za-z][A-Za-z0-9\s]{0,8}?)\s*\+\s*([\d.]+)\s*%'  # ref + pct%
            r'(?:\s*[/,]\s*([\d.]+)\s*%\s*pik)?',                    # optional PIK
            t, re.I
        )
        if m3:
            ref_part = m3.group(1).strip()
            if _is_ref_rate_token(ref_part):
                result = {}
                result['reference_rate'] = _normalize_ref_text(ref_part).upper()
                result['spread'] = m3.group(2) + '%'
                if m3.group(3) and float(m3.group(3)) != 0:
                    result['pik_rate'] = m3.group(3) + '%'
                return result

        return None

    def _parse_rate_area(self, rate_cells: List[str]) -> Dict[str, str]:
        """
        Parse rate information from cells in the rate area.

        Handles:
        - Multi-cell FSK-style:  [SOFR] [+] [6.0%] [floor%]
        - PIK cells:             cells containing 'PIK'
        - Single-cell combined:  "SOFR + 6.00% / 3.5% PIK" or "12.50%"
        - CION-style single cell: "S+ 1200 , 1.00 % SOFR Floor"
        - OBDC-style two cells:  ["S+"] ["5.25 % (4.25% PIK)"]
        """
        result: Dict[str, str] = {}
        # Flatten and filter separator-only cells
        tokens = [c.strip() for c in rate_cells if c.strip() and c.strip() not in ('+', '/', '')]

        # If the rate area is a single token (one cell), try the single-cell parser first
        if len(tokens) == 1:
            parsed = self._parse_single_cell_rate(tokens[0])
            if parsed:
                return parsed

        i = 0
        ref_rate = ''
        percentages: List[str] = []
        pik_parts: List[str] = []

        while i < len(tokens):
            tok = tokens[i]
            tok_lower = tok.lower()

            # Combined basis-point format: "SF+500" → ref_rate='SF', spread='5.0%'
            # Also handles multi-char: "1ML+625", "SOFR+475", "SA+350", "S+500", etc.
            _combined = re.match(r'^([A-Za-z0-9][A-Za-z0-9]{0,7})\+(\d{2,5})$', tok)
            if _combined and _is_ref_rate_token(_combined.group(1)):
                if not ref_rate:
                    ref_rate = _combined.group(1).upper()
                bps = int(_combined.group(2))
                percentages.append(f'{bps / 100}%')
                i += 1
                continue

            # "S+" style: reference rate with attached plus sign (e.g. OBDC format)
            if tok.endswith('+') and _is_ref_rate_token(tok[:-1].strip()):
                if not ref_rate:
                    ref_rate = tok[:-1].strip().upper()
                i += 1
                continue

            if _is_ref_rate_token(tok):
                if not ref_rate:
                    ref_rate = tok.upper()

            elif 'pik' in tok_lower:
                # Handle combined cells like "5.25 % (4.25 % PIK)":
                # extract any non-PIK percentage as the spread, rest goes to pik_parts
                non_pik = re.findall(r'([\d.]+)\s*%(?!\s*(?:pik|PIK))', tok)
                if non_pik and not percentages:
                    percentages.append(non_pik[0] + '%')
                pik_parts.append(tok)

            elif re.search(r'[\d.]+\s*%', tok):
                # Percentage — could be spread, floor, or cash rate
                pct = _parse_percentage(tok)
                if pct:
                    percentages.append(pct)

            elif re.match(r'^[\d.]+$', tok):
                # Plain number in rate context (e.g. "6.0" without %)
                try:
                    v = float(tok)
                    if 0 < v < 30:
                        percentages.append(f'{v}%')
                except ValueError:
                    pass

            i += 1

        if ref_rate:
            result['reference_rate'] = ref_rate
            if percentages:
                result['spread'] = percentages[0]
            if len(percentages) >= 2:
                result['floor_rate'] = percentages[1]
        elif percentages:
            # Fixed/cash rate
            result['cash_rate'] = percentages[0]
            if len(percentages) >= 2:
                result['floor_rate'] = percentages[1]

        # PIK rate — take the LAST non-zero percentage from all PIK cells
        # (format like "(0.0% PIK / 1.8% PIK)" → 1.8%)
        if pik_parts:
            pik_text = ' '.join(pik_parts)
            all_pct = re.findall(r'([\d.]+)\s*%', pik_text)
            # Prefer the last non-zero; fall back to last one
            non_zero = [p for p in all_pct if float(p) != 0]
            pik_val = non_zero[-1] if non_zero else (all_pct[-1] if all_pct else None)
            if pik_val and float(pik_val) != 0:
                result['pik_rate'] = pik_val + '%'

        return result

    # ── Monetary value extraction ─────────────────────────────────────────────

    def _extract_monetary(self, cells: List[str], col_map: Dict, money_start: int,
                          row: Dict) -> None:
        """
        Extract principal, amortized_cost, and fair_value from cells.

        Strategy:
        1. If col_map has explicit positions, use them (skipping any separator).
        2. Otherwise, collect all numeric values after money_start.
        """
        def _get_num_at(col: int) -> Optional[str]:
            """Read a numeric value at or near col (skip $ / , / currency separators)."""
            for offset in range(4):
                idx = col + offset
                if idx >= len(cells):
                    break
                txt = cells[idx].strip()
                if not txt or txt in ('$', ',', '—', '-', '\ufffd', '\u00a0'):
                    continue
                # Treat 2-3 letter currency codes (SEK, EUR, GBP…) as separators
                if re.match(r'^[A-Z]{2,4}$', txt) and txt.lower() in _CURRENCY_CODES:
                    continue
                # Skip replacement char or special unicode
                if len(txt) == 1 and ord(txt[0]) > 127:
                    continue
                # Non-digit text (stop scanning) — but not currency codes handled above
                if re.match(r'^[a-zA-Z]{3,}', txt):
                    break
                num = _parse_number(txt)
                if num is not None:
                    return _fmt_num(num)
            return None

        # Try positional extraction first
        principal_col = col_map.get('principal', -1)
        ac_col = col_map.get('amortized_cost', -1)
        fv_col = col_map.get('fair_value', -1)

        if principal_col >= 0:
            v = _get_num_at(principal_col)
            if v is not None:
                row['principal_amount'] = v
        if ac_col >= 0:
            v = _get_num_at(ac_col)
            if v is not None:
                row['amortized_cost'] = v
                row['cost'] = v
        if fv_col >= 0:
            v = _get_num_at(fv_col)
            if v is not None:
                row['fair_value'] = v

        # If positional lookup didn't work, scan from money_start
        if not (row.get('amortized_cost') or row.get('fair_value')):
            nums = []
            for i in range(money_start, len(cells)):
                txt = cells[i].strip()
                if not txt or txt in ('$', ',', '—', '-', '\ufffd'):
                    continue
                # Skip currency codes
                if re.match(r'^[A-Z]{2,4}$', txt) and txt.lower() in _CURRENCY_CODES:
                    continue
                if len(txt) == 1 and ord(txt[0]) > 127:
                    continue
                num = _parse_number(txt)
                if num is not None:
                    nums.append(num)

            if len(nums) >= 3:
                row['principal_amount'] = _fmt_num(nums[-3])
                row['amortized_cost'] = _fmt_num(nums[-2])
                row['cost'] = row['amortized_cost']
                row['fair_value'] = _fmt_num(nums[-1])
            elif len(nums) == 2:
                row['amortized_cost'] = _fmt_num(nums[-2])
                row['cost'] = row['amortized_cost']
                row['fair_value'] = _fmt_num(nums[-1])
            elif len(nums) == 1:
                row['fair_value'] = _fmt_num(nums[-1])

    # ── Unit scaling ─────────────────────────────────────────────────────────

    def _scale_monetary(self, rows: List[Dict], factor: float) -> List[Dict]:
        """Scale all monetary fields by factor (e.g. ×1000 for millions→thousands)."""
        fields = ['principal_amount', 'amortized_cost', 'fair_value', 'cost',
                  'commitment_limit', 'undrawn_commitment']
        for row in rows:
            for field in fields:
                val = row.get(field)
                if val:
                    try:
                        scaled = round(float(val) * factor)
                        row[field] = str(scaled)
                    except (ValueError, TypeError):
                        pass
        return rows

    # ── CSV output ───────────────────────────────────────────────────────────

    def _write_csv(self, rows: List[Dict], path: Path,
                   ticker: str, filing_date: str) -> None:
        all_cols = CSV_COLUMNS + ['data_quality_flags', 'ticker', 'filing_date']
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=all_cols, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                row.setdefault('ticker', ticker)
                row.setdefault('filing_date', filing_date)
                row.setdefault('data_quality_flags', '')
                writer.writerow(row)


# ── Cross-source enrichment helpers ─────────────────────────────────────────

def _company_key(name: str) -> str:
    """Normalize a company name for cross-source (XBRL ↔ HTML) matching."""
    if not name:
        return ''
    # Remove commas and dots from abbreviations first so that
    # "L.P." → "LP", "L.L.C." → "LLC", "Inc." → "Inc", etc.
    t = name.replace(',', ' ')
    t = re.sub(r'\b([A-Z])\.([A-Z])\.([A-Z])\.', r'\1\2\3', t)  # L.L.C. → LLC
    t = re.sub(r'\b([A-Z])\.([A-Z])\.', r'\1\2', t)              # L.P. → LP, S.A. → SA
    t = re.sub(r'\b([A-Za-z]{2,})\.', r'\1', t)                  # Inc. → Inc, Corp. → Corp
    # Strip common legal-entity suffixes (no dots left after above)
    t = re.sub(
        r'\b(?:LLC|INC|CORP|CORPORATION|INCORPORATED'
        r'|LP|LLP|LTD|LIMITED|HOLDINGS?|GROUP|PARTNERS?|CO|TRUST|SA)\b',
        ' ', t, flags=re.I,
    )
    # Lowercase, drop remaining non-alphanumeric punctuation, collapse whitespace
    t = re.sub(r'[^\w\s]', ' ', t.lower())
    t = ' '.join(t.split())
    # Some BDCs embed investment type + index in the XBRL company name dimension
    # (e.g. GBDC old format: "AAH Topco LLC One stop 1").  Strip trailing
    # investment-type keywords and numeric index so the key matches the HTML name.
    t = re.sub(
        r'\s+(?:one\s+stop|first\s+lien|second\s+lien|senior\s+secured'
        r'|senior\s+unsecured|term\s+loan|revolver(?:ing)?'
        r'|subordinated|mezzanine|preferred(?:\s+(?:stock|equity))?'
        r'|common(?:\s+(?:stock|equity))?|warrant|equity|debt'
        r'|lp\s+(?:interest|units?)|lp\s+interest|notes?|bonds?'
        r'|unsecured|secured)\s*\d*\s*$',
        '', t, flags=re.I,
    )
    # Strip trailing standalone index number (e.g. "acme corp 2" → "acme corp")
    t = re.sub(r'\s+\d+\s*$', '', t)
    return t.strip()


def _principal_key(amount_str: str) -> str:
    """Convert a principal/cost amount string (in thousands) to a rounded integer key."""
    if not amount_str:
        return ''
    try:
        val = float(str(amount_str).replace(',', '').replace('$', '').strip())
        return str(round(val))
    except (ValueError, TypeError):
        return ''


def _is_numeric_industry(s: str) -> bool:
    """True if s looks like a number (e.g. percent or code), not a sector name."""
    if not s or len(s) > 50:
        return True
    s = s.strip()
    # Bare number: 31.6, 246.5, 12
    if re.match(r'^-?\d+\.?\d*%?$', s):
        return True
    # Number with optional trailing % and whitespace
    if re.match(r'^\d+\.?\d*\s*%?\s*$', s):
        return True
    return False


def extract_soi_enrichment(
    html_content: Union[str, bytes],
    ticker: str,
    period_end: str,
) -> Tuple[Dict[str, str], Dict[Tuple[str, str], str], Dict[str, str]]:
    """
    Parse HTML SOI tables to build enrichment lookups for XBRL-extracted rows.

    Finds the Schedule of Investments table(s) in *html_content*, parses each
    row, and returns three dicts that can be used to fill missing fields in rows
    produced by the XBRL extractor:

        industry_map           – company_key  →  industry string
        maturity_by_principal  – (company_key, principal_key)  →  maturity_date
        maturity_default_map   – company_key  →  maturity_date
                                 (only populated when the company has exactly one
                                  unique maturity across all its rows)

    Avoids instantiating a full HTMLSOIParser (which would trigger a network
    request to load SEC company tickers) by using object.__new__ to obtain a
    bare instance whose pure-logic parsing methods can still be called.
    """
    if isinstance(html_content, (bytes, bytearray)):
        html_content = html_content.decode('utf-8', errors='replace')

    soup = BeautifulSoup(html_content, 'html.parser')
    tables = soup.find_all('table')

    all_table_tuples: List[Tuple] = []
    for i, table in enumerate(tables):
        row_lines = []
        for tr in table.find_all('tr'):
            cells = [c.get_text(separator=' ', strip=True) for c in tr.find_all(['td', 'th'])]
            if cells:
                row_lines.append('\t'.join(cells))
        table_text = '\n'.join(row_lines)
        if len(table_text.strip()) >= 50:
            all_table_tuples.append((str(table), table_text, i, f'table_{i}'))

    if not all_table_tuples:
        return {}, {}, {}

    soi_tables = fallback_table_detection(
        all_table_tuples,
        filing_date=period_end,
        filing_type='10-Q',
        period_end_date=period_end,
    )
    if not soi_tables:
        logger.info(
            'extract_soi_enrichment: no SOI tables detected (%d raw tables, period_end=%s)',
            len(all_table_tuples), period_end,
        )
        return {}, {}, {}

    # Detect unit scale from the full document so principal amounts can be
    # normalised to *thousands* for matching against XBRL-extracted values.
    html_lower = html_content.lower()
    if re.search(r'in\s+millions|\(\s*\$\s*in\s+millions\)', html_lower):
        unit_scale = 'millions'   # raw values are millions → ×1000 to get thousands
    elif re.search(r'in\s+thousands|\(\s*\$\s*in\s+thousands\)|\(\s*000', html_lower):
        unit_scale = 'thousands'  # raw values already in thousands
    else:
        unit_scale = 'thousands'  # safe default for BDC filings

    # Use HTMLSOIParser's pure-logic methods without triggering __init__
    # (all _parse_table / _find_header_row / _extract_row methods are stateless).
    # Carry state attributes let industry/type context flow across tables
    # (some BDCs split the SOI into many small tables without repeating headers).
    parser = object.__new__(HTMLSOIParser)
    parser._carry_industry = ''
    parser._carry_investment_type = ''

    industry_map: Dict[str, str] = {}
    maturity_by_principal: Dict[Tuple[str, str], str] = {}
    company_maturities: Dict[str, List[str]] = {}

    for table_html, _table_text, _table_num, _table_id in soi_tables:
        table_soup = BeautifulSoup(table_html, 'html.parser')
        table_tag = table_soup.find('table')
        if not table_tag:
            continue
        try:
            rows = parser._parse_table(table_tag, ticker, period_end)
        except Exception:
            continue

        for row in rows:
            cn = (row.get('company_name') or '').strip()
            if not cn:
                continue
            ck = _company_key(cn)

            # Industry: first non-empty value per company wins (skip numeric leakage e.g. % of net assets)
            ind = (row.get('industry') or '').strip()
            if ind and ck not in industry_map and not _is_numeric_industry(ind):
                industry_map[ck] = ind

            # Maturity date
            mat = (row.get('maturity_date') or '').strip()
            if mat:
                principal_str = (
                    row.get('principal_amount') or row.get('amortized_cost') or ''
                )
                pk_raw = _principal_key(principal_str)
                if pk_raw:
                    # Normalise to thousands so keys match XBRL-extracted values
                    if unit_scale == 'millions':
                        try:
                            pk = str(round(float(pk_raw) * 1000))
                        except (ValueError, TypeError):
                            pk = pk_raw
                    else:
                        pk = pk_raw
                    maturity_by_principal[(ck, pk)] = mat
                # Track unique maturities per company for the default map
                company_maturities.setdefault(ck, [])
                if mat not in company_maturities[ck]:
                    company_maturities[ck].append(mat)

    # Default maturity: only when exactly 1 unique value across all a company's rows
    maturity_default_map: Dict[str, str] = {
        ck: mats[0]
        for ck, mats in company_maturities.items()
        if len(mats) == 1
    }

    if industry_map or maturity_by_principal or maturity_default_map:
        logger.info(
            'HTML enrichment lookup: %d companies with industry, '
            '%d (company,principal) maturity entries, %d single-maturity defaults',
            len(industry_map), len(maturity_by_principal), len(maturity_default_map),
        )
    else:
        logger.info(
            'extract_soi_enrichment: %d SOI tables parsed but no industry/maturity extracted (missing columns?)',
            len(soi_tables),
        )
    return industry_map, maturity_by_principal, maturity_default_map


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='HTML-based BDC Schedule of Investments parser (no LLM)'
    )
    parser.add_argument('--ticker', required=True, help='BDC ticker symbol')
    parser.add_argument('--years-back', type=int, default=1,
                        help='Number of years back to process (default: 1)')
    parser.add_argument('--force', action='store_true',
                        help='Re-process even if output CSV already exists')
    parser.add_argument('--filing-type', default='10-Q', choices=['10-Q', '10-K'],
                        help='Filing type to process (default: 10-Q)')
    parser.add_argument('--output-dir', default=str(_REPO_ROOT / 'output'),
                        help='Output directory for CSV files')
    parser.add_argument('--data-dir', default=str(_REPO_ROOT / 'data'),
                        help='Data directory for SEC client cache')

    args = parser.parse_args()

    p = HTMLSOIParser(data_dir=args.data_dir, output_dir=args.output_dir)
    results = p.process_ticker(
        args.ticker,
        years_back=args.years_back,
        force=args.force,
        filing_type=args.filing_type,
    )

    if results:
        logger.info('Successfully produced %d file(s): %s',
                    len(results), [str(r) for r in results])
    else:
        logger.warning('No output produced for %s', args.ticker)
        sys.exit(1)


if __name__ == '__main__':
    main()
