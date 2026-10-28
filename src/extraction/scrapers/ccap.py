"""
Custom scraper for CCAP (Crescent Capital BDC, Inc.).

CCAP files its consolidated schedule of investments with this header layout
(single-width columns in a 19-column grid):

    Country/Security/Industry/Company | Investment Type | Interest Term * |
    Interest Rate | Maturity/Dissolution Date | Principal, Par or Shares ** |
    Cost | Percentage of Net Assets *** | Fair Value

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Company names carry footnote refs in the same cell
    ("Auveco Holdings (4)(5)"), which are stripped.
  * Interest Term packs reference/spread/floor/PIK, e.g.
    "S + 525, 100 Floor" or "S + 600 (425 PIK), 100 Floor".
  * The Interest Rate column is the all-in cash rate.
  * Equity rows (Common/Preferred Stock, Partnership Interest) use the
    "Principal, Par or Shares" column for shares and have no rate cells.
  * Country rows ("United States"), section rows ("Debt Investments"),
    industry rows ("Automobiles & Components") and totals are skipped;
    the industry is carried down from the last industry row.
  * The 10-Q contains the current SOI and a prior year-end SOI; the period
    date is taken from each table's own title row.
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

_HEADING_RE = re.compile(
    r"Schedule\s+of\s+Investments[^\n]{0,80}?"
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),\s+(\d{4})",
    re.IGNORECASE,
)


def _norm_header(label: str) -> str:
    s = re.sub(r"\s*\([a-zA-Z0-9]+\)\s*", "", label or "")
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9/%$]", "", s)


def _cell_range(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip()
        if v and v not in ("$", "€", "%"):
            return v
    return ""


class CCAPScraper(BaseCompanyScraper):
    ticker = "CCAP"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Country/Security/Industry/Company" not in t["text"][:800]:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("CCAP: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_industry = ""

        current_heading = None
        for t in soi_tables:
            heading = self._table_heading(t)
            if heading:
                current_heading = heading
                break
        logger.info("CCAP: current SOI heading: %r", current_heading)

        for t in soi_tables:
            if current_heading is None or self._table_heading(t) != current_heading:
                continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue

            for row in grid[header_idx + 1:]:
                c0 = _cell_range(row, 0, 1)
                typ = _cell_range(row, 1, 2)
                low = c0.lower()

                if not c0:
                    continue

                if not typ:
                    if low.startswith("total") or low.startswith("subtotal"):
                        continue
                    if low in ("investments", "debt investments",
                               "equity investments", "cash & cash equivalents",
                               "cash and cash equivalents", "united states",
                               "foreign investments"):
                        continue
                    current_industry = self._clean_company(c0)
                    continue

                company = self._clean_company(c0)
                record = self._row_to_record(row, company, typ, current_industry)
                rows.append(record)

        logger.info("CCAP: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, type_raw: str,
                       industry: str) -> Dict[str, str]:
        inv_type = self._map_type(type_raw)
        term = _cell_range(row, 2, 3)
        rate = self._parse_percent_cell(_cell_range(row, 3, 4))
        maturity = self._parse_month_year(_cell_range(row, 4, 5))
        par = self._clean_number(_cell_range(row, 6, 7))
        cost = self._clean_number(_cell_range(row, 10, 11))
        pct = self._parse_percent_cell(_cell_range(row, 14, 15))
        fair = self._clean_number(_cell_range(row, 18, 19))

        reference, spread, floor, pik = self._parse_interest_term(term)

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", par
        else:
            principal, shares = par, ""

        return {
            "company_name": company,
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": rate,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": floor,
            "acquisition_date": "",
            "maturity_date": maturity,
            "principal_amount": principal,
            "amortized_cost": cost,
            "fair_value": fair,
            "percent_of_net_assets": pct,
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
            if any(_norm_header(c) == "country/security/industry/company" for c in row):
                return i
        return None

    @staticmethod
    def _clean_company(value: str) -> str:
        s = re.sub(r"\s*(\(\d+\))+\s*$", "", (value or "").strip())
        return s.strip()

    @staticmethod
    def _map_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s or "unitranche" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "common stock" in s or "common equity" in s:
            return "Common Equity"
        if "preferred stock" in s or "preferred equity" in s:
            return "Preferred Equity"
        if "partnership interest" in s or "lp interest" in s:
            return "Partnership Interest"
        if "warrant" in s:
            return "Warrants"
        if "equity" in s:
            return "Other Equity"
        return "Other"

    @staticmethod
    def _parse_interest_term(s: str) -> tuple:
        """Return (reference, spread, floor, pik) from 'S + 525, 100 Floor'."""
        if not s:
            return "", "", "", ""
        reference, spread, floor, pik = "", "", "", ""
        m = re.search(r"\b(S|L|E|SOFR|LIBOR|EURIBOR|SONIA)\s*\+\s*(\d+(?:\.\d+)?)", s, re.IGNORECASE)
        if m:
            ref_raw = m.group(1).upper()
            ref_map = {"S": "SOFR", "L": "LIBOR", "E": "Euribor",
                       "SOFR": "SOFR", "LIBOR": "LIBOR",
                       "EURIBOR": "Euribor", "SONIA": "SONIA"}
            reference = ref_map.get(ref_raw, ref_raw)
            spread = str(int(round(float(m.group(2)) / 100.0 * 100)) / 100.0)
            if spread.endswith(".0"):
                spread = spread[:-2]
            spread += "%"
        m = re.search(r"(\d+(?:\.\d+)?)\s*Floor", s, re.IGNORECASE)
        if m:
            floor = str(int(round(float(m.group(1)) / 100.0 * 100)) / 100.0)
            if floor.endswith(".0"):
                floor = floor[:-2]
            floor += "%"
        m = re.search(r"(\d+(?:\.\d+)?)\s*PIK", s, re.IGNORECASE)
        if m:
            pik = str(int(round(float(m.group(1)) / 100.0 * 100)) / 100.0)
            if pik.endswith(".0"):
                pik = pik[:-2]
            pik += "%"
        return reference, spread, floor, pik

    @staticmethod
    def _parse_month_year(value: str) -> str:
        m = re.fullmatch(r"(\d{1,2})/(\d{4})", (value or "").strip())
        if not m:
            return ""
        return f"{m.group(2)}-{int(m.group(1)):02d}-01"

    @staticmethod
    def _parse_percent_cell(value: str) -> str:
        if not value:
            return ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", value)
        if m:
            return m.group(1) + "%"
        if re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
            return value.strip() + "%"
        return ""

    @classmethod
    def _table_heading(cls, table: Dict[str, Any]) -> Optional[str]:
        text = re.sub(r"\s+", " ", table.get("text") or "")
        m = _HEADING_RE.search(text[:600])
        if not m:
            return None
        return f"{m.group(1)} {m.group(2)}, {m.group(3)}"
