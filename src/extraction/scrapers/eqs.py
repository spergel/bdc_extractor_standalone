"""
Custom scraper for EQS (Equus Total Return, Inc.).

EQS's schedule uses two header rows:

    Name and Location of Portfolio Company | Industry | Date of Initial
    Investment | Investment | Principal | Cost of Investment | Fair Value

Layout notes:
  * The company and headquarters city share one cell, e.g.
    "Morgan E&P, Inc.Houston, TX".
  * A company can have multiple instrument rows; an instrument row may omit
    the company cell.
  * Rows with only cost/fair values are subtotals and are skipped.
  * Amounts are in thousands (no scaling needed).
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
    "portfolio", "industry", "date", "investment", "principal", "cost",
    "fairvalue",
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


class EQSScraper(BaseCompanyScraper):
    ticker = "EQS"

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
        logger.info("EQS: found %d SOI tables", len(soi))
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
        logger.info("EQS: current SOI period: %s", first_period)

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
            ranges = self._column_ranges(grid, hi)
            if "portfolio" not in ranges or "fairvalue" not in ranges:
                continue

            for row in grid[hi + 1:]:
                raw_name = _cell(row, *ranges["portfolio"])
                investment = _cell(row, *ranges["investment"]) \
                    if "investment" in ranges else ""
                cost_raw = _cell(row, *ranges["cost"]) if "cost" in ranges else ""
                fair_raw = _cell(row, *ranges["fairvalue"])
                principal_raw = _cell(row, *ranges["principal"]) \
                    if "principal" in ranges else ""

                low = raw_name.lower()
                if low.startswith("total"):
                    if re.match(r"total\s+(portfolio\s+)?investments\b", low):
                        done = True
                        break
                    continue
                if low.startswith(("control investments", "non-affiliate investments")):
                    continue
                if not raw_name and not investment:
                    # Unlabeled company subtotal row.
                    continue

                has_values = bool(cost_raw or fair_raw)
                company, location = self._split_company(raw_name) if raw_name else ("", "")

                if company and not investment and not has_values:
                    current_company = company
                    current_industry = _cell(row, *ranges["industry"]) \
                        if "industry" in ranges else current_industry
                    continue
                if company and has_values:
                    current_company = company
                    current_industry = _cell(row, *ranges["industry"]) \
                        if "industry" in ranges and _cell(row, *ranges["industry"]) \
                        else current_industry
                if not company:
                    company = current_company
                if not company or (not investment and not has_values):
                    continue

                inv_type = self._map_type(investment)
                shares = ""
                m = re.search(r"([\d,]+)\s+shares", investment or "", re.I)
                if m:
                    shares = m.group(1).replace(",", "")

                principal = self._clean_number(principal_raw)
                if inv_type in _EQUITY_TYPES:
                    shares = shares or principal
                    principal = ""

                rows.append({
                    "company_name": company,
                    "investment_type": inv_type,
                    "industry": current_industry,
                    "cash_rate": "",
                    "pik_rate": "",
                    "reference_rate": "",
                    "spread": "",
                    "floor_rate": "",
                    "acquisition_date": self._parse_month(
                        _cell(row, *ranges["date"])) if "date" in ranges else "",
                    "maturity_date": "",
                    "principal_amount": principal,
                    "amortized_cost": self._clean_number(cost_raw),
                    "fair_value": self._clean_number(fair_raw),
                    "percent_of_net_assets": "",
                    "cost": self._clean_number(cost_raw),
                    "commitment_limit": "",
                    "undrawn_commitment": "",
                    "rate_cap": "",
                    "shares": shares,
                })
            if done:
                break

        logger.info("EQS: parsed %d investment rows", len(rows))
        return rows

    @staticmethod
    def _header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid[:4]):
            text = " ".join(row).lower()
            above = " ".join(grid[i - 1]).lower() if i else ""
            if "portfolio company" in text and "fair" in text + above:
                return i
        return None

    @staticmethod
    def _column_ranges(grid: List[List[str]], hi: int) -> Dict[str, tuple]:
        row = grid[hi]
        above = grid[hi - 1] if hi else []
        pos: Dict[str, int] = {}
        for i in range(len(row)):
            label = (row[i] or "")
            if above and i < len(above):
                label = (above[i] or "") + " " + label
            n = _norm_header(label)
            if not n:
                continue
            if "portfolio" in n or n.startswith("name"):
                pos.setdefault("portfolio", i)
            elif n.startswith("industry"):
                pos.setdefault("industry", i)
            elif n.startswith("date"):
                pos.setdefault("date", i)
            elif n.startswith("principal"):
                pos.setdefault("principal", i)
            elif n.startswith("cost"):
                pos.setdefault("cost", i)
            elif n.startswith("fair"):
                pos.setdefault("fairvalue", i)
            elif n.startswith("investment"):
                pos.setdefault("investment", i)
        ordered = sorted((pos[k], k) for k in _COLUMN_KEYS if k in pos)
        ranges: Dict[str, tuple] = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _split_company(value: str) -> tuple:
        s = (value or "").strip()
        m = re.match(r"^(.*?)([A-Z][A-Za-z .'-]+,\s*[A-Z]{2})$", s)
        if m:
            return m.group(1).strip().rstrip(","), m.group(2).strip()
        return s.rstrip(","), ""

    @staticmethod
    def _map_type(value: str) -> str:
        s = (value or "").lower()
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s or "shares" in s:
            return "Common Equity"
        if "note" in s or "loan" in s or "debt" in s:
            return "Unsecured Debt"
        if "unit" in s or "interest" in s:
            return "Other Equity"
        return "Other Equity"

    @staticmethod
    def _parse_month(value: str) -> str:
        m = re.match(r"([A-Za-z]+)\s+(\d{4})", value or "")
        if not m:
            return ""
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%B %Y").date().isoformat()
        except ValueError:
            return ""

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
