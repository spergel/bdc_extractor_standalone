"""
Custom scraper for HTGC (Hercules Capital, Inc.).

HTGC files its consolidated schedule of investments as HTML tables with this
header layout (each header cell spans 3 columns):

    Portfolio Company | Type of Investment | Maturity Date |
    Interest Rate and Floor | Principal Amount | Cost | Value | Footnotes

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Debt rows: type = "Senior Secured" / "Unsecured" / "Convertible Debt";
    the Interest cell packs reference/spread/floor/PIK, e.g.
    "1-month SOFR + 8.14%, Floor rate 9.14%" or
    "PIK Interest 3-month SOFR + 9.99%, Floor rate 10.99%" or
    "Prime + 0.65%, Floor rate 9.15%, PIK Interest 2.00%, 5.05% Exit Fee".
  * Equity rows: the type cell holds "Equity" and the specific instrument
    ("Common Stock", "Preferred Series C", "SAFE", …) sits in the Interest
    column; the acquisition date sits in the Maturity column and the
    Principal column holds shares/units.
  * Industry rows ("Application Software") and subtotal/total rows span the
    whole row; the company name is carried down across continuation rows.
  * 10-Qs include the current SOI, a prior year-end SOI and a separate
    "Schedule of Investments in and advances to affiliates" (excluded).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_REFERENCE_MAP = {
    "SOFR": "SOFR",
    "PRIME": "Prime",
    "LIBOR": "LIBOR",
    "EURIBOR": "Euribor",
    "SONIA": "SONIA",
    "CORRA": "CORRA",
}

_EQUITY_TYPES = {
    "Common Equity", "Preferred Equity", "Other Equity",
    "Partnership Interest", "Warrants",
}

_HEADING_RE = re.compile(
    r"Schedule\s+of\s+Investments([^\n]{0,80}?)"
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),\s+(\d{4})",
    re.IGNORECASE,
)


def _norm_header(label: str) -> str:
    s = re.sub(r"\s*\([a-zA-Z0-9]+\)\s*", "", label or "")
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9/%]", "", s)


def _cell_range(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip()
        if v and v not in ("$", "€", "%"):
            return v
    return ""


class HTGCScraper(BaseCompanyScraper):
    ticker = "HTGC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Portfolio Company" not in t["text"][:600]:
                continue
            grid = self.parse_table_to_rows(t["html"])
            for row in grid[:4]:
                labels = {_norm_header(c) for c in row if c.strip()}
                if "portfoliocompany" in labels and "typeofinvestment" in labels:
                    soi.append(t)
                    break
        logger.info("HTGC: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_company = ""
        current_industry = ""

        current_heading = None
        for t in soi_tables:
            heading, is_affiliate = self._heading_before(filing_result, t)
            if heading and not is_affiliate:
                current_heading = heading
                break
        logger.info("HTGC: current SOI heading: %r", current_heading)

        for t in soi_tables:
            if current_heading:
                heading, is_affiliate = self._heading_before(filing_result, t)
                if is_affiliate or heading != current_heading:
                    continue
            else:
                continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            pos = self._header_positions(grid[header_idx])
            if "portfoliocompany" not in pos or "typeofinvestment" not in pos:
                continue

            company_rng = (pos["portfoliocompany"], pos["portfoliocompany"] + 3)
            type_rng = (pos["typeofinvestment"], pos["typeofinvestment"] + 3)
            # All SOI variants (debt / equity) share the same column
            # positions; only the header labels differ.
            date_rng = (6, 9)
            rate_rng = (9, 12)
            prin_rng = (12, 15)
            cost_rng = (18, 21)
            value_rng = (24, 27)

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, *company_rng)
                type_raw = _cell_range(row, *type_rng)
                low = company.lower()

                if not company and not type_raw:
                    continue

                if not type_raw:
                    # Section header ("Debt Investments", "Equity
                    # Investments", "Cash & Cash Equivalents").
                    continue

                if _norm_header(company) == _norm_header(type_raw):
                    # Industry / subtotal / total row spanning the whole row.
                    if low.startswith("total") or low.startswith("subtotal"):
                        continue
                    current_industry = company
                    continue

                if company:
                    current_company = company
                else:
                    company = current_company

                record = self._row_to_record(
                    row, company, type_raw, current_industry,
                    date_rng, rate_rng, prin_rng, cost_rng, value_rng)
                rows.append(record)

        logger.info("HTGC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, type_raw: str,
                       industry: str,
                       date_rng: tuple, rate_rng: tuple, prin_rng: tuple,
                       cost_rng: tuple, value_rng: tuple) -> Dict[str, str]:
        type_low = type_raw.lower()
        date_cell = _cell_range(row, *date_rng)
        rate_cell = _cell_range(row, *rate_rng)
        par = self._clean_number(_cell_range(row, *prin_rng))
        cost = self._clean_number(_cell_range(row, *cost_rng))
        fair = self._clean_number(_cell_range(row, *value_rng))

        if type_low == "equity":
            inv_type = self._map_specific_type(rate_cell)
            acquisition = self._parse_date(date_cell)
            maturity = ""
            principal, shares = "", par
            reference = spread = floor = cash = pik = ""
        elif type_low == "warrant":
            inv_type = "Warrants"
            acquisition = self._parse_date(date_cell)
            maturity = ""
            principal, shares = "", par
            reference = spread = floor = cash = pik = ""
        else:
            inv_type = self._map_debt_type(type_raw)
            reference, spread, floor, cash, pik = self._parse_interest(rate_cell)
            acquisition = ""
            maturity = self._parse_month_year(date_cell) or self._parse_date(date_cell)
            principal, shares = par, ""

        return {
            "company_name": company,
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": cash,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": floor,
            "acquisition_date": acquisition,
            "maturity_date": maturity,
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
            if any(_norm_header(c) == "portfoliocompany" for c in row):
                return i
        return None

    @staticmethod
    def _header_positions(row: List[str]) -> Dict[str, int]:
        pos: Dict[str, int] = {}
        for i, c in enumerate(row):
            n = _norm_header(c)
            if n and n not in pos:
                pos[n] = i
        return pos

    @staticmethod
    def _map_debt_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "second lien" in s:
            return "Second Lien"
        if "senior secured" in s or "first lien" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s or "convertible" in s:
            return "Unsecured Debt"
        if "revolv" in s:
            return "Revolver"
        return "Other"

    @staticmethod
    def _map_specific_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return "Other Equity"
        if "common stock" in s or "ordinary share" in s or "common unit" in s:
            return "Common Equity"
        if "preferred" in s:
            return "Preferred Equity"
        if "warrant" in s:
            return "Warrants"
        if "lp interest" in s or "lp unit" in s:
            return "Partnership Interest"
        return "Other Equity"

    @staticmethod
    def _parse_interest(s: str) -> tuple:
        """Return (reference, spread, floor, cash, pik)."""
        if not s:
            return "", "", "", "", ""
        reference, spread, floor, pik = "", "", "", ""
        m = re.search(r"(?:1-month|3-month|6-month)?\s*(SOFR|PRIME|LIBOR|EURIBOR|SONIA|CORRA)\s*\+\s*(\d+(?:\.\d+)?)%", s, re.IGNORECASE)
        if m:
            reference = _REFERENCE_MAP.get(m.group(1).upper(), m.group(1).upper())
            spread = m.group(2) + "%"
        m = re.search(r"Floor rate\s+(\d+(?:\.\d+)?)%", s, re.IGNORECASE)
        if m:
            floor = m.group(1) + "%"
        m = re.search(r"PIK Interest\s+(\d+(?:\.\d+)?)%", s, re.IGNORECASE)
        if m:
            pik = m.group(1) + "%"
        return reference, spread, floor, "", pik

    @staticmethod
    def _parse_month_year(value: str) -> str:
        m = re.fullmatch(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})", (value or "").strip(), re.IGNORECASE)
        if not m:
            return ""
        months = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
                  "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
        return f"{m.group(2)}-{months[m.group(1).lower()]:02d}-01"

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

    @classmethod
    def _heading_before(cls, filing_result: Any,
                        table: Dict[str, Any]) -> tuple:
        text = cls._context_before(filing_result, table)
        matches = list(_HEADING_RE.finditer(text))
        if not matches:
            return None, False
        m = matches[-1]
        between = m.group(1)
        is_affiliate = "affiliate" in between.lower()
        return f"{m.group(2)} {m.group(3)}, {m.group(4)}", is_affiliate
