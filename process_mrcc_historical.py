#!/usr/bin/env python3
"""Process MRCC 10-Q filings for the past year."""

from llm_table_scraper import LLMTableScraper
from sec_api_client import SECAPIClient

def main():
    sec_client = SECAPIClient(data_dir='data')
    scraper = LLMTableScraper(
        llm_provider='gemini',
        output_dir='output',
        debug_dir='debug_tables'
    )
    
    filings = sec_client.get_historical_10q_filings('MRCC', years_back=1)
    print(f'Found {len(filings)} filings for the past year:')
    for f in filings:
        print(f"  - {f['date']}: {f['accession']}")
    
    print('\nProcessing each filing...\n')
    
    original_fetch = scraper.sec_client.fetch_filing_by_index_url
    
    for f in filings:
        print(f"Processing MRCC 10-Q for filing date {f['date']}...")
        
        # Fetch the specific historical filing
        filing_result = sec_client.fetch_filing_by_index_url(
            index_url=f['index_url'],
            ticker='MRCC',
            filing_type='10-Q',
            save_to_file=False,
        )
        
        if not filing_result:
            print(f"  Skipping - could not fetch filing result")
            continue
        
        # Temporarily override the fetch method to return our pre-fetched result
        def _fake_fetch(index_url, ticker, filing_type, save_to_file=False):
            return filing_result
        
        scraper.sec_client.fetch_filing_by_index_url = _fake_fetch
        
        # Process the filing (the year is used for lookup, but our fake fetch ensures
        # we get the correct historical filing with the right filing_date)
        year = int(f['date'].split('-')[0])
        csv_path = scraper.process_filing(ticker='MRCC', filing_type='10-Q', year=year)
        
        if csv_path:
            print(f"  ✅ Saved: {csv_path}")
        else:
            print(f"  ❌ Failed to generate CSV")
        
        # Restore original method
        scraper.sec_client.fetch_filing_by_index_url = original_fetch
        print()

if __name__ == '__main__':
    main()









