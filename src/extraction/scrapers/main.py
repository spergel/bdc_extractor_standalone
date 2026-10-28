"""
Custom scraper for MAIN (Main Street Capital Corporation).

MAIN files its consolidated schedule of investments as HTML tables with this
header layout (each header cell spans 3 columns):

    Portfolio Company | Business Description | Type of Investment |
    Investment Date | Shares/Units | Total Rate |
    Reference Rate and Spread (6 cols) | PIK Rate | MaturityDate |
    Principal | Cost | Fair Value

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * A company's name and business description appear on their own row; the
    investment rows below have an empty company cell and carry the type,
    dates, rates and amounts.  The company name is therefore carried forward
    across subsequent rows.
  * Type comes from the "Type of Investment" cell:
    "Secured Debt" -> First Lien, "Common Stock" -> Common Equity, etc.
  * The Reference Rate and Spread group spans 6 columns: ref in the first 3
    ("SF+" = SOFR) and spread in the last 3.
  * Section headers ("Control Investments (5)") have a company cell but no
    business description and are skipped.  Subtotal rows have neither.
  * 10-Qs include the current-period and prior year-end comparative SOI;
    headings read "Consolidated Schedule of Investments (Continued)
    <date>", so prior sections are filtered on the date.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_REFERENCE_MAP = {
    "SF+": "SOFR",
    "SF": "SOFR",
    "S+": "SOFR",
    "S": "SOFR",
    "L+": "LIBOR",
    "L": "LIBOR",
    "E+": "Euribor",
    "E": "Euribor",
    "P+": "Prime",
    "P": "Prime",
    "SONIA+": "SONIA",
    "SONIA": "SONIA",
    "CORRA+": "CORRA",
    "CORRA": "CORRA",
}

_HEADING_RE = re.compile(
    r"(?:Consolidated\s+)?Schedule\s+of\s+Investments"
    r"(?:\s*\(Continued\))?\s+"
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),\s+(\d{4})",
    re.IGNORECASE,
)

_EQUITY_TYPES = {
    "Common Equity", "Preferred Equity", "Other Equity",
    "Partnership Interest", "Warrants",
}


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


class MAINScraper(BaseCompanyScraper):
    ticker = "MAIN"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Portfolio Company" not in t["text"][:600] and "Type of Investment" not in t["text"][:1000]:
                continue
            grid = self.parse_table_to_rows(t["html"])
            for row in grid[:4]:
                labels = {_norm_header(c) for c in row if c.strip()}
                if "portfoliocompany" in labels and "typeofinvestment" in labels:
                    soi.append(t)
                    break
        logger.info("MAIN: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_company = ""

        current_heading = None
        for t in soi_tables:
            heading = self._heading_before(filing_result, t)
            if heading:
                current_heading = heading
                break
        logger.info("MAIN: current SOI heading: %r", current_heading)

        for t in soi_tables:
            if current_heading:
                heading = self._heading_before(filing_result, t)
                if heading and heading != current_heading:
                    continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            pos = self._header_positions(grid[header_idx])
            if "portfoliocompany" not in pos or "typeofinvestment" not in pos:
                continue

            company_pos = pos["portfoliocompany"]
            desc_pos = pos.get("businessdescription")
            type_pos = pos["typeofinvestment"]
            ref_start = pos.get("referencerateandspread")

            if ref_start is not None:
                ref_span = self._span(grid[header_idx], ref_start)
                ref_rng = (ref_start, ref_start + 3)
                spread_rng = (ref_start + 3, ref_start + ref_span)
            else:
                ref_rng = spread_rng = None

            for row in grid[header_idx + 1:]:
                company = self._strip_footnote_refs(
                    _cell_range(row, company_pos, company_pos + 3))
                if company:
                    # Company header row.  Only rows that also carry a
                    # business description introduce a new portfolio company;
                    # others are section headers.
                    desc = (_cell_range(row, desc_pos, desc_pos + 3)
                            if desc_pos is not None else "")
                    if desc:
                        current_company = company
                    continue

                type_raw = _cell_range(row, type_pos, type_pos + 3)
                if not type_raw:
                    # Subtotal / continuation row.
                    continue

                record = self._row_to_record(row, current_company, type_raw,
                                             ref_rng, spread_rng, pos)
                rows.append(record)

        logger.info("MAIN: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, type_raw: str,
                       ref_rng: Optional[tuple], spread_rng: Optional[tuple],
                       pos: Dict[str, int]) -> Dict[str, str]:
        inv_type = self._map_type(type_raw)

        def grp(key: str) -> str:
            p = pos.get(key)
            if p is None:
                return ""
            return _cell_range(row, p, p + 3)

        acquisition = self._parse_date(grp("investmentdate"))
        shares = self._clean_number(grp("shares/units"))
        cash = self._parse_percent_cell(grp("totalrate"))
        ref_raw = _cell_range(row, *ref_rng) if ref_rng else ""
        spread = self._parse_percent_cell(
            _cell_range(row, *spread_rng)) if spread_rng else ""
        pik = self._parse_percent_cell(grp("pikrate"))
        maturity = self._parse_date(grp("maturitydate"))
        principal = self._clean_number(grp("principal"))
        cost = self._clean_number(grp("cost"))
        fair = self._clean_number(grp("fairvalue"))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", shares

        ref_clean = (ref_raw or "").strip().upper()
        reference = _REFERENCE_MAP.get(ref_clean, ref_raw)

        return {
            "company_name": company,
            "investment_type": inv_type,
            "industry": "",
            "cash_rate": cash,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": "",
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
    def _span(row: List[str], start: int) -> int:
        if start >= len(row):
            return 3
        cell = row[start]
        end = start
        while end < len(row) and row[end] == cell:
            end += 1
        return end - start

    @staticmethod
    def _strip_footnote_refs(value: str) -> str:
        if not value:
            return ""
        return re.sub(r"\s*(?:\(\d+\)\s*)+$", "", value.strip()).strip()

    @staticmethod
    def _map_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "secured debt" in s:
            return "First Lien"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "common stock" in s or "common equity" in s:
            return "Common Equity"
        if "preferred" in s:
            return "Preferred Equity"
        if "lp interest" in s or "lp interests" in s:
            return "Partnership Interest"
        if "membership" in s:
            return "Other Equity"
        if "member" in s or "unit" in s:
            return "Other Equity"
        if "equity" in s:
            return "Other Equity"
        if "warrant" in s:
            return "Warrants"
        return "Other"

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
                        table: Dict[str, Any]) -> Optional[str]:
        text = cls._context_before(filing_result, table)
        matches = list(_HEADING_RE.finditer(text))
        if not matches:
            return None
        m = matches[-1]
        return f"{m.group(1)} {m.group(2)}, {m.group(3)}"
