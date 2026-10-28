"""
Custom scraper for TRIN (Trinity Capital Inc.).

TRIN files its consolidated schedule of investments with this header:

    Portfolio Company(1) | Type of Investment(2) | Investment Date(3) |
    Maturity Date | Interest Rate(4) | Principal Amount(5) | Cost |
    Fair Value(6) | Footnotes

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Section rows ("Debt Securities- United States") are skipped; industry
    rows ("Artificial Intelligence (\"AI\") Automation & Infrastructure")
    are carried down.
  * Continuation rows omit the company name and only carry the instrument
    (e.g. a second "Equipment Financing" tranche); the current company is
    reused.
  * Interest rate is prose: "Variable interest rate SOFR 3 Month Term +
    7.3%; EOT 0.0%" or "Fixed interest rate 12.7%; EOT 4.0%".
  * Dates are written out ("June 17, 2024").
  * Parsing stops at the "TOTAL INVESTMENTS" row of the current schedule.
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
    "portfoliocompany", "typeofinvestment", "investmentdate", "maturitydate",
    "interestrate", "principalamount", "cost", "fairvalue",
]

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


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


class TRINScraper(BaseCompanyScraper):
    ticker = "TRIN"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid:
                continue
            head = " ".join((c or "") for c in grid[0]).lower()
            if "portfolio company" not in head:
                continue
            if "type of investment" not in head:
                continue
            if "principal amount" not in head and "shares" not in head:
                continue
            if "fair value" not in head:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("TRIN: found %d SOI tables", len(soi))
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
        seen_block = False

        if not soi_tables:
            return []
        current_period = self._period_for(filing_result, soi_tables[0])
        logger.info("TRIN: current SOI period: %s", current_period)

        for t in soi_tables:
            period = self._period_for(filing_result, t)
            if current_period and period and period != current_period:
                continue
            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[header_idx])
            if "portfoliocompany" not in ranges or "fairvalue" not in ranges:
                continue

            for row in grid[header_idx + 1:]:
                row_text = " ".join((c or "") for c in row).lower()
                if "total investments" in row_text:
                    logger.info("TRIN: parsed %d investment rows", len(rows))
                    return rows
                company = _cell_range(row, *ranges["portfoliocompany"])
                instrument = _cell_range(row, *ranges["typeofinvestment"]) \
                    if "typeofinvestment" in ranges else ""
                low = company.lower()

                if low.startswith("sub-total") or low.startswith("subtotal"):
                    continue
                if low.startswith("debt securities") or low.startswith("equity securities"):
                    continue
                if low.startswith("total investments"):
                    logger.info("TRIN: parsed %d investment rows", len(rows))
                    return rows

                if not company and not instrument:
                    continue

                if not company:
                    # Continuation tranche: reuse the current company.
                    if not instrument:
                        continue
                    rows.append(self._row_to_record(row, current_company,
                                                    current_industry,
                                                    instrument, ranges))
                    continue

                if not instrument:
                    if low.startswith("total") or low.startswith("subtotal"):
                        continue
                    if "debt securities" in low:
                        if seen_block:
                            # Prior-period schedule restarts.
                            logger.info("TRIN: parsed %d investment rows",
                                        len(rows))
                            return rows
                        seen_block = True
                        continue
                    if "equity securities" in low:
                        continue
                    current_industry = re.sub(
                        r"\s*\([a-z0-9]+\)\s*$", "", company).strip()
                    continue

                if low.startswith("total"):
                    continue

                current_company = re.sub(r"\s*\(\d+\)\s*$", "", company).strip()
                rows.append(self._row_to_record(row, current_company,
                                                current_industry,
                                                instrument, ranges))

        logger.info("TRIN: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, industry: str,
                       instrument: str, ranges: Dict[str, tuple]) -> Dict[str, str]:
        def grp(key: str) -> str:
            rng = ranges.get(key)
            if not rng:
                return ""
            return _cell_range(row, *rng)

        inv_type = self._map_type(instrument)
        reference, spread, cash = self._parse_rate(grp("interestrate"))

        principal = self._clean_number(grp("principalamount"))
        cost = self._clean_number(grp("cost"))
        fair = self._clean_number(grp("fairvalue"))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", principal
        else:
            shares = ""

        return {
            "company_name": company,
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": cash,
            "pik_rate": "",
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": "",
            "acquisition_date": self._parse_date(grp("investmentdate")),
            "maturity_date": self._parse_date(grp("maturitydate")),
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
    def _map_type(instrument: str) -> str:
        s = (instrument or "").lower()
        if "second lien" in s:
            return "Second Lien"
        if "secured loan" in s or "first lien" in s or "term loan" in s:
            return "First Lien"
        if "equipment financing" in s:
            return "Other Debt"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s:
            return "Common Equity"
        if "equity" in s or "unit" in s:
            return "Other Equity"
        return "Other"

    @staticmethod
    def _parse_rate(value: str) -> tuple:
        """Return (reference, spread, cash)."""
        if not value:
            return "", "", ""
        s = value.replace("\u00a0", " ")

        vm = re.search(r"SOFR[^+]*\+\s*(\d+(?:\.\d+)?)\s*%", s, re.IGNORECASE)
        if vm:
            return "SOFR", vm.group(1) + "%", ""
        pm = re.search(r"PRIME[^+]*\+\s*(\d+(?:\.\d+)?)\s*%", s, re.IGNORECASE)
        if pm:
            return "Prime", pm.group(1) + "%", ""

        fm = re.search(r"Fixed[^0-9]*(\d+(?:\.\d+)?)\s*%", s, re.IGNORECASE)
        if fm:
            return "", "", fm.group(1) + "%"

        m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if m:
            return "", m.group(1) + "%", ""
        return "", "", ""

    @staticmethod
    def _parse_date(value: str) -> str:
        v = (value or "").strip()
        m = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", v)
        if m:
            month = _MONTHS.get(m.group(1).lower())
            if month:
                return f"{m.group(3)}-{month:02d}-{int(m.group(2)):02d}"
        m2 = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
        if m2:
            return f"{m2.group(3)}-{int(m2.group(1)):02d}-{int(m2.group(2)):02d}"
        return ""

    @staticmethod
    def _period_for(filing_result: Any, table: Dict[str, Any]) -> Optional[str]:
        html = (filing_result.text_map or {}).get(table.get("doc") or "", "")
        if not html:
            return None
        pos = table.get("pos") or 0
        raw = re.sub(r"<[^>]+>", " ", html[max(0, pos - 6000):pos])
        raw = re.sub(r"&#160;|&nbsp;", " ", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s+", " ", raw)
        m = re.search(r"Schedule\s+of\s+Investments\s+"
                      r"(January|February|March|April|May|June|July|August|"
                      r"September|October|November|December)\s+(\d{1,2}),\s*(\d{4})",
                      raw, re.IGNORECASE)
        if not m:
            return None
        return f"{m.group(1).upper()} {m.group(2)}, {m.group(3)}"
