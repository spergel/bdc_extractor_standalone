"""
Standard data categories for BDC investment extraction.
Use these in LLM prompts and post-processing validation.
"""

# Standard Industry Categories (39 total)
STANDARD_INDUSTRIES = """
Standard Industry Categories (use EXACTLY these values):
- Software
- Healthcare Services
- Pharmaceuticals & Biotechnology
- Medical Devices & Equipment
- Healthcare Technology
- Financial Services
- Insurance
- Real Estate
- Business Services
- Manufacturing
- Industrial Services
- Aerospace & Defense
- Automotive
- Construction & Engineering
- Chemicals
- Metals & Mining
- Media & Entertainment
- Marketing & Advertising
- Education
- Legal Services
- Consumer Products
- Food & Beverage
- Retail
- Apparel & Textiles
- Hotels & Leisure
- Transportation & Logistics
- Airlines
- Oil & Gas
- Energy & Utilities
- Energy Services
- Packaging
- Environmental Services
- Telecommunications
- Technology Hardware
- Internet & Media
- Investment Vehicles
- Diversified
- Agriculture
- Other
"""

# Standard Investment Types (16 total)
STANDARD_INVESTMENT_TYPES = """
Standard Investment Types (use EXACTLY these values):
- First Lien (for: First Lien Senior Secured, Senior Secured Loan, Unitranche, 1st Lien)
- Revolver (for: Revolving Loan, Revolver, First Lien Revolver)
- Delayed Draw (for: Delayed Draw Term Loan, DDTL, Delayed Draw)
- Second Lien (for: Second Lien, 2nd Lien, Junior Secured)
- Subordinated Debt (for: Subordinated Loan, Mezzanine Debt, Sub Debt)
- Unsecured Debt
- Common Equity (for: Common Stock, Common Units, Common Shares, Class A/B/C Units)
- Preferred Equity (for: Preferred Stock, Preferred Units, Series A/B Preferred)
- Partnership Interest (for: LLC Units, LP Interests, Member Units, Partnership Units)
- Warrants
- Unfunded Commitment
- Structured Note
- Money Market Fund
- Other Debt (for: Term Loan, Secured Debt, Senior Debt)
- Other
"""

# Standard Reference Rates (32 total)
STANDARD_REFERENCE_RATES = """
Standard Reference Rates (use EXACTLY these values):
- SOFR (Secured Overnight Financing Rate)
- SOFR (Q) - Quarterly
- SOFR (M) - Monthly
- SOFR (S) - Semi-annual
- SOFR (A) - Annual
- LIBOR
- LIBOR (Q) - Quarterly
- LIBOR (M) - Monthly
- LIBOR (S) - Semi-annual
- Euribor
- Euribor (Q), (M), (S)
- Prime (for: Prime Rate, Base Rate, P)
- Prime (Q), (M), (S)
- SONIA (Sterling Overnight Index Average)
- SONIA (Q), (M), (S)
- CDOR (Canadian Dollar Offered Rate)
- CDOR (Q), (M), (S)
- BKBM (Bank Bill Benchmark Rate)
- BKBM (Q)
- N/A (for: n/a, None, Fixed Rate, or when not applicable)
"""

def get_llm_standards_prompt():
    """Get standardized values prompt for LLM extraction."""
    return f"""
=== DATA STANDARDIZATION REQUIREMENTS ===

{STANDARD_INDUSTRIES}

{STANDARD_INVESTMENT_TYPES}

{STANDARD_REFERENCE_RATES}

EXTRACTION GUIDELINES:
1. ALWAYS use exact standard values from the lists above
2. For industry: Match to the most specific standard category
3. For investment_type: Use standard type (e.g., "First Lien" not "First lien senior secured loan")
4. For reference_rate: Use standard abbreviation (e.g., "SOFR" or "SOFR (Q)")
5. If uncertain, use "Other" for industry or investment_type, "N/A" for reference_rate

EXAMPLES:
- Industry: "Healthcare Providers & Services" → "Healthcare Services"
- Investment Type: "First lien senior secured loan" → "First Lien"
- Investment Type: "Revolving credit facility" → "Revolver"
- Reference Rate: "3 Month SOFR" → "SOFR (Q)"
- Reference Rate: "SF+" → "SOFR"
- Reference Rate: "L" or "LIBOR" → "LIBOR"
"""
