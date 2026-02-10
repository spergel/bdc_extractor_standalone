#!/usr/bin/env python3
"""
Clean data quality issues:
- Remove dates from numeric columns (principal_amount, amortized_cost, etc.)
- Remove non-dates from date columns (acquisition_date, maturity_date)
- Remove reference rates from rate columns (pik_rate, cash_rate)
- Convert parentheses to negative numbers
- Standardize n/a values to empty
"""
import csv
import re
from pathlib import Path
from collections import Counter, defaultdict

def is_valid_date(value: str) -> bool:
    """Check if value is a valid YYYY-MM-DD date"""
    return bool(re.match(r'^\d{4}-\d{2}-\d{2}$', value))

def clean_date_column(value: str, column_name: str) -> str:
    """Clean date columns - keep only valid YYYY-MM-DD dates"""
    if not value or value.strip() == '':
        return ''
    
    value = value.strip()
    
    # Keep valid dates
    if is_valid_date(value):
        return value
    
    # Remove everything else (n/a, percentages, numbers, reference rates)
    return ''

def clean_numeric_column(value: str) -> str:
    """Clean numeric columns - remove dates, keep only numbers"""
    if not value or value.strip() == '':
        return ''
    
    value = value.strip()
    
    # Remove dates (YYYY-MM-DD format)
    if re.match(r'^\d{4}-\d{2}-\d{2}$', value):
        return ''
    
    # Remove "n/a" variations
    if value.lower() in ['n/a', 'na', 'none']:
        return ''
    
    # Convert parentheses to negative (e.g., "(2)" -> "-2")
    if re.match(r'^\([\d.]+\)$', value):
        num = value.strip('()')
        return f'-{num}'
    
    # Keep valid numbers (with optional commas, decimals, negative sign)
    # Allow: 123, 123.45, -123.45, 1,234.56
    if re.match(r'^-?\d{1,3}(,?\d{3})*(\.\d+)?$', value):
        return value
    
    # If it doesn't look like a number, remove it
    return ''

def clean_rate_column(value: str) -> str:
    """Clean rate columns (cash_rate, pik_rate) - keep only numeric values"""
    if not value or value.strip() == '':
        return ''
    
    value = value.strip()
    
    # Remove "n/a" variations
    if value.lower() in ['n/a', 'na', 'none']:
        return ''
    
    # Remove reference rates that don't belong here
    reference_rates = ['SOFR', 'LIBOR', 'L', 'S', 'SF', 'P', 'PRIME', 'EURIBOR', 
                       'SONIA', 'CDOR', 'BKBM', 'BASE RATE', '-']
    if value.upper() in reference_rates:
        return ''
    
    # Remove formulas like "L + 6.00%"
    if '+' in value or 'LIBOR' in value.upper() or 'SOFR' in value.upper():
        return ''
    
    # Remove "PIK" text
    if value.upper() == 'PIK':
        return ''
    
    # Keep valid percentages or decimals
    # Allow: 5.50, 5.50%, 0.50
    if re.match(r'^\d+\.?\d*%?$', value):
        return value
    
    # Remove anything else
    return ''

def process_csv_files(dry_run=True):
    """Process all CSV files and clean problematic columns"""
    data_dir = Path("frontend/public/data/investments")
    csv_files = list(data_dir.glob("**/*.csv"))
    
    # Track statistics
    stats = {
        'files_processed': 0,
        'rows_processed': 0,
        'changes': Counter(),
        'before': defaultdict(Counter),
        'after': defaultdict(Counter)
    }
    
    # Columns to clean
    date_columns = ['acquisition_date', 'maturity_date']
    numeric_columns = ['principal_amount', 'amortized_cost', 'fair_value', 
                       'percent_of_net_assets', 'cost', 'commitment_limit', 
                       'undrawn_commitment']
    rate_columns = ['cash_rate', 'pik_rate']
    
    for csv_file in csv_files:
        print(f"Processing: {csv_file.name}")
        rows = []
        changes_in_file = 0
        
        try:
            # Read the file
            with open(csv_file, 'r', encoding='utf-8', newline='') as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                
                for row in reader:
                    # Clean date columns
                    for col in date_columns:
                        if col in row:
                            original = row[col]
                            cleaned = clean_date_column(original, col)
                            if original != cleaned:
                                changes_in_file += 1
                                stats['changes'][f'{col}_cleaned'] += 1
                            stats['before'][col][original] += 1
                            stats['after'][col][cleaned] += 1
                            row[col] = cleaned
                    
                    # Clean numeric columns
                    for col in numeric_columns:
                        if col in row:
                            original = row[col]
                            cleaned = clean_numeric_column(original)
                            if original != cleaned:
                                changes_in_file += 1
                                stats['changes'][f'{col}_cleaned'] += 1
                            stats['before'][col][original] += 1
                            stats['after'][col][cleaned] += 1
                            row[col] = cleaned
                    
                    # Clean rate columns
                    for col in rate_columns:
                        if col in row:
                            original = row[col]
                            cleaned = clean_rate_column(original)
                            if original != cleaned:
                                changes_in_file += 1
                                stats['changes'][f'{col}_cleaned'] += 1
                            stats['before'][col][original] += 1
                            stats['after'][col][cleaned] += 1
                            row[col] = cleaned
                    
                    stats['rows_processed'] += 1
                    rows.append(row)
            
            # Write back if not dry run
            if not dry_run and changes_in_file > 0:
                with open(csv_file, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                print(f"  [OK] Updated {changes_in_file} cells")
            elif dry_run and changes_in_file > 0:
                print(f"  Would update {changes_in_file} cells")
            else:
                print(f"  No changes needed")
            
            stats['files_processed'] += 1
            
        except Exception as e:
            print(f"  [ERROR] {e}")
    
    return stats

def print_statistics(stats):
    """Print statistics about the cleanup"""
    print("\n" + "="*100)
    print("CLEANUP SUMMARY")
    print("="*100)
    print(f"Files processed: {stats['files_processed']}")
    print(f"Rows processed: {stats['rows_processed']:,}")
    print(f"Total changes: {sum(stats['changes'].values()):,}")
    
    print("\n" + "="*100)
    print("CHANGES BY COLUMN")
    print("="*100)
    for col, count in sorted(stats['changes'].items(), key=lambda x: x[1], reverse=True):
        print(f"{col:<40} {count:>10,} changes")
    
    # Show examples of what was removed from key columns
    print("\n" + "="*100)
    print("EXAMPLES OF DATA REMOVED")
    print("="*100)
    
    # Show acquisition_date issues removed
    if 'acquisition_date' in stats['before']:
        removed = {k: v for k, v in stats['before']['acquisition_date'].items() 
                   if k and not is_valid_date(k)}
        if removed:
            print("\nacquisition_date - Non-dates removed:")
            for val, count in Counter(removed).most_common(15):
                print(f"  {val:<40} {count:>8,}")
    
    # Show maturity_date issues removed
    if 'maturity_date' in stats['before']:
        removed = {k: v for k, v in stats['before']['maturity_date'].items() 
                   if k and not is_valid_date(k)}
        if removed:
            print("\nmaturity_date - Non-dates removed:")
            for val, count in Counter(removed).most_common(15):
                print(f"  {val:<40} {count:>8,}")
    
    # Show principal_amount issues removed
    if 'principal_amount' in stats['before']:
        removed = {k: v for k, v in stats['before']['principal_amount'].items() 
                   if k and re.match(r'^\d{4}-\d{2}-\d{2}$', k)}
        if removed:
            print("\nprincipal_amount - Dates removed:")
            total = sum(removed.values())
            print(f"  Total dates removed: {total:,}")
            for val, count in Counter(removed).most_common(10):
                print(f"  {val:<40} {count:>8,}")

if __name__ == "__main__":
    import sys
    
    dry_run = True
    if len(sys.argv) > 1 and sys.argv[1] == '--apply':
        dry_run = False
        print("WARNING: APPLYING CHANGES TO FILES\n")
    else:
        print("DRY RUN MODE (use --apply to make changes)\n")
    
    stats = process_csv_files(dry_run=dry_run)
    print_statistics(stats)
    
    if dry_run:
        print("\n" + "="*100)
        print("To apply these changes, run: python clean_column_data.py --apply")
        print("="*100)
