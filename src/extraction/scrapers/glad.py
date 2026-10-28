"""
Custom scraper for GLAD (Gladstone Capital Corporation).

GLAD files a very compact schedule of investments: everything about a
position lives in one "Company and Investment" cell.

    Company and Investment(A)(B)(K)(Q) | Principal/Shares/Units(I)(J) |
    Cost | Fair Value

Example row text:

    Sicilian Oven Restaurants LLC - Term Debt
        (S + 6.4%, 10.1% Cash, Due 11/2030)(C)(H)

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Section rows ("Secured First Lien Debt - 113.5%") set the investment
    type; industry rows ("Beverage, Food, and Tobacco - 7.1%") are carried
    down as the industry.
  * The instrument and its terms are parsed out of the company cell:
    company | instrument | (reference + spread, cash rate, PIK, due date).
  * "Line of Credit" rows are Revolvers.
  * Each table repeats its own title ("...JUNE 30, 2026..."), so the prior
    year-end copy (SEPTEMBER 30, 2025) is filtered out by period.
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

_SECTION_KEYWORDS = (
    "secured first lien", "secured second lien", "first lien", "second lien",
    "senior secured", "subordinated", "unsecured", "equity investments",
    "non-control", "control investments", "affiliate investments",
)

_HEADER_TITLE_RE = re.compile(
    r"SCHEDULE\s+OF\s+INVESTMENTS\s*(JUNE|MARCH|DECEMBER|SEPTEMBER)\s*"
    r"(\d{1,2}),?\s*(\d{4})",
    re.IGNORECASE,
)


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


class GLADScraper(BaseCompanyScraper):
    ticker = "GLAD"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "SCHEDULE OF INVESTMENTS" not in t["text"][:400].upper():
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("GLAD: found %d SOI tables", len(soi))
        return soi

    @staticmethod
    def _table_period(table: Dict[str, Any]) -> Optional[str]:
        m = _HEADER_TITLE_RE.search(table.get("text") or "")
        if not m:
            return None
        return f"{m.group(1).upper()} {int(m.group(2))}, {m.group(3)}"

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        if not soi_tables:
            return []

        current_period = self._table_period(soi_tables[0])
        logger.info("GLAD: current SOI period: %s", current_period)

        rows: List[Dict[str, str]] = []
        current_type = "First Lien"
        current_industry = ""

        for t in soi_tables:
            period = self._table_period(t)
            if period and current_period and period != current_period:
                continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[self._best_header_row(grid, header_idx)])
            if "principal/shares/units" not in ranges \
                    or "fairvalue" not in ranges:
                # Older filings use a layout this scraper does not model yet.
                continue

            for row in grid[header_idx + 1:]:
                text = _cell_range(row, *ranges["companyandinvestment"])
                low = text.lower()

                if not text:
                    continue
                if low.startswith("total") or low.startswith("subtotal"):
                    continue

                principal = _cell_range(row, *ranges["principal/shares/units"])
                cost = _cell_range(row, *ranges["cost"])
                fair = _cell_range(row, *ranges["fairvalue"])
                has_values = bool(principal or cost or fair)

                if not has_values:
                    if any(k in low for k in _SECTION_KEYWORDS):
                        sec_type = self._map_section_type(text)
                        if sec_type:
                            current_type = sec_type
                    else:
                        current_industry = re.sub(
                            r"\s*-\s*[\d.]+%?\s*$", "", text).strip()
                    continue

                if " - " not in text:
                    # Company-level subtotal row (values only).
                    continue

                company, instrument = text.split(" - ", 1)
                inv_type = self._map_instrument(instrument) or current_type
                record = self._row_to_record(row, company.strip(), instrument,
                                             current_industry, inv_type, ranges)
                rows.append(record)

        logger.info("GLAD: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, instrument: str,
                       industry: str, inv_type: str,
                       ranges: Dict[str, tuple]) -> Dict[str, str]:
        reference, spread, cash, pik, maturity = self._parse_terms(instrument)

        par = self._clean_number(_cell_range(row, *ranges["principal/shares/units"]))
        cost = self._clean_number(_cell_range(row, *ranges["cost"]))
        fair = self._clean_number(_cell_range(row, *ranges["fairvalue"]))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", par
        else:
            principal, shares = par, ""

        return {
            "company_name": company,
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": cash,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": "",
            "acquisition_date": "",
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
            if any(_norm_header(c) == "companyandinvestment" for c in row):
                return i
        return None


    @staticmethod
    def _best_header_row(grid, hi: int):
        """Headers sometimes span several rows; pick the richest one."""
        best_row, best_score = hi, -1
        for i in range(hi, min(hi + 3, len(grid))):
            pos = {_norm_header(c) for c in grid[i] if (c or "").strip()}
            score = len(pos & {"companyandinvestment", "cost", "fairvalue",
                               "principal/shares/units", "principalamount",
                               "principal", "parvalue", "shares/units"})
            if score > best_score:
                best_row, best_score = i, score
        return best_row

    @staticmethod
    def _column_ranges(row: List[str]) -> Dict[str, tuple]:
        keys = ["companyandinvestment", "principal/shares/units", "cost",
                "fairvalue"]
        pos: Dict[str, int] = {}
        for i, c in enumerate(row):
            n = _norm_header(c)
            if n and n not in pos:
                pos[n] = i
        # Older filings use different labels for the position column.
        if "principal/shares/units" not in pos:
            for alt in ("principalamount", "principal", "parvalue", "par",
                        "amount", "shares/units", "shares"):
                if alt in pos:
                    pos["principal/shares/units"] = pos[alt]
                    break
        ordered = sorted((pos[k], k) for k in keys if k in pos)
        ranges: Dict[str, tuple] = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _map_section_type(raw: str) -> str:
        s = (raw or "").lower()
        if "first lien" in s:
            return "First Lien"
        if "second lien" in s:
            return "Second Lien"
        if "subordinated" in s or "mezzanine" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "equity" in s:
            return "Other Equity"
        return ""

    @staticmethod
    def _map_instrument(instrument: str) -> str:
        s = (instrument or "").lower()
        if "line of credit" in s or "revolver" in s:
            return "Revolver"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s or "membership" in s:
            return "Common Equity"
        if "warrant" in s:
            return "Warrants"
        if "term debt" in s or "term loan" in s or "senior debt" in s:
            return "First Lien"
        if "subordinated" in s or "mezzanine" in s:
            return "Subordinated Debt"
        if "equity" in s or "units" in s:
            return "Other Equity"
        return ""

    @staticmethod
    def _parse_terms(instrument: str) -> tuple:
        """Return (reference, spread, cash, pik, maturity)."""
        s = instrument or ""
        reference = ""
        spread = ""
        cash = ""
        pik = ""
        maturity = ""

        m = re.search(r"\(\s*([A-Z])\s*\+\s*(\d+(?:\.\d+)?)\s*%", s)
        if m:
            ref_map = {"S": "SOFR", "L": "LIBOR", "P": "Prime", "E": "Euribor"}
            reference = ref_map.get(m.group(1).upper(), m.group(1).upper())
            spread = m.group(2) + "%"
        else:
            fm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:Fixed|Cash)", s)
            if fm:
                spread = fm.group(1) + "%"

        cm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*Cash", s)
        if cm:
            cash = cm.group(1) + "%"

        pm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", s)
        if pm:
            pik = pm.group(1) + "%"

        dm = re.search(r"Due\s+(\d{1,2})/(\d{4})", s, re.IGNORECASE)
        if dm:
            maturity = f"{dm.group(2)}-{int(dm.group(1)):02d}-01"

        return reference, spread, cash, pik, maturity
