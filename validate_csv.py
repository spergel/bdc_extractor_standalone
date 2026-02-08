#!/usr/bin/env python3
"""Validate CSV files for column count issues."""

import csv
import sys
from pathlib import Path
from io import StringIO

def validate_csv_file(filepath):
    """Check if all rows in CSV have exactly 16 columns."""
    issues = []
    expected_columns = 16
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            
            for row_num, row in enumerate(reader, start=1):
                col_count = len(row)
                
                if row_num == 1:
                    # Header row
                    if col_count != expected_columns:
                        issues.append({
                            'row': row_num,
                            'type': 'header',
                            'columns': col_count,
                            'expected': expected_columns,
                            'sample': row[:5] if row else []
                        })
                else:
                    # Data rows
                    if col_count != expected_columns:
                        issues.append({
                            'row': row_num,
                            'type': 'data',
                            'columns': col_count,
                            'expected': expected_columns,
                            'sample': row[:5] if row else [],
                            'company_name': row[0] if row else ''
                        })
    
    except Exception as e:
        return {'error': str(e)}
    
    return {
        'file': str(filepath),
        'total_issues': len(issues),
        'issues': issues
    }

def main():
    output_dir = Path('output')
    
    # Find all MRCC investment CSV files
    csv_files = list(output_dir.glob('MRCC_investments_*.csv'))
    
    if not csv_files:
        print("No MRCC investment CSV files found in output directory")
        return
    
    print(f"Validating {len(csv_files)} CSV file(s)...\n")
    
    all_issues = []
    for csv_file in sorted(csv_files):
        print(f"Checking {csv_file.name}...")
        result = validate_csv_file(csv_file)
        
        if 'error' in result:
            print(f"  ❌ Error: {result['error']}\n")
            continue
        
        if result['total_issues'] == 0:
            print(f"  ✅ All rows have exactly 16 columns\n")
        else:
            print(f"  ⚠️  Found {result['total_issues']} row(s) with incorrect column count:\n")
            
            for issue in result['issues'][:10]:  # Show first 10 issues
                if issue['type'] == 'header':
                    print(f"    Row {issue['row']} (HEADER): {issue['columns']} columns (expected {issue['expected']})")
                    print(f"      Sample: {issue['sample']}")
                else:
                    print(f"    Row {issue['row']}: {issue['columns']} columns (expected {issue['expected']})")
                    print(f"      Company: {issue['company_name'][:60] if issue['company_name'] else 'N/A'}")
                    print(f"      Sample: {issue['sample']}")
            
            if result['total_issues'] > 10:
                print(f"    ... and {result['total_issues'] - 10} more issue(s)")
            
            print()
            all_issues.append(result)
    
    if all_issues:
        print(f"\n⚠️  Summary: Found issues in {len(all_issues)} file(s)")
        total_issues = sum(r['total_issues'] for r in all_issues)
        print(f"   Total problematic rows: {total_issues}")
    else:
        print("\n✅ All CSV files are valid - every row has exactly 16 columns!")

if __name__ == '__main__':
    main()









