"""
Custom scraper for BCIC (BCP Investment Corporation).

BCIC files its schedule of investments with this single-width header:

    Investment | Industry | Interest Rate | Reference Rate and Spread |
    Floor | Maturity | Par/Shares | Cost | Fair Value | Footnotes

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Investment cells carry tranche suffixes in parentheses
    ("13 Scents Inc. (Revolver)" -> Revolver).
  * Section rows set the investment type
    ("First Lien/Senior Secured Debt -147.3%", "Second Lien/Senior Secured Debt",
    "Subordinated Debt", "Equity", "Collateralized Loan Obligations",
    "Joint Ventures", "Derivatives").
  * Reference rate cells pack PIK ("SOFR+7.00%,3.00% PIK").
  * Maturity dates use MM/DD/YY.
  * Only the first (current) SOI table is parsed; the prior year-end copy
    follows.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_EQUITY_TYPES = {
    "Common Equity", "Preferred Equity", "Other Equity",
    "Partnership Interest", "Warrants",
}

_COLUMN_KEYS = [
    "investment", "industry", "interestrate", "referencerateandspread",
    "floor", "maturity", "par/shares", "cost", "fairvalue",
]

_SUFFIX_RE = re.compile(r"\(([A-Za-z][A-Za-z \-/]{2,30})\)\s*$")


def _norm_header(label: str) -> str:
    s = re.sub(r"\s*\([a-zA-Z0-9]+\)\s*", "", label or "")
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9/%$]", "", s)


def _cell_range(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip()
        if v and v not in ("$", "€", "%", "£", "—", "-"):
            return v
    return ""


class BCICScraper(BaseCompanyScraper):
    ticker = "BCIC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Investment" not in t["text"][:120]:
                continue
            if "Reference Rate" not in t["text"][:600]:
                continue
            if "Fair Value" not in t["text"][:600]:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("BCIC: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        if not soi_tables:
            return []

        rows: List[Dict[str, str]] = []
        current_type = "First Lien"

        for t in soi_tables:
            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[header_idx])
            # The prior year-end copy labels the industry column "Industry (27)".
            if any("industry" in (c or "").lower() and "(27)" in (c or "")
                   for c in grid[header_idx]):
                break

            for row in grid[header_idx + 1:]:
                investment = _cell_range(row, *ranges["investment"])
                industry = _cell_range(row, *ranges["industry"]) \
                    if "industry" in ranges else ""
                low = investment.lower()

                if not investment:
                    continue
                if low.startswith("total") or low.startswith("subtotal"):
                    continue

                if not industry:
                    sec_type = self._map_section_type(investment)
                    if sec_type:
                        current_type = sec_type
                    continue

                company, suffix_type = self._split_company(investment)
                inv_type = suffix_type or current_type

                rows.append(self._row_to_record(row, company, industry,
                                                inv_type, ranges))

        logger.info("BCIC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, industry: str,
                       inv_type: str, ranges: Dict[str, tuple]) -> Dict[str, str]:
        def grp(key: str) -> str:
            rng = ranges.get(key)
            if not rng:
                return ""
            return _cell_range(row, *rng)

        reference, spread, pik = self._parse_ref_spread(grp("referencerateandspread"))
        par = self._clean_number(grp("par/shares"))
        cost = self._clean_number(grp("cost"))
        fair = self._clean_number(grp("fairvalue"))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", par
        else:
            principal, shares = par, ""

        return {
            "company_name": company,
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": self._parse_percent_cell(grp("interestrate")),
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": self._parse_percent_cell(grp("floor")),
            "acquisition_date": "",
            "maturity_date": self._parse_date(grp("maturity")),
            "principal_amount": principal,
            "amortized_cost": cost,
            "fair_value": fair,
            "percent_of_net_assets": "",
            "cost": cost,
            "commitment_limit": "",
            "undrawn_commitment": "",
            "rate_cap": "",
            "shares": shares,
        }

    # --------------------------------------------------------------
    # Helpers
    # --------------------------------------------------------------
    @staticmethod
    def _find_header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid):
            if any(_norm_header(c) == "investment" for c in row):
                return i
        return None

    @staticmethod
    def _column_ranges(row: List[str]) -> Dict[str, tuple]:
        pos: Dict[str, int] = {}
        for i, c in enumerate(row):
            n = _norm_header(c)
            if n and n not in pos:
                pos[n] = i
        ordered = sorted((pos[k], k) for k in _COLUMN_KEYS if k in pos)
        ranges: Dict[str, tuple] = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _split_company(value: str) -> tuple:
        s = (value or "").strip()
        m = _SUFFIX_RE.search(s)
        if m:
            suffix = m.group(1)
            company = s[:m.start()].strip()
            low = suffix.lower()
            if "revolver" in low:
                return company, "Revolver"
            if "delayed draw" in low or "term loan" in low:
                return company, "First Lien"
            if "preferred" in low:
                return company, "Preferred Equity"
            if "common" in low:
                return company, "Common Equity"
            if "warrant" in low:
                return company, "Warrants"
            if "unit" in low or "equity" in low or "interest" in low:
                return company, "Other Equity"
            return company, ""
        return s, ""

    @staticmethod
    def _map_section_type(raw: str) -> str:
        s = (raw or "").lower()
        if "first lien" in s or "senior secured" in s and "second" not in s:
            return "First Lien"
        if "second lien" in s:
            return "Second Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "collateralized loan" in s or "clo" in s:
            return "Other Debt"
        if "joint venture" in s:
            return "Partnership Interest"
        if "derivative" in s:
            return "Other"
        if "equity" in s:
            return "Other Equity"
        return ""

    @staticmethod
    def _parse_ref_spread(value: str) -> tuple:
        """Return (reference, spread, pik)."""
        if not value:
            return "", "", ""
        s = value.replace("\u00a0", " ")
        pik = ""
        pik_m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", s, re.IGNORECASE)
        if pik_m:
            pik = pik_m.group(1) + "%"

        ref_map = {"SOFR": "SOFR", "LIBOR": "LIBOR", "PRIME": "Prime",
                   "EURIBOR": "Euribor"}
        m = re.search(r"(SOFR|LIBOR|PRIME|EURIBOR)\s*\+\s*(\d+(?:\.\d+)?)\s*%", s, re.IGNORECASE)
        if m:
            ref = ref_map.get(m.group(1).upper(), m.group(1).upper())
            return ref, m.group(2) + "%", pik
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if m:
            return "", m.group(1) + "%", pik
        return "", "", pik

    @staticmethod
    def _parse_date(value: str) -> str:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", (value or "").strip())
        if not m:
            return ""
        year = m.group(3)
        if len(year) == 2:
            year = "20" + year
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    @staticmethod
    def _parse_percent_cell(value: str) -> str:
        if not value:
            return ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", value)
        if m:
            return m.group(1) + "%"
        return ""
