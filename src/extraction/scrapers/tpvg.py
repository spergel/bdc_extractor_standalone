"""
Custom scraper for TPVG (TriplePoint Venture Growth BDC Corp.).

TPVG files three portfolio schedules in one 10-Q:

    Debt:    Portfolio Company | Type of Investment | Acquisition Date |
             Outstanding Principal | Cost | Fair Value
    Warrants: ... Type of Warrant | Acquisition Date | Shares | Cost | Fair Value
    Equity:  ... Type of Equity | Acquisition Date | Shares | Cost | Fair Value

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Industry rows span the full table; company names are carried across
    multiple instrument rows.
  * Rows with only cost/fair values are subtotals and are skipped.
  * Cash-equivalent tables are excluded.
  * Prior-period copies of each schedule follow the current ones; parsing
    stops at the first different period heading.
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


def _any(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip().replace("\u200b", "").strip()
        if v and v not in ("$", "€", "%", "£", "—", "-"):
            return v
    return ""


class TPVGScraper(BaseCompanyScraper):
    ticker = "TPVG"

    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid or len(grid[0]) < 15:
                continue
            hi = self._header_row(grid)
            if hi is None:
                continue
            context = self._context_before(filing_result, t).lower()
            if "cash equivalent" in context:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("TPVG: found %d SOI tables", len(soi))
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
        logger.info("TPVG: current SOI period: %s", first_period)

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
            if "portfolio" not in ranges or "fairvalue" not in ranges:
                continue

            head = " ".join(c for c in grid[hi] if c).lower()
            if "type of warrant" in head:
                section = "Warrants"
            elif "type of equity" in head:
                section = "Equity"
            else:
                section = "Debt"

            for row in grid[hi + 1:]:
                name = _cell(row, *ranges["portfolio"])
                type_raw = _cell(row, *ranges["type"]) if "type" in ranges else ""
                qty = _cell(row, *ranges["quantity"]) if "quantity" in ranges else ""
                cost = _any(row, *ranges["cost"]) if "cost" in ranges else ""
                fair = _any(row, *ranges["fairvalue"])
                low = name.lower()
                row_text = " ".join(c for c in row if c).lower()
                flat = re.sub(r"[^a-z]", "", row_text)

                if "totalinvestments" in flat or "totalportfoliocompany" in flat:
                    done = True
                    break
                if not name and not type_raw:
                    # Subtotals carry only cost/fair.
                    continue
                if low.startswith(("total", "subtotal", "sub-total")):
                    continue

                if name and not type_raw and not cost and not fair:
                    if len(set(c for c in row if c)) == 1:
                        current_industry = self._clean(name)
                    else:
                        current_company = self._clean(name)
                    continue
                if name:
                    current_company = self._clean(name)
                if not current_company:
                    continue

                inv_type = self._map_type(type_raw, section)
                cash, pik, spread, ref = self._rate(type_raw)

                principal, shares = "", ""
                q = (qty or "").replace(",", "")
                if section == "Debt":
                    principal = self._clean_number(q)
                else:
                    m = re.search(r"([\d.]+)", q)
                    shares = m.group(1) if m else ""

                rows.append({
                    "company_name": current_company,
                    "investment_type": inv_type,
                    "industry": current_industry,
                    "cash_rate": cash,
                    "pik_rate": pik,
                    "reference_rate": ref,
                    "spread": spread,
                    "floor_rate": "",
                    "acquisition_date": self._parse_date(
                        _cell(row, *ranges["acquisition"])) \
                        if "acquisition" in ranges else "",
                    "maturity_date": "",
                    "principal_amount": principal,
                    "amortized_cost": self._clean_number(cost),
                    "fair_value": self._clean_number(fair),
                    "percent_of_net_assets": "",
                    "cost": self._clean_number(cost),
                    "commitment_limit": "",
                    "undrawn_commitment": "",
                    "rate_cap": "",
                    "shares": shares,
                })

        logger.info("TPVG: parsed %d investment rows", len(rows))
        return rows

    @staticmethod
    def _header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid[:4]):
            labels = {_norm_header(c) for c in row}
            if "portfoliocompany" in labels and "fairvalue" in labels:
                return i
        return None

    @staticmethod
    def _column_ranges(row: List[str]) -> Dict[str, tuple]:
        pos: Dict[str, int] = {}
        for i, c in enumerate(row):
            n = _norm_header(c)
            if not n:
                continue
            if n.startswith("portfolio"):
                pos.setdefault("portfolio", i)
            elif n.startswith("type"):
                pos.setdefault("type", i)
            elif n.startswith("acquisition"):
                pos.setdefault("acquisition", i)
            elif n.startswith("outstandingprincipal") or n.startswith("shares"):
                pos.setdefault("quantity", i)
            elif n.startswith("cost"):
                pos.setdefault("cost", i)
            elif n.startswith("fair"):
                pos.setdefault("fairvalue", i)
        ordered = sorted((pos[k], k) for k in
                         ["portfolio", "type", "acquisition", "quantity",
                          "cost", "fairvalue"] if k in pos)
        ranges: Dict[str, tuple] = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _clean(value: str) -> str:
        s = re.sub(r"\s*\(\d+\)(?:\s*\(\d+\))*\s*$", "", value or "")
        s = re.sub(r"\s*\(f/k/a[^)]*\)", "", s, flags=re.I)
        return s.strip().rstrip(",")

    @staticmethod
    def _map_type(value: str, section: str) -> str:
        s = (value or "").lower()
        if section == "Warrants" or "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s or section == "Equity":
            return "Common Equity"
        if "revolving" in s:
            return "Revolver"
        if "term loan" in s or "growth capital loan" in s or "loan" in s:
            return "First Lien"
        return "First Lien" if section == "Debt" else "Other Equity"

    @staticmethod
    def _rate(value: str) -> tuple:
        s = value or ""
        cash = pik = spread = ref = ""
        cm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:cash|interest)", s, re.I)
        pm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", s, re.I)
        if cm:
            cash = cm.group(1) + "%"
        if pm:
            pik = pm.group(1) + "%"
        rm = re.search(r"(SOFR|LIBOR|PRIME|BSBY)", s, re.I)
        if rm:
            ref = rm.group(1).upper()
        sm = re.search(r"(?:SOFR|LIBOR|PRIME|BSBY)\s*\+\s*([\d.]+)\s*%", s, re.I)
        if sm:
            spread = sm.group(1) + "%"
        if not cash and not pik:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
            if m:
                cash = m.group(1) + "%"
        return cash, pik, spread, ref

    @staticmethod
    def _parse_date(value: str) -> str:
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", value or "")
        if not m:
            return ""
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    @staticmethod
    def _period_for(filing_result: Any, table: Dict[str, Any]) -> Optional[str]:
        date_re = (r"(January|February|March|April|May|June|July|August|September|"
                   r"October|November|December)\s+(\d{1,2}),\s*(\d{4})")
        # The table's own text is the reliable place for the schedule date.
        raw = table.get("text") or ""
        matches = re.findall(date_re, raw, re.I)
        if not matches:
            html = (filing_result.text_map or {}).get(table.get("doc") or "", "")
            if not html:
                return None
            pos = table.get("pos") or 0
            raw = re.sub(r"<[^>]+>", " ", html[max(0, pos - 6000):pos])
            raw = re.sub(r"&#160;|&nbsp;", " ", raw, flags=re.I)
            raw = re.sub(r"\s+", " ", raw)
            matches = re.findall(date_re, raw, re.I)
        if not matches:
            return None
        month, day, year = matches[-1]
        try:
            return datetime.strptime(f"{month} {day}, {year}", "%B %d, %Y").date().isoformat()
        except ValueError:
            return None

    @staticmethod
    def _context_before(filing_result: Any, table: Dict[str, Any]) -> str:
        html = (filing_result.text_map or {}).get(table.get("doc") or "", "")
        if not html:
            return ""
        pos = table.get("pos") or 0
        raw = html[max(0, pos - 3000):pos]
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"&#160;|&nbsp;", " ", text, flags=re.I)
        return re.sub(r"\s+", " ", text)
