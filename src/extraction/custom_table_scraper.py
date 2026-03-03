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
    # company_name: "investment (" matches BCIC-style "Investment (2), (4)..."
    # "company/" matches CCAP-style "Company/Security/Country"
    ("company_name", ["portfolio company", "company name", "issuer", "borrower", "name", "investment (", "company/"]),
    ("investment_type", ["type of investment", "investment type", "instrument type", "security type"]),
    ("industry", ["industry", "sector", "business description"]),
    ("cash_rate", ["total rate", "cash rate", "cash interest", "interest rate", "coupon"]),
    ("pik_rate", ["pik rate", "pik", "payment-in-kind"]),
    ("reference_rate", ["reference rate and spread", "reference rate", "interest", "base rate", "index rate"]),
    ("spread", ["spread", "margin"]),
    ("acquisition_date", ["investment date", "acquisition date", "purchase date", "origination date", "acquisition"]),
    ("maturity_date", ["maturity date", "maturity", "due date"]),
    # principal_amount: "par/shares", "par" for BCIC-style "Par/Shares (++)"
    ("principal_amount", ["stated principal", "principal amount", "principal balance", "par/shares", "par", "principal", "face amount", "face value"]),
    ("amortized_cost", ["amortized cost", "cost basis", "book value"]),
    ("fair_value", ["fair value", "fair market value"]),
    ("percent_of_net_assets", ["% of net assets", "percent of net assets", "net assets %", "% net assets"]),
    ("cost", ["cost"]),
    ("commitment_limit", ["commitment limit", "total commitment", "commitment"]),
    ("undrawn_commitment", ["undrawn commitment", "undrawn", "unfunded"]),
]


def _normalize(s: str) -> str:
    return " ".join(s.lower().split()).strip()


def _normalize_company_name(s: str) -> str:
    """Collapse multiple spaces to one and strip (fixes HTML artifacts like 'ProAir Holdco  LLC')."""
    if not s or not s.strip():
        return ""
    return " ".join(s.split()).strip()


# Trailing footnote refs in company names, e.g. (2), (6), (2)(6). Strip so "Name(2)" = "Name".
_FOOTNOTE_REF_PATTERN = re.compile(r"\s*\(\d+\)(?:\s*\(\d+\))*\s*$")


def _strip_footnote_refs(s: str) -> str:
    """Remove trailing footnote refs like (2), (2)(6) from company name."""
    if not s or not s.strip():
        return s
    return _FOOTNOTE_REF_PATTERN.sub("", s).strip()


# Instrument/position suffixes in company names -> (suffix_pattern, investment_type)
# Order matters: longer/more specific first. Used to strip suffix and infer investment_type.
_INSTRUMENT_SUFFIXES: List[Tuple[str, str]] = [
    # Dash-form (BCIC-style): "Company - Warrant", "Company - Term Loan A", etc. (footnote stripped first)
    (r"\s*-\s*Term\s+Loan\s+C\s*$", "First Lien"),
    (r"\s*-\s*Term\s+Loan\s+B\s*$", "First Lien"),
    (r"\s*-\s*Term\s+Loan\s+A\s*$", "First Lien"),
    (r"\s*-\s*Put\s+Option\s*$", "Put Option"),
    (r"\s*-\s*Warrant\s*$", "Warrants"),
    (r"\s*-\s*Class\s+O\s+Preferred\s*$", "Preferred Equity"),
    (r"\s*-\s*Series\s+M-1\s*$", "Common Equity"),
    (r"\s*-\s*Series\s+[A-Z]\s*$", "Preferred Equity"),  # Series A, Series B, etc.
    (r"\s*-\s*Class\s+[A-Z]\s*$", "Preferred Equity"),     # Class A, Class B (when not Class O Preferred)
    (r"\s*-\s*Common\s+Equity\s*$", "Common Equity"),
    (r"\s*-\s*Preferred\s*$", "Preferred Equity"),
    (r"\s*\(Revolver[^)]*\)\s*$", "Revolver"),  # (Revolver), (Revolver 2022), etc.
    (r"\s*\(Delayed\s+Draw\s+Term\s+Loan\)\s*$", "Delayed Draw"),
    (r"\s*\(Delayed\s+Draw\)\s*$", "Delayed Draw"),
    (r"\s*\(Term\s+Loan\s+B\)\s*$", "First Lien"),
    (r"\s*\(Term\s+Loan\s+C\)\s*$", "First Lien"),
    (r"\s*\(Term\s+Loan\s+A2\)\s*$", "First Lien"),
    (r"\s*\(Term\s+Loan\s+A1\)\s*$", "First Lien"),
    (r"\s*\(Term\s+Loan\s+A\)\s*$", "First Lien"),
    (r"\s*\(Term\s+Loan\)\s*$", "First Lien"),
    (r"\s*\(Super\s+Senior\s+B\)\s*$", "First Lien"),
    (r"\s*\(Super\s+Senior\s+A\)\s*$", "First Lien"),
    (r"\s*\(Senior\s+B\)\s*$", "First Lien"),
    (r"\s*\(Senior\s+A\)\s*$", "First Lien"),
    (r"\s*\(Second\s+Out\)\s*$", "First Lien"),
    (r"\s*\(Warrant\)\s*$", "Warrants"),
    (r"\s*\(Put\s+Option[^)]*\)\s*$", "Derivatives"),
    (r"\s*\(Preferred\)\s*$", "Preferred Equity"),
    (r"\s*\(Class\s+O\s+Preferred\)\s*$", "Preferred Equity"),
    (r"\s*\(Series\s+[A-Z]\)\s*$", "Preferred Equity"),  # e.g. (Series A)
]


def _strip_instrument_suffix_and_infer_type(company_cell: str) -> Tuple[str, str]:
    """
    If company name ends with a known instrument suffix (e.g. " (Revolver)", " - Warrant"),
    return (company_name_with_suffix_stripped, inferred_investment_type).
    Trailing footnote refs like (2), (2)(6) are always stripped so "Name(2)" = "Name".
    """
    normalized = _normalize_company_name(company_cell.replace(",", " "))
    if not normalized:
        return ("", "")
    normalized = _strip_footnote_refs(normalized)
    if not normalized:
        return ("", "")
    inferred_type = ""
    for pattern, inv_type in _INSTRUMENT_SUFFIXES:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            inferred_type = inv_type
            normalized = normalized[: match.start()].strip()
            break
    normalized = _normalize_company_name(normalized)
    return (normalized, inferred_type)


# Section category names that are never company names (BCIC/BCSF/PTMN schedule headers).
# If company cell equals or starts with one of these (optionally followed by " -N%"), skip the row.
_SECTION_CATEGORY_NAMES = (
    "first lien/senior secured debt",
    "second lien/senior secured debt",
    "subordinated debt",
    "collateralized loan obligations",
    "joint ventures",
    "preferred stock and units",
    "common stock and membership units",
    "asset manager affiliates",
    "derivatives",
    "non-controlled affiliated investments",
    "non-control affiliated investments",
    "non-controlled/non-affiliate investments",
    "non-controlled non-affiliate investments",
    "first lien debt",
    "equity",
    "joint venture",
    # BCSF-style industry section headers (exact match)
    "aerospace & defense",
    "automotive",
    "consumer & retail",
    "healthcare",
    "software & technology",
    "financial services",
    "business services",
    "industrials",
    "media & entertainment",
    "food & beverage",
    "transportation & logistics",
    "telecommunications",
    "energy",
    "environmental services",
    "containers & packaging",
    "chemicals & materials",
    # BCSF variants (different wording / order; include comma variant)
    "beverage food & tobacco",
    "beverage, food & tobacco",
    "capital equipment",
    "chemicals plastics & rubber",
    "construction & building",
    "consumer goods: durable",
    "consumer goods: non-durable",
    "consumer goods: wholesale",
    "containers packaging & glass",
    "energy: electricity",
    "energy: oil & gas",
)


def _is_section_category_only(cell: str) -> bool:
    """True if cell is exactly a section category name (e.g. 'Collateralized Loan Obligations')."""
    if not cell or len(cell) < 3:
        return False
    lower = _normalize(cell)
    # Exact match or with trailing " -N.N%"
    for cat in _SECTION_CATEGORY_NAMES:
        if lower == cat or re.match(re.escape(cat) + r"\s*-\s*\d.*%?\s*$", lower):
            return True
    return False


# BCSF (and similar) section header text -> canonical industry label for CSV output
_SECTION_TO_INDUSTRY_LABEL: Dict[str, str] = {
    "beverage food & tobacco": "Food & Beverage",
    "beverage, food & tobacco": "Food & Beverage",
    "capital equipment": "Industrials",
    "chemicals plastics & rubber": "Chemicals & Materials",
    "chemicals & materials": "Chemicals & Materials",
    "construction & building": "Industrials",
    "consumer goods: durable": "Consumer & Retail",
    "consumer goods: non-durable": "Consumer & Retail",
    "consumer goods: wholesale": "Consumer & Retail",
    "containers packaging & glass": "Containers & Packaging",
    "containers & packaging": "Containers & Packaging",
    "energy: electricity": "Energy",
    "energy: oil & gas": "Energy",
    "energy": "Energy",
    "aerospace & defense": "Aerospace & Defense",
    "automotive": "Automotive",
    "consumer & retail": "Consumer & Retail",
    "healthcare": "Healthcare",
    "software & technology": "Software & Technology",
    "financial services": "Financial Services",
    "business services": "Business Services",
    "industrials": "Industrials",
    "media & entertainment": "Media & Entertainment",
    "food & beverage": "Food & Beverage",
    "transportation & logistics": "Transportation & Logistics",
    "telecommunications": "Telecommunications",
    "environmental services": "Environmental Services",
}


def _section_category_to_industry_label(cell: str) -> str:
    """If cell is a known industry section category, return industry label for CSV; else ''.
    
    Returns empty string for schedule type headers (e.g. 'Non-Controlled/Non-Affiliate Investments')
    which are section categories but NOT industry labels.
    """
    if not cell or len(cell) < 3:
        return ""
    lower = _normalize(cell)
    # Strip trailing " -N.N%" if present; collapse commas for lookup (e.g. "Beverage, Food & Tobacco")
    lower = re.sub(r"\s*-\s*\d+\.?\d*\s*%?\s*$", "", lower).strip()
    lower_no_comma = re.sub(r",\s*", " ", lower)
    label = _SECTION_TO_INDUSTRY_LABEL.get(lower, "") or _SECTION_TO_INDUSTRY_LABEL.get(lower_no_comma, "")
    if label:
        return label
    # Schedule type headers should NOT become industry labels
    schedule_patterns = (
        "non-controlled", "non-affiliate", "affiliated", "controlled",
        "first lien", "second lien", "senior secured", "subordinated",
        "unsecured", "collateralized loan", "joint venture", "preferred stock",
        "common stock", "membership", "asset manager", "derivatives", "equity",
    )
    if any(pat in lower for pat in schedule_patterns):
        return ""
    # Unknown industry section: use title-case (e.g. new BCSF-specific industry names)
    return cell.strip().title() if cell.strip() else ""


def _is_fragment_company_name(company_clean: str) -> bool:
    """
    True if company name looks like a table-truncation fragment (e.g. "hner Inc." from "H.W. Lochner Inc.").
    Skip when it's a single word of 1-4 letters followed by " Inc." or " LLC".
    """
    if not company_clean or len(company_clean) > 15:
        return False
    s = company_clean.strip()
    # One short word + " Inc." or " LLC" (with optional period)
    if re.match(r"^[A-Za-z]{1,4}\s+(?:Inc\.?|LLC\.?)\s*$", s):
        return True
    return False


def _is_section_summary_row(cell: str) -> bool:
    """
    True if cell is a BCIC-style summary row (e.g. "Investments in Non-Control... -194.2%"
    or "Second Lien/Senior Secured Debt-2.2%" or "Joint Ventures-23.0%") that should be skipped.
    NOTE: Does NOT match section category names (like "Aerospace & Defense") without percentage;
    those are handled by _is_section_category_only in rows_to_csv to carry forward industry.
    """
    if not cell or len(cell) < 5:
        return False
    lower = _normalize(cell)
    # Must end with " - N%", "-N.N%", or " -N.N%" (dash with optional space, then number, then %)
    has_percentage = bool(re.search(r"-\s*\d+\.?\d*\s*%?\s*$", lower))
    if has_percentage:
        # Has percentage suffix - check if it's a section summary line
        if lower.startswith("investments in ") or "non-control" in lower or "non-affiliate" in lower:
            return True
        if re.match(r"^(first lien|second lien|senior secured|subordinated|unsecured|collateralized loan|joint ventures|preferred stock and units|common stock and membership|asset manager affiliates|derivatives)\s*", lower):
            return True
    # BCSF-style section total row (e.g. "Aerospace & Defense Total")
    if re.search(r"\s+total\s*$", lower):
        return True
    return False


def _parse_section_header(cell: str) -> Optional[str]:
    """
    If cell looks like a section header (e.g. "Senior Secured First Lien Debt - 178.5%"),
    return the standard investment_type; else None.
    Also treats BCIC-style " -158.7%" (no space after dash) as section header.
    """
    if not cell or len(cell) < 5:
        return None
    lower = _normalize(cell)
    # Must look like "Category - N.N%" or "Category-N.N%" (dash then number then %)
    if not re.search(r"-\s*\d+\.?\d*\s*%", lower):
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

    # Position-based fallback for BCIC-style tables: after collapse we often have 9 columns
    # with Principal/Par, Cost, Fair Value at indices 6, 7, 8 (company=0, industry=1, rate=2, ref=3, floor=4, maturity=5)
    num_cols = len(headers)
    if num_cols >= 9 and "company_name" in used_keys:
        _apply_pcfv_fallback(col_map, used_keys, num_cols)

    return col_map


def _apply_pcfv_fallback(col_map: Dict[int, str], used_keys: set, num_cols: int) -> None:
    """
    When Principal/Cost/Fair Value are not mapped (e.g. BCIC colspan/merge leaves headers
    empty or misaligned), assign by position: 6=principal_amount, 7=cost/amortized_cost, 8=fair_value.
    Only assigns indices that are not already mapped.
    """
    pcfv = [
        (6, "principal_amount"),
        (7, "amortized_cost"),  # same column often used for cost; cost can also map to 7
        (8, "fair_value"),
    ]
    for col_idx, key in pcfv:
        if col_idx >= num_cols:
            continue
        if key in used_keys:
            continue
        if col_idx in col_map:
            continue
        col_map[col_idx] = key
        used_keys.add(key)
    # If we mapped amortized_cost to 7 but not cost, use 7 for cost too (one "Cost" column in table)
    if 7 in col_map and col_map[7] == "amortized_cost" and "cost" not in used_keys:
        # Don't overwrite col_map[7]; instead allow cost to read from same column via key_to_col_idx
        # We can't have two keys for one column in col_map. So leave cost unmapped unless we have
        # a separate cost column. Rows will have amortized_cost filled and cost empty, which is OK.
        pass


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
    # Skip BCIC-style section summary rows (e.g. "Investments in Non-Control... -194.2%")
    if _is_section_summary_row(cell):
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
    """If first row has very few non-empty headers, use second row to fill in (multi-row header).
    When a cell in row 0 is very long (title-like, > 50 chars) but row 1 has a shorter actual
    column header, prefer the row 1 value (e.g. CCAP: title row vs 'Company/Security/Country')."""
    if len(rows) < 2 or not rows[0] or not rows[1]:
        return rows
    h0, h1 = rows[0], rows[1]
    max_len = max(len(h0), len(h1))
    merged = []
    for i in range(max_len):
        a = (h0[i] if i < len(h0) else "").strip()
        b = (h1[i] if i < len(h1) else "").strip()
        # When row-0 cell is a very long title-like string and row-1 has a shorter column header,
        # prefer the row-1 value (handles tables where row 0 is a spanning title cell).
        if a and b and len(a) > 50 and len(b) <= 50:
            merged.append(b)
        else:
            merged.append(a or b or "")
    return [merged] + rows[2:]


def rows_to_csv(rows: List[List[str]], initial_section_industry: str = "") -> Tuple[str, str]:
    """
    Map parsed table rows (header + data) to the standard 16-column CSV.
    Uses header names to identify columns; infers investment_type from section
    headers when present (e.g. "Senior Secured First Lien Debt - 178.5%").
    Skips section header rows and other non-data rows.
    Carries industry from section rows into subsequent data rows (BCSF-style).
    When processing multiple tables, pass the returned final_section_industry
    as initial_section_industry for the next table.

    Args:
        rows: First row is header; rest are data rows.
        initial_section_industry: Industry to use for rows until a section row is seen.

    Returns:
        (CSV string with header row + data rows, final_section_industry for next table).
    """
    if not rows or len(rows) < 2:
        return (_header_only_csv(), initial_section_industry)

    # Optional: merge two-row headers (row 0 sparse -> fill from row 1)
    non_empty_headers = sum(1 for c in rows[0] if (c or "").strip())
    if non_empty_headers <= 3 and len(rows) >= 2:
        rows = _merge_headers_if_sparse(rows)

    headers = rows[0]
    col_map = _build_column_map(headers)
    key_to_col_idx = {v: k for k, v in col_map.items()}

    out_lines = [",".join(OUTPUT_COLUMNS)]
    current_section_type = ""
    current_section_industry = initial_section_industry
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
        # BCIC-style section summary row (e.g. "Second Lien/Senior Secured Debt-2.2%"): skip
        if _is_section_summary_row(company_cell):
            continue

        # Strip instrument suffixes from company name and infer investment_type (e.g. "(Revolver)" -> Revolver)
        company_clean, inferred_investment_type = _strip_instrument_suffix_and_infer_type(company_cell)
        # Skip if cleaned name is just a section category (e.g. "Collateralized Loan Obligations");
        # carry industry from this section for subsequent data rows (BCSF-style tables)
        if _is_section_category_only(company_clean):
            # Only update industry for INDUSTRY section headers, not schedule type headers
            # (e.g. "Aerospace & Defense" updates industry, "Non-Controlled/Non-Affiliate" does not)
            industry_label = _section_category_to_industry_label(company_cell)
            if industry_label:
                current_section_industry = industry_label
            continue
        # Skip table-truncation fragments (e.g. "hner Inc." from "H.W. Lochner Inc.")
        if _is_fragment_company_name(company_clean):
            continue

        # Hierarchical table: empty company but has type/maturity/amounts -> continuation row
        is_continuation = not company_cell and _is_continuation_row(row, col_map)
        if is_continuation:
            if not last_company_name:
                continue  # No company to attach to yet
            company_cell = last_company_name
            company_clean, inferred_investment_type = _strip_instrument_suffix_and_infer_type(company_cell)
        elif not _is_data_row(row, col_map):
            continue
        # else: company_clean and inferred_investment_type already set above

        values = [""] * len(OUTPUT_COLUMNS)
        for out_idx, key in enumerate(OUTPUT_COLUMNS):
            col_idx = key_to_col_idx.get(key)
            # Single "Cost" column often used for amortized cost; use same column for cost when unmapped
            if key == "cost" and col_idx is None:
                col_idx = key_to_col_idx.get("amortized_cost")
            raw = ""
            if col_idx is not None and col_idx < len(row):
                raw = (row[col_idx] or "").strip()

            if key == "company_name":
                values[out_idx] = company_clean
            elif key in ("principal_amount", "amortized_cost", "fair_value", "cost",
                       "commitment_limit", "undrawn_commitment"):
                values[out_idx] = _clean_number(raw)
            elif key == "percent_of_net_assets":
                values[out_idx] = _clean_number(raw)
            elif key in ("acquisition_date", "maturity_date"):
                values[out_idx] = _clean_date(raw)
            elif key == "investment_type":
                # Prefer type inferred from company name suffix (e.g. " (Revolver)" -> Revolver)
                # Default empty to "First Lien" (BCIC/BDC rows are usually debt when type is missing)
                raw_type = (inferred_investment_type or raw or current_section_type or "First Lien").replace(",", " ").strip()
                # Normalize common table values to standard schema
                lower_type = raw_type.lower()
                if lower_type == "secured debt" or ("secured debt" in lower_type and "second" not in lower_type):
                    raw_type = "First Lien"
                elif "common stock" in lower_type:
                    raw_type = "Common Equity"
                # BCSF-style: "First Lien Senior Secured Loan - Revolver" -> Revolver, etc.
                elif " - revolver" in lower_type or "senior secured loan - revolver" in lower_type:
                    raw_type = "Revolver"
                elif " - delayed draw" in lower_type or "senior secured loan - delayed draw" in lower_type:
                    raw_type = "Delayed Draw"
                elif "equity interest" in lower_type:
                    raw_type = "Other Equity"
                elif "first lien senior secured loan" in lower_type:
                    raw_type = "First Lien"
                elif "second lien senior secured loan" in lower_type:
                    raw_type = "Second Lien"
                elif "subordinated debt" in lower_type:
                    raw_type = "Subordinated Debt"
                values[out_idx] = raw_type
            elif key == "industry":
                # Use table industry column if present; else carry from section header (BCSF)
                if raw.replace(",", " ").strip():
                    values[out_idx] = raw.replace(",", " ").strip()
                elif current_section_industry:
                    values[out_idx] = current_section_industry
                else:
                    values[out_idx] = ""
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
        return (_header_only_csv(), current_section_industry)
    return ("\n".join(out_lines), current_section_industry)


def _header_only_csv() -> str:
    return ",".join(OUTPUT_COLUMNS)
