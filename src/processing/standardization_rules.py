#!/usr/bin/env python3
"""
Standardization rules and functions for data cleaning.
Contains all standardization logic for industries, investment types, rates, etc.
"""
import re
from collections import defaultdict
from typing import Optional, Tuple

# ===== INDUSTRY STANDARDIZATION =====

# ----- Industry normalization (aligned with frontend dataCleaning.ts) -----
# Used when building company_exposures.primary_industry so backend and frontend
# use the same ~25 categories and junk filtering.

_JUNK_INDUSTRY_EXACT = frozenset([
    "cash", "cash equivalents", "cash and cash equivalents", "cash & cash equivalents",
    "debt", "equity", "equity interest", "common stock", "preferred stock",
    "expense", "fee expense", "financing", "investment",
    "n/a", "unknown", "various", "other", "industry", "inc.", "l", "libor",
    # Section/category labels that are not industries
    "majority owned company", "joint venture", "non-qualifying assets",
    "connectivity", "shopping facilitators", "multi-sector holdings",
    # Table header artifacts
    "(in thousands)", "(in thousands, except per share data)",
    "(in millions)", "(dollar amounts in thousands)",
])

# Ordered: first match wins. (keywords list, category name)
_INDUSTRY_RULES = [
    (["aerospace", "defense", "defence", "military", "avionics", "aerostructure", "armored component", "space technolog", "aircrew", "air crew"], "Aerospace & Defense"),
    (["insurance", "underwriting", "actuarial", "managing general a"], "Insurance"),
    (["healthcar", "health care", "pharma", "medical", "biotech", "biopharm", "dental", "hospital", "clinical",
      "physician", "surgical", "surgery", "patient", "nursing", "ophthalm", "dermatol", "infusion", "anesthesi",
      "behavioral health", "mental health", "telehealth", "telemedicine", "orthop", "orthot", "prosthe", "oncol",
      "gastroenter", "urolog", "vascular practice", "opioid", "opiod", "substance abuse", "substance use",
      "autism", "pain treatment", "vision care", "cancer", "life science", "drug develop", "drug manufactur",
      "drug discover", "drug delivery", "therapeutic", "ambulance", "veterinar", "animal health", "animal nutrition",
      "physical therapy", "wellness", "supplement", "fertility", "chiropr", "medical device", "health & pharma"], "Healthcare"),
    (["education", "school", "learning", "academic", "university", "k-12", "literacy"], "Education"),
    (["energy", "oil &", "oil and", "oil gas", "oil, gas", "oil field", "oilfield", "natural gas", "petroleum", "propane",
      "pipeline", "midstream", "wellhead", "proppant", "refining", "refinery", "electricity", "electric util",
      "power generat", "power plant", "power &", "power util", "independent power", "gas util", "fuel distribut",
      "solar energy", "solar power", "solar provider", "solar system", "renewable energy", "wind farm", "wind power",
      "energy & util", "energy util", "multi-utilit", "multi utilit"], "Energy"),
    (["real estate", "reit", "property manag", "homebuilding", "homebuilder", "housing"], "Real Estate"),
    (["environmental", "waste manage", "waste process", "waste service", "waste collect", "waste disposal",
      "solid waste", "recycl", "remediat", "water treatment", "sustainab"], "Environmental Services"),
    (["chemical", "plastic", "rubber", "polymer", "adhesive", "mineral", "mining", "quarr"], "Chemicals & Materials"),
    (["container", "packaging", "paper", "forest product", "pulp &", "pulp and"], "Containers & Packaging"),
    (["automotive", "automobile", "auto part", "auto body", "auto component", "auto collision", "auto repair",
      "auto aftermarket", "auto &", "vehicle", "car wash", "collision repair"], "Automotive"),
    (["food", "beverage", "restaurant", "tobacco", "bakery", "dairy", "meat", "seafood", "snack", "grocer", "supermarket",
      "confection", "spice", "seasoning", "catering", "foodservice", "frozen fruit", "wine", "beer", "spirits",
      "nutrition", "produce distribut"], "Food & Beverage"),
    (["hotel", "hospitality", "leisure", "travel", "lodging", "resort", "casino", "recreation", "fitness", "cruise",
      "amusement", "theme park", "golf", "gaming"], "Hospitality & Leisure"),
    (["transport", "logistic", "freight", "trucking", "shipping", "airline", "aviation", "fleet", "cargo", "ferry",
      "marine", "postal", "courier", "road & rail", "road &", "railroad"], "Transportation & Logistics"),
    (["media", "entertainment", "advertis", "publish", "broadcast", "music", "film", "sporting", "sports", "sport ",
      "esport", "e-sport", "soccer", "martial art", "streaming", "television", "radio", "movie", "cable &",
      "cable and", "satellite", "video gam"], "Media & Entertainment"),
    (["telecom", "tele-com", "communication service", "wireless carrier", "wireless telecom", "communications equipment",
      "diversified telecommunication", "integrated telecom"], "Telecommunications"),
    (["financial", "banking", "capital market", "asset manage", "wealth", "lending", "mortgage", "brokerage",
      "securities", "payment", "investment", "fund admin", "venture capital", "private equity", "credit rating",
      "finance", "fintech", "banks"], "Financial Services"),
    (["consumer", "retail", "e-commerce", "ecommerce", "apparel", "footwear", "fashion", "clothing", "household",
      "personal care", "personal product", "cosmetic", "beauty", "jewelry", "luxury", "sporting good", "textile",
      "pet ", "pet care"], "Consumer & Retail"),
    (["software", "saas", "technology", "high tech", "internet", "it service", "it solution", "it consulting",
      "it hardware", "information tech", "cloud", "cyber", "data analytics", "data center", "data service",
      "data processing", "data storage", "semiconductor", "electronic", "computer", "hardware", "networking",
      "virtualization"], "Software & Technology"),
    (["distribut", "wholesale", "wholesaler"], "Distribution & Wholesale"),
    (["manufactur", "industrial", "building", "construct", "machinery", "equipment", "engineer", "metal", "steel",
      "welding", "fabricat", "hvac", "plumbing", "electrical", "fire safety", "fire protect", "alarm", "janitorial",
      "cleaning service", "maintenance", "elevator", "fencing", "roofing", "paving", "signage", "capital goods",
      "conglomerat"], "Industrials"),
    (["service", "consult", "staffing", "outsourc", "human resource", "workforce", "recruit", "legal", "marketing",
      "management", "professional", "commercial", "government", "testing", "inspection", "provider of"], "Business Services"),
    (["agricult", "farm", "crop", "horticultur", "landscap", "nurseri", "lawn", "garden", "greenhouse", "animal care"], "Agriculture"),
    (["diversified"], "Diversified"),
]

# Canonical list of sectors for profile building and UI. LLM must pick only from this list.
ALLOWED_INDUSTRIES = sorted(set(c for _, c in _INDUSTRY_RULES) | {"Other"})


def normalize_industry(raw: str) -> str:
    """
    Normalize industry string to ~25 standard categories (aligned with frontend dataCleaning.ts).
    Returns empty string for junk/non-industry values so they can be skipped when aggregating.
    """
    if not raw or not raw.strip():
        return ""
    s = raw.strip()
    lower = s.lower().replace("&amp;", "&")
    # Junk: numbers/percentages
    if re.match(r"^\d[\d.,% ]*(%(\s*PIK)?)?$", s):
        return ""
    # Table header artifacts starting with "(" like "(In thousands)"
    if s.startswith("(") and s.endswith(")"):
        return ""
    # Investment type leakage / subtotal rows
    if re.match(r"^(first lien|second lien|senior secured|subordinat|unsecured|revolver|delayed draw|junior secured|unfunded|total )", lower):
        return ""
    # Cash equivalents category labels (GAIN/GLAD etc.): "cash equivalents - 6.6 %"
    if re.match(r"^cash\s+equivalents?\s*-\s*[\d.]+\s*%$", lower):
        return ""
    if lower in _JUNK_INDUSTRY_EXACT:
        return ""
    # Dimension string fragments leaking into industry (e.g. "Other Scorpio Bidco First-lien loan (EUR 2,511 par, due 4/2031)...")
    if len(s) > 80 or re.search(r"\bpar,?\s+due\b|\bdue\s+\d{1,2}/\d{4}\b|\bacquisition date\b|\breference rate\b", lower):
        return ""
    # FIRE taxonomy
    if lower.startswith("fire"):
        if "insurance" in lower:
            return "Insurance"
        if "real estate" in lower:
            return "Real Estate"
        return "Financial Services"
    # Services: X prefixes
    if lower.startswith("services: business") or lower.startswith("services business"):
        return "Business Services"
    if lower.startswith("services: consumer") or lower.startswith("services consumer"):
        return "Consumer & Retail"
    if lower.startswith("services: professional"):
        return "Business Services"
    # Exact GICS sub-industry names that are too short/ambiguous for substring matching
    _EXACT_GICS = {
        "road": "Transportation & Logistics",
        "communications": "Telecommunications",
    }
    if lower in _EXACT_GICS:
        return _EXACT_GICS[lower]
    # Keyword cascade
    for keywords, category in _INDUSTRY_RULES:
        if any(kw in lower for kw in keywords):
            return category
    return s


def standardize_industry(industry: str, mapping: dict = None) -> str:
    """Standardize industry name using _INDUSTRY_RULES cascade (same as normalize_industry).

    The optional *mapping* parameter is accepted for backward-compatibility but
    ignored — all classification now goes through ``normalize_industry()``.
    Returns ``"Other"`` (never empty string) for junk/unrecognized values so that
    post-processing always has a usable category.
    """
    result = normalize_industry(industry)
    if not result:
        return "Other" if (industry and industry.strip()) else ""
    return result


def create_mapping_dict():
    """Backward-compat stub — returns an empty dict. ``standardize_industry`` ignores it."""
    return {}


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
    if 'subordinated' in inv_type_lower or 'mezzanine' in inv_type_lower:
        return 'Subordinated Debt'
    if 'unsecured' in inv_type_lower:
        return 'Unsecured Debt'
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

_XBRL_MEMBER_TO_RATE = {
    'securedovernightfinancingratemember': 'SOFR',
    'onemonthsecuredovernightfinancingratemember': 'SOFR',
    'threemonthsecuredovernightfinancingratemember': 'SOFR',
    'sixmonthsecuredovernightfinancingratemember': 'SOFR',
    'londoninterbankofferedratemember': 'LIBOR',
    'onemonthlondoninterbankofferedratemember': 'LIBOR',
    'threemonthlondoninterbankofferedratemember': 'LIBOR',
    'eurodollarmember': 'LIBOR',
    'eurointerbankofferedratemember': 'Euribor',
    'canadianovernightreporateaveragemember': 'CORRA',
    'canadianovernightreporateaveragecorramember': 'CORRA',
    'canadiandollarofferedratemember': 'CDOR',
    'bankbillswapbidratemember': 'BBSW',
    'bankbillswapratemember': 'BBSW',
    'bloombergshorttermbankyieldindexmember': 'BSBY',
    'onemonthbloombergshorttermbankyieldindexmember': 'BSBY',
    'bbsymember': 'BSBY',
    'norwegianinterbankofferedratenibormember': 'NIBOR',
    'baseratemember': 'Prime',
    'fixedratemember': 'Fixed',
    'soniamember': 'SONIA',
    'sterlingovernightratemember': 'SONIA',
}


def standardize_reference_rate(rate: str, mapping: dict) -> str:
    """Standardize reference rate name."""
    if not rate or rate.strip() == '':
        return ''

    # Handle XBRL member format: "YYYYMMDD#MEMBERNAME" or "YYYY#MEMBERNAME"
    if '#' in rate:
        member = rate.split('#', 1)[1].lower()
        if member in _XBRL_MEMBER_TO_RATE:
            return _XBRL_MEMBER_TO_RATE[member]
        # Unknown member — blank it out rather than show raw XBRL
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
# "Strive Health Holdings LLC. Warrant Acquisition Date 9/28/2023" → company only
_COMPANY_WARRANT_ACQUISITION_DATE_SUFFIX = re.compile(
    r"\s+Warrant\s+Acquisition\s+Date\s+\d{1,2}/\d{1,2}/\d{2,4}\s*$",
    re.IGNORECASE
)

# Instrument/position suffixes to strip so "Company - Revolver" and "Company - Delayed Draw" → same company
# Include "Common Equity" and "Series A Preferred Shares" so "Zoro - Common Equity" / "Zoro Common Equity" → "Zoro" (BXSL)
_COMPANY_INSTRUMENT_SUFFIXES = re.compile(
    r'\s*-\s*('
    r'Delayed\s+Draw(?:\s+Term\s+Loan)?|DDTL|Revolver|Revolving\s+(?:Credit|Loan)|'
    r'Term\s+Loan\s*[A-Z0-9]*|First\s+Lien|Second\s+Lien|Senior\s+Secured|'
    r'Incremental\s+Term\s+Loan|Initial\s+Term\s+Loan|'
    r'Class\s+[A-Z0-9\-]+\s+(?:Common\s+)?Units?|'
    r'[A-Z0-9\-]+\s+Units?|'
    r'Blocker\s+Units?|Blocker\s+Note|'
    r'LLC\s+Units?|'
    r'Preferred\s+(?:Equity\s+)?(?:Units?|Shares?|Stock)|'
    r'Common\s+(?:Equity\s+)?(?:Units?|Shares?|Stock)|'
    r'Common\s+Equity|'
    r'Series\s+A\s+Preferred\s+Shares?|'
    r'Member\s+Interest[s]?|Partnership\s+Interest[s]?|'
    r'Subordinated\s+(?:Note[s]?|Debt)|Unsecured\s+(?:Note[s]?|Debt)|'
    r'Mezzanine\s+(?:Note[s]?|Loan)|'
    r'PIK\s+Note[s]?|'
    r'Unit[s]?|Note[s]?|Warrant[s]?'
    r')\s*$',
    re.IGNORECASE
)
# Same instrument phrases without leading " - " so "Zoro Common Equity" → "Zoro"; "Ansett Aviation Training Equity Interest" → "Ansett Aviation Training"
_COMPANY_INSTRUMENT_SUFFIX_NO_DASH = re.compile(
    r'\s+(?:Common\s+Equity|Series\s+A\s+Preferred\s+Shares?|Equity\s+Interest|First\s+Lien\s+Senior\s+Secured\s+Loan)\s*$',
    re.IGNORECASE
)

# "Investments in Non-Control... {type} {company}" — extract the company name
_INVESTMENTS_IN_PREFIX = re.compile(
    r'^Investments?\s+in\s+(?:Non-?Control\w*|Control\w*|Affiliated)\s+'
    r'(?:Non-?Affiliate\w*\s+)?'
    r'(?:Affiliated\s+)?'
    r'(?:Investments?\s+)?'
    r'(?:Portfolio\s+Companies?\s+)?'
    r'(?:Preferred\s+Equity|Common\s+Equity'
    r'|First\s+Lien(?:\s*/\s*Senior\s+Secured|\s+Secured)?\s+Debt'
    r'|Second\s+Lien\s+Debt|Subordinated\s+(?:Debt|Note[s]?)|Unsecured\s+Debt'
    r'|Senior\s+(?:Secured\s+)?Debt|Secured\s+Debt|Equity|Warrants?)'
    r'\s+(?:Issuer\s+Name\s+)?',
    re.IGNORECASE
)

# Bare "Investments in Non-Control/Controlled" with no company after it
_INVESTMENTS_IN_BARE = re.compile(
    r'^Investments?\s+in\s+(?:Non-?Control\w*|Control\w*|Affiliated)(?:\s+Non-?Affiliate\w*)?$',
    re.IGNORECASE
)

# Non-company entries to filter out (CLOs, cash, header rows, type/category labels, etc.)
_NON_COMPANY_PATTERNS = re.compile(
    r'^('
    r'Total\s+(?:Investments|Fair\s+Value|Portfolio|Net)'     # "Total Investments" but NOT "Total Vision LLC"
    r'|Subtotal'
    r'|CLO\b|CDO\b|Collateralized'    # CLOs/CDOs
    r'|Cash\s+and\s+Cash'             # "Cash and Cash Equivalents"
    r'|Cash\s+Equivalents?'
    r'|U\.?S\.?\s+Treasury'           # US Treasury
    r'|Money\s+Market'
    r'|Net\s+(?:Assets?|Unrealized)'
    r'|Schedule\s+of\s+Investments'
    r'|Consolidated\s+Schedule'
    r'|See\s+(?:Notes?|Accompanying)'
    r'|Common\s+Equity/Equity\s+Interests/Warrants'   # SLRC: investment type label leaked as company
    r'|Equipment\s+Financing'                          # SLRC: category label leaked as company
    # Section subtotals that DSPy sometimes emits as data rows (e.g. "Senior Secured Loans—First Lien")
    r'|Senior\s+Secured\s+Loans?'
    # Numeric/symbol junk rows
    r'|[\$\d,\.\(\)\s]+$'                             # pure number / $ / parenthetical number
    # Bank accounting terms from NEWT-style filings
    r'|Deposits?\s*:?$'
    r'|Noninterest\s+(?:expense|income)'
    r'|Other\s+portfolio\s+companies\s+unrealized'
    r')',
    re.IGNORECASE
)

# Long header-style prefixes from HRZN and similar BDCs that prepend section headers to company names:
#   "Portfolio Company Debt Securities- United States Biotechnology Pendulum Therapeutics Inc."
# We strip through the geography; the embedded sector text is then handled by _extract_company_from_hrzn_blob.
_PORTFOLIO_COMPANY_PREFIX = re.compile(
    r'^Portfolio\s+Company\s+(?:Debt\s+Securities|Equity\s+Investments?|Warrant\s+Investments?)'
    r'\s*[-–—]\s*(?:United\s+States|International|Europe|Canada|Global|Israel|Asia|Latin\s+America)\s*',
    re.IGNORECASE
)

# Common junk prefixes from table headers that leak into company names
_JUNK_COMPANY_PREFIXES = re.compile(
    r'^(?:'
    r'(?:First|Second|Senior|Junior|Subordinated|Unsecured)\s+(?:Lien|Secured)\s*[-–—:]\s*'
    r'|(?:Debt|Equity)\s+Investments?\s*[-–—:]\s*'
    r')',
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
        name = _COMPANY_WARRANT_ACQUISITION_DATE_SUFFIX.sub("", name).strip()
    return name


def _strip_instrument_suffix(name: str) -> str:
    """Remove trailing ' - Instrument/position type' so different tranches resolve to one company."""
    if not name:
        return name
    # Strip " Common Equity" / " Series A Preferred Shares" without dash (e.g. "Zoro Common Equity" → "Zoro")
    prev = None
    while prev != name and _COMPANY_INSTRUMENT_SUFFIX_NO_DASH.search(name):
        prev = name
        name = _COMPANY_INSTRUMENT_SUFFIX_NO_DASH.sub('', name).strip()
    if ' - ' not in name:
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


def is_non_company_entry(name: str) -> bool:
    """Return True if the name looks like a non-company entry (CLO, cash, header row, etc.)."""
    if not name or not name.strip():
        return True
    return bool(_NON_COMPANY_PATTERNS.match(name.strip()))


# Sector phrases that appear before company names in HRZN-style blobs.
# Built from ALLOWED_INDUSTRIES + HRZN-specific sector labels.
_HRZN_SECTOR_PHRASES = sorted([
    # From ALLOWED_INDUSTRIES
    "Aerospace & Defense", "Agriculture", "Automotive", "Business Services",
    "Chemicals & Materials", "Consumer & Retail", "Containers & Packaging",
    "Distribution & Wholesale", "Diversified", "Education", "Energy",
    "Environmental Services", "Financial Services", "Food & Beverage",
    "Healthcare", "Hospitality & Leisure", "Industrials", "Insurance",
    "Media & Entertainment", "Real Estate", "Software & Technology",
    "Telecommunications", "Transportation & Logistics",
    # HRZN-specific sector labels that differ from canonical
    "Artificial Intelligence & Automation", "Biotechnology", "Connectivity",
    "Consumer Products & Services", "Education Technology",
    "Finance and Insurance", "Green Technology",
    "Healthcare Technology", "Medical Devices",
    "Other Healthcare Services", "Real Estate Technology",
    "Space Technology", "Supply Chain Technology", "Transportation Technology",
], key=lambda s: -len(s))  # longest first for greedy matching


# HRZN no-space concatenated format (e.g. from XBRL dimension labels):
#   "NonaffiliateDebtInvestmentsLifeScienceOnkosSurgicalIncMedicalDeviceTermLoanOneMember"
#   "NonControlledAffiliateDebtInvestmentsShengrowIncOtherSustainabilityRevolverMember"
#   "ControlledAffiliateDebtInvestmentsNexiiIncOtherSustanabilityTermLoanOneMember" (typo: Sustanability)
# Pattern: prefix + [leading sector?] + company (CamelCase) + trailing sector? + TermLoan.../RevolverMember
_HRZN_CONCAT_PREFIX = re.compile(
    r'^(?:Non(?:affiliate(?:d)?|ControlledAffiliate)|ControlledAffiliate)DebtInvestments',
    re.IGNORECASE
)
_HRZN_CONCAT_LEADING_SECTOR = re.compile(
    r'^(LifeScience|Sustainability)',
    re.IGNORECASE
)
_HRZN_CONCAT_TRAILING = re.compile(
    r'(Biotechnology|MedicalDevice|AlternativeEnergy|EnergyEfficiency|OtherSustainability|OtherSustanability)?'
    r'(TermLoa?n(One|Two|Three|Four|Five|Six|Seven|Eight)?|Revolver)Member\s*$',
    re.IGNORECASE
)
# Map stripped industry tokens (camelCase) to display industry for extracted_industry
_HRZN_CONCAT_INDUSTRY_MAP = {
    'lifescience': 'Life Sciences',
    'sustainability': 'Sustainability',
    'biotechnology': 'Biotechnology',
    'medicaldevice': 'Medical Devices',
    'alternativeenergy': 'Energy',
    'energyefficiency': 'Energy',
    'othersustainability': 'Environmental Services',
    'othersustanability': 'Environmental Services',  # typo in source data
}
# HRZN: trailing sector word concatenated after LLC/Inc/Corp with no space (e.g. "Holdings LLCSoftware")
_HRZN_TRAILING_CONCAT_SECTOR = re.compile(
    r'^(.*)(LLC|Inc\.?|Corp\.?|Ltd\.?)(Software|Biotechnology|Healthcare|Sustainability)\s*$',
    re.IGNORECASE
)
_HRZN_TRAILING_SECTOR_INDUSTRY = {
    'software': 'Software & Technology',
    'biotechnology': 'Biotechnology',
    'healthcare': 'Healthcare',
    'sustainability': 'Sustainability',
}


def _camel_case_to_words(s: str) -> str:
    """Insert spaces before capitals so 'OnkosSurgicalInc' -> 'Onkos Surgical Inc'."""
    if not s or ' ' in s:
        return s.strip()
    # Space before capital that follows lowercase or digit
    s = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', s)
    # Space before Inc/LLC/Corp/Ltd when preceded by uppercase (e.g. MMLUSInc -> MMLUS Inc)
    s = re.sub(r'(?<=[A-Z])(?=(?:Inc|LLC|Corp|Ltd)\b)', ' ', s, flags=re.IGNORECASE)
    return s.strip()


def _parse_hrzn_concatenated(s: str) -> Tuple[str, Optional[str]]:
    """
    Parse HRZN no-space concatenated company name; return (cleaned_company, extracted_industry).
    If s doesn't match the pattern, return (s, None).
    Examples:
      NonaffiliateDebtInvestmentsLifeScienceOnkosSurgicalIncMedicalDeviceTermLoanOneMember
        -> ("Onkos Surgical Inc.", "Medical Devices")
      NonaffiliateDebtInvestmentsLifeScienceCastleCreekBiosciencesBiotechnologyTermLoanOneMember
        -> ("Castle Creek Biosciences", "Biotechnology")
    """
    if not s or ' ' in s or not _HRZN_CONCAT_PREFIX.match(s):
        return (s, None)
    rest = _HRZN_CONCAT_PREFIX.sub('', s, count=1)
    extracted_industry: Optional[str] = None
    # Strip leading sector (LifeScience | Sustainability)
    m_lead = _HRZN_CONCAT_LEADING_SECTOR.match(rest)
    if m_lead:
        tok = m_lead.group(1).lower()
        extracted_industry = _HRZN_CONCAT_INDUSTRY_MAP.get(tok)
        rest = rest[m_lead.end():]
    # Capture trailing industry (more specific) before stripping
    m_trail = _HRZN_CONCAT_TRAILING.search(rest)
    if m_trail and m_trail.group(1):
        tok = m_trail.group(1).lower()
        extracted_industry = _HRZN_CONCAT_INDUSTRY_MAP.get(tok, extracted_industry)
    # Strip trailing (Industry)?TermLon?...Member
    rest = _HRZN_CONCAT_TRAILING.sub('', rest)
    if not rest:
        return (s, None)
    # Convert camelCase to words
    company = _camel_case_to_words(rest)
    if not company:
        return (s, None)
    # Normalize Inc/LLC/Corp
    company = re.sub(r'\bInc(?!\.)(?=\s|$)', 'Inc.', company, flags=re.IGNORECASE)
    company = re.sub(r'\bCorp(?!\.)(?=\s|$)', 'Corp.', company, flags=re.IGNORECASE)
    return (company.strip(), extracted_industry)


def _extract_from_hrzn_blob(blob: str) -> str:
    """Extract company name from HRZN-style blob after geography prefix is stripped.

    Input examples (after "Portfolio Company Debt Securities- United States" removed):
      "Biotechnology Pendulum Therapeutics Inc."
      "Applied Digital Corp."
      "Artificial Intelligence & Automation Ambient Photonics Inc."
      "Biotechnology Total Pendulum Therapeutics Inc."  (subtotal row)
      "Biotechnology"  (section header, no company)
    """
    if not blob or not blob.strip():
        return ''
    blob = blob.strip()

    # "Total" at the start means subtotal row → not a real company
    if re.match(r'^Total\b', blob):
        return ''
    # Remove embedded "Total " mid-string (subtotal label before company name)
    blob = re.sub(r'\bTotal\s+(?=[A-Z])', '', blob).strip()

    # Strip "Type of Investment..." trailing junk from messy HRZN scrapes
    blob = re.sub(r'\s*Type\s+of\s+Investment\b.*$', '', blob, flags=re.IGNORECASE).strip()

    # Strip known sector phrase prefix (longest match first)
    blob_lower = blob.lower()
    stripped_sector = False
    for phrase in _HRZN_SECTOR_PHRASES:
        pl = phrase.lower()
        if blob_lower.startswith(pl):
            rest = blob[len(pl):].strip()
            if rest:
                blob = rest
                stripped_sector = True
                break
    # Also try "Other " prefix before sector  ("Other Healthcare Services Cellares Corp.")
    if not stripped_sector and blob_lower.startswith('other '):
        inner = blob_lower[6:]  # after "Other "
        for phrase in _HRZN_SECTOR_PHRASES:
            pl = phrase.lower()
            if inner.startswith(pl):
                rest = blob[6 + len(pl):].strip()
                if rest:
                    blob = rest
                    stripped_sector = True
                    break

    if not blob:
        return ''

    # If we successfully stripped a sector prefix, the remainder is the company name
    if stripped_sector and blob:
        return blob.strip()

    # If remains has a legal suffix, it's a company name
    if re.search(r'\b(?:Inc\.?|LLC|Corp\.?|Ltd\.?|LP|PLC|plc|Co\.|Limited)\s*$', blob):
        return blob.strip()

    # No legal suffix and no sector stripped → likely just a section header
    return ''


# ----- Ticker-specific company name cleanup (XBRL / scraper output) -----
# See docs/TICKER_COMPANY_NAME_FIXES.md for the full list and examples.


def _apply_ticker_specific_company_cleanup(name: str, ticker: Optional[str]) -> Tuple[str, Optional[str]]:
    """
    Apply ticker-specific cleanup so company_name column contains only the business name,
    not type/industry/value that leaked from XBRL dimension strings or table headers.
    Returns (cleaned_name, extracted_industry). extracted_industry is the industry phrase
    stripped from the name (e.g. "Automobile Components") when we remove it, so callers
    can set row["industry"] when empty; None if no industry was stripped.
    """
    if not name or not name.strip() or not ticker:
        return (name if name else "", None)
    ticker_upper = ticker.strip().upper()
    s = name.lstrip('\ufeff').strip()  # Strip BOM that sometimes appears in XBRL strings, then whitespace
    extracted_industry: Optional[str] = None
    # Section headers: "Amounts related to investments transferred to or from other 1940 Act classification during the period Affiliate/Control Investments" (MAIN, MSIF)
    if re.match(
        r'^(?:Other\s+)?Amounts\s+related\s+to\s+investments\s+transferred\s+to\s+or\s+from\s+other\s+1940\s+Act\s+classification\s+during\s+the\s+period\s*(?:,\s*)?(?:Affiliate\s+Investments|Control\s+Investments|Control\s+investments)?\s*$',
        s,
        re.I,
    ):
        return ('', None)
    # Fragment ") Industry X" or ") Insurance" (broken parenthetical) → clear for any ticker
    if s.startswith(') ') and re.match(r'^\)\s*(?:Industry\s+\S+|Insurance)\s*$', s, re.I):
        return ('', None)

    # RAND: "Mountain Regional Equipment Solutions - $3 000", "HDI Acquisition LLC. - $1 245",
    # " - 3 000" (no $), "Mountain Regional Equipment Solutions - Warrant for 1% Membership Interest", " - 37" (Common Equity)
    if ticker_upper == 'RAND':
        s = re.sub(r'\s*-\s*\$\s*[\d\s,]+\s*$', '', s)
        s = re.sub(r'\s*-\s*[\d\s,]+\s*$', '', s)  # " - 3 000" or " - 37" value/line item
        s = re.sub(r'\s*-\s*Warrant\s+for\s+[\d.%]+\s+Membership\s+Interest\s*$', '', s, flags=re.I)
        s = re.sub(r'\s*-\s*First\s+Lien\s*$', '', s, flags=re.I)
        s = re.sub(r'\s*-\s*Common\s+Equity\s*$', '', s, flags=re.I)
        s = re.sub(r'\s*-\s*Other\s*$', '', s, flags=re.I)
        return (s.strip(), None)

    # OXSQ: "Senior Secured Notes - Business Services - Access CIG" → "Access CIG"
    if ticker_upper == 'OXSQ':
        if re.match(r'^Senior\s+Secured\s+Notes\s+-', s, re.I):
            parts = [p.strip() for p in s.split(' - ')]
            if len(parts) >= 3:
                return (parts[-1], None)  # company is last segment
            if len(parts) == 2:
                return ('', None)  # "Senior Secured Notes - Industry" subtotal, no company
        return (s, None)

    # SAR: "Non-control/Non-affiliate investments - 229.3% - Corporate Education Software" etc.
    # "Ta TT Buyer LLC-Media: Broadcasting & Subscription-Term Loan B (6/24)-Loan" → "Ta TT Buyer LLC" (take segment before "-Industry:")
    if ticker_upper == 'SAR':
        s = re.sub(r'^Non-control/Non-affiliate\s+investments\s+-\s*[\d.]+\s*%\s*-\s*', '', s, flags=re.I)
        s = re.sub(r'^Affiliate\s+investments\s+-\s*[\d.]+\s*%\s*-\s*', '', s, flags=re.I)
        s = re.sub(r'^Control(led)?\s+investments\s+-\s*[\d.]+\s*%\s*-\s*', '', s, flags=re.I)
        if s.startswith('Healthcare & Pharmaceuticals '):
            s = s[len('Healthcare & Pharmaceuticals '):].strip()
            extracted_industry = 'Healthcare'
        # "CompanyName-Industry: SubIndustry-Instrument..." → company is first segment (before first "-")
        if '-' in s:
            parts = s.split('-', 1)
            if len(parts) == 2 and re.match(r'^\s*(Media|Transportation|Services)\s*:', parts[1], re.I):
                s = parts[0].strip()
        # "Cloudpermit - Municipal Government Software - Delayed Draw Term Loan..." → "Cloudpermit"
        if ' - ' in s:
            first = s.split(' - ', 1)[0].strip()
            if first and not re.match(r'^(First|Second|Delayed|Term|Senior)', first, re.I):
                s = first
        # Sector-only (no company name) → clear
        _sar_sector_only = frozenset([
            'alternative investment management software', 'architecture & engineering software',
            'association management software', 'consumer services', 'corporate education software',
            'custom millwork software', 'cyber security', 'dental practice management',
            'direct selling software', 'education services', 'education software',
            'field service management', 'government software', 'municipal government software',
            'healthcare & pharmaceuticals',
        ])
        if s.strip().lower() in _sar_sector_only:
            return ('', None)
        return (s.strip(), extracted_industry)

    # GBDC: "Armstrong Bidco Limited One stop 1", "Accelya Lux Finco S.A.R.L. One stop" → company only (strip facility/tranche label)
    if ticker_upper == 'GBDC':
        s = re.sub(r'\s+One\s+stop,?(?:\s+\d+)?\s*$', '', s, flags=re.IGNORECASE).strip()
        # Concatenated two names (e.g. "Arnott LLCBaduhenna Bidco Limited...") → take part after "LLC" when no space after LLC
        if re.search(r'LLC[A-Z]', s):
            m = re.match(r'^.+?(LLC)([A-Z][a-zA-Z\s]+)$', s)
            if m:
                s = m.group(2).strip()
                s = re.sub(r'\s+One\s+stop,?(?:\s+\d+)?\s*$', '', s, flags=re.IGNORECASE).strip()

    # HRZN: no-space concatenated names (XBRL dimension style):
    #   "NonaffiliateDebtInvestmentsLifeScienceOnkosSurgicalIncMedicalDeviceTermLoanOneMember"
    #   "NonControlledAffiliateDebtInvestmentsShengrowIncOtherSustainabilityRevolverMember"
    #   -> company name + industry from stripped sector
    if ticker_upper == 'HRZN' and ' ' not in s and _HRZN_CONCAT_PREFIX.match(s):
        parsed, ind = _parse_hrzn_concatenated(s)
        if parsed:
            return (parsed, ind)
        # fall through if parsing left empty (shouldn't happen for valid rows)

    # HRZN: trailing sector concatenated after legal suffix with no space ("LLCSoftware" -> "LLC")
    if ticker_upper == 'HRZN' and ' ' in s:
        m_conc = _HRZN_TRAILING_CONCAT_SECTOR.match(s)
        if m_conc:
            s = (m_conc.group(1) + m_conc.group(2)).strip()
            ext = _HRZN_TRAILING_SECTOR_INDUSTRY.get(m_conc.group(3).lower())
            return (s, ext)
        # Sector prefix leak: "Technology Samba TV Inc." -> "Samba TV Inc."
        if re.match(r'^(?:Technology|echnology)\s+\S', s):
            s = re.sub(r'^(?:Technology|echnology)\s+', '', s, flags=re.I).strip()
            if not extracted_industry:
                extracted_industry = 'Software & Technology'

    # MFIC: "Business Services Jacent ..."; "Affiliated Investments Golden Bear 2016-R LLC"; "Automobile Components K&N Parent Inc." → company only
    if ticker_upper == 'MFIC':
        # "Non-Controlled/Affiliated Investments {sector}" section headers → clear
        if re.match(r'^Non-Controlled/Affiliated\s+Investments\s*', s, re.I):
            rest = re.sub(r'^Non-Controlled/Affiliated\s+Investments\s*', '', s, flags=re.I).strip()
            # If remainder is a sector only (no real company after), clear
            is_sector = any(rest.lower().startswith(p.lower()) for p in _HRZN_SECTOR_PHRASES)
            if not rest or is_sector:
                # Try stripping sector to get company
                for phrase in _HRZN_SECTOR_PHRASES:
                    if rest.lower().startswith(phrase.lower()):
                        company = rest[len(phrase):].strip()
                        if company and len(company) > 3:
                            return (company, phrase)
                return ('', None)
            return (rest.strip(), None)
        # Bare "Non-Controlled/Non-Affiliated Investments" section header (no company after) → clear
        if re.match(r'^Non-Controlled/Non-Affiliated\s+Investments\s*$', s, re.I):
            return ('', None)
        # "Non-Controlled/Non-Affiliated Investments {Industry} {Company}" or section-only "Non-Controlled/Non-Affiliated Investments Automotive"
        s = re.sub(r'^Non-Controlled/Non-Affiliated\s+Investments\s+', '', s, flags=re.I).strip()
        s = re.sub(r'^Affiliated\s+Investments\s+', '', s, flags=re.I)
        s = re.sub(r'\s+Investment\s+Type\s+.*$', '', s, flags=re.I)
        # "Automobile Components " prefix and industry-only "Automobile Components," or "Automobile Components"
        if s.startswith('Automobile Components '):
            s = s[len('Automobile Components '):].strip()
            extracted_industry = 'Automobile Components'
        if s.startswith('Paper & Forest Products '):
            s = s[len('Paper & Forest Products '):].strip()
            if not extracted_industry:
                extracted_industry = 'Paper & Forest Products'
        if s.startswith('Personal Care Products '):
            s = s[len('Personal Care Products '):].strip()
            if not extracted_industry:
                extracted_industry = 'Personal Care Products'
        if s.startswith('Professional Services '):
            s = s[len('Professional Services '):].strip()
            if not extracted_industry:
                extracted_industry = 'Professional Services'
        # "Industry - Company" format (case-insensitive; map to canonical industry)
        _mfic_hyphen_prefixes = [
            ('Commercial services & supplies - ', 'Business Services'),
            ('Commercial Services & Supplies - ', 'Business Services'),
            ('Personal care products - ', 'Consumer & Retail'),
            ('Personal Care Products - ', 'Consumer & Retail'),
            ('Wireless telecommunication services - ', 'Telecommunications'),
            ('Wireless telecommunication services-', 'Telecommunications'),
            ('Wireless Telecommunication Services - ', 'Telecommunications'),
            ('Household durables - ', 'Consumer & Retail'),
            ('Household Durables ', 'Consumer & Retail'),
            ('Household durables ', 'Consumer & Retail'),
            ('Pharmaceuticals - ', 'Healthcare'),
            ('Pharmaceuticals ', 'Healthcare'),
            ('Consumer Services ', 'Consumer & Retail'),
        ]
        for prefix, ind in _mfic_hyphen_prefixes:
            if s.lower().startswith(prefix.lower()):
                s = s[len(prefix):].strip()
                if not extracted_industry:
                    extracted_industry = ind
                break
        # Dedupe repeated phrase e.g. "Summer Fridays Summer Fridays" → "Summer Fridays"
        s = re.sub(r'^(.+?)\s+\1\s*$', r'\1', s).strip()
        # Dedupe repeated word e.g. "AGDATA AGDATA Midco" → "AGDATA Midco"
        prev = None
        while prev != s:
            prev = s
            s = re.sub(r'\b(\S+)\s+\1\b', r'\1', s)
        if s.strip() in ('Automobile Components', 'Automobile Components,'):
            return ('', None)
        for phrase in _HRZN_SECTOR_PHRASES:
            if s.lower().startswith(phrase.lower()):
                s = s[len(phrase):].strip()
                if not extracted_industry:
                    extracted_industry = phrase
                break
        if s.startswith('Hotels Restaurants & Leisure '):
            s = s[len('Hotels Restaurants & Leisure '):].strip()
            if not extracted_industry:
                extracted_industry = 'Hospitality & Leisure'
        # GICS-style industry prefixes (e.g. "Advertising Printing & Publishing ", "Wireless Telecommunication Services Global Eagle...", "Consumer Services Clarus Commerce...")
        _mfic_gics = [
            ('Advertising Printing & Publishing', 'Media & Entertainment'),
            ('Wireless Telecommunication Services', 'Telecommunications'),
            ('Consumer Services', 'Consumer & Retail'),
            ('Commercial Services & Supplies', 'Business Services'),
            ('Media & Entertainment', 'Media & Entertainment'),
            ('Beverage Food & Tobacco', 'Food & Beverage'),
            ('Chemicals Plastics & Rubber', 'Chemicals & Materials'),
            ('Construction & Building', 'Industrials'),
            ('Hotel Gaming Leisure', 'Hospitality & Leisure'),
            ('Hotel Gaming and Leisure', 'Hospitality & Leisure'),
        ]
        for ind, canon in _mfic_gics:
            if s.startswith(ind + ' '):
                s = s[len(ind):].strip()
                if not extracted_industry:
                    extracted_industry = canon
                break
        # Section-only: industry name with no company
        _mfic_section_only = frozenset([
            'advertising printing & publishing', 'consumer services', 'commercial services & supplies',
            'hotels restaurants & leisure', 'paper & forest products', 'personal care products',
            'professional services', 'wireless telecommunication services', 'beverage food & tobacco',
            'chemicals plastics & rubber', 'construction & building', 'hotel gaming leisure',
            'hotel gaming and leisure',
        ])
        if not s or s.strip().lower() in _mfic_section_only:
            return ('', None)
        # Trailing instrument text: " ... First Lien Secured Debt - Term Loan SOFR+560..." or " - Delayed Draw ...", " - Revolver ..."
        s = re.sub(r'\s+First\s+Lien\s+Secured\s+Debt\s+[-–—].*$', '', s, flags=re.I).strip()
        s = re.sub(r'\s+[-–—]\s+Delayed\s+Draw\s+.*$', '', s, flags=re.I).strip()
        s = re.sub(r'\s+[-–—]\s+Revolver\s+.*$', '', s, flags=re.I).strip()
        # "Jacent Jacent Strategic Merchandising" → dedupe repeated word at start
        s = re.sub(r'^(\S+)\s+\1\s+', r'\1 ', s)
        return (s.strip(), extracted_industry)

    # MSDL: strip subsector prefixes that leak into company_name, e.g.
    # "Electronic Equipment Instruments & Components Abracon Group Holdings LLC"
    # and "Software Everbridge Holdings LLC" → keep pure company name.
    if ticker_upper == 'MSDL':
        _msdl_prefixes = [
            'Electronic Equipment Instruments & Components ',
            'Software ',
            'Services ',
        ]
        for pref in _msdl_prefixes:
            if s.startswith(pref):
                rest = s[len(pref):].strip()
                # If nothing meaningful remains (sector-only subtotal row), drop it
                if not rest:
                    return ('', None)
                s = rest
                break
        return (s.strip(), None)

    # ICMB: "Non-Controlled/Non-Affiliated Investments Senior Secured First Lien Debt Investments {Industry} {Company}"
    # Also: "in non-controlled Affiliated Investments Techniplas Foreign Holdco LP" (lowercase variant)
    if ticker_upper == 'ICMB':
        # "in non-controlled Affiliated Investments {Company}" → strip prefix
        s = re.sub(r'^in\s+Non-Controlled\s+Affiliated\s+Investments\s+', '', s, flags=re.I)
        # Section headers: "Non-Controlled/Affiliated Investments" (with or without trailing type) → clear
        if re.match(r'^Non-Controlled/Affiliated\s+Investments\s*(?:Equity(?:\s+Warrants\s+and\s+Other\s+Investments)?|Senior\s+Secured\s+First\s+Lien\s+Debt\s+Investments)?\s*$', s, re.I):
            return ('', None)
        # "Non-Controlled/Affiliated Investments {type} {sector} {company}" → strip prefix + type
        s = re.sub(r'^Non-Controlled/Affiliated\s+Investments\s+(?:Equity\s+Warrants\s+and\s+Other\s+Investments\s+|Senior\s+Secured\s+First\s+Lien\s+Debt\s+Investments\s+)?', '', s, flags=re.I)
        # "Non-Controlled/Non-Affiliated Investments" section headers → clear
        if re.match(r'^Non-Controlled/Non-Affiliated\s+Investments\s*(?:Equity(?:\s+Warrants\s+and\s+Other\s+Investments)?|(?:Unsecured\s+)?Debt(?:\s+Investments)?|Senior\s+Secured\s+First\s+Lien\s+Debt\s+Investments)?\s*$', s, re.I):
            return ('', None)
        # "Non-Controlled/Non-Affiliated Investments Equity Warrants and Other Investments {sector} {company}" → strip prefix + type
        s = re.sub(r'^Non-Controlled/Non-Affiliated\s+Investments\s+Equity\s+Warrants\s+and\s+Other\s+Investments\s+', '', s, flags=re.I)
        # "Non-Controlled/Non-Affiliated Investments Debt {sector} {company}" → strip prefix + "Debt"
        s = re.sub(r'^Non-Controlled/Non-Affiliated\s+Investments\s+Debt\s+', '', s, flags=re.I)
        # Original: "Non-Controlled/Non-Affiliated Investments Senior Secured First Lien Debt Investments {sector} {company}"
        s = re.sub(r'^Non-Controlled/Non-Affiliated\s+Investments\s+Senior\s+Secured\s+First\s+Lien\s+Debt\s+Investments\s+', '', s, flags=re.I)
        # "Non-Controlled/Non-Affiliated Investments Unsecured Debt Investments {sector} {company}"
        s = re.sub(r'^Non-Controlled/Non-Affiliated\s+Investments\s+Unsecured\s+Debt\s+Investments\s+', '', s, flags=re.I)
        for phrase in _HRZN_SECTOR_PHRASES:
            pl = phrase.lower()
            if s.lower().startswith(pl):
                s = s[len(phrase):].strip()
                extracted_industry = phrase
                break
        # Also try GICS-style: "Commercial Services & Supplies ", "Containers & Packaging ", "Consumer Services ", etc.
        if not extracted_industry:
            for ind in ['Automobile Components', 'Commercial Services & Supplies', 'Containers & Packaging',
                        'Consumer Staples Distribution & Retail', 'Construction & Engineering', 'Consumer Services',
                        'Diversified Consumer Services', 'Electronic Equipment Instruments & Components',
                        'Entertainment', 'Food Products', 'Hotels Restaurants & Leisure', 'Household Durables',
                        'Insurance', 'Interactive Media & Services', 'IT Services', 'Professional Services']:
                if s.startswith(ind + ' '):
                    s = s[len(ind):].strip()
                    extracted_industry = 'Consumer & Retail' if ind in ('Consumer Services', 'Diversified Consumer Services') else ind
                    break
        return (s.strip(), extracted_industry)

    # LIEN: category rows that are not company names; "US Corporate Debt Senior Secured U.S. Notes {Industry} {Company} Facility Type..."
    # Also: "First Lien Senior Secured Canadian Debt Information Tulip.io Inc." → "Tulip.io Inc."
    if ticker_upper == 'LIEN':
        # Strip "First Lien Senior Secured Canadian/U.S. Debt" prefix when followed by sector+company
        s = re.sub(r'^First\s+Lien\s+Senior\s+Secured\s+(?:Canadian|U\.?S\.?)\s+Debt\s+', '', s, flags=re.I)
        # Clear section headers (bare or doubled): "First Lien Senior Secured Canadian Debt" or "First Lien Senior Secured U.S. Debt First Lien Senior Secured U.S. Debt"
        if re.match(r'^(?:First\s+Lien\s+Senior\s+Secured\s+(?:Canadian|U\.?S\.?)\s+Debt\s*)+$', s, re.I):
            return ('', None)
        # "Senior Secured U.S. Notes" section header; "First Lien Secured Canadian Debt" (without Senior)
        if re.match(r'^(?:Senior\s+Secured\s+U\.?S\.?\s+Notes|First\s+Lien\s+Secured\s+Canadian\s+Debt)\s*$', s, re.I):
            return ('', None)
        if re.match(r'^U\.?S\.?\s+Corporate\s+Debt\s*$', s, re.I):
            return ('', None)
        # "AYR Wellness Senior Secured Notes Due12/10/2024 Fixed Interest Rate 12.5%" → "AYR Wellness"
        s = re.sub(r'\s+Senior\s+Secured\s+Notes?\s+Due\S*.*$', '', s, flags=re.I).strip()
        # "Fluent Corp.Term Loan All in Rate 13.00%..." → "Fluent Corp." (note: may have no space)
        s = re.sub(r'(?:\s+)?Term\s+Loan\s+All\s+in\s+Rate\s+.*$', '', s, flags=re.I).strip()
        # "& Glass Delayed Draw Term Loan All in Rate..." → strip "Delayed Draw Term Loan..." suffix
        s = re.sub(r'\s+Delayed\s+Draw\s+Term\s+Loan\s+.*$', '', s, flags=re.I).strip()
        if re.match(r'^State\s+Street\s+.*Money\s+Market\s+Fund\s*$', s, re.I):
            return ('', None)
        # "US Corporate Debt First Lien Senior Secured U.S. Debt Retail Trade Portofino Labs" → "Portofino Labs"
        s = re.sub(r'^US\s+Corporate\s+Debt\s+First\s+Lien\s+Senior\s+Secured\s+U\.?S\.?\s+Debt\s+', '', s, flags=re.I)
        # "US Corporate Debt Senior Secured U.S. Notes Cannabis Ascend Wellness Holdings Facility Type..." → "Ascend Wellness Holdings"
        s = re.sub(r'^US\s+Corporate\s+Debt\s+Senior\s+Secured\s+U\.?S\.?\s+Notes\s+', '', s, flags=re.I)
        s = re.sub(r'\s+Facility\s+Type\s+.*$', '', s, flags=re.I)
        s = re.sub(r'\s+Initial\s+Acquisition\s+Date\s+.*$', '', s, flags=re.I)
        # "Ascend Wellness Senior Secured Note All in Rate 12.75%..." → "Ascend Wellness"
        s = re.sub(r'\s+Senior\s+Secured\s+Note\s+All\s+in\s+Rate\s+.*$', '', s, flags=re.I)
        for ind in ['Cannabis ', 'Finance and Insurance ', 'Materials ', 'Healthcare ', 'Retail Trade ', 'Real Estate and Rental and Leasing ', 'Information ']:
            if s.startswith(ind):
                s = s[len(ind):].strip()
                break
        # "Canadian Warrants Information Tulip.io Inc." → "Tulip.io Inc."
        s = re.sub(r'^Canadian\s+Warrants\s+Information\s+', '', s, flags=re.I)
        # "U.S. Warrants Retail Trade Portofino Labs" → "Portofino Labs"; "Total U.S. Warrants" → section header
        if re.match(r'^Total\s+U\.?S\.?\s+Warrants\s*$', s, re.I):
            return ('', None)
        s = re.sub(r'^U\.?S\.?\s+Warrants\s+', '', s, flags=re.I)
        for ind in ['Retail Trade ', 'Real Estate and Rental and Leasing ', 'Information ']:
            if s.startswith(ind):
                s = s[len(ind):].strip()
                break
        return (s.strip(), None)

    # CCAP: "VetStrategy Investment Type Common Stock", "United States Debt Investments Materials Online Labels Group LLC", "Equity Investments Consumer Services Legalshield Investment Type Common Stock"
    # Also: "Company APC Bidco LimitedInvestment Type Delayed Draw Term Loan ..." (no space before Investment Type)
    if ticker_upper == 'CCAP':
        # "Investment Type ..." with no real company → clear
        if re.match(r'^Investment\s+Type\s+', s, re.I):
            return ('', None)
        # "United States Debt Investments {Industry} {Company}" or "Equity Investments {Industry} {Company}"
        s = re.sub(r'^United\s+States\s+Debt\s+Investments\s+', '', s, flags=re.I)
        s = re.sub(r'^Equity\s+Investments\s+', '', s, flags=re.I)
        # "Company {CompanyName}" (XBRL dimension prefix "Company ") → strip leading "Company "
        s = re.sub(r'^Company\s+(?=[A-Z])', '', s)
        # Strip trailing " Investment Type ..." (Common Stock, Preferred Stock, Unitranche First Lien Term Loan..., Class A, etc.)
        # Use no leading-space requirement to catch "LimitedInvestment Type" concatenations
        s = re.sub(r'Investment\s+Type\s+.*$', '', s, flags=re.I)
        # Strip leading "& Components " (leftover when "Automobile" was stripped elsewhere) so "& Components Auveco Holdings" → "Auveco Holdings"
        if s.startswith('& Components '):
            s = s[len('& Components '):].strip()
        # Strip leading industry (GICS-style) so "Materials Online Labels Group LLC" → "Online Labels Group LLC"; "Pharmaceuticals Biotechnology & Life Sciences LSCS Holdings" → "LSCS Holdings"
        _ccap_industries = ['Pharmaceuticals Biotechnology & Life Sciences ', 'Biotechnology & Life Sciences ', 'Automobiles & Components ', 'Automobile & Components ', 'Commercial & Professional ', 'Food Beverage & Tobacco ', 'Materials ', 'Semiconductor and Semiconductor Equipment ', 'Technology Hardware & Equipment ', 'Consumer Services ', 'Health Care Equipment & Services ', 'Software & Services ']
        for ind in _ccap_industries:
            if s.startswith(ind):
                s = s[len(ind):].strip()
                extracted_industry = ind.strip()
                break
        # Section header only (industry with no company) → empty
        _ccap_section_only = ('Automobile & Components', 'Commercial & Professional', 'Food Beverage & Tobacco', 'Materials', 'Semiconductor and Semiconductor Equipment', 'Technology Hardware & Equipment', 'Consumer Services', 'Health Care Equipment & Services', 'Software & Services', 'United States Debt Investments', 'Equity Investments', 'Biotechnology & Life Sciences')
        if not s or s.strip() in _ccap_section_only:
            return ('', None)
        # Debt type section headers (no company name): "Senior Secured First Lien", "First Lien Term Loan", "Unitranche First Lien", etc.
        if re.match(
            r'^(?:Unitranche(?:\s+First)?\s+Lien|(?:Senior\s+Secured\s+)?(?:First|Second)\s+Lien)(?:\s+(?:Term\s+Loan|Revolver|Senior\s+Secured))?\s*(?:Maturity/Dissolution\s+Date\s+.*)?$',
            s, re.I,
        ):
            return ('', None)
        # Prefix leak from section labels: "Services <Company>" -> "<Company>"
        s = re.sub(r'^Services\s+', '', s, flags=re.I).strip()
        return (s.strip(), extracted_industry)

    # GECC: "Universal Fiber Systems Industry Chemicals Security Common Equity Initial Acquisition Date 10/16/2024 - 1" or "Industry Chemicals Security 1st Lien"
    # Also: "Advancion 1500 E Lake Cook Rd Buffalo Grove IL 60089 Chemicals Security 2nd Lien Secured Loan Interest Rate ..."
    if ticker_upper == 'GECC':
        # Non-company pseudo-rows that leak from GECC schedules:
        # - "Interest rate floor of 0.50%"
        # - "SOFR", "One-month SOFR", "Three-month SOFR", "Six-month SOFR", "Prime"
        # - "Ruby Tuesday warrants", "Vivos warrants"
        # - short-term investments / money-market line items
        if re.match(r'^Interest\s+rate\s+floor\s+of\s+[\d.]+%\s*$', s, re.I):
            return ('', None)
        if re.match(r'^(?:One-month|Three-month|Six-month)\s+SOFR\s*$', s, re.I):
            return ('', None)
        if re.match(r'^(?:SOFR|Prime)\s*$', s, re.I):
            return ('', None)
        if re.search(r'\b(?:warrants?)\s*$', s, re.I):
            return ('', None)
        if re.search(r'(?:short[\s\-]?term\s+investments?|money\s+market)', s, re.I):
            return ('', None)
        # Strip street address and everything after: " 1500 E Lake Cook Rd ..." → strip from "\d{3,6} [A-Z]" (street number + capital)
        s = re.sub(r'\s+\d{3,6}\s+[A-Z].*$', '', s)
        # Strip from " Security {type}" onward (security type is not the company name)
        s = re.sub(r'\s+Security\s+(?:1st|2nd|Senior|Sub|Unsecured|CLO|Unsec)\b.*$', '', s, flags=re.I)
        # Strip trailing " Initial Acquisition Date ..." suffix
        s = re.sub(r'\s+Initial\s+Acquisition\s+Date\s+.*$', '', s, flags=re.I)
        # Strip trailing " Industry ..." suffix
        s = re.sub(r'\s+Industry\s+.*$', '', s, flags=re.I)
        return (s.strip(), None)

    # PNNT / PFLT: "in Non-Controlled Non-Affiliated Portfolio Companies - X% First Lien Secured Debt - Y% Issuer Name CompanyName"
    # or "in Non-Controlled Non-Affiliated Portfolio Companies First Lien Secured Debt Issuer Name Route 66 Development Acquisition 01/28/2025" (no %)
    # or "in Non-Controlled ... Common Equity/Warrants CompanyName - Common Equity Acquisition ... Industry X"
    # or "First Lien Secured Debt Issuer Name CompanyName" (standalone, no prefix)
    # or "Equity Securities Issuer Name CompanyName" (standalone)
    # or "Related Party PSSL First/Equity ... Issuer Name CompanyName ..."
    if ticker_upper in ('PNNT', 'PFLT'):
        # Strip leading `"` (CSV escaping artifact that sometimes appears in XBRL strings)
        s = s.lstrip('"')
        # Garbled strings: Unicode replacement char (\ufffd) = corrupted XBRL encoding → clear
        if '\ufffd' in s:
            return ('', None)
        # "in Non-Control Non-Affiliate Portfolio Companies..." (abbreviated older-format variant) → clear
        if re.match(r'^in\s+Non-Control\s+Non-Affiliate\b', s, re.I):
            return ('', None)
        # "n-Controlled Non-Affiliated..." (truncated, missing leading "i") → clear
        if re.match(r'^n-Controlled\b', s, re.I):
            return ('', None)
        # "Non-Affiliated Portfolio Companies..." (missing "in Non-Controlled" prefix) → clear
        if re.match(r'^Non-Affiliated\s+Portfolio\s+Companies', s, re.I):
            return ('', None)
        # "Industry Air FInvestments in Non-Controlled..." (garbled industry+investment prefix) → clear
        if re.search(r'FInvestments?\s+in\s+Non-Controlled', s, re.I):
            return ('', None)
        # No company: "- Unfunded Term Loan Acquisition 08/15/2025" or "- Common Equity Acquisition 10/29/2024 Industry Insurance"
        if re.match(r'^-\s*(?:Unfunded\s+Term\s+Loan\s+Acquisition|Common\s+Equity\s+Acquisition)\s+', s, re.I):
            return ('', None)
        # "Related Party PSLF/PSSL Cash and Cash Equivalents - X% Issuer Name BlackRock..." → money market, not portfolio company → clear
        if re.match(r'^Related\s+Party\s+(?:PSLF|PSSL)\s+Cash\s+and\s+Cash\s+Equivalents', s, re.I):
            return ('', None)
        # "Related Party First Lien..." (section header, not a portfolio company) → strip prefix and handle below
        s = re.sub(r'^Related\s+Party\s+(?!(?:PSSL|PSLF)\s)', '', s, flags=re.I).strip()
        # Strip "Related Party PSSL/PSLF " prefix (some rows have it at start)
        s = re.sub(r'^Related\s+Party\s+(?:PSSL|PSLF)\s+', '', s, flags=re.I)
        # Strip "Related Party PSSL/PSLF " embedded in middle (e.g. "First Lien Secured Debt - 770.3% Related Party PSLF Issuer Name NBH Group LLC")
        s = re.sub(r'\s+Related\s+Party\s+(?:PSSL|PSLF)\s+', ' ', s, flags=re.I).strip()
        # Bare "Issuer Name CompanyName" (no type prefix, from partially-processed rows)
        s = re.sub(r'^Issuer\s+Name\s+', '', s, flags=re.I)
        # "First/Second/Subordinated? Lien Secured Debt [- X%] [Issuer] Issuer Name {Company}"
        # The middle part (dash, %, numbers) is flexible: use [\s\-\d%,.]* to cover all variants:
        # "- 1 347.5%-", "1707.4% ", "- 1464.6 " (no %), "- " (no number), "- 1296.4% Issuer " (doubled)
        # Also: "Issuer NameCompany" (no space, no-space variant) and "Issuer VRS Buyer" (Issuer without Name)
        m = re.match(
            r'^(?:First|Second|Subordinated?)\s+(?:Lien\s+)?Secured\s+Debt[\s\-\d%,.]*(?:Issuer\s+)?Issuer\s+Name\s*(.+)$',
            s, re.I,
        )
        if m:
            s = m.group(1).strip()
        # "First Lien Secured Debt - X%ssuer Name {Company}" (garbled, missing "I") → extract company
        m_garbled = re.match(
            r'^(?:First|Second|Subordinated?)\s+(?:Lien\s+)?Secured\s+Debt[\s\-\d%,.]*%ssuer\s+Name\s+(.+)$',
            s, re.I,
        )
        if m_garbled:
            s = m_garbled.group(1).strip()
        # "First Lien Secured Debt Issuer {Company}" (missing "Name") → extract company
        m_no_name = re.match(
            r'^(?:First|Second|Subordinated?)\s+(?:Lien\s+)?Secured\s+Debt[\s\-\d%,.]*Issuer\s+([A-Z].+)$',
            s, re.I,
        )
        if m_no_name:
            s = m_no_name.group(1).strip()
        # Bare "Secured Debt [- X%] Issuer Name" (no First/Second prefix, from partially-processed rows)
        m2 = re.match(
            r'^Secured\s+Debt[\s\-\d%,.]*(?:Issuer\s+)?Issuer\s+Name\s+(.+)$',
            s, re.I,
        )
        if m2:
            s = m2.group(1).strip()
        # Bare section header: "First Lien Secured Debt - 1" or "First Lien Secured Debt 1707.4%%" or "... Total" (no Issuer Name) → clear
        if re.match(r'^(?:First|Second|Subordinated?)\s+(?:Lien\s+)?Secured\s+Debt\s*(?:[-–—]\s*)?[\d\s,.]+\s*%*(?:\s+Total)?\s*$', s, re.I):
            return ('', None)
        # "First/Second/Subordinated? Lien Secured? Debt Issuer Name {Company}" (no percentage; "Subordinate/Subordinated Debt" may have no "Secured")
        s = re.sub(r'^(?:First|Second|Subordinated?)\s+(?:Lien\s+)?(?:Secured\s+)?Debt(?:/Corporate\s+Notes)?\s+Issuer\s+Name\s+', '', s, flags=re.I)
        # Bare "Debt - X%- Issuer Name CompanyName" (when First/Second prefix was already stripped in prior pass)
        s = re.sub(r'^Debt\s*[-–—]\s*[\d\s,.]+\s*%[-\s]+Issuer\s+Name\s+', '', s, flags=re.I)
        # "Equity Securities/Security - X% Issuer Name {Company}" or "Equity Security - X% Issuer Name {Company}"
        s = re.sub(r'^Equity\s+Securit(?:ies|y)\s*[-–—]\s*[\d\s,.]+\s*%\s*[-–—]?\s*Issuer\s+Name\s+', '', s, flags=re.I)
        # "Equity Securities/Security - X%" section header (any format) → clear
        if re.match(r'^Equity\s+Securit(?:ies|y)\s*[-–—]', s, re.I):
            return ('', None)
        # "Equity Securities/Security Issuer Name {Company}" (no percentage)
        s = re.sub(r'^Equity\s+Securit(?:ies|y)\s+Issuer\s+Name\s+', '', s, flags=re.I)
        # "in Non-Controlled Non-Affiliated Portfolio Companies Common Equity/Warrants AG Investco - Common Equity Acquisition 11/5/2018 Industry Software"
        s = re.sub(r'^in\s+Non-Controlled\s+Non-Affiliated\s+Portfolio\s+Companies\s+Common\s+Equity/Warrants\s+', '', s, flags=re.I)
        # Catch-all: any "in [Non-]Controlled... Issuer Name {Company}" prefix → extract company
        # Handles: Non-Affiliated, Affiliated, and Controlled Affiliated variants; double-I typo; all debt/equity types
        catch = re.match(
            r'^(?:IInvestments?\s+in\s+|in\s+)(?:Non-)?Controlled\s+\S[\S\s]+?\s+Issuer\s+Name\s+(.+)$',
            s, re.I,
        )
        if catch:
            s = catch.group(1).strip()
        else:
            # Percentage variant: "in ... - X% First Lien Secured Debt - Y% Issuer Name CompanyName"
            m = re.match(
                r'^in\s+Non-Controlled\s+Non-Affiliated\s+Portfolio\s+Companies\s+-\s*[\d.]+\s*%\s+First\s+Lien\s+Secured\s+Debt\s+-\s*[\d.]+\s*%\s+(?:Issuer\s+Name\s+)?(.+)$',
                s, re.I,
            )
            if m:
                s = m.group(1).strip()
            elif re.match(r'^(?:IInvestments?\s+in\s+|in\s+)(?:Non-)?Controlled\s+', s, re.I):
                # Check for "in Non-Controlled Non-Affiliated Portfolio Companies {DebtType} {CompanyName}"
                # (no "Issuer Name" - used in some PFLT/PNNT XBRL member format)
                no_issuer_m = re.match(
                    r'^(?:\"?Investments?\s+in\s+|in\s+)Non-Controlled\s+Non-Affiliated\s+Portfolio\s+Companies\s+'
                    r'(?:First|Second|Subordinated?)\s+(?:Lien\s+)?(?:Secured\s+)?Debt\s+'
                    r'(.+)$',
                    s, re.I,
                )
                if no_issuer_m:
                    s = no_issuer_m.group(1).strip()
                else:
                    # Still starts with "in [Non-]Controlled..." but no company name found → section header → clear
                    return ('', None)
            else:
                # No-percentage variant: "in Non-Controlled Non-Affiliated Portfolio Companies First Lien Secured Debt Issuer Name CompanyName"
                s = re.sub(
                    r'^in\s+Non-Controlled\s+Non-Affiliated\s+Portfolio\s+Companies\s+(?:First|Second)\s+Lien\s+Secured\s+Debt\s+Issuer\s+Name\s+',
                    '', s, flags=re.I,
                ).strip()
                # Trailing acquisition date e.g. " Route 66 Development Acquisition 01/28/2025" → "Route 66 Development Acquisition"
                s = re.sub(r'\s+\d{1,2}/\d{1,2}/\d{2,4}\s*$', '', s).strip()
        # Strip trailing " - Common Equity Acquisition M/D/YYYY Industry X" or " - Unfunded Common Equity Acquisition ..."
        # Note: allow no-space before Industry ("2024Industry") with \s*
        s = re.sub(r'\s*[-–—]\s*(?:Unfunded\s+)?Common\s+Equity\s+Acquisition\s+\d{1,2}/\d{1,2}/\d{2,4}\s*Industry\s+\S+.*$', '', s, flags=re.I)
        # Strip trailing " - Common Equity" or " - Preferred Equity" artifact
        s = re.sub(r'\s*[-–—]\s*(?:Common|Preferred)\s+Equity\s*$', '', s, flags=re.I)
        # Strip trailing " Industry X" (sector leak after company name)
        s = re.sub(r'\s+Industry\s+\S[\S\s]*$', '', s, flags=re.I).strip()
        # Strip trailing " Maturity M/D/YYYY ..." (some rows have maturity after company)
        s = re.sub(r'\s+Maturity\s+\d{1,2}/\d{1,2}/\d{2,4}\b.*$', '', s, flags=re.I).strip()
        # "Cartessa Aesthetics LLC-Revolver- Maturity..." → company only
        s = re.sub(r'[-–—]\s*Revolver\s*[-–—].*$', '', s, flags=re.I)
        # "Morse Defense Maturity 06/23/2028 Industry Aerospace and Defense Current Coupon 8.8%..." → "Morse Defense" (before rate-only check)
        s = re.sub(r'\s+Maturity\s+\d{1,2}/\d{1,2}/\d{4}\s+Industry\s+.*$', '', s, flags=re.I)
        # ": Diversified and Production Current Coupon 14.06%..." or "[Industry] Current Coupon ..." = rate text (no company)
        if re.match(r'^:\s*', s) or re.search(r'Current\s+Coupon\s+[\d.]+%?\s*(?:\(PIK\s+[\d.]+%\))?\s*Basis\s+Point\s+Spread', s, re.I):
            return ('', None)
        # "Current Coupon {num}%" or "Current Coupon {num} Basis Point..." (% optional for older filings)
        if re.match(r'^(?:[\w\s&]+\s)?Current\s+Coupon\s*(?:PIK\s+)?[\d.]+\s*%?(?:\s|$)', s, re.I):
            return ('', None)
        # "Current {X}upon..." = garbled "Current Coupon" where company name embedded in "Coupon" → clear
        if re.match(r'^(?:[\w\s&]+\s)?Current\s+\w+upon\b', s, re.I):
            return ('', None)
        # Bare "Current Coupon" alone (rate info, no company)
        if re.match(r'^Current\s+Coupon\s*$', s, re.I):
            return ('', None)
        # PFLT industry-category names leaked as company names (equity SOI subtotals)
        _pflt_industry_cats = {
            'providers and services', 'equipment and supplies', 'education and childcare',
            'equipment and services', 'plastics and rubber', 'chemicals', 'chemicals and materials',
        }
        if s.lower() in _pflt_industry_cats:
            return ('', None)
        # Artifact: closing paren without matching open paren = truncated/artifact → strip paren
        if s.endswith(')') and '(' not in s:
            s = s.rstrip(')').strip()
        # "Acquisition 08/15/2025" only (no company name) → clear (also bare "Acquisition" after date stripped)
        if re.match(r'^Acquisition(?:\s+\d{1,2}/\d{1,2}/\d{2,4})?\s*$', s, re.I):
            return ('', None)
        # Bare "LLC" or "Inc." only = fragment from over-stripping; clear
        if s.strip() in ('LLC', 'Inc.', 'Corp.', 'Ltd.', 'LP'):
            return ('', None)
        # Bare "Technology" (sector leak, not company name)
        if s.strip().lower() == 'technology':
            return ('', None)
        return (s.strip(), None)

    # PFX: "Non-Controlled/Non-Affiliated Investments - Advocates for Disabled Vets"; "Affiliated Investments - MB Precision..."; "Altisource S.A.R.L. - Services: Business - Equity"
    if ticker_upper == 'PFX':
        s = re.sub(r'^Non-Controlled/Non-Affiliated\s+Investments\s*[-–—]\s*', '', s, flags=re.I)
        if re.match(r'^Non-Controlled', s, re.I):
            s = re.sub(r'^Non-Controlled[^\-]*[-–—]\s*', '', s, flags=re.I)
        # "Affiliated Investments - FST Holdings" → "FST Holdings"; "Affiliated Investments" only → section header
        if re.match(r'^Affiliated\s+Investments\s*$', s, re.I):
            return ('', None)
        s = re.sub(r'^Affiliated\s+Investments\s*[-–—]\s*', '', s, flags=re.I)
        # "Controlled Investments - ECC Capital Corp. - Real Estate - Equity" → "ECC Capital Corp."; "Controlled Investments" only → section header
        if re.match(r'^Controlled\s+Investments\s*$', s, re.I):
            return ('', None)
        if re.match(r'^Controlled\s+Investments\s*[-–—]\s*Subtotal', s, re.I):
            return ('', None)
        s = re.sub(r'^Controlled\s+Investments\s*[-–—]\s*', '', s, flags=re.I)
        # "Altisource S.A.R.L. - Services: Business - Equity" or " - Services: Business - Senior Secured..." → company only
        s = re.sub(r'\s*[-–—]\s*Services:\s*Business(?:\s*[-–—].*)?$', '', s, flags=re.I).strip()
        # "Altisource S.A.R.L - Business - Senior Secured..." or "Altisource S.A.R.L - Business" / "S.A.R.L-Business" → company only
        s = re.sub(r'\s*[-–—]\s*Business(?:\s*[-–—].*)?$', '', s, flags=re.I).strip()
        if ' - Real Estate - ' in s:
            s = s.split(' - Real Estate - ')[0].strip()
        # Trailing " - Real Estate" (e.g. "PREIT Associates - Real Estate")
        s = re.sub(r'\s*[-–—]\s*Real\s+Estate\s*$', '', s, flags=re.I).strip()
        if ' - Construction & Building - ' in s:
            s = s.split(' - Construction & Building - ')[0].strip()
        return (s.strip(), None)

    # RWAY: "Non-Control/Non-Affiliate Investments Debt Investments Application Software Airship Group" → "Airship Group"
    # Also: "Affiliate Investments Debt Investments Healthcare Technology Gynesonics Inc." → "Gynesonics Inc."
    if ticker_upper == 'RWAY':
        # "Affiliate Investments" alone or "Affiliate Investments Debt/Equity Investments" alone → section header
        if re.match(r'^Affiliate\s+Investments\s*(?:(?:Debt|Equity)\s+[Ii]nvestments|Warrants?)?\s*$', s, re.I):
            return ('', None)
        # "Affiliate Investments Debt/Equity/Warrants Investments {sector} {company}" → strip prefix
        s = re.sub(r'^Affiliate\s+Investments\s+(?:(?:Debt|Equity)\s+[Ii]nvestments|Warrants?)\s+', '', s, flags=re.I)
        s = re.sub(r'^Non-\s*Control/?\s*/\s*Non-\s*Affiliate\s+Investments\s+Debt\s+Investments\s+', '', s, flags=re.I)
        s = re.sub(r'^Non-Control/Non-Affiliate\s+Investments\s+Debt\s+Investments\s+', '', s, flags=re.I)
        s = re.sub(r'^Non-Control/Non-Affiliate\s+Investments\s+Equity\s+Investments\s+', '', s, flags=re.I)
        for phrase in _HRZN_SECTOR_PHRASES:
            pl = phrase.lower()
            if s.lower().startswith(pl):
                s = s[len(phrase):].strip()
                extracted_industry = phrase
                break
        # "Data Processing & Outsourced Services", "Electronic Equipment & Instruments", etc.
        if not extracted_industry:
            for ind in ['Data Processing & Outsourced Services', 'Electronic Equipment & Instruments', 'Healthcare Equipment',
                        'Healthcare Technology', 'Health Care Technology', 'Application Software',
                        'Internet & Direct Marketing Retail', 'Internet Software and Services', 'Human Resource & Employment Services',
                        'Equipment']:
                if s.startswith(ind + ' '):
                    s = s[len(ind):].strip()
                    extracted_industry = ind
                    break
        # "Hurricane Cleanco Limited Investment Type Senior Secured..." → "Hurricane Cleanco Limited"
        # Also catches no-space variant "LimitedInvestment Type..." from XBRL concatenation
        s = re.sub(r'Investment\s+Type\s+.*$', '', s, flags=re.I).strip()
        # Strip leading "Senior Secured " prefix left after investment type stripping
        s = re.sub(r'^Senior\s+Secured\s+', '', s, flags=re.I).strip()
        # Industry-only (no real company name) → clear
        if s.strip().lower() in ('application software', 'data processing & outsourced services', 'equipment',
                                  'electronic equipment & instruments', 'internet & direct marketing retail',
                                  'internet software and services', 'human resource & employment services'):
            return ('', None)
        return (s.strip(), extracted_industry)

    # TCPC: "Non-Controlled Affiliates Hylan Intermediate Holdings II LLC" → "Hylan Intermediate Holdings II LLC"; "Controlled Affiliates AutoAlert LLC" → "AutoAlert LLC"; "Debt Investments ..." / "Equity Securities ..."
    if ticker_upper == 'TCPC':
        s = re.sub(r'^Non-Controlled\s+Affiliates\s+', '', s, flags=re.I)
        s = re.sub(r'^Controlled\s+Affiliates\s+', '', s, flags=re.I)
        s = re.sub(r'^Debt\s+Investments\s+', '', s, flags=re.I)
        # "Equity Securities {sector} {Company}" (equity section has sector in name)
        _tcpc_equity_sectors = [
            ('Internet Software and Services', 'Software & Technology'),
            ('Internet Software and Service', 'Software & Technology'),  # typo variant
            ('Healthcare Providers and Services', 'Healthcare'),
            ('Professional Services', 'Business Services'),
            ('Software', 'Software & Technology'),
        ]
        for phrase, ind in _tcpc_equity_sectors:
            prefix = 'Equity Securities ' + phrase + ' '
            if s.startswith(prefix):
                s = s[len(prefix):].strip()
                extracted_industry = ind
                break
        if not extracted_industry:
            s = re.sub(r'^Equity\s+Securities\s+', '', s, flags=re.I)
        for phrase in _HRZN_SECTOR_PHRASES:
            pl = phrase.lower()
            if s.lower().startswith(pl):
                s = s[len(phrase):].strip()
                extracted_industry = phrase
                break
        # GICS / section prefixes (Professional Service(s) → Business Services for industry)
        _tcpc_ind_map = {
            'Professional Services ': 'Business Services', 'Professional Service ': 'Business Services',
            'Consumer Services ': 'Consumer & Retail', 'Household Durables ': 'Consumer & Retail',
            'Wireless Telecommunication Services ': 'Telecommunications',
            'Internet Software and Services ': 'Software & Technology', 'Pharmaceuticals ': 'Healthcare',
        }
        for ind, canon in _tcpc_ind_map.items():
            if s.startswith(ind):
                s = s[len(ind):].strip()
                extracted_industry = canon
                break
        if not extracted_industry:
            for ind in ['Aerospace & Defense', 'Automobiles', 'Building Products', 'Capital Markets', 'Commercial Services & Supplies',
                        'Communications Equipment', 'Electric Utilities', 'Electronic Equipment', 'Healthcare', 'Software', 'Telecommunications']:
                if s.startswith(ind + ' '):
                    s = s[len(ind):].strip()
                    extracted_industry = 'Business Services' if ind.startswith('Professional Service') else ind.strip()
                    break
                if s.strip() == ind:
                    s = ''
                    extracted_industry = ind.strip()
                    break
        # Strip trailing loan term details: "Company Name Subordinated E1 Term Loan Ref LIBOR Spread 12.50% ..."
        s = re.sub(r'\s+(?:Subordinated|Senior\s+Secured|First\s+Lien|Second\s+Lien|Sr\s+Secured)\s+[A-Z]?\d?\s*(?:Term\s+Loan|Revolver|Note)\s+Ref\b.*$', '', s, flags=re.I).strip()
        # Strip when loan type appears after a closing paren: ") Sr Secured Revolver Ref ..."
        s = re.sub(r'\s+(?:Sr\s+Secured|Senior\s+Secured|First\s+Lien)\s+(?:Revolver|Term\s+Loan)\s+Ref\b.*$', '', s, flags=re.I).strip()
        # Also strip when "Term Loan Ref" or "Credit Facility Ref" or "Bank Guarantee" appears
        s = re.sub(r'\s+(?:Term\s+Loan\s+Ref|Credit\s+Facility\s+Ref|Bank\s+Guarantee)\s+.*$', '', s, flags=re.I).strip()
        # Prefix leak from section labels: "Services <Company>" -> "<Company>"
        s = re.sub(r'^Services\s+', '', s, flags=re.I).strip()
        return (s.strip(), extracted_industry)

    # TSLX: rate-only; "Other Investments Ares CLO Ltd."; "Equity and Other Investments Business Services ReliaQuest"; "Debt Investments Automotive" (section-only → ''); "Pharmaceuticals TherapeuticsMD"
    # Also: "Consumer Products Rapid Data GmbH Initial Acquisition Date 7/11/2023 Reference Rate..."
    if ticker_upper == 'TSLX':
        # Broader rate-only match: "Spread S + ...", "Spread 0.08 Interest Rate ...", etc.
        if re.match(r'^Spread\s+', s, re.I):
            return ('', None)  # rate-only dimension, no company name
        if re.match(r'^Equity\s+and\s+Other\s+Investments\s*$', s, re.I):
            return ('', None)  # section header, no company
        s = re.sub(r'^Other\s+Investments\s+', '', s, flags=re.I)
        s = re.sub(r'^Equity\s+and\s+Other\s+Investments\s+', '', s, flags=re.I)
        s = re.sub(r'^Debt\s+Investments\s+', '', s, flags=re.I)
        # Trailing " Initial Acquisition Date M/D/YYYY Reference Rate..." (catches most bad TSLX names)
        s = re.sub(r'\s+Initial\s+Acquisition\s+Date\s+\d{1,2}/\d{1,2}/\d{4}\b.*$', '', s, flags=re.I).strip()
        # Trailing " First-lien loan (...) " / " Subordinated note (...) " = instrument description, not company
        s = re.sub(r'\s+(?:First-lien|Second-lien|Subordinated)\s+(?:loan|note)\s+\(.*$', '', s, flags=re.I).strip()
        # Section-only: "Automotive", "Business Services", "Chemicals", "Communications", "Education", "Financial Services", "Healthcare", "Human Resource Support Services", "Internet Services", "Manufacturing", "Other", "Pharmaceuticals", "Transportation"
        _tslx_section_only = frozenset(
            'automotive business services chemicals communications education financial services healthcare '
            'human resource support services internet services manufacturing other pharmaceuticals transportation '
            'hotel gaming and leisure hotel gaming leisure consumer products leisure'.split()
        )
        if s.strip().lower() in _tslx_section_only:
            return ('', None)
        if s.startswith('Human Resource Support Services '):
            s = s[len('Human Resource Support Services '):].strip()
            if not extracted_industry:
                extracted_industry = 'Business Services'
        if s.startswith('Pharmaceuticals '):
            s = s[len('Pharmaceuticals '):].strip()
            extracted_industry = 'Healthcare'
        if s.startswith('Internet Services '):
            s = s[len('Internet Services '):].strip()
            if not extracted_industry:
                extracted_industry = 'Software & Technology'
        # Additional TSLX sector prefixes not in _HRZN_SECTOR_PHRASES
        for sect, ind in [('Consumer Products ', 'Consumer & Retail'), ('Manufacturing ', 'Industrials'), ('Leisure ', 'Hospitality & Leisure')]:
            if s.startswith(sect):
                s = s[len(sect):].strip()
                if not extracted_industry:
                    extracted_industry = ind
                break
        # Trailing " Convertible Preference Shares (N Shares) Initial Acquisition Date M/D/YYYY"
        s = re.sub(r'\s+Convertible\s+Preference\s+Shares\s+\(\d+\s+Shares\)\s+Initial\s+Acquisition\s+Date\s+\d{1,2}/\d{1,2}/\d{2,4}\s*$', '', s, flags=re.I)
        for phrase in _HRZN_SECTOR_PHRASES:
            pl = phrase.lower()
            if s.lower().startswith(pl):
                s = s[len(phrase):].strip()
                if not extracted_industry:
                    extracted_industry = phrase
                break
        return (s.strip(), extracted_industry)

    # PSBD: "Debt Investments First Lien Senior Secured Acrisure" → "Acrisure"; "Packaging Interest Rate 10.26% (S +CSA + 6.00%) Maturity Date 3/31/2028 One" = rate/maturity dimension row, no company
    if ticker_upper == 'PSBD':
        if re.match(r'^.+\s+Interest\s+Rate\s+[\d.]+\s*%.*Maturity\s+Date\s+', s, re.I):
            return ('', None)
        s = re.sub(r'^Debt\s+Investments\s+(?:First\s+Lien\s+)?(?:Senior\s+Secured\s+)?(?:Second\s+Lien\s+)?(?:Subordinated\s+)?', '', s, flags=re.I).strip()
        return (s, None)

    # CGBD: "Investment Non-Affiliated Issuer First Lien Debt ACR Group Borrower LLC" → "ACR Group Borrower LLC"
    # "Credit Fund First Lien Debt ACR Group Borrower LLC" → "ACR Group Borrower LLC"
    # "Investment Affiliated Issuer Investment Funds Middle Market Credit Fund LLC" → "Middle Market Credit Fund LLC"
    # "Investment Non-Affiliated Issuer" / "Credit Fund" (section-only) → ""
    if ticker_upper == 'CGBD':
        # Some CGBD labels come as pipe-delimited hierarchy tokens.
        # Example: "| Affiliated Issuer | Investment Funds | Middle Market Credit Fund LLC"
        s = re.sub(r'^\|\s*(?:Investment\s+)?(?:Non-Affiliated|Affiliated)(?:\s+Issuer)?\s*\|\s*', '', s, flags=re.I)
        s = re.sub(r'^\|\s*', '', s, flags=re.I)
        s = re.sub(r'^(?:Investment\s+)?(?:Non-Affiliated|Affiliated)(?:\s+Issuer)?\s*', '', s, flags=re.I)
        s = re.sub(r'^\|\s*', '', s, flags=re.I)
        s = re.sub(r'^(?:Investment\s+(?:Non-Affiliated|Affiliated)\s+Issuer|Credit\s+Fund)\s*', '', s, flags=re.I)
        s = re.sub(
            r'^(?:First\s+Lien\s+Debt|Second\s+Lien\s+Debt|First\s+and\s+Second\s+Lien\s+Debt|Equity\s+Investments|Investment\s+Funds)\s*\|\s*',
            '',
            s,
            flags=re.I,
        )
        s = re.sub(
            r'^(?:First\s+Lien\s+Debt|Second\s+Lien\s+Debt|First\s+and\s+Second\s+Lien\s+Debt|Equity\s+Investments|Investment\s+Funds)\s*',
            '',
            s,
            flags=re.I,
        ).strip()
        # Standalone "First Lien" / "Second Lien" prefix remaining (e.g. "First Lien Direct Travel Inc.")
        s = re.sub(r'^(?:First|Second)\s+Lien\s+(?:Senior\s+Secured\s+)?', '', s, flags=re.I).strip()
        # Normalize long affiliated fund legal name to concise display label.
        if re.match(r'^Middle\s+Market\s+Credit\s+Fund\s+LLC\.?$', s, re.I):
            s = 'Middle Market'
        # Bare "Second Lien" or "First Lien" (nothing left after stripping) → section header → clear
        if re.match(r'^(?:First|Second)\s+Lien\s*$', s, re.I):
            return ('', None)
        return (s, None)

    # GSBD: "<pct>% <Geography> - <pct>% <InvType> - <pct>% <Company> [Industry <X>]" → company only
    # Also: "Equity Securities - <pct>% <Geography> - <pct>% <InvType> - <pct>% <Company>"
    # "Non-Controlled Affiliates Pluralsight Inc." → "Pluralsight Inc."
    if ticker_upper == 'GSBD':
        # Strip leading type + percent wrappers that leak from XBRL dimension labels:
        # "Unsecured Debt - 1.60% CivicPlus LLC", "Common Stock - 0.01% Prairie ..."
        s = re.sub(
            r'^\s*(?:Unsecured\s+Debt|Common\s+Stock|Preferred\s+Stock|Debt\s+Investments?|Equity\s+Investments?)\s*[-–—]\s*[\d.]+\s*%\s*',
            '',
            s,
            flags=re.I,
        ).strip()

        # Dimension-only rows → blank
        if re.match(r'^\d+(?:\.\d+)?%$', s.strip()):
            return ('', None)
        if re.match(r'^-\s*\d+(?:\.\d+)?%$', s.strip()):
            return ('', None)
        if re.match(r'^(?:Initial\s+Acquisition\s+Date|Maturity)\s+\d', s, re.I):
            return ('', None)
        if re.match(r'^(?:Foreign\s+Currency|Interest\s+Rate|Total\s+Liabilities)', s, re.I):
            return ('', None)
        if re.match(r'^(?:Debt\s+Investments?|Equity\s+Investments?|Total\b)', s, re.I):
            return ('', None)
        # 2-segment percentage section headers ("206.87% Canada - 7.61%") → blank (no company)
        if re.match(r'^\d+(?:\.\d+)?%\s+\S[\w\s]*\s*[-–—]\s*[\d.]+%\s*$', s.strip()):
            return ('', None)
        # "Equity Securities - X% ..." section headers (any number of segments, no real company) → blank
        # These are portfolio composition breakdowns, not company names
        if re.match(r'^Equity\s+Securities\s*[-–—]', s, re.I):
            return ('', None)
        # "<pct>% <Geography> [-–—] <pct>% <InvType> [-–—] <pct>% <Company> [Industry <X>]"
        s = re.sub(r'^\d+(?:\.\d+)?%\s+.+?\s*[-–—]\s*[\d.]+\s*%\s+.+?\s*[-–—]\s*[\d.]+\s*%\s*', '', s, flags=re.I)
        # "Equity Securities [-–—] <pct>% <Geography> [-–—] <pct>% <InvType> [-–—] <pct>% <Company>"
        s = re.sub(r'^Equity\s+Securities\s*[-–—]\s*[\d.]+\s*%\s+.+?\s*[-–—]\s*[\d.]+\s*%\s+.+?\s*[-–—]\s*[\d.]+\s*%\s*', '', s, flags=re.I)
        # Strip trailing "Initial Acquisition Date <D>" then "Industry <X>" suffixes
        s = re.sub(r'\s+Initial\s+Acquisition\s+Date\s+\S+\s*$', '', s.strip(), flags=re.I)
        s = re.sub(r'\s+Industry\s+\w[\w\s]*$', '', s.strip(), flags=re.I)
        s = re.sub(r'^Non-Controlled\s+Affiliates\s+', '', s, flags=re.I)
        s = re.sub(r'^Controlled\s+Affiliates\s+', '', s, flags=re.I)
        _gsbd_section_only = frozenset(['Pharmaceuticals Inc.', 'Biotechnology Research Inc.', 'Consumer Services LLC', 'Non-Controlled Affiliates', 'Inc.'])
        if s.strip() in _gsbd_section_only:
            return ('', None)
        # If still starts with a percentage after all strips → section header, clear
        if re.match(r'^\d+(?:\.\d+)?%', s.strip()):
            return ('', None)
        # If result is a bare country/investment-type fragment → clear
        _gsbd_fragments = frozenset([
            'canada', 'united states', 'united kingdom', 'germany', 'australia', 'netherlands',
            'france', 'sweden', 'luxembourg', 'ireland', 'cayman islands', 'italy', 'spain',
            'denmark', 'finland', 'new zealand', 'common stock', 'preferred stock',
            'first lien senior secured', 'senior secured loans', 'equity securities',
            'first lien', 'second lien',
            ') media',
        ])
        if s.strip().lower() in _gsbd_fragments:
            return ('', None)
        if s.startswith('Automotive '):
            rest = s[len('Automotive '):].strip()
            if len(rest) > 5:  # "Automotive Parts Inc." → "Parts Inc." (company); "Automotive" only already handled above
                s = rest
                extracted_industry = 'Automotive'
        return (s.strip(), extracted_industry)

    # WHF: "Alvaria Holdco (Cayman) (d/b/a Aspect Software Inc.) First Lien Secured Term Loan" → company only
    # Also: "Solar Holdings Bidco Limited First Lien Secured Delayed Draw Loan Interest Rate 10.93"
    if ticker_upper == 'WHF':
        s = re.sub(r'\s+First\s+Lien\s+Secured\s+Term\s+Loan(\s+One|\s+Two|\s+Three|\s+Four|\s+Five|\s+Six|\s+Seven|\s+Eight|\s+Nine|\s+Ten)?\s*$', '', s, flags=re.I)
        s = re.sub(r'\s+First\s+Lien\s+Secured\s+Revolver\s*$', '', s, flags=re.I)
        s = re.sub(r'\s+First\s+Lien\s+Secured\s+Delayed\s+Draw\s+Loan(?:\s+Interest\s+Rate\s+[\d.]+)?(?:\s+(?:One|Two|Three|Four|Five))?\s*$', '', s, flags=re.I)
        return (s.strip(), None)

    # TRIN: section-only "Portfolio Company Debt Securities" → ''; "Portfolio Company Warrant Investments United States Real Estate Technology Knockaway Inc." → "Knockaway Inc."; "Portfolio Company Debt Securities- Healthcare Technology Unmind Ltd." → "Unmind Ltd."
    if ticker_upper == 'TRIN':
        _trin_section_only = (
            'Portfolio Company Debt Securities', 'Portfolio Company Warrant Investments',
            'Portfolio Company Equity Investments', 'Portfolio Company Investment in Securities',
            'Portfolio Company Cash and Cash Equivalents', 'Portfolio Company Portfolio Investments and Cash and Cash Equivalents',
            'Portfolio Company Equity Investments Canada',
        )
        if s.strip() in _trin_section_only:
            return ('', None)
        # "Portfolio Company Warrant Investments United States {Sector} {Company}" or "United {Company}" (abbrev)
        s = re.sub(r'^Portfolio\s+Company\s+Warrant\s+Investments\s+United\s+States\s+', '', s, flags=re.I)
        s = re.sub(r'^Portfolio\s+Company\s+Warrant\s+Investments\s+United\s+', '', s, flags=re.I)
        # "Portfolio Company Equity Investments Canada {Company}"
        s = re.sub(r'^Portfolio\s+Company\s+Equity\s+Investments\s+Canada\s+', '', s, flags=re.I)
        s = re.sub(r'^Portfolio\s+Company\s+Debt\s+Securities\s*[-–—]\s*', '', s, flags=re.I)
        s = re.sub(r'^United\s+States\s+', '', s, flags=re.I)  # after Debt Securities- United States ...
        # "Portfolio Company Cash and Cash Equivalents Goldman Sachs..." or "Other cash accounts" → keep remainder as name or clear
        s = re.sub(r'^Portfolio\s+Company\s+Cash\s+and\s+Cash\s+Equivalents\s+', '', s, flags=re.I)
        if s.strip().lower() in ('other cash accounts',):
            return ('', None)
        s = re.sub(r'^Affiliate\s+Investments\s+', '', s, flags=re.I)
        # Generic: strip "Portfolio Company " prefix when not caught by specific patterns above
        # (e.g. "Portfolio Company Finance and Insurance Bestow Inc.")
        s = re.sub(r'^Portfolio\s+Company\s+', '', s, flags=re.I)
        for ind in ['Digital Assets Technology and Services', 'Application Software', 'Healthcare Technology',
                    'Software & Technology', 'Technology', 'Healthcare', 'Finance and Insurance']:
            if s.startswith(ind + ' '):
                s = s[len(ind):].strip()
                if not extracted_industry:
                    extracted_industry = ind
                break
        if not extracted_industry:
            for phrase in _HRZN_SECTOR_PHRASES:
                if s.lower().startswith(phrase.lower()):
                    s = s[len(phrase):].strip()
                    extracted_industry = phrase
                    break
        if s.strip().lower() in ('technology', 'software', 'healthcare', 'financial services', 'finance and insurance'):
            return ('', None)  # sector-only row, not a company
        return (s.strip(), extracted_industry)

    # FDUS:
    #   - "Affiliate Investments Pfanstiehl Inc." → "Pfanstiehl Inc."
    #   - "Non-control/Non-affiliate Investments Acendre Midco Inc." → "Acendre Midco Inc."
    #   - pure section headers like "Non-control/Non-affiliate Investments" → no company
    if ticker_upper == 'FDUS':
        s = re.sub(r'^Affiliate\s+Investments\s+', '', s, flags=re.I)
        # Some filings prepend this axis text before the real label.
        s = re.sub(r'^Investment\s+Identifier\s+\[Axis\]\s*:\s*', '', s, flags=re.I)
        # Repair malformed duplicated prefixes seen in some FDUS files.
        s = re.sub(
            r'^(?:Non-?cont\w*)\s*Non-?control/Non-?affiliate\s+Investments?',
            'Non-control/Non-affiliate Investments',
            s,
            flags=re.I,
        )
        # Drop header-only rows
        if re.match(r'^Non-?control/Non-?affiliate\s+Investments?\s*$', s.strip(), re.I):
            return ('', None)
        # Strip leading affiliation prefix when followed by an issuer/fund name
        s = re.sub(r'^Non-?control/Non-?affiliate\s+Investments?\s*', '', s, flags=re.I)
        return (s.strip(), None)

    # OCSL: "Alvotech Holdings S.A. Biotechnology" → "Alvotech Holdings S.A." (trailing sector leak)
    if ticker_upper == 'OCSL':
        s = re.sub(r'\s+Biotechnology\s*$', '', s, flags=re.I)
        return (s.strip(), None)

    # BCSF: "Ansett Aviation Training...", long dimension blobs; "Non-controlled/Non-Affiliated Investments {Industry} {Company}..."
    if ticker_upper == 'BCSF':
        # Section-only: "Non-Controlled/Non-Affiliated Investments Equity Warrants and..." / "...Debt Professional..." (no real company)
        if re.match(r'^Non-Controlled/Non-Affiliated\s+Investments\s+Equity\s+Warrants\s', s, re.I):
            return ('', None)
        if re.match(r'^Non-Controlled/Non-Affiliated\s+Investments\s+Debt\s+Professional\s', s, re.I):
            return ('', None)
        # Industry-only section headers (no company name)
        _bcsf_section_only = frozenset([
            'Aerospace & Defense', 'Healthcare & Pharmaceuticals', 'Beverage Food & Tobacco',
            'Chemicals Plastics & Rubber', 'Services: Business', 'Services: Consumer',
            'FIRE: Insurance', 'FIRE: Finance', 'Capital Equipment', 'Construction & Building',
            'Construction & Building Service', 'Consumer Goods: Non-Durable', 'Consumer Goods: Durable',
            'Consumer Goods: Wholesale', 'Automotive', 'Beverage', 'Chemicals', 'Containers',
        ])
        if s.strip() in _bcsf_section_only:
            return ('', None)
        # "Services: Consumer {Company}" (e.g. "Services: Consumer Eagle Parent Corp.")
        if s.startswith('Services: Consumer '):
            s = s[len('Services: Consumer '):].strip()
            if not extracted_industry:
                extracted_industry = 'Consumer & Retail'
        # "Non-controlled/Non-Affiliated Investments {Industry} " (data uses this; also support Non-Controlled/Affiliate(d) and Australian Dollar)
        _bcsf_base = r'^Non-Controlled/Non-Affiliated\s+Investments\s+'
        _bcsf_industries = [
            ('Healthcare & Pharmaceuticals', 'Healthcare'),
            ('Consumer Goods: Non-Durable', 'Consumer & Retail'),
            ('Consumer Goods: Durable', 'Consumer & Retail'),
            ('Consumer Goods: Wholesale', 'Consumer & Retail'),
            ('Beverage Food & Tobacco', 'Food & Beverage'),
            ('Chemicals Plastics & Rubber', 'Chemicals & Materials'),
            ('Construction & Building Service', 'Industrials'),
            ('Construction & Building', 'Industrials'),
            ('Capital Equipment', 'Industrials'),
            ('FIRE: Insurance', 'Insurance'),
            ('FIRE: Finance', 'Financial Services'),
            ('Services: Business', 'Business Services'),
            ('Aerospace & Defense', 'Aerospace & Defense'),
            ('Automotive', 'Automotive'),
            ('Beverage', 'Food & Beverage'),
            ('Chemicals', 'Chemicals & Materials'),
        ]
        for phrase, ind in _bcsf_industries:
            pat = _bcsf_base + re.escape(phrase) + r'\s+'
            if re.match(pat, s, re.I):
                extracted_industry = ind
                s = re.sub(pat, '', s, flags=re.I)
                break
        # "-FIRE: Insurance-" (leading hyphen variant)
        if not extracted_industry and re.match(r'^Non-Controlled/Non-Affiliated\s+Investments\s+-FIRE:\s+Insurance\s*-?\s*', s, re.I):
            extracted_industry = 'Insurance'
            s = re.sub(r'^Non-Controlled/Non-Affiliated\s+Investments\s+-FIRE:\s+Insurance\s*[-–—]?\s*', '', s, flags=re.I)
        # Legacy: "Non-Controlled/Affiliate(d) Investments Aerospace & Defense " / "Australian Dollar Aerospace & Defense "
        if re.match(r'^Non-Controlled/Affiliated\s+Investments\s+Aerospace\s+&\s+Defense\s+', s, re.I) or re.match(r'^Non-Controlled/Affiliate\s+Investments\s+Aerospace\s+&\s+Defense\s+', s, re.I) or re.match(r'^Australian\s+Dollar\s+Aerospace\s+&\s+Defense\s+', s, re.I):
            extracted_industry = 'Aerospace & Defense'
        s = re.sub(r'^Non-Controlled/Affiliated\s+Investments\s+Aerospace\s+&\s+Defense\s+', '', s, flags=re.I)
        s = re.sub(r'^Non-Controlled/Affiliate\s+Investments\s+Aerospace\s+&\s+Defense\s+', '', s, flags=re.I)
        s = re.sub(r'^Australian\s+Dollar\s+Aerospace\s+&\s+Defense\s+', '', s, flags=re.I)
        # Leading industry without "Non-Controlled/..." prefix (e.g. "Capital Equipment AXH Air Coolers...", "Healthcare & Pharmaceuticals AEG Vision...")
        for phrase, ind in _bcsf_industries:
            if s.startswith(phrase + ' '):
                rest = s[len(phrase):].strip()
                if len(rest) > 10 or len(rest.split()) >= 2:  # avoid stripping "Capital Equipment LLC" → "LLC"
                    s = rest
                    if not extracted_industry:
                        extracted_industry = ind
                    break
        # Trailing " First Lien Senior Secured Loan BBSY Spread X% Interest Rate Y% Maturity Date M/D/YYYY" (long dimension)
        s = re.sub(r'\s+First\s+Lien\s+Senior\s+Secured\s+Loan\s+BBSY\s+Spread\s+[\d.]+\s*%\s+Interest\s+Rate\s+[\d.]+\s*%\s+Maturity\s+Date\s+\d{1,2}/\d{1,2}/\d{4}\s*$', '', s, flags=re.I)
        # " Acquisition Date 3/24/2022" (position-only row)
        s = re.sub(r'\s+Acquisition\s+Date\s+\d{1,2}/\d{1,2}/\d{2,4}\s*$', '', s, flags=re.I)
        # " Equity Interest" / " First Lien Senior Secured Loan" handled by generic _strip_instrument_suffix after ticker cleanup
        return (s.strip(), extracted_industry)

    # KBDC (user ref as KCDC): "Aerospace & defense - Fastener Distribution Holdings" → "Fastener Distribution Holdings"; "Household durables - Curio Brands" → "Curio Brands"; same "Industry - Company" as MFIC
    if ticker_upper == 'KBDC':
        _kbdc_hyphen_prefixes = [
            ('Commercial services & supplies - ', 'Business Services'),
            ('Commercial Services & Supplies - ', 'Business Services'),
            ('Commercial services & supplies-', 'Business Services'),
            ('Personal care products - ', 'Consumer & Retail'),
            ('Personal Care Products - ', 'Consumer & Retail'),
            ('Personal care products-', 'Consumer & Retail'),
            ('Wireless telecommunication services - ', 'Telecommunications'),
            ('Wireless telecommunication services-', 'Telecommunications'),
            ('Wireless Telecommunication Services - ', 'Telecommunications'),
            ('Pharmaceuticals - ', 'Healthcare'),
            ('Pharmaceuticals ', 'Healthcare'),
            ('Pharmaceuticals-', 'Healthcare'),
            ('Consumer Services ', 'Consumer & Retail'),
        ]
        for prefix, ind in _kbdc_hyphen_prefixes:
            if s.lower().startswith(prefix.lower()):
                s = s[len(prefix):].strip()
                if not extracted_industry:
                    extracted_industry = ind
                break
        if s.startswith('Trading companies & distributors-'):
            s = s[len('Trading companies & distributors-'):].strip()
            extracted_industry = 'Trading companies & distributors'
        if s.startswith('Professional services - '):
            s = s[len('Professional services - '):].strip()
            extracted_industry = 'Professional services'
        if s.lower().startswith('household durables - '):
            s = s[len('Household durables - '):].strip()
            if not extracted_industry:
                extracted_industry = 'Consumer & Retail'
        if s.lower().startswith('household durables-'):
            s = s[len('Household durables-'):].strip()
            if not extracted_industry:
                extracted_industry = 'Consumer & Retail'
        for phrase in _HRZN_SECTOR_PHRASES + ['Aerospace & defense', 'Automobile components', 'Building products', 'Biotechnology']:
            prefix = phrase + ' - '
            if s.lower().startswith(prefix.lower()):
                s = s[len(prefix):].strip()
                if not extracted_industry:
                    extracted_industry = phrase if phrase in _HRZN_SECTOR_PHRASES else phrase.title()
                break
        if re.match(r'^[A-Za-z\s&]+$', s) and len(s) < 35 and s.lower() in ['aerospace & defense', 'automobile components', 'building products', 'biotechnology', 'trading companies & distributors']:
            return ('', None)  # subtotal row
        return (s, extracted_industry)

    # HTGC: "Debt Investments {Sector}" section headers; "CompanyName Equity Acquisition Date M/D/YYYY Series X" trailing junk
    if ticker_upper == 'HTGC':
        s = re.sub(r'^Debt\s+Investments\s+', '', s, flags=re.I)
        # Strip trailing " Equity Acquisition Date M/D/YYYY Series ..." → company name only
        s = re.sub(r'\s+Equity\s+Acquisition\s+Date\s+\d{1,2}/\d{1,2}/\d{4}\b.*$', '', s, flags=re.I).strip()
        # Strip trailing " Senior Secured Maturity Date ..." → company name only
        s = re.sub(r'\s+(?:Senior\s+Secured\s+)?Maturity\s+Date\s+\w.*$', '', s, flags=re.I).strip()
        # Known HTGC sector-only labels (after "Debt Investments" stripped) → section header, clear
        _htgc_sectors = frozenset([
            'biotechnology tools', 'communications & networking', 'consumer & business products',
            'consumer & business services', 'defense technologies', 'diversified financial services',
            'drug discovery & development', 'healthcare services', 'information technology',
            'internet consumer & business services', 'life sciences tools & services',
            'merchant finance', 'oil & gas', 'semiconductors & equipment',
            'sustainable & impact investing', 'technology royalties',
        ])
        if s.strip().lower() in _htgc_sectors:
            return ('', None)
        # Sector phrases from _HRZN_SECTOR_PHRASES that are HTGC section headers too
        for phrase in _HRZN_SECTOR_PHRASES:
            if s.strip().lower() == phrase.lower():
                return ('', None)
            if s.lower().startswith(phrase.lower() + ' '):
                s = s[len(phrase):].strip()
                extracted_industry = phrase
                break
        return (s.strip(), extracted_industry)

    # MSDL: "Spread S + X% ..." = rate-only; "Counterparty BNP Paribas ..." = interest rate swap, no company
    # "First Lien Debt - non-controlled/non-affiliated {Company}" → company only
    if ticker_upper == 'MSDL':
        if re.match(r'^Spread\s+S\s*\+', s, re.I):
            return ('', None)
        if re.match(r'^Counterparty\s+\w+', s, re.I):
            return ('', None)
        s = re.sub(r'^First\s+Lien\s+Debt\s*[-–—]\s*non-controlled/non-affiliated\s+', '', s, flags=re.I).strip()
        return (s, None)

    # BBDC: "Second Lien Senior Secured Term Loan" = section header only, no company
    if ticker_upper == 'BBDC':
        if re.match(r'^(?:First|Second)\s+Lien\s+Senior\s+Secured\s+Term\s+Loan\s*$', s, re.I):
            return ('', None)
        # Sector prefix leak: "Technology Service Stream BidCo Pty Ltd." -> "Service Stream BidCo Pty Ltd."
        if re.match(r'^(?:Technology|echnology)\s+\S', s):
            s = re.sub(r'^(?:Technology|echnology)\s+', '', s, flags=re.I).strip()
            if not extracted_industry:
                extracted_industry = 'Software & Technology'
        return (s, extracted_industry)

    # BCIC: "Senior Secured Loans", "Subordinated Notes" = section headers; "247% of Net Asset Value at Fair Value" = NAV row
    if ticker_upper == 'BCIC':
        if re.match(r'^(?:Senior\s+Secured\s+Loans?|Subordinated\s+Notes?|Second\s+Lien\s+Loans?)\s*$', s, re.I):
            return ('', None)
        if re.match(r'^\d+(?:\.\d+)?%\s+of\s+Net\s+Asset\s+Value', s, re.I):
            return ('', None)
        return (s, None)

    # NMFC: "First Lien Investments" = section header
    if ticker_upper == 'NMFC':
        if re.match(r'^(?:First|Second)\s+Lien\s+Investments?\s*$', s, re.I):
            return ('', None)
        return (s, None)

    # SLRC: "Senior Secured Loans - 127.3%" or "Senior Secured Loans 132.0%" = section header with percentage
    if ticker_upper == 'SLRC':
        if re.match(r'^Senior\s+Secured\s+Loans?\s*(?:[-–—]\s*)?[\d.]*%?\s*$', s, re.I):
            return ('', None)
        return (s, None)

    # BCIC (and similar): ": Broadcasting & Subscription", ": Cargo", ": Consumer" = section header, not company
    if s.strip().startswith(': '):
        rest = s.strip()[2:].strip()
        if rest.lower() in ('broadcasting & subscription', 'cargo', 'consumer', 'electricity', 'oil & gas'):
            return ('', None)

    return (s, None)


def clean_company_name(company_name: str, ticker: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Normalize company name: strip instrument/position suffixes, then legal entity suffixes.
    Returns (cleaned_name, extracted_industry). extracted_industry is set when we strip an industry
    prefix from the name (e.g. "Biotechnology & Life Sciences"); callers can use it to set
    row["industry"] when empty. Use clean_company_name(...)[0] for name-only.
    """
    if not company_name or company_name.strip() == '':
        return ('', None)

    name = company_name.strip()
    extracted_industry: Optional[str] = None
    # Ticker-specific cleanup first (XBRL/scraper artifacts)
    if ticker:
        name, extracted_industry = _apply_ticker_specific_company_cleanup(name, ticker)
        if not name:
            return ('', None)
    name = ' '.join(name.split())

    # "Warrant Investments and {Industry} and {Company}" (HTGC/XBRL) → company only; "Warrant Investments and {Industry}" only → section header
    if name.startswith('Warrant Investments and '):
        rest = name[len('Warrant Investments and '):].strip()
        if ' and ' in rest:
            name = rest.split(' and ', 1)[1].strip()  # "Electronics & Computer Hardware and Skydio Inc." → "Skydio Inc."
        else:
            return ('', None)  # section header, no company

    # Handle bare "Investments in Non-Controlled" (no company after)
    if _INVESTMENTS_IN_BARE.match(name):
        return ('', None)

    # Handle "Investments in Non-Control... {type} {company}" (CION/MRCC format)
    m_inv = _INVESTMENTS_IN_PREFIX.match(name)
    if m_inv:
        remainder = name[m_inv.end():].strip()
        if remainder:
            name = remainder
        else:
            return ('', None)  # Just a section header

    # Filter out non-company entries entirely
    if is_non_company_entry(name):
        return ('', None)

    # Handle HRZN-style "Portfolio Company..." prefix: strip through geography, extract company
    m_hrzn = _PORTFOLIO_COMPANY_PREFIX.match(name)
    if m_hrzn:
        remainder = name[m_hrzn.end():]
        name = _extract_from_hrzn_blob(remainder)
        if not name:
            return ('', None)

    # Strip junk prefixes from table headers ("First Lien - Acme Corp" → "Acme Corp")
    name = _JUNK_COMPANY_PREFIXES.sub('', name).strip()

    # Strip trailing rate/date e.g. "KLO Intermediate Holdings LLC L+775 1.50% LIBOR Floor 4/7/2022"
    name = _strip_rate_date_suffix(name)

    # Strip instrument/position suffix so "Zeus Fire & Security - Delayed Draw" → "Zeus Fire & Security"
    name = _strip_instrument_suffix(name)
    # Trailing " - " or " -" with nothing after (e.g. "Zoro -" from "Zoro - Common Equity" when suffix already stripped)
    name = re.sub(r'\s*[-–—]\s*$', '', name).strip()

    # Strip trailing numeric footnote markers: "Castle Creek Biosciences, Inc. (2)(12)" → "Castle Creek Biosciences, Inc."
    if '(' in name and ')' in name:
        name = re.sub(r'(\s*\(\d+\))+\s*$', '', name).strip()
    # Normalize remaining parenthetical: "Rocaceia LLC (Quality Lease and Rental Holdings LLC)" → "Rocaceia LLC"
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

    # Company-name-based industry: e.g. "Advanced Aircrew" → Aerospace & Defense (filing may label section "Services: Business")
    if name and "aircrew" in name.lower():
        extracted_industry = "Aerospace & Defense"

    return (name, extracted_industry)


# Trailing legal entity suffixes we treat as equivalent for clustering (Inc. = Ltd. = LLC = Corp. = same company)
_COMPANY_LEGAL_SUFFIX = re.compile(
    r"\s*,?\s*(?:Inc\.?|Incorporated|Ltd\.?|Limited|LLC|L\.L\.C\.?|Corp\.?|Corporation|L\.P\.?|LP|plc)\s*$",
    re.IGNORECASE,
)


def normalize_legal_entity_for_clustering(name: str) -> str:
    """
    Strip trailing legal entity suffix for clustering so that
    "Labvantage Solutions Inc.", "Labvantage Solutions Ltd.", "Labvantage Solutions LLC"
    all map to "Labvantage Solutions" and resolve to the same company_id.
    Use only for clustering; display/canonical names keep the suffix.
    """
    if not name or not name.strip():
        return name
    s = name.strip()
    # Strip trailing legal suffix (may repeat if e.g. ", Inc." appears)
    prev = None
    while prev != s and _COMPANY_LEGAL_SUFFIX.search(s):
        prev = s
        s = _COMPANY_LEGAL_SUFFIX.sub("", s).strip()
    return s if s else name.strip()


# ===== RESTANDARDIZE EXISTING CSVs =====

import csv
from pathlib import Path


def restandardize_csv(path: str | Path, *, dry_run: bool = False) -> dict:
    """Re-apply standardization rules to an existing consolidated CSV.

    Reads the CSV, applies standardize_industry(), standardize_investment_type(),
    and clean_company_name() to each row, then writes back in-place.

    Returns a summary dict with counts of rows processed and fields changed.
    """
    path = Path(path)
    if not path.exists():
        return {"error": f"File not found: {path}", "rows": 0, "changes": 0}

    rows = []
    changes = 0

    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if not fieldnames:
            return {"error": f"No headers in {path}", "rows": 0, "changes": 0}
        for row in reader:
            changed = False
            # Industry
            if "industry" in row:
                new_val = standardize_industry(row["industry"])
                if new_val != row["industry"]:
                    row["industry"] = new_val
                    changed = True
            # Investment type
            if "investment_type" in row:
                new_val = standardize_investment_type(row["investment_type"])
                if new_val != row["investment_type"]:
                    row["investment_type"] = new_val
                    changed = True
            # Company name (pass ticker for BDC-specific cleanup when available)
            if "company_name" in row:
                ticker = (row.get("ticker") or "").strip() or None
                new_val, extracted_industry = clean_company_name(row["company_name"], ticker=ticker)
                if new_val != row["company_name"]:
                    row["company_name"] = new_val
                    changed = True
                # When we stripped an industry prefix from the name (or inferred from company name, e.g. Advanced Aircrew), use it for industry if empty or Other
                if extracted_industry and "industry" in row:
                    current_ind = (row.get("industry") or "").strip()
                    if not current_ind or current_ind == "Other":
                        canonical = standardize_industry(extracted_industry)
                        if canonical:
                            row["industry"] = canonical
                            changed = True
                # If still empty/Other and no extracted_industry, try inferring from company name keywords (e.g. "Forescout Technologies" → Software & Technology)
                if "industry" in row:
                    current_ind = (row.get("industry") or "").strip()
                    if (not current_ind or current_ind == "Other") and new_val and not extracted_industry:
                        hint = normalize_industry(new_val)
                        if hint and hint in ALLOWED_INDUSTRIES and hint != "Other":
                            row["industry"] = hint
                            changed = True
            if changed:
                changes += 1
            rows.append(row)

    if not dry_run and changes > 0:
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    return {"path": str(path), "rows": len(rows), "changes": changes}


# ===== CROSS-BDC INDUSTRY PROPAGATION =====

def build_company_industry_map(data_dir: str | Path) -> dict[str, str]:
    """Build company_id → best industry mapping from all consolidated CSVs.

    Scans all top-level CSVs (TICKER.csv) and per-period CSVs (TICKER/DATE.csv)
    in *data_dir*. For each company_id, picks the most frequent non-Other industry.
    """
    from collections import Counter, defaultdict

    data_dir = Path(data_dir)
    company_industries: dict[str, Counter] = defaultdict(Counter)

    for csv_path in sorted(data_dir.rglob("*.csv")):
        with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or "company_id" not in reader.fieldnames:
                continue
            for row in reader:
                cid = row.get("company_id", "").strip()
                ind = row.get("industry", "").strip()
                if cid and ind and ind != "Other" and ind != "":
                    company_industries[cid][ind] += 1

    # Pick the most common industry for each company
    result: dict[str, str] = {}
    for cid, counter in company_industries.items():
        best_industry, _ = counter.most_common(1)[0]
        result[cid] = best_industry
    return result


def propagate_industries(data_dir: str | Path, *, dry_run: bool = False) -> dict:
    """Fill missing/Other industries using cross-BDC company_id → industry mapping.

    Returns summary dict with files_changed, rows_filled counts.
    """
    data_dir = Path(data_dir)
    company_map = build_company_industry_map(data_dir)

    files_changed = 0
    total_filled = 0

    for csv_path in sorted(data_dir.rglob("*.csv")):
        rows = []
        filled = 0

        with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            if not fieldnames or "company_id" not in fieldnames or "industry" not in fieldnames:
                continue
            for row in reader:
                cid = row.get("company_id", "").strip()
                ind = row.get("industry", "").strip()
                if cid and (not ind or ind == "Other") and cid in company_map:
                    row["industry"] = company_map[cid]
                    filled += 1
                rows.append(row)

        if filled > 0:
            total_filled += filled
            files_changed += 1
            if not dry_run:
                with open(csv_path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)

    return {
        "companies_with_industry": len(company_map),
        "files_changed": files_changed,
        "rows_filled": total_filled,
    }
