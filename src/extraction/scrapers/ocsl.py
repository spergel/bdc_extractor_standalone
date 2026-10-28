"""
Custom scraper for OCSL (Oaktree Specialty Lending Corporation).

OCSL files a static consolidated schedule of investments with this header
layout (each header cell spans 3 columns):

    Portfolio Company | Industry | Type of Investment | Index | Spread |
    Cash Interest Rate | PIK | Maturity Date | Shares | Principal | Cost |
    Fair Value | Notes

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Every data row carries company, industry, type and amounts.
  * The 10-Q also contains rollforward schedules ("Fair Value at
    October 1, 2025 … Fair Value at June 30, 2026") and other schedules;
    only the static tables (those with a plain "Fair Value" column) are
    used, which avoids double counting.
  * Index cell ("SOFR+") -> reference; Spread/Cash/PIK cells hold the
    rate numbers with a separate "%" cell.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_REFERENCE_MAP = {
    "SOFR+": "SOFR",
    "SOFR": "SOFR",
    "LIBOR+": "LIBOR",
    "LIBOR": "LIBOR",
    "EURIBOR+": "Euribor",
    "EURIBOR": "Euribor",
    "PRIME+": "Prime",
    "PRIME": "Prime",
    "SONIA+": "SONIA",
    "SONIA": "SONIA",
    "CORRA+": "CORRA",
    "CORRA": "CORRA",
}

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
    return re.sub(r"[^a-z0-9/%]", "", s)


def _cell_range(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip()
        if v and v not in ("$", "€", "%"):
            return v
    return ""


class OCSLScraper(BaseCompanyScraper):
    ticker = "OCSL"

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
                # Static SOI tables have Industry, a type column and a plain
                # Fair Value column.  Rollforward tables have
                # "Fair Value at ..." columns and are excluded.
                if ("portfoliocompany" in labels and "industry" in labels
                        and "fairvalue" in labels
                        and ("typeofinvestment" in labels or "investmenttype" in labels)):
                    soi.append(t)
                    break
        logger.info("OCSL: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []

        # Keep only the current-period SOI section.  The filing also contains
        # the prior year-end static SOI ("September 30, 2025") and joint
        # venture portfolio tables inside the notes, which must be excluded.
        current_heading = None
        for t in soi_tables:
            heading = self._heading_before(filing_result, t)
            if heading:
                current_heading = heading
                break
        logger.info("OCSL: current SOI heading: %r", current_heading)

        for t in soi_tables:
            if current_heading:
                heading = self._heading_before(filing_result, t)
                if heading != current_heading:
                    continue
            else:
                continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            pos = self._header_positions(grid[header_idx])
            if "portfoliocompany" not in pos or "fairvalue" not in pos:
                continue
            type_key = ("typeofinvestment" if "typeofinvestment" in pos
                        else "investmenttype")
            if type_key not in pos:
                continue

            company_rng = (pos["portfoliocompany"], pos["portfoliocompany"] + 3)
            type_rng = (pos[type_key], pos[type_key] + 3)

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, *company_rng)
                type_raw = _cell_range(row, *type_rng)
                low = company.lower()

                if not company and not type_raw:
                    continue
                if low.startswith("total") or low.startswith("subtotal"):
                    continue
                if _norm_header(company) in ("portfoliocompany", "typeofinvestment"):
                    continue
                if not type_raw or _norm_header(company) == _norm_header(type_raw):
                    # Section header row ("Control Investments").
                    continue

                record = self._row_to_record(row, company, type_raw, pos)
                rows.append(record)

        logger.info("OCSL: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, type_raw: str,
                       pos: Dict[str, int]) -> Dict[str, str]:
        inv_type = self._map_type(type_raw)
        industry = _cell_range(row, pos["industry"], pos["industry"] + 3)

        ref_raw = _cell_range(row, pos["index"], pos["index"] + 3)
        ref_clean = (ref_raw or "").strip().upper()
        reference = _REFERENCE_MAP.get(ref_clean, ref_raw)

        spread = self._parse_percent_cell(
            _cell_range(row, pos["spread"], pos["spread"] + 3))
        cash = self._parse_percent_cell(
            _cell_range(row, pos["cashinterestrate"],
                        pos["cashinterestrate"] + 3))
        pik = self._parse_percent_cell(
            _cell_range(row, pos["pik"], pos["pik"] + 3))
        maturity = self._parse_date(
            _cell_range(row, pos["maturitydate"], pos["maturitydate"] + 3))

        shares = self._clean_number(
            _cell_range(row, pos["shares"], pos["shares"] + 3))
        principal = self._clean_number(
            _cell_range(row, pos["principal"], pos["principal"] + 3))
        cost = self._clean_number(
            _cell_range(row, pos["cost"], pos["cost"] + 3))
        fair = self._clean_number(
            _cell_range(row, pos["fairvalue"], pos["fairvalue"] + 3))

        if inv_type in _EQUITY_TYPES:
            principal = ""
        else:
            shares = ""

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
    def _map_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "revolv" in s:
            return "Revolver"
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "clo note" in s or "fixed rate bond" in s or "credit linked note" in s:
            return "Other Debt"
        if "common stock" in s or "common equity" in s:
            return "Common Equity"
        if "preferred" in s:
            return "Preferred Equity"
        if "membership" in s:
            return "Other Equity"
        if "earnout" in s:
            return "Other Equity"
        if "lp interest" in s or "lp units" in s:
            return "Partnership Interest"
        if "warrant" in s:
            return "Warrants"
        if "equity" in s:
            return "Other Equity"
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
