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
    standardize_industry,
    standardize_investment_type,
    create_reference_rate_mapping,
    standardize_reference_rate,
    clean_spread,
    clean_company_name,
    normalize_industry,
    ALLOWED_INDUSTRIES,
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

            # Ensure we have a data quality flag column
            if 'data_quality_flags' not in fieldnames:
                fieldnames = fieldnames + ['data_quality_flags']

            # Infer ticker from filename for ticker-specific company name cleanup
            file_ticker = None
            stem = input_file.stem
            if stem.startswith("custom_scraper_"):
                parts = stem.split("_")
                file_ticker = parts[2] if len(parts) >= 3 else None
            elif stem.startswith("_investments_") or "_investments_" in stem:
                file_ticker = stem.split("_investments_")[0].strip("_")
            elif re.match(r"^[A-Z]{2,5}_investments_", stem, re.I):
                file_ticker = stem.split("_")[0]
            
            for row in reader:
                original_row = row.copy()

                # Collect per-row data quality issues
                issues = []
                
                # Standardize industry
                if 'industry' in row:
                    original_industry = row['industry']
                    standardized_industry = standardize_industry(original_industry)
                    row['industry'] = standardized_industry
                    if original_industry != standardized_industry:
                        rows_changed += 1
                
                # Standardize investment_type
                if 'investment_type' in row:
                    original_type = row['investment_type']
                    standardized_type = standardize_investment_type(original_type)
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
                
                # Clean company_name (with optional ticker for BDC-specific cleanup)
                if 'company_name' in row:
                    original_name = row['company_name']
                    ticker = (row.get('ticker') or '').strip() or file_ticker
                    cleaned_name, extracted_industry = clean_company_name(original_name, ticker=ticker or None)
                    row['company_name'] = cleaned_name
                    if original_name != cleaned_name:
                        rows_changed += 1
                    # Use industry from stripped title or company name (e.g. Advanced Aircrew → Aerospace & Defense) when industry is empty or Other
                    current_ind = (row.get('industry') or '').strip()
                    if extracted_industry and (not current_ind or current_ind == 'Other'):
                        canonical = standardize_industry(extracted_industry)
                        if canonical:
                            row['industry'] = canonical
                            rows_changed += 1
                    # If still empty/Other, infer from company name keywords (e.g. "Forescout Technologies" → Software & Technology)
                    if 'industry' in row:
                        current_ind = (row.get('industry') or '').strip()
                        if (not current_ind or current_ind == 'Other') and cleaned_name and not extracted_industry:
                            hint = normalize_industry(cleaned_name)
                            if hint and hint in ALLOWED_INDUSTRIES and hint != 'Other':
                                row['industry'] = hint
                                rows_changed += 1

                # --- Simple numeric sanity checks / flags ---
                try:
                    fv = float(row.get('fair_value', '') or 0)
                    principal = float(row.get('principal_amount', '') or 0)
                    cost = float(row.get('cost', '') or 0)
                except ValueError:
                    fv = principal = cost = 0

                # Flag obviously tiny cost relative to principal/FV (e.g. 13 vs 13,242)
                if cost > 0 and (principal > 0 or fv > 0):
                    denom = principal if principal > 0 else fv
                    # If cost is less than 5% of principal/FV but principal/FV are reasonably large,
                    # it's almost certainly a bad scrape like HFZ (13 vs 13,242).
                    if denom >= 1_000 and cost / denom < 0.05:
                        issues.append("cost_suspicious_vs_size")

                # Flag obviously tiny fair value relative to principal/cost.
                # This is where we often see equity/warrant commas/scale wrong,
                # e.g. principal 4,040 vs FV 4.
                fv_denom = 0.0
                if principal > 0:
                    fv_denom = principal
                elif cost > 0:
                    fv_denom = cost

                if fv > 0 and fv_denom >= 1_000:
                    ratio = fv / fv_denom
                    # If FV is less than 5% of principal/cost on a reasonably sized position,
                    # it's almost certainly a scaling / comma error rather than a true 95% loss.
                    if ratio < 0.05:
                        issues.append("fair_value_suspicious_vs_size")

                # Flag probable type mismatches hinted by company_name suffixes.
                name_lower = (row.get('company_name') or '').lower()
                type_lower = (row.get('investment_type') or '').lower()

                # Examples:
                #  - "LVF Holdings Inc (Revolver)" but type == "First Lien"
                #  - "MacQueen Equipment LLC (Delayed Draw)" but type == "First Lien"
                #  - "MC Asset Management (Corporate) LLC" but type == "First Lien"
                if "revolver" in name_lower and "revolver" not in type_lower:
                    issues.append("type_hint_revolver_from_name")
                if "delayed draw" in name_lower and "delayed draw" not in type_lower:
                    issues.append("type_hint_delayed_draw_from_name")
                if "preferred equity" in name_lower and "preferred" not in type_lower:
                    issues.append("type_hint_pref_equity_from_name")
                if "corporate" in name_lower and "corporate" not in type_lower:
                    issues.append("type_hint_corporate_from_name")

                # You can extend with more checks later (negative FV, etc.)

                # Persist issues into a pipe-separated flag field
                if issues:
                    existing = row.get('data_quality_flags', '') or ''
                    merged = set(filter(None, [s.strip() for s in existing.split('|')])) | set(issues)
                    row['data_quality_flags'] = '|'.join(sorted(merged))
                else:
                    # Preserve existing value or keep empty
                    row['data_quality_flags'] = row.get('data_quality_flags', '') or ''
                
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
    When using default pattern, also includes custom_scraper_*.csv (BCIC, etc.).
    Args:
        directory: Directory containing CSV files
        pattern: Glob pattern for files to process (default: *_investments_*.csv)
    """
    csv_files = list(directory.glob(pattern))
    if pattern == "*_investments_*.csv":
        csv_files = list(dict.fromkeys(csv_files + list(directory.glob("custom_scraper_*.csv"))))

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
