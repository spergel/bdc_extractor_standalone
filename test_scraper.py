#!/usr/bin/env python3
"""
Test Script for LLM Table Scraper

This script tests the table extraction and processing logic without requiring
an OpenAI API key. Useful for development and debugging.

Usage:
    python test_scraper.py --ticker MRCC --year 2025
"""

import os
import logging
import argparse
from pathlib import Path
from typing import List, Tuple, Optional

# Import the SEC client
from sec_api_client import SECAPIClient

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TestScraper:
    """Test version of the scraper without LLM dependency."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.sec_client = SECAPIClient(data_dir=str(self.data_dir))

    def extract_tables_from_filing_documents(self, filing_result) -> List[Tuple[str, str, int, str]]:
        """
        Extract HTML tables from individual filing documents.

        Returns list of (table_html, table_text, table_number, table_id) tuples.
        """
        import requests
        from bs4 import BeautifulSoup

        all_tables = []

        if not hasattr(filing_result, 'documents') or not filing_result.documents:
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

    def is_investment_table(self, table_text: str, context: str = "") -> Tuple[bool, float]:
        """
        Determine if a table contains investment/portfolio data.

        Returns (is_investment_table, confidence_score).
        """
        text_lower = table_text.lower()
        context_lower = context.lower()

        # Check for investment-related keywords
        investment_keywords = [
            'schedule of investments',
            'investments',
            'portfolio companies',
            'loan portfolio',
            'debt securities',
            'equity securities',
            'portfolio of investments',
            'principal amount',
            'fair value',
            'interest rate',
            'maturity',
            'acquisition',
            'portfolio company',
            'borrower',
            'lender'
        ]

        keyword_matches = sum(1 for keyword in investment_keywords
                            if keyword.lower() in text_lower or keyword.lower() in context_lower)

        # Additional checks for investment table characteristics
        investment_indicators = [
            '%' in text_lower and ('rate' in text_lower or 'spread' in text_lower),
            '$' in text_lower and any(term in text_lower for term in ['million', 'thousand', 'cost', 'value']),
            'sf' in text_lower or 'sofr' in text_lower or 'libor' in text_lower,
            any(date_pattern in text_lower for date_pattern in ['2024', '2025', '2026', '2027'])
        ]

        indicator_matches = sum(investment_indicators)

        # Calculate confidence score
        keyword_score = keyword_matches / len(investment_keywords) if investment_keywords else 0
        indicator_score = indicator_matches / len(investment_indicators)
        confidence = (keyword_score * 0.6) + (indicator_score * 0.4)

        # Require minimum confidence and some investment indicators
        is_investment = confidence > 0.3 and indicator_matches >= 2

        return is_investment, confidence

    def extract_table_context(self, html_content: str, table_index: int) -> str:
        """Extract context around a table to help with classification."""
        from bs4 import BeautifulSoup

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
            if current.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'p']:
                text = current.get_text(strip=True)
                if text:
                    context_parts.insert(0, text)
                    context_chars += len(text)
            elif current.name == 'table':
                # Stop at previous table
                break
            current = current.previous_sibling

        return ' '.join(context_parts)

    def analyze_filing(self, ticker: str, filing_type: str = "10-Q", year: Optional[int] = None):
        """
        Analyze a filing and report on found tables without processing them.
        """
        logger.info(f"Analyzing {ticker} {filing_type} for {year or 'latest'}")

        try:
            # Get the filing index URL first
            index_url = self.sec_client.get_filing_index_url(
                ticker=ticker,
                filing_type=filing_type,
                year=year
            )

            if not index_url:
                logger.warning(f"No {filing_type} filing found for {ticker}")
                return

            logger.info(f"Found filing index: {index_url}")

            # Extract accession number from URL
            import re
            accession_match = re.search(r'/(\d{10}-\d{2}-\d{6})', index_url)
            accession_number = accession_match.group(1) if accession_match else "unknown"

            # Fetch the actual filing content using the index URL
            filing_result = self.sec_client.fetch_filing_by_index_url(
                index_url=index_url,
                ticker=ticker,
                filing_type=filing_type,
                save_to_file=False
            )

            if not filing_result:
                logger.warning(f"Could not fetch filing content from {index_url}")
                return

            logger.info(f"Retrieved {filing_type} filing: {accession_number}")

            # Extract tables from individual filing documents
            tables = self.extract_tables_from_filing_documents(filing_result)

            print(f"📄 Filing has {len(filing_result.documents) if hasattr(filing_result, 'documents') else 0} documents")
            print(f"📊 Tables found: {len(tables)}")

            if not tables:
                logger.warning("No tables found in filing documents")
                return

            print(f"\n{'='*80}")
            print(f"ANALYSIS REPORT: {ticker} {filing_type} ({filing_result.filing_date})")
            print(f"Accession: {filing_result.accession_number}")
            print(f"Total tables found: {len(tables)}")
            print(f"{'='*80}")

            investment_tables = []

            for table_html, table_text, table_num, table_id in tables:
                # Get context for better classification
                context = self.extract_table_context(filing_result.text, table_num)

                # Check if this is an investment table
                is_investment, confidence = self.is_investment_table(table_text, context)

                # Truncate text for display
                preview_text = table_text[:200] + "..." if len(table_text) > 200 else table_text
                preview_context = context[:100] + "..." if len(context) > 100 else context

                status = "✅ INVESTMENT" if is_investment else "❌ OTHER"
                confidence_icon = "🟢" if confidence > 0.7 else "🟡" if confidence > 0.4 else "🔴"

                print(f"\nTable {table_num + 1}: {status} {confidence_icon} (confidence: {confidence:.2f})")
                print(f"Context: {preview_context}")
                print(f"Content: {preview_text}")

                if is_investment:
                    investment_tables.append((table_num, confidence))

            print(f"\n{'='*80}")
            print(f"SUMMARY: Found {len(investment_tables)} potential investment tables")
            if investment_tables:
                print("Investment tables:", [f"#{num+1}({conf:.2f})" for num, conf in investment_tables])
            print(f"{'='*80}")

            return len(investment_tables)

        except Exception as e:
            logger.error(f"Error analyzing filing for {ticker}: {e}")
            return 0

def main():
    """Main test function."""
    parser = argparse.ArgumentParser(description="Test LLM table scraper (no API key required)")
    parser.add_argument('--ticker', required=True, help='Company ticker symbol')
    parser.add_argument('--filing-type', default='10-Q', choices=['10-Q', '10-K', '8-K', '424B'],
                       help='Type of SEC filing to analyze')
    parser.add_argument('--year', type=int, help='Year to fetch filing for (default: latest)')

    args = parser.parse_args()

    try:
        # Initialize test scraper
        scraper = TestScraper()

        # Analyze the filing
        investment_tables = scraper.analyze_filing(
            ticker=args.ticker,
            filing_type=args.filing_type,
            year=args.year
        )

        if investment_tables and investment_tables > 0:
            print(f"\n✅ Analysis complete! Found {investment_tables} potential investment tables.")
            print("💡 To process these tables, run the full scraper with an OpenAI API key:")
            print(f"   python llm_table_scraper.py --ticker {args.ticker} --filing-type {args.filing_type}" +
                  (f" --year {args.year}" if args.year else ""))
        else:
            print(f"\n❌ Analysis complete. No investment tables detected for {args.ticker}.")

    except Exception as e:
        logger.error(f"Test failed: {e}")
        exit(1)

if __name__ == "__main__":
    main()
