"""
Custom scraper for GSBD (Goldman Sachs BDC, Inc.).

GSBD files its consolidated schedule of investments with this header layout:

    Investment | Industry | Interest Rate | Reference Rate and Spread |
    Maturity | Par | Cost | Fair Value | Footnotes

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Investment Type is not a column; it comes from section rows
    ("1st Lien/Senior Secured Debt -7.9%",
    "2nd Lien/Senior Secured Debt -4.1%", "Unsecured Debt -0.6%").
  * Reference Rate and Spread cell packs both: "S+4.75%" (SOFR),
    "C+4.75%" (CDOR), "L+…" (LIBOR), "E+…" (Euribor).
  * Maturity is MM/DD/YY ("11/01/30" -> 2030-11-01).
  * Equity rows use the Par column for shares.
  * Country rows ("Canada -7.9%"), section headers and "Total …" rows are
    skipped; the type is carried from the last type-section header.
  * The 10-Q contains the current SOI and the prior year-end SOI; only the
    current section ("as of June 30, 2026") is kept.
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
        if v and v not in ("$", "€", "%"):
            return v
    return ""


class GSBDScraper(BaseCompanyScraper):
    ticker = "GSBD"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Investment" not in t["text"][:400]:
                continue
            if "Reference Rate and Spread" not in t["text"][:1200]:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("GSBD: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_type = ""

        current_heading = None
        for t in soi_tables:
            heading = self._heading_before(filing_result, t)
            if heading:
                current_heading = heading
                break
        logger.info("GSBD: current SOI heading: %r", current_heading)

        for t in soi_tables:
            if current_heading is None:
                continue
            if self._heading_before(filing_result, t) != current_heading:
                continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, 0, 1)
                industry = _cell_range(row, 1, 2)
                low = company.lower()

                if not company:
                    continue

                if not industry:
                    if low.startswith("total"):
                        continue
                    sec_type = self._map_section_type(company)
                    if sec_type:
                        current_type = sec_type
                    continue

                record = self._row_to_record(row, company, industry, current_type)
                rows.append(record)

        logger.info("GSBD: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, industry: str,
                       sec_type: str) -> Dict[str, str]:
        cash = self._parse_percent_cell(_cell_range(row, 2, 3))
        reference, spread = self._parse_ref_spread(_cell_range(row, 3, 4))
        maturity = self._parse_date(_cell_range(row, 4, 5))
        par = self._clean_number(_cell_range(row, 7, 8))
        cost = self._clean_number(_cell_range(row, 10, 11))
        fair = self._clean_number(_cell_range(row, 13, 14))

        if sec_type in _EQUITY_TYPES:
            principal, shares = "", par
        else:
            principal, shares = par, ""

        return {
            "company_name": company,
            "investment_type": sec_type,
            "industry": industry,
            "cash_rate": cash,
            "pik_rate": "",
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
            if any(_norm_header(c) == "investment" for c in row):
                return i
        return None

    @staticmethod
    def _map_section_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "2nd lien" in s or "second lien" in s:
            return "Second Lien"
        if "1st lien" in s or "first lien" in s or "last-out" in s:
            return "First Lien"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "equity" in s:
            return "Other Equity"
        return ""

    @staticmethod
    def _parse_ref_spread(s: str) -> tuple:
        if not s:
            return "", ""
        m = re.search(r"\b(S|C|L|E|SOFR|CDOR|LIBOR|EURIBOR|SONIA)\s*\+\s*(\d+(?:\.\d+)?)%", s, re.IGNORECASE)
        if not m:
            return "", ""
        ref_raw = m.group(1).upper()
        ref_map = {"S": "SOFR", "C": "CDOR", "L": "LIBOR", "E": "Euribor",
                   "SOFR": "SOFR", "CDOR": "CDOR", "LIBOR": "LIBOR",
                   "EURIBOR": "Euribor", "SONIA": "SONIA"}
        return ref_map.get(ref_raw, ref_raw), m.group(2) + "%"

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
