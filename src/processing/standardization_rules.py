#!/usr/bin/env python3
"""
Standardization rules and functions for data cleaning.
Contains all standardization logic for industries, investment types, rates, etc.
"""
import re
from collections import defaultdict

# ===== INDUSTRY STANDARDIZATION =====

STANDARD_INDUSTRIES = {
    'Software': ['software', 'saas', 'application', 'it services', 'internet software', 'cloud computing', 'software and services', 'software & services', 'information technology', 'enterprise software', 'software development', 'it consulting'],
    'Healthcare Services': ['healthcare services', 'health care services', 'health care providers', 'healthcare providers', 'hospitals', 'medical services', 'healthcare facilities', 'managed care', 'dental services', 'veterinary', 'animal health', 'healthcare provider', 'medical staffing'],
    'Pharmaceuticals & Biotechnology': ['pharmaceuticals', 'pharmaceutical', 'biotech', 'biotechnology', 'life sciences', 'drug', 'biopharmaceutical', 'pharma'],
    'Business Services': ['business services', 'professional services', 'consulting services', 'administrative services', 'staffing', 'human resources', 'hr services', 'business process', 'bpo', 'outsourcing'],
    'Financial Services': ['financial services', 'insurance', 'banking', 'asset management', 'investment', 'lending', 'fintech', 'payment processing', 'payments', 'financial technology'],
    'Manufacturing': ['manufacturing', 'industrial manufacturing', 'production', 'fabrication', 'assembly'],
    'Industrial Services': ['industrial services', 'facilities services', 'building services', 'maintenance services', 'industrial equipment', 'commercial services'],
    'Media & Entertainment': ['media', 'entertainment', 'broadcasting', 'digital media', 'content', 'advertising', 'marketing services', 'marketing', 'publishing'],
    'Telecommunications': ['telecommunications', 'telecom', 'wireless', 'communications', 'networking'],
    'Retail': ['retail', 'consumer retail', 'specialty retail', 'e-commerce', 'online retail', 'distribution'],
    'Food & Beverage': ['food', 'beverage', 'restaurant', 'food services', 'food service', 'hospitality', 'hotel', 'lodging'],
    'Energy': ['energy', 'oil and gas', 'oil & gas', 'renewable energy', 'utilities', 'power generation'],
    'Transportation': ['transportation', 'logistics', 'freight', 'shipping', 'trucking', 'warehousing'],
    'Real Estate': ['real estate', 'property', 'commercial real estate', 'reit'],
    'Construction': ['construction', 'building construction', 'infrastructure', 'engineering & construction'],
    'Education': ['education', 'educational services', 'training', 'schools', 'learning'],
    'Automotive': ['automotive', 'auto', 'vehicle', 'car'],
    'Aerospace & Defense': ['aerospace', 'defense', 'aviation'],
    'Consumer Products': ['consumer products', 'consumer goods', 'household products'],
    'Chemicals': ['chemicals', 'specialty chemicals', 'chemical'],
    'Environmental Services': ['environmental', 'waste management', 'recycling'],
    'Mining': ['mining', 'minerals', 'metals'],
    'Agriculture': ['agriculture', 'farming', 'agribusiness'],
    'Packaging': ['packaging', 'containers'],
    'Technology Hardware': ['hardware', 'electronics', 'semiconductor', 'components'],
    'Other': []  # Catch-all
}

def create_mapping_dict():
    """Create a flattened keyword -> standard category mapping."""
    mapping = {}
    for standard_name, keywords in STANDARD_INDUSTRIES.items():
        for keyword in keywords:
            mapping[keyword.lower()] = standard_name
    return mapping

def standardize_industry(industry: str, mapping: dict) -> str:
    """Standardize industry name using keyword matching."""
    if not industry or industry.strip() == '':
        return ''
    
    industry_lower = industry.lower().strip()
    
    # Direct mapping
    if industry_lower in mapping:
        return mapping[industry_lower]
    
    # Keyword search
    for keyword, standard_name in mapping.items():
        if keyword in industry_lower:
            return standard_name
    
    return 'Other'

# ===== INVESTMENT TYPE STANDARDIZATION =====

INVESTMENT_TYPE_MAPPING = {}  # We'll use priority matching instead

def create_investment_type_mapping():
    """Returns empty dict - we use priority matching."""
    return INVESTMENT_TYPE_MAPPING

def standardize_investment_type(inv_type: str, mapping: dict = None) -> str:
    """Standardize investment type using priority-based matching."""
    if not inv_type or inv_type.strip() == '':
        return ''
    
    inv_type_lower = inv_type.lower().strip()
    
    # Priority matching (check specific patterns first)
    if 'delayed draw' in inv_type_lower or 'ddtl' in inv_type_lower:
        return 'Delayed Draw'
    if 'revolv' in inv_type_lower or 'revolver' in inv_type_lower:
        return 'Revolver'
    if 'second lien' in inv_type_lower or '2nd lien' in inv_type_lower:
        return 'Second Lien'
    if 'first lien' in inv_type_lower or '1st lien' in inv_type_lower or 'senior secured' in inv_type_lower:
        return 'First Lien'
    if 'subordinated' in inv_type_lower:
        return 'Subordinated Debt'
    if 'warrant' in inv_type_lower:
        return 'Warrants'
    if 'preferred' in inv_type_lower and any(x in inv_type_lower for x in ['equity', 'stock', 'unit', 'share']):
        return 'Preferred Equity'
    if 'common' in inv_type_lower and any(x in inv_type_lower for x in ['equity', 'stock', 'unit', 'share']):
        return 'Common Equity'
    if 'partnership' in inv_type_lower or 'member interest' in inv_type_lower:
        return 'Partnership Interest'
    if any(x in inv_type_lower for x in ['equity', 'stock', 'share']):
        return 'Other Equity'
    if any(x in inv_type_lower for x in ['debt', 'loan', 'note', 'bond']):
        return 'Other Debt'
    
    return 'Other'

# ===== REFERENCE RATE STANDARDIZATION =====

REFERENCE_RATE_MAPPING = {
    'sofr': 'SOFR',
    'sf': 'SOFR',
    's': 'SOFR',
    'sofr (q)': 'SOFR (Q)',
    'sofr (m)': 'SOFR (M)',
    'sofr (s)': 'SOFR (S)',
    'sofr (a)': 'SOFR (A)',
    'libor': 'LIBOR',
    'l': 'LIBOR',
    'libor (q)': 'LIBOR (Q)',
    'libor (m)': 'LIBOR (M)',
    'libor (s)': 'LIBOR (S)',
    'euribor': 'Euribor',
    'euribor (q)': 'Euribor (Q)',
    'euribor (m)': 'Euribor (M)',
    'prime': 'Prime',
    'p': 'Prime',
    'base rate': 'Prime',
    'sonia': 'SONIA',
    'cdor': 'CDOR',
    'bkbm': 'BKBM',
}

def create_reference_rate_mapping():
    """Returns the reference rate mapping dictionary."""
    return REFERENCE_RATE_MAPPING

def standardize_reference_rate(rate: str, mapping: dict) -> str:
    """Standardize reference rate name."""
    if not rate or rate.strip() == '':
        return ''
    
    rate_lower = rate.lower().strip()
    
    # Remove common suffixes/prefixes
    rate_clean = rate_lower.replace('+', '').replace(' ', '').strip()
    
    # Direct mapping
    if rate_clean in mapping:
        return mapping[rate_clean]
    
    # Try without period indicators
    base_rate = rate_clean.split('(')[0].strip()
    if base_rate in mapping:
        return mapping[base_rate]
    
    # Check if it's "N/A"
    if rate_lower in ['n/a', 'na', 'none', '-']:
        return 'N/A'
    
    return rate

# ===== SPREAD CLEANING =====

def clean_spread(spread: str) -> str:
    """Clean the spread column - remove dates, reference rates, n/a."""
    if not spread or spread.strip() == '':
        return ''
    
    spread = spread.strip()
    
    # Remove dates (YYYY-MM-DD format)
    if re.match(r'\d{4}-\d{2}-\d{2}', spread):
        return ''
    
    # Remove month/year formats (5/2022, 10/2017, 08/2021, etc.)
    if re.match(r'\d{1,2}/\d{4}', spread):
        return ''
    
    # Remove reference rates
    if spread.upper() in ['SOFR', 'SOFR (M)', 'SOFR (Q)', 'SOFR (S)', 
                          'LIBOR', 'LIBOR (M)', 'LIBOR (Q)', 
                          'L', 'SF', 'P', 'PRIME', 'BASE RATE']:
        return ''
    
    # Standardize "n/a" to empty
    if spread.lower() in ['n/a', 'na', 'none']:
        return ''
    
    return spread

# ===== COMPANY NAME CLEANING =====

# Trailing rate/date junk in company names: "KLO Intermediate Holdings LLC L+775 1.50% LIBOR Floor 4/7/2022" → "KLO Intermediate Holdings LLC"
_COMPANY_RATE_DATE_SUFFIX = re.compile(
    r"\s+(?:L\+[\d.]+\s+)?[\d.]+\s*%\s*(?:LIBOR|SOFR|Prime|Base\s+Rate)?\s*Floor?\s*\d{1,2}/\d{1,2}/\d{2,4}\s*$",
    re.IGNORECASE
)
# Also strip trailing " L+number" or " L+number %" without full date
_COMPANY_RATE_ONLY_SUFFIX = re.compile(
    r"\s+L\+[\d.]+\s*(?:[\d.]+\s*%)?\s*(?:LIBOR|SOFR|Prime)?\s*Floor?\s*$",
    re.IGNORECASE
)

# Instrument/position suffixes to strip so "Company - Revolver" and "Company - Delayed Draw" → same company
_COMPANY_INSTRUMENT_SUFFIXES = re.compile(
    r'\s*-\s*('
    r'Delayed\s+Draw|Revolver|'
    r'Term\s+Loan|First\s+Lien|Second\s+Lien|'
    r'Class\s+[A-Z0-9\-]+\s+(?:Common\s+)?Units?|'
    r'[A-Z0-9\-]+\s+Units?|'
    r'Blocker\s+Units?|Blocker\s+Note|'
    r'LLC\s+Units?|'
    r'Preferred\s+(?:Equity\s+)?(?:Units?|Shares?)|'
    r'Common\s+(?:Equity\s+)?(?:Units?|Shares?)|'
    r'Member\s+Interest[s]?|'
    r'Subordinated\s+Note[s]?|'
    r'Unit[s]?|Note[s]?|Warrant[s]?'
    r')\s*$',
    re.IGNORECASE
)


def _strip_rate_date_suffix(name: str) -> str:
    """Remove trailing rate/date text e.g. ' L+775 1.50% LIBOR Floor 4/7/2022' so same company merges."""
    if not name:
        return name
    prev = None
    while prev != name:
        prev = name
        name = _COMPANY_RATE_DATE_SUFFIX.sub("", name).strip()
        name = _COMPANY_RATE_ONLY_SUFFIX.sub("", name).strip()
    return name


def _strip_instrument_suffix(name: str) -> str:
    """Remove trailing ' - Instrument/position type' so different tranches resolve to one company."""
    if not name or ' - ' not in name:
        return name
    # Strip known instrument suffixes (may need multiple passes if multiple " - " exist)
    prev = None
    while prev != name and _COMPANY_INSTRUMENT_SUFFIXES.search(name):
        prev = name
        name = _COMPANY_INSTRUMENT_SUFFIXES.sub('', name).strip()
    # Also strip generic " - Something" when Something looks like a security (ends with Units, Note, etc.)
    if ' - ' in name:
        before, after = name.rsplit(' - ', 1)
        after = after.strip()
        if re.search(r'\b(?:units?|notes?|warrants?|shares?|class\s+[a-z0-9\-]+)\s*$', after, re.I):
            name = before.strip()
    return name


def clean_company_name(company_name: str) -> str:
    """Normalize company name: strip instrument/position suffixes, then legal entity suffixes."""
    if not company_name or company_name.strip() == '':
        return ''

    name = company_name.strip()
    name = ' '.join(name.split())

    # Strip trailing rate/date e.g. "KLO Intermediate Holdings LLC L+775 1.50% LIBOR Floor 4/7/2022"
    name = _strip_rate_date_suffix(name)

    # Strip instrument/position suffix so "Zeus Fire & Security - Delayed Draw" → "Zeus Fire & Security"
    name = _strip_instrument_suffix(name)

    # Normalize parenthetical: "Rocaceia LLC (Quality Lease and Rental Holdings LLC)" → "Rocaceia LLC"
    if '(' in name and ')' in name:
        name = re.sub(r'\s*\([^)]+\)\s*$', '', name).strip()

    # Normalize LLC variations
    name = re.sub(r'\bL\.?\s*L\.?\s*C\.?\b', 'LLC', name, flags=re.IGNORECASE)

    # Normalize Inc variations
    name = re.sub(r'\bIncorporated\b', 'Inc.', name, flags=re.IGNORECASE)
    name = re.sub(r'\bInc(?!\.)(?=\s|$)', 'Inc.', name, flags=re.IGNORECASE)

    # Normalize Corp variations
    name = re.sub(r'\bCorporation\b', 'Corp.', name, flags=re.IGNORECASE)
    name = re.sub(r'\bCorp(?!\.)(?=\s|$)', 'Corp.', name, flags=re.IGNORECASE)

    # Normalize LP variations
    name = re.sub(r'\bLimited\s+Partnership\b', 'LP', name, flags=re.IGNORECASE)
    name = re.sub(r'\bL\.?\s*P\.?\b', 'LP', name, flags=re.IGNORECASE)

    # Normalize Ltd variations
    name = re.sub(r'\bLimited(?=\s*$)', 'Ltd.', name, flags=re.IGNORECASE)
    name = re.sub(r'\bLtd(?!\.)(?=\s|$)', 'Ltd.', name, flags=re.IGNORECASE)

    # Fix case for common suffixes
    name = re.sub(r'\bllc\b', 'LLC', name)
    name = re.sub(r'\blp\b', 'LP', name)

    # Truncate anything after the legal entity so "ACS Holdings LLC Class A-1 Membership Units" → "ACS Holdings LLC"
    # Also "Accenture plc Class A" → "Accenture plc". (same company_id; full string can stay on row for display.)
    m = re.match(r'^(.*\b(?:LLC|Inc\.|Corp\.|LP|Ltd\.|plc))(\s+.+)$', name, re.IGNORECASE)
    if m:
        name = m.group(1).strip()

    name = ' '.join(name.split())
    return name
