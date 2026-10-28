"""
Custom scraper for NMFC (New Mountain Finance Corporation).

NMFC files its consolidated schedule of investments with this header layout
(each header cell spans 3 columns):

    Portfolio Company, Location and Industry | Type of Investment |
    Reference | Spread | Total Coupon | Acquisition Date |
    Maturity/Expiration Date | Principal, Par Value or Shares |
    Cost | Fair Value | Percent of Net Assets

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * A company header row contains only the company name; the following
    position rows put the industry in the company column and the instrument
    in the Type column ("First lien (3)(10)(12)" -> footnote refs stripped).
  * Company-level subtotal rows (cost/fair value/% NAV only, no type) are
    skipped - the individual positions already sum to them.
  * Reference cells may carry a suffix ("SOFR(Q)" -> SOFR).
  * Dates are MM/YYYY ("01/2025" -> 2025-01-01).
  * The 10-Q contains the current SOI and the prior year-end SOI; only the
    current heading ("June 30, 2026") is kept.
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
    r"(?:As\s+of\s+)?(?:Consolidated\s+)?Schedule\s+of\s+Investments[^\n]{0,80}?"
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
        if v and v not in ("$", "€", "%", "£", "—", "-"):
            return v
    return ""


class NMFCScraper(BaseCompanyScraper):
    ticker = "NMFC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Portfolio Company" not in t["text"][:500]:
                continue
            grid = self.parse_table_to_rows(t["html"])
            for row in grid[:4]:
                labels = {_norm_header(c) for c in row if c.strip()}
                if ("typeofinvestment" in labels or "type-of-investment" in labels) \
                        and "spread" in labels:
                    soi.append(t)
                    break
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("NMFC: found %d SOI tables", len(soi))
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
            heading = self._heading_before(filing_result, t)
            if heading:
                current_heading = heading
                break
        logger.info("NMFC: current SOI heading: %r", current_heading)

        last_heading = None
        for t in soi_tables:
            heading = self._heading_before(filing_result, t)
            if heading:
                last_heading = heading
            effective = heading or last_heading
            if current_heading is None or effective != current_heading:
                continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            pos = self._header_positions(grid[header_idx])
            if "typeofinvestment" not in pos and "type-of-investment" not in pos:
                continue
            type_key = ("typeofinvestment" if "typeofinvestment" in pos
                        else "type-of-investment")

            company_rng = (0, 3)
            type_rng = (pos[type_key], pos[type_key] + 3)

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, *company_rng)
                type_raw = _cell_range(row, *type_rng)
                low = company.lower()

                if not company and not type_raw:
                    # Subtotal row (cost/fair/% NAV only) or empty row.
                    continue

                if not type_raw:
                    if low.startswith("total") or low.startswith("subtotal"):
                        continue
                    if low in ("non-controlled/non-affiliate investments",
                               "non-controlled/non-affiliated investments",
                               "funded debt investments - united states",
                               "equity investments", "investments",
                               "cash and cash equivalents",
                               "cash & cash equivalents"):
                        continue
                    current_company = company
                    continue

                if company:
                    current_industry = company
                industry = current_industry

                record = self._row_to_record(row, current_company, type_raw,
                                             industry, pos)
                rows.append(record)

        logger.info("NMFC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, type_raw: str,
                       industry: str, pos: Dict[str, int]) -> Dict[str, str]:
        inv_type = self._map_type(self._clean_type(type_raw))

        def grp(key: str) -> str:
            p = pos.get(key)
            if p is None:
                return ""
            return _cell_range(row, p, p + 3)

        ref_raw = grp("reference").strip().upper()
        ref_raw = re.sub(r"\s*\(Q\)\s*$", "", ref_raw)
        ref_map = {"SOFR": "SOFR", "LIBOR": "LIBOR", "EURIBOR": "Euribor",
                   "SONIA": "SONIA", "CDOR": "CDOR", "PRIME": "Prime"}
        reference = ref_map.get(ref_raw, ref_raw)

        spread = self._parse_percent_cell(grp("spread"))
        coupon_raw = grp("totalcoupon")
        cash, pik = self._split_coupon(coupon_raw)

        acquisition = self._parse_month_year(grp("acquisitiondate"))
        maturity = self._parse_month_year(grp("maturity/expirationdate"))
        par = self._clean_number(grp("principalamount,parvalueorshares"))
        cost = self._clean_number(grp("cost"))
        fair = self._clean_number(grp("fairvalue"))
        pct = self._parse_percent_cell(grp("percentofnetassets"))

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
            if any(_norm_header(c) in ("typeofinvestment", "type-of-investment")
                   for c in row):
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
    def _clean_type(value: str) -> str:
        return re.sub(r"\s*(\(\d+\))+\s*$", "", (value or "").strip()).strip()

    @staticmethod
    def _map_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "second lien" in s or "second-lien" in s:
            return "Second Lien"
        if "first lien" in s or "first-lien" in s or "unitranche" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "common stock" in s or "common equity" in s:
            return "Common Equity"
        if "preferred" in s:
            return "Preferred Equity"
        if "warrant" in s:
            return "Warrants"
        if "equity" in s or "unit" in s or "llc interest" in s:
            return "Other Equity"
        return "Other"

    @staticmethod
    def _split_coupon(s: str) -> tuple:
        """Return (cash, pik) from a coupon cell like '8.91%' or
        '9.29% (incl. 4.00% PIK)'."""
        if not s:
            return "", ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if not m:
            return "", ""
        cash = m.group(1) + "%"
        pik_m = re.search(r"(?:incl\.?\s*)?(\d+(?:\.\d+)?)\s*%\s*PIK", s, re.IGNORECASE)
        pik = pik_m.group(1) + "%" if pik_m else ""
        return cash, pik

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
