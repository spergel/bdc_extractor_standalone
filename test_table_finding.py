#!/usr/bin/env python3
"""
Test script to find investment schedule tables without LLM processing.
"""

import logging
import argparse
from sec_api_client import SECAPIClient
from llm_table_scraper import LLMTableScraper

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_table_finding(ticker: str, filing_type: str = "10-Q", year: int = None):
    """Test finding investment schedule tables."""
    
    # Initialize SEC client
    sec_client = SECAPIClient(data_dir="data")
    
    logger.info(f"Testing table finding for {ticker} {filing_type} for {year or 'latest'}")
    
    # Get the filing index URL
    index_url = sec_client.get_filing_index_url(
        ticker=ticker,
        filing_type=filing_type,
        year=year
    )
    
    if not index_url:
        logger.warning(f"No {filing_type} filing found for {ticker}")
        return
    
    # Fetch the filing
    filing_result = sec_client.fetch_filing_by_index_url(
        index_url=index_url,
        ticker=ticker,
        filing_type=filing_type,
        save_to_file=False
    )
    
    if not filing_result:
        logger.warning("Could not fetch filing content")
        return
    
    logger.info(f"Retrieved filing: {filing_result.filing_date}")
    
    # Extract tables directly (simplified version)
    from bs4 import BeautifulSoup
    import requests
    
    all_tables = []
    
    if not filing_result.documents:
        logger.warning("No documents found in filing result")
        return
    
    for doc in filing_result.documents:
        if not (doc.filename.endswith('.htm') or doc.filename.endswith('.html')):
            continue
        if doc.filename.endswith('.xml') or 'xml' in doc.filename.lower():
            continue
        
        try:
            logger.info(f"Processing document: {doc.filename}")
            response = requests.get(doc.url, headers=sec_client.headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            doc_tables = soup.find_all('table')
            
            logger.info(f"Found {len(doc_tables)} tables in {doc.filename}")
            
            for i, table in enumerate(doc_tables):
                table_html = str(table)
                table_text = table.get_text(separator=' ', strip=True)
                
                if len(table_text.strip()) < 100:
                    continue
                
                table_id = f"{doc.filename}_table_{i}"
                all_tables.append((table_html, table_text, len(all_tables), table_id))
        
        except Exception as e:
            logger.warning(f"Failed to process document {doc.filename}: {e}")
            continue
    
    logger.info(f"Found {len(all_tables)} total tables in filing")
    
    # Use the new refined detection method
    logger.info("\nSearching for investment schedule tables using refined method...")
    
    # Create a minimal scraper instance just for the method
    try:
        from llm_table_scraper import LLMTableScraper
        scraper = LLMTableScraper(
            llm_provider="gemini",
            gemini_api_key="dummy",  # Won't be used
            output_dir="output",
            debug_dir="debug_tables"
        )
        
        investment_tables = scraper.find_investment_schedule_tables(
            all_tables,
            filing_result.filing_date,
            filing_result.text,
            filing_result.documents if hasattr(filing_result, 'documents') else None
        )
    except Exception as e:
        logger.warning(f"Could not use refined method: {e}, using fallback")
        # Fallback to simple content-based detection
        investment_tables = []
        for html, text, table_num, table_id in all_tables:
            text_lower = text.lower()
            if ('portfolio company' in text_lower and
                ('principal' in text_lower and 'fair value' in text_lower)):
                investment_tables.append((html, text, table_num, table_id))
    
    logger.info(f"\n{'='*60}")
    logger.info(f"RESULTS:")
    logger.info(f"  Total tables found: {len(all_tables)}")
    logger.info(f"  Investment schedule tables found: {len(investment_tables)}")
    logger.info(f"{'='*60}\n")
    
    # Show details of investment tables
    for i, (html, text, table_num, table_id) in enumerate(investment_tables):
        text_len = len(text)
        company_indicators = ['llc', 'inc.', 'corp', 'ltd.', 'lp', 'llp']
        company_count = sum(1 for indicator in company_indicators if indicator in text.lower())
        
        logger.info(f"Table {table_num} ({table_id}):")
        logger.info(f"  Size: {text_len} characters")
        logger.info(f"  Company indicators: {company_count}")
        logger.info(f"  Preview: {text[:200]}...")
        logger.info("")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test investment schedule table finding")
    parser.add_argument('--ticker', required=True, help='Company ticker symbol')
    parser.add_argument('--filing-type', default='10-Q', help='Type of SEC filing')
    parser.add_argument('--year', type=int, help='Year to fetch filing for')
    
    args = parser.parse_args()
    
    test_table_finding(args.ticker, args.filing_type, args.year)

