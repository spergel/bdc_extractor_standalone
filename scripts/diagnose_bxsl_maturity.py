#!/usr/bin/env python3
"""
Diagnose why BXSL maturity dates are missing from HTML enrichment.
"""
import sys
import re
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / 'src' / 'extraction'))

from bs4 import BeautifulSoup
from sec_api_client import SECAPIClient
from html_soi_parser import extract_soi_enrichment, _company_key
from table_detection import fallback_table_detection

def main():
    data_dir = str(_REPO_ROOT / 'data')
    client = SECAPIClient(data_dir=data_dir)

    # Get BXSL 10-Q filings
    filings = list(client.get_historical_10q_filings('BXSL', years_back=1))
    if not filings:
        logger.error('No BXSL filings found')
        return

    filing = filings[0]
    logger.info('Using filing: %s (period end: %s)', filing.get('date'), filing.get('period_end_date'))

    # Fetch the filing
    from sec_api_client import _sec_get
    import requests

    index_url = filing['index_url']
    logger.info('Index URL: %s', index_url)

    result = client.fetch_filing_by_index_url(
        index_url=index_url,
        ticker='BXSL',
        filing_type='10-Q',
        save_to_file=False,
        document_types=['.htm', '.html'],
        main_document_only=True,
    )
    if not result or not result.documents:
        logger.error('Could not fetch filing')
        return

    # Find the main HTML document
    html_doc = None
    for doc in result.documents:
        fn = doc.filename.lower()
        if fn.endswith('.htm') or fn.endswith('.html'):
            html_doc = doc
            break

    if not html_doc:
        logger.error('No HTML document found')
        return

    logger.info('Main HTML doc: %s', html_doc.filename)
    resp = _sec_get(html_doc.url, headers=client.headers)
    resp.raise_for_status()
    html_content = resp.text
    logger.info('HTML size: %.1f MB', len(html_content) / 1e6)

    period_end = filing.get('period_end_date') or filing.get('date')

    # Parse tables and detect SOI tables
    soup = BeautifulSoup(html_content, 'html.parser')
    tables = soup.find_all('table')
    logger.info('Total HTML tables: %d', len(tables))

    all_table_tuples = []
    for i, table in enumerate(tables):
        row_lines = []
        for tr in table.find_all('tr'):
            cells = [c.get_text(separator=' ', strip=True) for c in tr.find_all(['td', 'th'])]
            if cells:
                row_lines.append('\t'.join(cells))
        table_text = '\n'.join(row_lines)
        if len(table_text.strip()) >= 50:
            all_table_tuples.append((str(table), table_text, i, f'table_{i}'))

    logger.info('Non-trivial tables: %d', len(all_table_tuples))

    soi_tables = fallback_table_detection(
        all_table_tuples,
        filing_date=period_end,
        filing_type='10-Q',
        period_end_date=period_end,
    )
    logger.info('SOI tables detected: %d', len(soi_tables))

    if not soi_tables:
        logger.error('No SOI tables found!')
        return

    # For each SOI table, try to find the header
    from html_soi_parser import HTMLSOIParser, _company_key
    parser = object.__new__(HTMLSOIParser)
    parser._carry_industry = ''
    parser._carry_investment_type = ''

    for table_html, table_text, table_num, table_id in soi_tables[:5]:
        logger.info('\n=== SOI Table %d ===', table_num)
        tsoup = BeautifulSoup(table_html, 'html.parser')
        table_tag = tsoup.find('table')
        if not table_tag:
            continue

        trs = list(table_tag.find_all('tr'))
        logger.info('Rows in table: %d', len(trs))

        # Show first 3 rows (headers)
        for ri, tr in enumerate(trs[:4]):
            cells_raw = tr.find_all(['td', 'th'])
            cell_texts = []
            for c in cells_raw:
                txt = c.get_text(separator=' ', strip=True)
                cs = c.get('colspan', '1')
                cell_texts.append(f'{txt!r}(cs={cs})')
            logger.info('Row %d: %s', ri, ' | '.join(cell_texts))

        # Try _find_header_row
        header_idx, col_map, fmt, header_rows = parser._find_header_row(trs)
        logger.info('Header: idx=%d, fmt=%s, header_rows=%d, col_map=%s',
                    header_idx, fmt, header_rows, col_map)

        if header_idx >= 0:
            # Try parsing a few rows
            rows = parser._parse_table(table_tag, 'BXSL', period_end)
            logger.info('Parsed %d rows', len(rows))
            mat_rows = [r for r in rows if r.get('maturity_date')]
            logger.info('Rows with maturity_date: %d', len(mat_rows))
            if mat_rows:
                for r in mat_rows[:3]:
                    logger.info('  %s: mat=%s, principal=%s',
                                r.get('company_name', '')[:40],
                                r.get('maturity_date'),
                                r.get('principal_amount'))
            else:
                # Show sample rows to see what's there
                for r in rows[:3]:
                    logger.info('  Sample row: %s', {k: v for k, v in r.items() if v})

    # Now run the full extract_soi_enrichment
    logger.info('\n=== Running extract_soi_enrichment ===')
    ind_map, mat_by_prin, mat_default = extract_soi_enrichment(html_content, 'BXSL', period_end)
    logger.info('industry_map: %d entries', len(ind_map))
    logger.info('maturity_by_principal: %d entries', len(mat_by_prin))
    logger.info('maturity_default_map: %d entries', len(mat_default))
    if mat_default:
        for k, v in list(mat_default.items())[:5]:
            logger.info('  default: %s -> %s', k, v)
    if mat_by_prin:
        for (ck, pk), v in list(mat_by_prin.items())[:5]:
            logger.info('  by_principal: (%s, %s) -> %s', ck, pk, v)

if __name__ == '__main__':
    main()
