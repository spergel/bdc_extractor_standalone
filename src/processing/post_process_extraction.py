#!/usr/bin/env python3
"""
Post-processing script to standardize newly extracted investment data.
Run this after llm_table_scraper.py to ensure data quality.

Usage:
    python post_process_extraction.py --file output/ARCC_investments_2025-11-06.csv
    python post_process_extraction.py --directory output/
"""

import csv
import argparse
import logging
import re
from pathlib import Path
from typing import List, Tuple
from standardization_rules import (
    create_mapping_dict as create_industry_mapping,
    standardize_industry,
    create_investment_type_mapping,
    standardize_investment_type,
    create_reference_rate_mapping,
    standardize_reference_rate,
    clean_spread,
    clean_company_name
)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def post_process_csv(input_file: Path, output_file: Path = None) -> Tuple[int, int, int]:
    """
    Post-process a CSV file to standardize industries, investment types, and reference rates.
    
    Args:
        input_file: Path to input CSV
        output_file: Path to output CSV (default: overwrite input)
    
    Returns:
        Tuple of (rows_processed, rows_changed, errors)
    """
    if not output_file:
        output_file = input_file
    
    # Create mapping dictionaries
    industry_mapping = create_industry_mapping()
    investment_type_mapping = create_investment_type_mapping()
    ref_rate_mapping = create_reference_rate_mapping()
    
    rows = []
    rows_changed = 0
    errors = 0
    
    try:
        # Read the file
        with open(input_file, 'r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            
            if not fieldnames:
                logger.error(f"No headers found in {input_file}")
                return 0, 0, 1
            
            for row in reader:
                original_row = row.copy()
                
                # Standardize industry
                if 'industry' in row:
                    original_industry = row['industry']
                    standardized_industry = standardize_industry(original_industry, industry_mapping)
                    row['industry'] = standardized_industry
                    if original_industry != standardized_industry:
                        rows_changed += 1
                
                # Standardize investment_type
                if 'investment_type' in row:
                    original_type = row['investment_type']
                    standardized_type = standardize_investment_type(original_type, investment_type_mapping)
                    row['investment_type'] = standardized_type
                    if original_type != standardized_type:
                        rows_changed += 1
                
                # Standardize reference_rate
                if 'reference_rate' in row:
                    original_rate = row['reference_rate']
                    standardized_rate = standardize_reference_rate(original_rate, ref_rate_mapping)
                    row['reference_rate'] = standardized_rate
                    if original_rate != standardized_rate:
                        rows_changed += 1
                
                # Clean spread
                if 'spread' in row:
                    original_spread = row['spread']
                    cleaned_spread = clean_spread(original_spread)
                    row['spread'] = cleaned_spread
                    if original_spread != cleaned_spread:
                        rows_changed += 1
                
                # Clean company_name
                if 'company_name' in row:
                    original_name = row['company_name']
                    cleaned_name = clean_company_name(original_name)
                    row['company_name'] = cleaned_name
                    if original_name != cleaned_name:
                        rows_changed += 1
                
                rows.append(row)
        
        # Write back
        with open(output_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        
        logger.info(f"✓ Processed {input_file.name}: {len(rows)} rows, {rows_changed} changes")
        return len(rows), rows_changed, errors
        
    except Exception as e:
        logger.error(f"✗ Error processing {input_file}: {e}")
        return 0, 0, 1

def process_directory(directory: Path, pattern: str = "*_investments_*.csv") -> None:
    """
    Process all CSV files in a directory.
    
    Args:
        directory: Directory containing CSV files
        pattern: Glob pattern for files to process
    """
    csv_files = list(directory.glob(pattern))
    
    if not csv_files:
        logger.warning(f"No files matching '{pattern}' found in {directory}")
        return
    
    logger.info(f"Found {len(csv_files)} files to process")
    
    total_rows = 0
    total_changes = 0
    total_errors = 0
    
    for csv_file in csv_files:
        rows, changes, errors = post_process_csv(csv_file)
        total_rows += rows
        total_changes += changes
        total_errors += errors
    
    logger.info(f"\n{'='*60}")
    logger.info(f"SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"Files processed: {len(csv_files)}")
    logger.info(f"Total rows: {total_rows:,}")
    logger.info(f"Total changes: {total_changes:,}")
    logger.info(f"Errors: {total_errors}")

def main():
    parser = argparse.ArgumentParser(
        description="Post-process extracted investment data to standardize values"
    )
    parser.add_argument('--file', type=Path, help='Single CSV file to process')
    parser.add_argument('--directory', type=Path, help='Directory of CSV files to process')
    parser.add_argument('--pattern', default='*_investments_*.csv', 
                       help='Glob pattern for files (default: *_investments_*.csv)')
    
    args = parser.parse_args()
    
    if not args.file and not args.directory:
        parser.error("Must specify either --file or --directory")
    
    if args.file:
        if not args.file.exists():
            logger.error(f"File not found: {args.file}")
            return 1
        
        rows, changes, errors = post_process_csv(args.file)
        
        if errors == 0:
            logger.info(f"\n✓ Success: Processed {rows:,} rows with {changes:,} standardizations")
            return 0
        else:
            logger.error(f"\n✗ Failed with {errors} errors")
            return 1
    
    elif args.directory:
        if not args.directory.exists():
            logger.error(f"Directory not found: {args.directory}")
            return 1
        
        process_directory(args.directory, args.pattern)
        return 0

if __name__ == "__main__":
    exit(main())
