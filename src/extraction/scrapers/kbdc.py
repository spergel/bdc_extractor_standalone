"""
Custom scraper for KBDC (Kayne Anderson BDC, Inc.).

KBDC's consolidated schedule uses:

    Portfolio Company | Footnotes | Investment | Interest Rate | Spread |
    PIK Rate | Reference | Maturity Date | Principal/Par | Amortized Cost |
    Fair Value | Percentage of Net Assets

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Company names are carried across tranche rows and industry rows.
  * The prior year-end schedule follows the current one; parsing stops at the
    first different period heading.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_EQUITY_TYPES = {
    "Common Equity", "Preferred Equity", "Other Equity",
    "Partnership Interest", "Warrants",
}

_COLUMN_KEYS = [
    "portfoliocompany", "footnotes", "investment", "interestrate", "spread",
    "pikrate", "reference", "maturitydate", "principalpar", "amortizedcost",
    "fairvalue", "netassets",
]


def _norm_header(label: str) -> str:
    s = re.sub(r"\s*\([a-zA-Z0-9]+\)\s*", "", label or "")
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9]", "", s)


def _cell(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip().replace("\u200b", "").strip()
        if v and v not in ("$", "€", "%", "£", "—", "-", "n/a", "N/A"):
            return v
    return ""


class KBDCScraper(BaseCompanyScraper):
    ticker = "KBDC"

    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid or len(grid[0]) < 20:
                continue
            if self._header_row(grid) is None:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("KBDC: found %d SOI tables", len(soi))
        return soi

    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        if not soi_tables:
            return []

        first_period = next(
            (p for p in (self._period_for(filing_result, t) for t in soi_tables) if p),
            None,
        )
        logger.info("KBDC: current SOI period: %s", first_period)

        rows: List[Dict[str, str]] = []
        current_industry = ""
        last_company = ""

        for t in soi_tables:
            period = self._period_for(filing_result, t)
            if first_period and period and period != first_period:
                break

            grid = self.parse_table_to_rows(t["html"])
            hi = self._header_row(grid)
            if hi is None:
                continue
            ranges = self._column_ranges(grid[hi])
            if "portfoliocompany" not in ranges or "fairvalue" not in ranges:
                continue

            for row in grid[hi + 1:]:
                company = _cell(row, *ranges["portfoliocompany"])
                investment = _cell(row, *ranges["investment"]) \
                    if "investment" in ranges else ""
                interest = _cell(row, *ranges["interestrate"]) \
                    if "interestrate" in ranges else ""
                spread = _cell(row, *ranges["spread"]) \
                    if "spread" in ranges else ""
                pik = _cell(row, *ranges["pikrate"]) \
                    if "pikrate" in ranges else ""
                reference = _cell(row, *ranges["reference"]) \
                    if "reference" in ranges else ""
                maturity = _cell(row, *ranges["maturitydate"]) \
                    if "maturitydate" in ranges else ""
                principal = _cell(row, *ranges["principalpar"]) \
                    if "principalpar" in ranges else ""
                cost = self._clean_number(
                    _cell(row, *ranges["amortizedcost"])) \
                    if "amortizedcost" in ranges else ""
                fair = self._clean_number(_cell(row, *ranges["fairvalue"]))
                pct = _cell(row, *ranges["netassets"]) \
                    if "netassets" in ranges else ""

                low = company.lower()
                if not company and not investment:
                    continue
                if low.startswith(("total", "subtotal", "sub-total")):
                    continue
                if company and not investment and not cost and not fair:
                    current_industry = self._clean(company)
                    continue
                if not investment:
                    continue

                if company:
                    last_company = self._clean(company)
                else:
                    company = last_company
                if not company:
                    continue

                inv_type = self._map_type(investment)
                ref = self._norm_ref(reference)
                cash = self._pct(interest)
                spread_pct = self._pct(spread)
                pik_pct = self._pct(pik)

                shares = ""
                principal_amount = ""
                par_clean = (principal or "").replace(",", "")
                if re.search(r"shares?|units?", par_clean, re.I):
                    m = re.search(r"([\d.]+)", par_clean)
                    shares = m.group(1) if m else ""
                else:
                    principal_amount = self._clean_number(par_clean)

                if inv_type in _EQUITY_TYPES:
                    shares = shares or principal_amount
                    principal_amount = ""

                rows.append({
                    "company_name": company,
                    "investment_type": inv_type,
                    "industry": current_industry,
                    "cash_rate": cash,
                    "pik_rate": pik_pct,
                    "reference_rate": ref,
                    "spread": spread_pct,
                    "floor_rate": "",
                    "acquisition_date": "",
                    "maturity_date": self._parse_date(maturity),
                    "principal_amount": principal_amount,
                    "amortized_cost": cost,
                    "fair_value": fair,
                    "percent_of_net_assets": self._pct(pct),
                    "cost": cost,
                    "commitment_limit": "",
                    "undrawn_commitment": "",
                    "rate_cap": "",
                    "shares": shares,
                })

        logger.info("KBDC: parsed %d investment rows", len(rows))
        return rows

    @staticmethod
    def _header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid[:3]):
            labels = {_norm_header(c) for c in row}
            if "portfoliocompany" in labels and "fairvalue" in labels:
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
    def _clean(value: str) -> str:
        s = re.sub(r"\s*\((?:fka|formerly)[^)]*\)", "", value or "", flags=re.I)
        s = re.sub(r"\s*\(\d+\)(?:\s*\(\d+\))*\s*$", "", s)
        return s.strip().rstrip(",")

    @staticmethod
    def _map_type(value: str) -> str:
        s = (value or "").lower()
        if "revolving" in s:
            return "Revolver"
        if "delayed draw" in s:
            return "Delayed Draw"
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
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
    def _pct(value: str) -> str:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%?", value or "")
        return m.group(1) + "%" if m else ""

    @staticmethod
    def _norm_ref(value: str) -> str:
        s = (value or "").upper()
        if "SOFR" in s:
            return "SOFR"
        if "LIBOR" in s:
            return "LIBOR"
        if "PRIME" in s:
            return "Prime"
        if "BSBY" in s:
            return "BSBY"
        return s.strip()

    @staticmethod
    def _parse_date(value: str) -> str:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", (value or "").strip())
        if not m:
            return ""
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    @staticmethod
    def _period_for(filing_result: Any, table: Dict[str, Any]) -> Optional[str]:
        html = (filing_result.text_map or {}).get(table.get("doc") or "", "")
        if not html:
            return None
        pos = table.get("pos") or 0
        raw = re.sub(r"<[^>]+>", " ", html[max(0, pos - 6000):pos])
        raw = re.sub(r"&#160;|&nbsp;", " ", raw, flags=re.I)
        raw = re.sub(r"\s+", " ", raw)
        matches = re.findall(
            r"(January|February|March|April|May|June|July|August|September|"
            r"October|November|December)\s+(\d{1,2}),\s*(\d{4})", raw, re.I)
        if not matches:
            return None
        month, day, year = matches[-1]
        try:
            return datetime.strptime(f"{month} {day}, {year}", "%B %d, %Y").date().isoformat()
        except ValueError:
            return None
