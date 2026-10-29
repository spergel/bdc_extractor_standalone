"""
Custom scraper for BCRED (Blackstone Private Credit Fund).

BCRED's schedule uses a wide 60-column layout:

    Investments | Footnotes | Reference Rate and Spread | Interest Rate |
    Acquisition Date | Maturity Date | Par Amount/Units | Cost | Fair Value |
    % of Net Assets

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Each row is an instrument; the company name is repeated on every row.
  * Full-row section labels (First Lien Debt, Second Lien Debt, Equity, ...)
    set the investment type; full-row industry labels set the industry.
  * Prior-period copies follow the current schedule; parsing stops at the
    first different period heading or the total row.
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
    "investments", "footnotes", "reference", "interest", "acquisition",
    "maturity", "par", "cost", "fairvalue", "percent",
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


class BCREDScraper(BaseCompanyScraper):
    ticker = "BCRED"

    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid or len(grid[0]) < 40:
                continue
            if self._header_row(grid) is None:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("BCRED: found %d SOI tables", len(soi))
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
        logger.info("BCRED: current SOI period: %s", first_period)

        rows: List[Dict[str, str]] = []
        current_section = ""
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
            if "investments" not in ranges or "fairvalue" not in ranges:
                continue

            for row in grid[hi + 1:]:
                company = _cell(row, *ranges["investments"])
                fair_raw = _cell(row, *ranges["fairvalue"])
                cost_raw = _cell(row, *ranges["cost"]) if "cost" in ranges else ""
                par_raw = _cell(row, *ranges["par"]) if "par" in ranges else ""
                low = company.lower()
                nonempty = [c.strip() for c in row if c.strip()]
                full_span = bool(nonempty) and len(set(nonempty)) == 1

                if low.startswith("total"):
                    flat = re.sub(r"[^a-z]", "", low)
                    if "totalinvestmentportfolio" in flat \
                            or "totalportfolioinvestments" in flat:
                        done = True
                        break
                    continue
                if not company:
                    continue
                if full_span:
                    label = self._clean(company)
                    if self._is_section(label):
                        current_section = label
                    else:
                        current_industry = label
                    continue
                if not fair_raw and not cost_raw and not par_raw:
                    # Company-only header row; carry it but add nothing.
                    continue

                inv_type = self._map_type(current_section, company)
                reference = _cell(row, *ranges["reference"]) \
                    if "reference" in ranges else ""
                interest = _cell(row, *ranges["interest"]) \
                    if "interest" in ranges else ""
                ref, spread = self._ref_spread(reference)
                cash, pik = self._cash_pik(interest, reference)

                shares = ""
                principal = ""
                par = (par_raw or "").replace(",", "")
                if re.search(r"shares?|units?", par, re.I):
                    m = re.search(r"([\d.]+)", par)
                    shares = m.group(1) if m else ""
                else:
                    principal = self._clean_number(par)

                if inv_type in _EQUITY_TYPES:
                    shares = shares or principal
                    principal = ""

                rows.append({
                    "company_name": self._clean(company),
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
                    "maturity_date": self._parse_date(
                        _cell(row, *ranges["maturity"])) \
                        if "maturity" in ranges else "",
                    "principal_amount": principal,
                    "amortized_cost": self._clean_number(cost_raw),
                    "fair_value": self._clean_number(fair_raw),
                    "percent_of_net_assets": self._pct(
                        _cell(row, *ranges["percent"])) \
                        if "percent" in ranges else "",
                    "cost": self._clean_number(cost_raw),
                    "commitment_limit": "",
                    "undrawn_commitment": "",
                    "rate_cap": "",
                    "shares": shares,
                })
            if done:
                break

        logger.info("BCRED: parsed %d investment rows", len(rows))
        return rows

    @staticmethod
    def _header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid[:3]):
            labels = {_norm_header(c) for c in row}
            if "investments" in labels and "fairvalue" in labels:
                return i
        return None

    @staticmethod
    def _column_ranges(row: List[str]) -> Dict[str, tuple]:
        pos: Dict[str, int] = {}
        for i, c in enumerate(row):
            n = _norm_header(c)
            if not n:
                continue
            if n.startswith("investments"):
                pos.setdefault("investments", i)
            elif n.startswith("footnotes"):
                pos.setdefault("footnotes", i)
            elif n.startswith("referencerate"):
                pos.setdefault("reference", i)
            elif n.startswith("interestrate"):
                pos.setdefault("interest", i)
            elif n.startswith("acquisition"):
                pos.setdefault("acquisition", i)
            elif n.startswith("maturity"):
                pos.setdefault("maturity", i)
            elif n.startswith("paramount"):
                pos.setdefault("par", i)
            elif n.startswith("cost"):
                pos.setdefault("cost", i)
            elif n.startswith("fairvalue"):
                pos.setdefault("fairvalue", i)
            elif n.startswith("percent"):
                pos.setdefault("percent", i)
        ordered = sorted((pos[k], k) for k in _COLUMN_KEYS if k in pos)
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
    def _is_section(value: str) -> bool:
        s = (value or "").lower()
        return any(k in s for k in (
            "first lien", "second lien", "subordinated", "unsecured",
            "equity", "warrant", "investment fund", "structured",
        ))

    @staticmethod
    def _map_type(section: str, company: str) -> str:
        s = (section or "").lower()
        c = (company or "").lower()
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s or "equity" in s:
            return "Common Equity"
        if "second lien" in s:
            return "Second Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "first lien" in s:
            return "First Lien"
        if "warrant" in c:
            return "Warrants"
        return "First Lien"

    @staticmethod
    def _ref_spread(value: str) -> tuple:
        s = value or ""
        m = re.search(r"(SOFR|LIBOR|PRIME|BSBY|SONIA|EURIBOR)", s, re.I)
        ref = {"SOFR": "SOFR", "LIBOR": "LIBOR", "PRIME": "Prime",
               "BSBY": "BSBY", "SONIA": "SONIA", "EURIBOR": "Euribor"}.get(
                   m.group(1).upper(), m.group(1).upper()) if m else ""
        sm = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        spread = sm.group(1) + "%" if sm else ""
        return ref, spread

    @staticmethod
    def _cash_pik(interest: str, reference: str) -> tuple:
        text = f"{interest} {reference}"
        cash = pik = ""
        cm = re.search(r"(\d+(?:\.\d+)?)\s*%", interest or "")
        if cm:
            cash = cm.group(1) + "%"
        pm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", text, re.I)
        if pm:
            pik = pm.group(1) + "%"
        return cash, pik

    @staticmethod
    def _pct(value: str) -> str:
        m = re.search(r"(\d+(?:\.\d+)?)", value or "")
        return m.group(1) + "%" if m else ""

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
