"""
Data filtering logic for cleaning extracted investment data.
"""

import csv
import logging
from io import StringIO
from typing import List

logger = logging.getLogger(__name__)


def filter_equity_types(csv_rows: List[str], debt_only: bool = False) -> List[str]:
    """
    Filter out equity investment types if debt_only is True.
    
    Args:
        csv_rows: List of CSV row strings (first row is header)
        debt_only: If True, exclude equity types (Common Equity, Preferred Equity, Warrant, etc.)
    
    Returns:
        Filtered list of CSV row strings
    """
    if not debt_only or not csv_rows:
        return csv_rows
    
    # Parse header
    header_row = csv_rows[0]
    reader = csv.reader(StringIO(header_row))
    header = list(reader)[0]
    
    # Find investment_type column index
    investment_type_idx = None
    for i, col in enumerate(header):
        if col.lower() in ['investment_type', 'investment type', 'type']:
            investment_type_idx = i
            break
    
    if investment_type_idx is None:
        logger.warning("Could not find investment_type column for equity filtering, returning all rows")
        return csv_rows
    
    # Equity types to exclude
    equity_keywords = [
        'common equity',
        'preferred equity', 
        'warrant',
        'equity interest',
        'membership interest',
        'common stock',
        'preferred stock',
    ]
    
    result = [header_row]  # Keep header
    filtered_count = 0
    
    for row_str in csv_rows[1:]:
        try:
            reader = csv.reader(StringIO(row_str))
            cols = list(reader)[0]
            
            if len(cols) > investment_type_idx:
                investment_type = cols[investment_type_idx].lower().strip()
                
                # Check if this is an equity type
                is_equity = any(keyword in investment_type for keyword in equity_keywords)
                
                if not is_equity:
                    result.append(row_str)
                else:
                    filtered_count += 1
                    logger.debug(f"Filtered out equity investment: {investment_type}")
            else:
                # Row has fewer columns than expected, keep it to avoid errors
                result.append(row_str)
        except Exception as e:
            logger.warning(f"Error parsing row for equity filtering: {e}, keeping row")
            result.append(row_str)
    
    if filtered_count > 0:
        logger.info(f"Filtered out {filtered_count} equity investments (debt-only mode)")
    
    return result


def remove_empty_header_rows(csv_rows: List[str]) -> List[str]:
    """
    Remove rows that have a company name but no financial data, when
    the same company name appears on other rows that DO have data.
    These are SEC table sub-headers (company name before listing tranches).

    Args:
        csv_rows: List of CSV row strings (first row is header)

    Returns:
        Filtered list with empty header rows removed
    """
    if len(csv_rows) < 2:
        return csv_rows

    # Parse all rows
    parsed = []
    for row_str in csv_rows[1:]:  # Skip header
        try:
            reader = csv.reader(StringIO(row_str))
            cols = list(reader)[0]
            if len(cols) >= 16:
                parsed.append((row_str, cols))
        except Exception:
            parsed.append((row_str, None))

    # Identify rows with no financial data
    # Financial columns: principal(9), amortized_cost(10), fair_value(11), cost(13)
    empty_rows = []
    populated_names = set()

    for row_str, cols in parsed:
        if cols is None:
            continue
        has_financials = any(
            cols[i].strip() for i in [9, 10, 11, 13] if i < len(cols)
        )
        name = cols[0].strip().lower()
        if has_financials and name:
            populated_names.add(name)

    # Filter: remove rows with no financials whose name appears on populated rows
    result = [csv_rows[0]]  # Keep header
    removed = 0
    for row_str, cols in parsed:
        if cols is None:
            result.append(row_str)
            continue
        has_financials = any(
            cols[i].strip() for i in [9, 10, 11, 13] if i < len(cols)
        )
        name = cols[0].strip().lower()
        if not has_financials and name in populated_names:
            removed += 1
            logger.debug(f"Removing empty header row for: {name}")
        else:
            result.append(row_str)

    if removed > 0:
        logger.info(f"Removed {removed} empty header rows")

    return result


def filter_llm_artifact_rows(csv_rows: List[str]) -> List[str]:
    """
    Remove rows that are LLM commentary, calculation notes, or malformed output
    (e.g. "* *Wait", "Undrawn = 14978 - 14075", "Principal 14978", backtick/pipe junk).
    """
    if len(csv_rows) < 2:
        return csv_rows

    result = [csv_rows[0]]  # Keep header
    removed = 0

    for row_str in csv_rows[1:]:
        try:
            reader = csv.reader(StringIO(row_str))
            cols = list(reader)[0]
            if not cols:
                result.append(row_str)
                continue
            first = (cols[0] or "").strip()
            if not first:
                result.append(row_str)
                continue

            # LLM "wait" / meta tokens
            if first.startswith("* *") or first.lower().startswith("* wait") or first == "* *Wait":
                removed += 1
                continue
            # Calculation notes (Undrawn = ..., Principal amount as standalone)
            if "undrawn =" in first.lower() or first.lower().startswith("undrawn ="):
                removed += 1
                continue
            if first.lower().startswith("principal ") and first[9:].strip().replace(",", "").replace(".", "").isdigit():
                removed += 1
                continue
            # Malformed: backticks or pipe in company name (LLM formatting artifacts)
            if "`" in first or " | " in first:
                removed += 1
                continue
            # Row that is mostly commas/empty with a junk first cell
            non_empty = sum(1 for c in cols if (c or "").strip())
            if non_empty <= 2 and (first.startswith("*") or first.startswith("`")):
                removed += 1
                continue

            result.append(row_str)
        except Exception:
            result.append(row_str)

    if removed > 0:
        logger.info(f"Filtered out {removed} LLM artifact/junk rows")

    return result
