"""
Custom scraper for CSWC (Capital Southwest Corporation).

CSWC files its consolidated schedule of investments with this header layout
(3-column header groups):

    Portfolio Company | Type of Investment | Current Interest Rate |
    Acquisition Date | Maturity | Principal | Cost | Fair Value |
    % of Net Assets

The "Current Interest Rate" group contains sub-columns:

    Coupon | Reference | Spread | Floor | PIK

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Company header rows are UPPERCASE ("BRANDNER DESIGN, LLC"); industry
    rows are title case ("Building & Infrastructure Products") and are
    carried down.
  * Data rows usually leave the company cell empty; the company/industry
    from the preceding header rows is used.  Type is per row
    ("First Lien", "Revolving Loan", "Common Equity", "Warrants").
  * Rows with only Cost/Fair Value (company subtotals) are skipped.
  * Reference "SOFR (Q)" -> SOFR; "Fixed" -> no reference.
  * The filing contains the current SOI and the prior quarter-end SOI;
    only tables titled "June 30, 2026" are kept.
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
    s = re.sub(r"[^a-z0-9/%$]", "", s)
    return s


def _cell_range(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip()
        if v and v not in ("$", "€", "%", "£", "—", "-"):
            return v
    return ""


class CSWCScraper(BaseCompanyScraper):
    ticker = "CSWC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Portfolio" not in t["text"][:500]:
                continue
            if "Type of Investment" not in t["text"][:2000]:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("CSWC: found %d SOI tables", len(soi))
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
            heading = self._table_heading(t)
            if heading:
                current_heading = heading
                break
        logger.info("CSWC: current SOI heading: %r", current_heading)

        last_heading = None
        for t in soi_tables:
            heading = self._table_heading(t)
            if heading:
                last_heading = heading
            effective = heading or last_heading
            if current_heading is None or effective != current_heading:
                continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, 0, 3)
                type_raw = _cell_range(row, 6, 9)
                low = company.lower()

                if not company and not type_raw:
                    # Subtotal row (cost/fair only) or empty.
                    continue

                if not type_raw:
                    if low.startswith("total") or low.startswith("subtotal"):
                        continue
                    if low in ("debt investments", "equity investments",
                               "investments", "cash and cash equivalents",
                               "cash & cash equivalents"):
                        continue
                    if company == company.upper() and any(ch.isalpha() for ch in company):
                        current_company = company
                    else:
                        current_industry = company
                    continue

                if low.startswith("coupon") or low.startswith("reference"):
                    # Repeated rate sub-header row.
                    continue

                if company:
                    if company == company.upper() and any(ch.isalpha() for ch in company):
                        current_company = company
                    else:
                        current_industry = company

                record = self._row_to_record(row, current_company, type_raw,
                                             current_industry)
                rows.append(record)

        logger.info("CSWC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, type_raw: str,
                       industry: str) -> Dict[str, str]:
        inv_type = self._map_type(type_raw)

        coupon = self._parse_percent_cell(_cell_range(row, 12, 15))
        ref_raw = _cell_range(row, 15, 18).strip().upper()
        ref_raw = re.sub(r"\s*\(Q\)\s*$", "", ref_raw)
        ref_map = {"SOFR": "SOFR", "LIBOR": "LIBOR", "EURIBOR": "Euribor",
                   "SONIA": "SONIA", "CDOR": "CDOR", "PRIME": "Prime",
                   "FIXED": ""}
        reference = ref_map.get(ref_raw, ref_raw)
        spread = self._parse_percent_cell(_cell_range(row, 18, 21))
        floor = self._parse_percent_cell(_cell_range(row, 21, 24))
        pik = self._parse_percent_cell(_cell_range(row, 24, 27))

        acquisition = self._parse_date(_cell_range(row, 30, 33))
        maturity = self._parse_date(_cell_range(row, 33, 36))
        par = self._clean_number(_cell_range(row, 39, 42))
        cost = self._clean_number(_cell_range(row, 42, 45))
        fair = self._clean_number(_cell_range(row, 45, 48))
        pct = self._parse_percent_cell(_cell_range(row, 51, 54))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", par
        else:
            principal, shares = par, ""

        return {
            "company_name": company,
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": coupon,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": floor,
            "acquisition_date": acquisition,
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
            if any(_norm_header(c).startswith("typeofinvestment") for c in row):
                return i
        return None

    @staticmethod
    def _map_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s or "unitranche" in s or "term loan" in s \
                or "delayed draw" in s or "tranche" in s:
            return "First Lien"
        if "revolving" in s or "revolver" in s:
            return "Revolver"
        if "third lien" in s or "promissory note" in s:
            return "Other Debt"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "common equity" in s or "common stock" in s:
            return "Common Equity"
        if "preferred" in s:
            return "Preferred Equity"
        if "warrant" in s:
            return "Warrants"
        if "equity" in s:
            return "Other Equity"
        return "Other"

    @staticmethod
    def _parse_date(value: str) -> str:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", (value or "").strip())
        if m:
            return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        return ""

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
