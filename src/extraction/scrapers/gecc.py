"""
Custom scraper for GECC (Great Elm Capital Corp.).

Schedule of investments:

    Portfolio Company | Industry | Security | Notes | Interest Rate |
    Initial Acquisition Date | Maturity Date | Par Amount / Quantity |
    Cost | Fair Value | Percentage of Class

Column map (27 columns):

    company      0     industry     2     security      4
    notes        6     rate         8     acquired     10
    maturity    12     par      14-15     cost     18-19
    fair value  22-23  % of class   26

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * The interest-rate cell packs reference, spread and all-in rate:
    "6MSOFR+5.25% (9.34%)".
  * Parsing stops at the prior-period copy of the schedule.
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


def _norm_header(label: str) -> str:
    s = (label or "").replace("\u200b", " ")
    s = re.sub(r"\s*\([a-zA-Z0-9]+\)\s*", "", s)
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9/%$]", "", s)


def _cell_range(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").replace("\u200b", "").strip()
        if v and v not in ("$", "%", "—", "-"):
            return v
    return ""


class GECCScraper(BaseCompanyScraper):
    ticker = "GECC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid or len(grid[0]) < 20:
                continue
            head = " ".join(_norm_header(c) for c in grid[0])
            if "portfoliocompany" not in head:
                continue
            if "fairvalue" not in head or "cost" not in head:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("GECC: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_company = ""
        current_month = self._month_before(filing_result, soi_tables[0]) \
            if soi_tables else ""

        for t in soi_tables:
            month = self._month_before(filing_result, t)
            if current_month and month and month != current_month:
                break

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[header_idx])

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, *ranges["company"])
                if company:
                    current_company = company
                else:
                    company = current_company
                if not company:
                    continue
                low = company.lower()
                if low.startswith("total investments"):
                    # The long-term subtotal precedes short-term investments.
                    if "excluding" in low:
                        continue
                    logger.info("GECC: parsed %d investment rows", len(rows))
                    return rows
                if low.startswith("total"):
                    continue

                security = _cell_range(row, *ranges["security"]) \
                    if "security" in ranges else ""
                fair = _cell_range(row, *ranges["fairvalue"]) \
                    if "fairvalue" in ranges else ""
                par = _cell_range(row, *ranges["par"]) \
                    if "par" in ranges else ""

                if not security and not fair and not par:
                    continue

                rows.append(self._row_to_record(row, company, security, ranges))

        logger.info("GECC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, security: str,
                       ranges: Dict[str, tuple]) -> Dict[str, str]:
        def grp(key: str) -> str:
            rng = ranges.get(key)
            return _cell_range(row, *rng) if rng else ""

        inv_type = self._map_type(security)

        reference = ""
        spread = ""
        cash = ""
        rate_raw = grp("rate")
        if rate_raw:
            m = re.search(r"(SOFR|LIBOR|PRIME|BSBY)", rate_raw, re.IGNORECASE)
            if m:
                reference = {"SOFR": "SOFR", "LIBOR": "LIBOR", "PRIME": "Prime",
                             "BSBY": "BSBY"}[m.group(1).upper()]
            m = re.search(r"(?:SOFR|LIBOR|PRIME|BSBY)\s*\+\s*(\d+(?:\.\d+)?)\s*%",
                          rate_raw, re.IGNORECASE)
            if m:
                spread = m.group(1) + "%"
            m = re.search(r"\(\s*(\d+(?:\.\d+)?)\s*%\s*\)", rate_raw)
            if m:
                cash = m.group(1) + "%"
            else:
                m = re.search(r"^(\d+(?:\.\d+)?)\s*%", rate_raw)
                if m:
                    cash = m.group(1) + "%"

        principal = self._clean_number(grp("par"))
        cost = self._clean_number(grp("cost"))
        fair = self._clean_number(grp("fairvalue"))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", principal
        else:
            shares = ""

        pct = ""

        return {
            "company_name": self._clean_company(company),
            "investment_type": inv_type,
            "industry": grp("industry"),
            "cash_rate": cash,
            "pik_rate": "",
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": "",
            "acquisition_date": self._parse_date(grp("acquisition")),
            "maturity_date": self._parse_date(grp("maturity")),
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
    def _header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid[:3]):
            joined = " ".join(_norm_header(c) for c in row)
            if "portfoliocompany" in joined and "fairvalue" in joined:
                return i
        return None

    @staticmethod
    def _column_ranges(row: List[str]) -> Dict[str, tuple]:
        pos: Dict[str, int] = {}
        for i, c in enumerate(row):
            n = _norm_header(c)
            if not n or i in pos.values():
                continue
            if n.startswith("portfoliocompany"):
                pos.setdefault("company", i)
            elif n.startswith("industry"):
                pos.setdefault("industry", i)
            elif n.startswith("security"):
                pos.setdefault("security", i)
            elif n.startswith("interestrate"):
                pos.setdefault("rate", i)
            elif n.startswith("initialacquisition"):
                pos.setdefault("acquisition", i)
            elif n.startswith("maturity"):
                pos.setdefault("maturity", i)
            elif n.startswith("paramount"):
                pos.setdefault("par", i)
            elif n.startswith("cost"):
                pos.setdefault("cost", i)
            elif n.startswith("fairvalue"):
                pos.setdefault("fairvalue", i)
            # "Percentage of Class" is not % of net assets - ignore it.
        ordered = sorted((v, k) for k, v in pos.items())
        ranges: Dict[str, tuple] = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _map_type(security: str) -> str:
        s = (security or "").lower()
        if "2nd lien" in s or "second lien" in s:
            return "Second Lien"
        if "1st lien" in s or "first lien" in s or "senior secured" in s:
            return "First Lien"
        if "subordinat" in s:
            return "Subordinated Debt"
        if "unsecured" in s or "note" in s:
            return "Unsecured Debt"
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s or "equity" in s or "unit" in s:
            return "Common Equity"
        return "Other"

    @staticmethod
    def _clean_company(value: str) -> str:
        s = (value or "").replace("\u200b", "").strip()
        return re.sub(r"(\s*\([^)]*\))+$", "", s).strip().strip(",").strip()

    @staticmethod
    def _parse_date(value: str) -> str:
        v = (value or "").strip()
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
        if m:
            return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        return ""

    @staticmethod
    def _month_before(filing_result: Any, table: Dict[str, Any]) -> str:
        html = (filing_result.text_map or {}).get(table.get("doc") or "", "")
        if not html:
            return ""
        pos = table.get("pos") or 0
        raw = re.sub(r"<[^>]+>", " ", html[max(0, pos - 6000):pos])
        raw = re.sub(r"&#160;|&nbsp;|\u200b", " ", raw)
        raw = re.sub(r"\s+", " ", raw)
        ms = re.findall(r"\b(January|February|March|April|May|June|July|August|"
                        r"September|October|November|December)\b", raw)
        return ms[-1] if ms else ""
