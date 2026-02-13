"""
Custom (non-LLM) table scraper for schedule of investments.

Takes the same parsed table rows we would send to the LLM and maps columns
by header name to the standard 16-column CSV schema. Infers investment_type
from section headers (e.g. "Senior Secured First Lien Debt - 178.5%") when
the table has no type column. Used for CION and similar BDC schedules.
"""

import re
import logging
from typing import List, Optional, Dict, Tuple

logger = logging.getLogger(__name__)

# Standard 16-column output order (same as LLM prompt)
OUTPUT_COLUMNS = [
    "company_name", "investment_type", "industry", "cash_rate", "pik_rate",
    "reference_rate", "spread", "acquisition_date", "maturity_date",
    "principal_amount", "amortized_cost", "fair_value", "percent_of_net_assets",
    "cost", "commitment_limit", "undrawn_commitment",
]

# Section header patterns (cell text) -> standard investment_type value
# Order matters: more specific first
SECTION_TO_INVESTMENT_TYPE = [
    (r"senior\s+secured\s+first\s+lien\s+debt", "First Lien"),
    (r"first\s+lien\s+(?:senior\s+)?secured", "First Lien"),
    (r"senior\s+secured\s+second\s+lien", "Second Lien"),
    (r"second\s+lien", "Second Lien"),
    (r"subordinated\s+debt", "Subordinated Debt"),
    (r"unsecured\s+debt", "Unsecured Debt"),
    (r"collateralized\s+securities\s+and\s+structured\s+products", "Structured Note"),
    (r"structured\s+products?", "Structured Note"),
    (r"clo\s+", "Structured Note"),
    (r"short\s+term\s+investments", "Money Market Fund"),
    (r"money\s+market", "Money Market Fund"),
    (r"preferred\s+equity", "Preferred Equity"),
    (r"common\s+equity", "Common Equity"),
    (r"partnership\s+interest", "Partnership Interest"),
    (r"revolver", "Revolver"),
    (r"delayed\s+draw", "Delayed Draw"),
    (r"warrants?", "Warrants"),
    (r"\bequity\s*$", "Common Equity"),  # "Equity" section
]

# Header substring patterns (lowercase) -> output column key
# More specific patterns first so we don't match "rate" to both cash_rate and reference_rate
HEADER_PATTERNS: List[Tuple[str, List[str]]] = [
    ("company_name", ["portfolio company", "company name", "issuer", "borrower", "name"]),
    ("investment_type", ["type of investment", "investment type", "instrument type", "security type"]),
    ("industry", ["industry", "sector", "business description"]),
    ("cash_rate", ["total rate", "cash rate", "cash interest", "interest rate", "coupon"]),
    ("pik_rate", ["pik rate", "pik", "payment-in-kind"]),
    ("reference_rate", ["reference rate and spread", "reference rate", "interest", "base rate", "index rate"]),
    ("spread", ["spread", "margin"]),
    ("acquisition_date", ["investment date", "acquisition date", "purchase date", "origination date", "acquisition"]),
    ("maturity_date", ["maturity date", "maturity", "due date"]),
    ("principal_amount", ["stated principal", "principal amount", "principal balance", "principal", "face amount", "face value"]),
    ("amortized_cost", ["amortized cost", "cost basis", "book value"]),
    ("fair_value", ["fair value", "fair market value"]),
    ("percent_of_net_assets", ["% of net assets", "percent of net assets", "net assets %", "% net assets"]),
    ("cost", ["cost"]),
    ("commitment_limit", ["commitment limit", "total commitment", "commitment"]),
    ("undrawn_commitment", ["undrawn commitment", "undrawn", "unfunded"]),
]


def _normalize(s: str) -> str:
    return " ".join(s.lower().split()).strip()


def _parse_section_header(cell: str) -> Optional[str]:
    """
    If cell looks like a section header (e.g. "Senior Secured First Lien Debt - 178.5%"),
    return the standard investment_type; else None.
    """
    if not cell or len(cell) < 5:
        return None
    lower = _normalize(cell)
    # Must look like "Category - N.N%" or "Category - NN%"
    if " - " not in lower or "%" not in lower:
        return None
    # Don't treat company names with " - " as section headers
    if any(s in lower for s in ("llc", "inc.", "corp", "ltd", "lp", "holdings", "co.", "company")):
        return None
    for pattern, inv_type in SECTION_TO_INVESTMENT_TYPE:
        if re.search(pattern, lower):
            return inv_type
    return None


def _build_column_map(headers: List[str]) -> Dict[int, str]:
    """Map table column index -> output column key. Only first 16 keys are used."""
    col_map: Dict[int, str] = {}
    used_keys = set()

    for col_idx, raw_header in enumerate(headers):
        if col_idx >= 50:  # Sanity limit
            break
        h = _normalize(raw_header)
        if not h:
            continue
        for key, patterns in HEADER_PATTERNS:
            if key in used_keys:
                continue
            for p in patterns:
                if p in h or h in p:
                    col_map[col_idx] = key
                    used_keys.add(key)
                    break
            if col_map.get(col_idx):
                break

    return col_map


def _parse_reference_and_spread(raw: str) -> Tuple[str, str]:
    """
    Parse combined cell like 'SF+ 7.40%', 'SOFR 6.25%', 'S+1000, 1.00% SOFR Floor' into (reference_rate, spread).
    Returns (ref, spread); either can be ''.
    """
    if not raw or not raw.strip():
        return ("", "")
    s = raw.strip()
    ref, spread = "", ""
    # Normalize reference rate abbreviations first
    upper = s.upper()
    if "SF+" in upper or "SOFR" in upper or "S+" in upper or " SF " in upper:
        ref = "SOFR"
    elif "L+" in upper or "LIBOR" in upper or " L " in upper:
        ref = "LIBOR"
    elif "PRIME" in upper or "P+" in upper:
        ref = "Prime"
    elif "EURIBOR" in upper:
        ref = "Euribor"
    elif "SONIA" in upper:
        ref = "SONIA"
    # Spread: X+NNNN often means NNNN bps (e.g. S+1000 = SOFR + 1000 bps = 10%); or N.N% in text
    bps_m = re.search(r"[SLP]\+\s*(\d{2,4})\b", upper)
    if bps_m:
        bps = int(bps_m.group(1))
        if bps >= 10 and bps <= 5000:  # likely bps
            spread = f"{bps / 100:.2f}%"
    if not spread:
        pct_m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if pct_m:
            spread = pct_m.group(1) + "%"
    if not ref and s:
        ref = s.replace(",", " ").strip()
    return (ref, spread)


def _clean_number(val: str) -> str:
    """Remove $ , % and return digits/decimal only for numeric fields."""
    if not val or not val.strip():
        return ""
    s = val.strip().replace(",", "").replace("$", "").replace("%", "").strip()
    if s in ("-", "") or re.match(r"^[\s\-]+$", s):
        return ""
    # Keep digits, decimal, minus
    s = re.sub(r"[^\d.\-]", "", s)
    return s


def _clean_date(val: str) -> str:
    """Try to normalize to YYYY-MM-DD."""
    if not val or not val.strip():
        return ""
    s = val.strip()
    # Already YYYY-MM-DD
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    # MM/DD/YYYY or M/D/YY
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        mo, d, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 2:
            y = "20" + y
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    # "Month DD, YYYY"
    months = "jan feb mar apr may jun jul aug sep oct nov dec"
    for i, mon in enumerate(months.split(), 1):
        if mon in s.lower():
            y = re.search(r"20\d{2}|\d{2}(?=\D*$)", s)
            if y:
                year = y.group(0)
                if len(year) == 2:
                    year = "20" + year
                day = re.search(r"\d{1,2}(?=\s*,|\s+20)", s)
                d = day.group(0).zfill(2) if day else "01"
                return f"{year}-{i:02d}-{d}"
            break
    return s


def _is_data_row(row: List[str], col_map: Dict[int, str]) -> bool:
    """Heuristic: row is a data row if it has a company-like name (LLC, Inc., etc.)."""
    company_idx = next((i for i, k in col_map.items() if k == "company_name"), None)
    if company_idx is None or company_idx >= len(row):
        return False
    cell = (row[company_idx] or "").strip()
    if not cell or len(cell) < 2:
        return False
    # Skip section header rows (e.g. "Senior Secured First Lien Debt - 178.5%")
    if _parse_section_header(cell) is not None:
        return False
    # Skip header/total/summary rows
    lower = cell.lower()
    if lower in ("total", "totals", "aggregate", "portfolio company", "company", ""):
        return False
    if lower.startswith("total ") or lower.startswith("total\t"):
        return False
    if "total investments" in lower or "liabilities in excess" in lower or "net assets" in lower and "100" in lower:
        return False
    if re.match(r"^(equity|senior secured|subordinated|unsecured)\s*[-–]\s*\d", lower):
        return False
    # Section header without percentage (e.g. "Senior Secured Second Lien Debt" as standalone row)
    if re.match(r"^senior secured (first|second) lien debt\s*$", lower) or re.match(r"^subordinated debt\s*$", lower) or re.match(r"^unsecured debt\s*$", lower):
        return False
    if re.match(r"^[\d\s.,$%\-]+$", cell):
        return False
    # Likely company if has common suffixes or looks like a name
    company_indicators = ["llc", "inc.", "corp", "ltd", "lp", "llp", "holdings", "co.", "company"]
    return any(ind in lower for ind in company_indicators) or (
        cell[0].isupper() and not cell.isdigit()
    )


def _is_continuation_row(row: List[str], col_map: Dict[int, str]) -> bool:
    """
    Row has empty company cell but has investment detail (type, maturity, or amounts).
    Used for hierarchical tables where company name appears only on the first row of each block.
    """
    company_idx = next((i for i, k in col_map.items() if k == "company_name"), None)
    if company_idx is None or company_idx >= len(row):
        return False
    company_cell = (row[company_idx] or "").strip()
    if company_cell:
        return False
    # Must have investment type or maturity (so we don't treat subtotal-only rows as continuation)
    def has_val(key: str) -> bool:
        idx = next((i for i, k in col_map.items() if k == key), None)
        if idx is None or idx >= len(row):
            return False
        return bool((row[idx] or "").strip())
    if has_val("investment_type"):
        return True
    if has_val("maturity_date"):
        return True
    # Or clear principal/cost/fair value (for tables that don't label type on every line)
    if has_val("principal_amount") or has_val("fair_value"):
        return True
    return False


def _merge_headers_if_sparse(rows: List[List[str]]) -> List[List[str]]:
    """If first row has very few non-empty headers, use second row to fill in (multi-row header)."""
    if len(rows) < 2 or not rows[0] or not rows[1]:
        return rows
    h0, h1 = rows[0], rows[1]
    max_len = max(len(h0), len(h1))
    merged = []
    for i in range(max_len):
        a = (h0[i] if i < len(h0) else "").strip()
        b = (h1[i] if i < len(h1) else "").strip()
        merged.append(a or b or "")
    return [merged] + rows[2:]


def rows_to_csv(rows: List[List[str]]) -> str:
    """
    Map parsed table rows (header + data) to the standard 16-column CSV.
    Uses header names to identify columns; infers investment_type from section
    headers when present (e.g. "Senior Secured First Lien Debt - 178.5%").
    Skips section header rows and other non-data rows.

    Args:
        rows: First row is header; rest are data rows.

    Returns:
        CSV string with header row + data rows (same schema as LLM output).
    """
    if not rows or len(rows) < 2:
        return _header_only_csv()

    # Optional: merge two-row headers (row 0 sparse -> fill from row 1)
    non_empty_headers = sum(1 for c in rows[0] if (c or "").strip())
    if non_empty_headers <= 3 and len(rows) >= 2:
        rows = _merge_headers_if_sparse(rows)

    headers = rows[0]
    col_map = _build_column_map(headers)
    key_to_col_idx = {v: k for k, v in col_map.items()}

    out_lines = [",".join(OUTPUT_COLUMNS)]
    current_section_type = ""
    last_company_name = ""

    for row in rows[1:]:
        # Pad row to max index we care about
        max_idx = max(col_map.keys(), default=-1)
        while len(row) <= max_idx:
            row.append("")

        company_idx = next((i for i, k in col_map.items() if k == "company_name"), None)
        company_cell = (row[company_idx] or "").strip() if company_idx is not None and company_idx < len(row) else ""

        # Section header row: update context and skip
        section_type = _parse_section_header(company_cell)
        if section_type is not None:
            current_section_type = section_type
            continue

        # Hierarchical table: empty company but has type/maturity/amounts -> continuation row
        is_continuation = not company_cell and _is_continuation_row(row, col_map)
        if is_continuation:
            if not last_company_name:
                continue  # No company to attach to yet
            # Use last company name for this row
            company_cell = last_company_name
        elif not _is_data_row(row, col_map):
            continue

        values = [""] * len(OUTPUT_COLUMNS)
        for out_idx, key in enumerate(OUTPUT_COLUMNS):
            col_idx = key_to_col_idx.get(key)
            raw = ""
            if col_idx is not None and col_idx < len(row):
                raw = (row[col_idx] or "").strip()

            if key == "company_name":
                values[out_idx] = company_cell.replace(",", " ").strip()
            elif key in ("principal_amount", "amortized_cost", "fair_value", "cost",
                       "commitment_limit", "undrawn_commitment"):
                values[out_idx] = _clean_number(raw)
            elif key == "percent_of_net_assets":
                values[out_idx] = _clean_number(raw)
            elif key in ("acquisition_date", "maturity_date"):
                values[out_idx] = _clean_date(raw)
            elif key == "investment_type":
                # Use section context when table has no type column or cell is empty
                raw_type = (raw or current_section_type or "").replace(",", " ").strip()
                # Normalize common table values to standard schema
                lower_type = raw_type.lower()
                if lower_type == "secured debt" or ("secured debt" in lower_type and "second" not in lower_type):
                    raw_type = "First Lien"
                elif "common stock" in lower_type:
                    raw_type = "Common Equity"
                values[out_idx] = raw_type
            elif key == "reference_rate":
                # May be combined "SF+ 7.40%" or "SOFR 6.25%" -> split into reference_rate + spread
                ref, parsed_spread = _parse_reference_and_spread(raw)
                values[out_idx] = ref.replace(",", " ").strip()
                # If we parsed a spread and there's no separate spread column (or it's empty), use it
                spread_idx = next((i for i, k in enumerate(OUTPUT_COLUMNS) if k == "spread"), 6)
                if parsed_spread and (not values[spread_idx] or values[spread_idx] == ""):
                    values[spread_idx] = parsed_spread
            elif key in ("cash_rate", "pik_rate"):
                # Keep % for display (e.g. "8.50%")
                values[out_idx] = raw.replace(",", " ").strip()
            elif key == "spread":
                # Keep %; don't overwrite if we already set from reference_rate parsing
                if raw.replace(",", " ").strip():
                    values[out_idx] = raw.replace(",", " ").strip()
            else:
                values[out_idx] = raw.replace(",", " ").strip()

        if not is_continuation and values[0]:
            last_company_name = values[0]

        out_lines.append(",".join(values))

    if len(out_lines) <= 1:
        return _header_only_csv()
    return "\n".join(out_lines)


def _header_only_csv() -> str:
    return ",".join(OUTPUT_COLUMNS)
