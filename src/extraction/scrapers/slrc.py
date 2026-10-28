"""
Custom scraper for SLRC (SLR Investment Corp.).

SLRC files its consolidated schedule of investments as several sections,
each with its own header variant:

    Senior Secured Loans:
      Description | Industry | Spread Above Index | Floor | Interest Rate |
      Acquisition Date | Maturity Date | Par Amount | Cost | Fair Value
    Equipment Financing:
      … | Interest Rate | Acquisition Date | Maturity Date | Shares/Units |
      Cost | Fair Value
    Common Equity / Equity Interests / Warrants:
      Description | Industry | Acquisition Date | Shares/Units | Cost |
      Fair Value

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Investment type comes from subsection headers
    ("First Lien Bank Debt/Senior Secured Loans",
    "Equipment Financing — …", "Common Equity/Equity Interests/Warrants").
  * Spread cells pack the reference ("P+200" -> Prime + 2.00%,
    "S+500" -> SOFR + 5.00%).
  * Dates are MM/YYYY ("1/2024" -> 2024-01-01).
  * The 10-Q also contains the prior year-end SOI ("December 31"); tables
    whose preceding context mentions December 31 are excluded.
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
    "description", "industry", "spreadaboveindex", "floor", "interestrate",
    "acquisitiondate", "maturitydate", "paramount", "shares/units",
    "cost", "fairvalue",
]


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


class SLRCScraper(BaseCompanyScraper):
    ticker = "SLRC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Description" not in t["text"][:600]:
                continue
            if "Acquisition Date" not in t["text"][:1200]:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("SLRC: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_type = "First Lien"

        for t in soi_tables:
            ctx = self._context_before(filing_result, t)
            if "december 31" in ctx.lower():
                continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[header_idx])
            if "description" not in ranges:
                continue

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, *ranges["description"])
                industry = _cell_range(row, *ranges["industry"]) \
                    if "industry" in ranges else ""
                low = company.lower()

                if not company:
                    continue

                if not industry:
                    if low.startswith("total") or low.startswith("subtotal"):
                        continue
                    if low.startswith("cash equivalents"):
                        continue
                    sec_type = self._map_section_type(company)
                    if sec_type:
                        current_type = sec_type
                    continue

                record = self._row_to_record(row, self._clean_company(company),
                                             industry, current_type, ranges)
                rows.append(record)

        logger.info("SLRC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, industry: str,
                       sec_type: str, ranges: Dict[str, tuple]) -> Dict[str, str]:
        def grp(key: str) -> str:
            rng = ranges.get(key)
            if not rng:
                return ""
            return _cell_range(row, *rng)

        def pct(key: str) -> str:
            rng = ranges.get(key)
            if not rng:
                return ""
            val = _cell_range(row, *rng)
            if not val:
                return ""
            if "%" not in val and rng and any("%" in (c or "") for c in row[rng[0]:rng[1]]):
                val += "%"
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", val)
            if m:
                return m.group(1) + "%"
            return ""

        spread_raw = grp("spreadaboveindex")
        reference, spread = self._parse_ref_spread(spread_raw)

        par = self._clean_number(grp("paramount"))
        shares = self._clean_number(grp("shares/units"))
        if sec_type in _EQUITY_TYPES:
            principal, shares = "", shares or par
        else:
            principal = par

        return {
            "company_name": company,
            "investment_type": sec_type,
            "industry": industry,
            "cash_rate": pct("interestrate"),
            "pik_rate": "",
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": pct("floor"),
            "acquisition_date": self._parse_month_year(grp("acquisitiondate")),
            "maturity_date": self._parse_month_year(grp("maturitydate")),
            "principal_amount": principal,
            "amortized_cost": self._clean_number(grp("cost")),
            "fair_value": self._clean_number(grp("fairvalue")),
            "percent_of_net_assets": "",
            "cost": self._clean_number(grp("cost")),
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
            if any(_norm_header(c) == "description" for c in row):
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
    def _clean_company(value: str) -> str:
        s = re.sub(r"\s*(\(\d+\))+\s*$", "", (value or "").strip())
        return s.strip().rstrip("*").strip()

    @staticmethod
    def _map_section_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s or "senior secured" in s:
            return "First Lien"
        if "equipment financing" in s:
            return "Other Debt"
        if "common equity" in s or "equity" in s or "warrant" in s:
            return "Other Equity"
        return ""

    @staticmethod
    def _parse_ref_spread(s: str) -> tuple:
        if not s:
            return "", ""
        s = re.sub(r"\(\d+\)$", "", (s or "").strip())
        m = re.search(r"(P|S|L|E|SOFR|PRIME|LIBOR|EURIBOR|SONIA)\s*\+\s*(\d+(?:\.\d+)?)", s, re.IGNORECASE)
        if not m:
            return "", ""
        ref_raw = m.group(1).upper()
        ref_map = {"P": "Prime", "S": "SOFR", "L": "LIBOR", "E": "Euribor",
                   "SOFR": "SOFR", "PRIME": "Prime", "LIBOR": "LIBOR",
                   "EURIBOR": "Euribor", "SONIA": "SONIA"}
        bps = float(m.group(2))
        spread = f"{bps / 100.0:g}%"
        return ref_map.get(ref_raw, ref_raw), spread

    @staticmethod
    def _parse_month_year(value: str) -> str:
        m = re.fullmatch(r"(\d{1,2})/(\d{4})", (value or "").strip())
        if not m:
            return ""
        return f"{m.group(2)}-{int(m.group(1)):02d}-01"

    @classmethod
    def _context_before(cls, filing_result: Any, table: Dict[str, Any]) -> str:
        html = (filing_result.text_map or {}).get(table.get("doc") or "", "")
        if not html:
            return ""
        pos = table.get("pos") or 0
        raw = html[max(0, pos - 4000):pos]
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"&#160;|&nbsp;", " ", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text)
