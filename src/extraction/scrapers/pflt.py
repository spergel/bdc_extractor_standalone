"""
Custom scraper for PFLT (PennantPark Floating Rate Capital Ltd.).

PFLT uses the same schedule-of-investments layout as PNNT:

    Issuer Name | Acquisition | Maturity | Industry | Current Coupon |
    Basis Point Spread Above Index | Par / Shares | Cost | Fair Value

See pnnt.py for the full layout notes.  PFLT adds a few more tranche
suffixes ("- First-Out Term Loan", "- Last-Out Term Loan").
"""

from __future__ import annotations

import re

from .pnnt import PNNTScraper


class PFLTScraper(PNNTScraper):
    ticker = "PFLT"

    @staticmethod
    def _split_issuer(value: str) -> tuple:
        s = (value or "").strip()
        m = re.search(r"\s*-\s*(Unfunded Revolver|Funded Revolver|Revolver|"
                      r"Unfunded Term Loan|Funded Term Loan|First-Out Term Loan|"
                      r"Last-Out Term Loan|Term Loan|Delayed Draw)\s*"
                      r"(?:\(\d+\))?\s*$",
                      s, re.IGNORECASE)
        if m:
            suffix = m.group(1).lower()
            company = s[:m.start()].strip()
            if "revolver" in suffix or "delayed draw" in suffix:
                return company, "Revolver"
            return company, "First Lien"
        company = re.sub(r"\s*(\(\d+\))+\s*$", "", s)
        return company.strip(), ""
