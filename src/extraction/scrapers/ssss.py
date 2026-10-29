"""
Custom scraper for SSSS (Neostellar Capital Corp., formerly SuRo Capital).

SSSS's schedule lists a company header row followed by instrument rows:

    Portfolio Investments | Headquarters/Industry | Date of Initial Investment |
    Shares/Principal/Quantity | Cost | Fair Value | % of Net Assets

Layout notes:
  * Company rows carry the headquarters city and no amounts.
  * Instrument rows carry the security name, quantity, cost and fair value;
    the company is carried from the preceding header row.
  * Amounts are in whole dollars and are converted to thousands.
  * The prior period copy follows the current one; parsing stops at the first
    different period heading.
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
    "portfolioinvestments", "headquartersindustry", "dateofinitialinvestment",
    "sharesprincipalquantity", "cost", "fairvalue", "netassets",
]


def _norm_header(label: str) -> str:
    s = re.sub(r"\s*\([a-zA-Z0-9]+\)\s*", "", label or "")
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9]", "", s)


def _cell(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip().replace("\u200b", "").strip()
        if v and v not in ("$", "€", "%", "£", "—", "-"):
            return v
    return ""


class SSSSScraper(BaseCompanyScraper):
    ticker = "SSSS"

    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid or len(grid[0]) < 15:
                continue
            if self._header_row(grid) is None:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("SSSS: found %d SOI tables", len(soi))
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
        logger.info("SSSS: current SOI period: %s", first_period)

        rows: List[Dict[str, str]] = []
        current_company = ""
        current_industry = ""
        done = False

        for t in soi_tables:
            if done:
                break
            period = self._period_for(filing_result, t)
            if first_period and period and period != first_period:
                break

            grid = self.parse_table_to_rows(t["html"])
            hi = self._header_row(grid)
            if hi is None:
                continue
            ranges = self._column_ranges(grid[hi])
            if "portfolioinvestments" not in ranges or "fairvalue" not in ranges:
                continue

            for row in grid[hi + 1:]:
                name = _cell(row, *ranges["portfolioinvestments"])
                city = _cell(row, *ranges["headquartersindustry"]) \
                    if "headquartersindustry" in ranges else ""
                quantity = _cell(row, *ranges["sharesprincipalquantity"]) \
                    if "sharesprincipalquantity" in ranges else ""
                cost_raw = _cell(row, *ranges["cost"]) if "cost" in ranges else ""
                fair_raw = _cell(row, *ranges["fairvalue"])
                pct_raw = _cell(row, *ranges["netassets"]) if "netassets" in ranges else ""

                low = name.lower()
                if not name:
                    continue
                if low.startswith("total"):
                    if re.match(r"total\s+(portfolio\s+)?investments\b", low):
                        done = True
                        break
                    continue
                if low.startswith(("subtotal", "non-controlled", "controlled")):
                    continue

                has_values = bool(cost_raw or fair_raw)
                if not has_values:
                    # Company header row, unless it is an industry/section row.
                    if city:
                        current_company = self._clean(name)
                        current_industry = self._industry(city)
                    continue
                if not current_company:
                    continue

                inv_type = self._map_type(name)
                industry = self._industry(city) or current_industry
                if industry:
                    current_industry = industry

                principal, shares = "", ""
                q = (quantity or "").replace(",", "")
                if q and re.search(r"[A-Za-z]", q):
                    m = re.search(r"([\d.]+)", q)
                    shares = m.group(1) if m else ""
                else:
                    principal = self._clean_number(q)

                if inv_type in _EQUITY_TYPES:
                    shares = shares or principal
                    principal = ""

                rows.append({
                    "company_name": current_company,
                    "investment_type": inv_type,
                    "industry": industry,
                    "cash_rate": "",
                    "pik_rate": "PIK" if "pik" in low else "",
                    "reference_rate": "",
                    "spread": "",
                    "floor_rate": "",
                    "acquisition_date": self._parse_date(
                        _cell(row, *ranges["dateofinitialinvestment"])) \
                        if "dateofinitialinvestment" in ranges else "",
                    "maturity_date": "",
                    "principal_amount": principal,
                    "amortized_cost": self._scale(cost_raw),
                    "fair_value": self._scale(fair_raw),
                    "percent_of_net_assets": self._pct(pct_raw),
                    "cost": self._scale(cost_raw),
                    "commitment_limit": "",
                    "undrawn_commitment": "",
                    "rate_cap": "",
                    "shares": shares,
                })
            if done:
                break

        logger.info("SSSS: parsed %d investment rows", len(rows))
        return rows

    @staticmethod
    def _header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid[:3]):
            labels = {_norm_header(c) for c in row}
            if "portfolioinvestments" in labels and "fairvalue" in labels:
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
        s = re.sub(r"\*+$", "", value or "").strip()
        s = re.sub(r"\s*\(\d+\)\s*$", "", s)
        return s.strip().rstrip(",")

    @staticmethod
    def _industry(value: str) -> str:
        s = re.sub(r"\s*\([^)]*\)", "", value or "")
        s = re.sub(r"^[A-Z][A-Za-z .]+,\s*[A-Z]{2}$", "", s)
        return s.strip()

    @staticmethod
    def _map_type(value: str) -> str:
        s = (value or "").lower()
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s or "ordinary" in s:
            return "Common Equity"
        if "note" in s or "debt" in s or "loan" in s:
            return "Unsecured Debt"
        if "unit" in s or "interest" in s:
            return "Other Equity"
        return "Other Equity"

    @staticmethod
    def _pct(value: str) -> str:
        m = re.search(r"(\d+(?:\.\d+)?)", value or "")
        return m.group(1) + "%" if m else ""

    @staticmethod
    def _scale(value: str) -> str:
        v = (value or "").replace(",", "").replace("$", "").strip()
        if not v or v == "-":
            return ""
        try:
            return str(int(round(float(v) / 1000.0)))
        except ValueError:
            return ""

    @staticmethod
    def _parse_date(value: str) -> str:
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", value or "")
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
