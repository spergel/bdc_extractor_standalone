"""
Data deduplication logic.

This module removes duplicate investment rows based on company name, investment type,
and principal amount matching.
"""

import csv
import logging
from io import StringIO
from datetime import datetime, date
from typing import List, Optional, Dict, Tuple, Any

logger = logging.getLogger(__name__)


def parse_maturity_date(maturity_str: str) -> Optional[date]:
    """
    Parse maturity date string to date for comparison.
    
    Args:
        maturity_str: Date string in various formats
        
    Returns:
        Parsed date or None if unparseable
    """
    if not maturity_str or not maturity_str.strip():
        return None
    s = maturity_str.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def deduplicate_csv_rows(csv_rows: List[Any]) -> List[str]:
    """
    Remove duplicate rows from CSV data.
    Duplicates are identified by: company_base + investment_type + principal (within 1%).
    When the same position appears with different maturity dates (e.g. 10-Q current vs prior
    year-end), prefers the row with the LATER maturity_date (current period).

    Args:
        csv_rows: List of (row_str, table_idx, table_size) or List[str] for legacy
        
    Returns:
        Deduplicated list of CSV row strings
    """
    if not csv_rows:
        return []

    # Normalize input: convert to [(row_str, table_idx, table_size), ...]
    rows_with_meta = []
    if isinstance(csv_rows[0], tuple):
        for item in csv_rows:
            row_str = item[0] if len(item) > 0 else ""
            table_idx = item[1] if len(item) > 1 else -1
            table_size = item[2] if len(item) > 2 else 0
            rows_with_meta.append((row_str, table_idx, table_size))
    else:
        for row_str in csv_rows:
            rows_with_meta.append((row_str, -1, 0))

    parsed_rows = []
    header_row = None
    orphan_count = 0

    for i, (row_str, table_idx, table_size) in enumerate(rows_with_meta):
        reader = csv.reader(StringIO(row_str))
        try:
            cols = list(reader)[0]
        except Exception:
            continue

        if i == 0:
            header_row = row_str
            continue

        if len(cols) < 3:
            continue
        
        # FILTER: Skip rows where ALL critical financial fields are empty
        # Ensure we have enough columns first
        expected_columns = 16
        while len(cols) < expected_columns:
            cols.append('')
        
        # Critical fields: principal(9), amortized_cost(10), fair_value(11), percent(12), cost(13)
        has_any_financial_data = any(
            cols[i].strip() and cols[i].strip() not in ['', 'N/A', 'n/a', '-', '0', '0.0']
            for i in [9, 10, 11, 12, 13]
        )
        
        if not has_any_financial_data:
            orphan_count += 1
            company_name = cols[0].strip() if cols else 'unknown'
            logger.debug(f"Skipping orphan row with no financial data: {company_name}")
            continue

        expected_columns = 16
        if len(cols) > 1 and len(cols) > expected_columns:
            col1 = cols[1].strip()
            if col1 and any(col1.endswith(s) for s in ['.', 'Inc.', 'LLC', 'Corp.', 'LP', 'Ltd.', 'Company']):
                cols[0] = f"{cols[0]}, {col1}"
                cols = [cols[0]] + cols[2:]

        while len(cols) < expected_columns:
            cols.append('')

        company_base = (cols[0].strip().lower().split('(')[0] if cols else "").strip()
        company_base = ' '.join(company_base.split())
        investment_type = ' '.join((cols[1].strip().lower() if len(cols) > 1 else "").split())
        maturity_date = cols[8].strip() if len(cols) > 8 else ""
        principal_str = (cols[9].strip() if len(cols) > 9 else "").replace(',', '').replace(' ', '')
        try:
            principal_val = float(principal_str) if principal_str else None
        except ValueError:
            principal_val = None

        non_empty_count = sum(1 for c in cols if c.strip())
        maturity_dt = parse_maturity_date(maturity_date)
        parsed_rows.append((company_base, investment_type, maturity_date, maturity_dt, principal_val,
                           non_empty_count, table_size, row_str, cols))

    def principal_match(a: Optional[float], b: Optional[float]) -> bool:
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        denom = max(abs(a), abs(b), 1.0)
        return abs(a - b) / denom <= 0.01

    # Group by (company_base, investment_type)
    grouped: Dict[Tuple[str, str], List[Tuple]] = {}
    for company_base, inv_type, maturity, maturity_dt, principal, non_empty, table_size, row_str, cols in parsed_rows:
        key = (company_base, inv_type)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append((principal, maturity_dt, non_empty, table_size, row_str, cols))

    result_rows: List[Tuple] = []
    for key, entries in grouped.items():
        # Find duplicates with matching principal and keep the best
        keep_mask = [True] * len(entries)
        for i in range(len(entries)):
            if not keep_mask[i]:
                continue
            principal_i, maturity_dt_i, non_empty_i, size_i, _, _ = entries[i]
            for j in range(len(entries)):
                if i == j or not keep_mask[j]:
                    continue
                principal_j, maturity_dt_j, non_empty_j, size_j, _, _ = entries[j]
                if not principal_match(principal_i, principal_j):
                    continue
                # Same position: keep the one with later maturity
                if maturity_dt_i is not None and maturity_dt_j is not None:
                    if maturity_dt_j > maturity_dt_i:
                        keep_mask[i] = False
                        break
                    elif maturity_dt_j < maturity_dt_i:
                        keep_mask[j] = False
                        continue
                elif maturity_dt_i is None and maturity_dt_j is not None:
                    keep_mask[i] = False
                    break
                elif maturity_dt_i is not None and maturity_dt_j is None:
                    keep_mask[j] = False
                    continue
                else:
                    if non_empty_j > non_empty_i or (non_empty_j == non_empty_i and size_j > size_i):
                        keep_mask[i] = False
                        break
                    elif non_empty_j < non_empty_i or (non_empty_j == non_empty_i and size_j < size_i):
                        keep_mask[j] = False
                        continue

        for idx, keep in enumerate(keep_mask):
            if keep:
                _, _, _, _, row_str, cols = entries[idx]
                result_rows.append((row_str, cols))

    result = [header_row] if header_row else []
    for row_str, cols in result_rows:
        output = StringIO()
        try:
            w = csv.writer(output, quoting=csv.QUOTE_MINIMAL, lineterminator='')
            w.writerow(cols)
            result.append(output.getvalue())
        except Exception as e:
            logger.warning(f"Failed to re-quote row in deduplication: {e}")
            result.append(row_str)

    if orphan_count > 0:
        logger.info(f"🧹 Filtered out {orphan_count} orphan rows with no financial data")

    return result
