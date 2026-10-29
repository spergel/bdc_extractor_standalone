"""
Custom scraper for RAND (Rand Capital Corporation).

RAND's schedule packs the company, location, business description, industry
and website into one cell:

    Company, location, description, (Industry), website |
    Type of Investment | Date Acquired | Equity | Cost | Fair Value | % NAV

Amounts are in whole dollars and are converted to thousands. Continuation
rows carry shares/units in the second column and omit the company name.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_EQUITY_TYPES = {"Common Equity", "Preferred Equity", "Other Equity",
                 "Partnership Interest", "Warrants"}
_COLUMNS = ["company", "type", "date", "equity", "cost", "fairvalue", "percent"]


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


class RANDScraper(BaseCompanyScraper):
    ticker = "RAND"

    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid or len(grid[0]) < 12:
                continue
            if self._header_row(grid) is None:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("RAND: found %d SOI tables", len(soi))
        return soi

    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi = self.find_soi_tables(tables, filing_result)
        if not soi:
            return []
        first_period = next((p for p in (self._period_for(filing_result, t) for t in soi) if p), None)
        logger.info("RAND: current SOI period: %s", first_period)
        rows = []
        company = ""
        industry = ""
        done = False
        seen = set()

        for t in soi:
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
            if "company" not in ranges or "fairvalue" not in ranges:
                continue
            for row in grid[hi + 1:]:
                raw = _cell(row, 0, 1)
                inv = _cell(row, *ranges["type"]) if "type" in ranges else ""
                extra = _cell(row, 1, 2) if not raw else ""
                cost = _cell(row, *ranges["cost"]) if "cost" in ranges else ""
                fair = _cell(row, *ranges["fairvalue"])
                low = raw.lower()
                nonempty = [c.strip() for c in row if c.strip()]
                full = bool(nonempty) and len(set(nonempty)) == 1
                if low.startswith(("total", "subtotal")):
                    if re.match(r"total\s+(portfolio\s+)?investments\b", low):
                        done = True
                        break
                    continue
                if full:
                    continue
                if not raw and not inv and not fair and not cost:
                    continue
                if not raw and inv.lower().startswith(("total", "subtotal")):
                    continue
                if not raw and extra.lower().startswith(("total", "subtotal")):
                    continue
                if raw:
                    company, industry = self._split_company(raw)
                if not company:
                    continue
                if not inv and not fair and not cost:
                    continue
                if raw:
                    inv_text = inv
                elif extra and not re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", extra):
                    inv_text = extra
                else:
                    inv_text = inv
                inv_type = self._map_type(inv_text)
                cash, pik, ref, spread = self._rate(inv_text)

                principal, shares = "", ""
                par = (extra or "").replace(",", "")
                if re.search(r"shares?|units?", par, re.I):
                    m = re.search(r"([\d.]+)", par)
                    shares = m.group(1) if m else ""
                elif par:
                    principal = self._clean_number(par)
                if inv_type in _EQUITY_TYPES:
                    shares = shares or principal
                    principal = ""

                key = (company, cost, fair)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "company_name": company,
                    "investment_type": inv_type,
                    "industry": industry,
                    "cash_rate": cash,
                    "pik_rate": pik,
                    "reference_rate": ref,
                    "spread": spread,
                    "floor_rate": "",
                    "acquisition_date": self._parse_date(
                        _cell(row, *ranges["date"])) if "date" in ranges else "",
                    "maturity_date": self._extract_maturity(inv_text),
                    "principal_amount": principal,
                    "amortized_cost": self._scale(cost),
                    "fair_value": self._scale(fair),
                    "percent_of_net_assets": self._pct(
                        _cell(row, *ranges["percent"])) if "percent" in ranges else "",
                    "cost": self._scale(cost),
                    "commitment_limit": "",
                    "undrawn_commitment": "",
                    "rate_cap": "",
                    "shares": shares,
                })
            if done:
                break
        logger.info("RAND: parsed %d investment rows", len(rows))
        return rows

    @staticmethod
    def _header_row(grid):
        for i, row in enumerate(grid[:3]):
            labels = [_norm_header(c) for c in row]
            if any(n.startswith("company") for n in labels) \
                    and any(n.startswith("fairvalue") for n in labels):
                return i
        return None

    @staticmethod
    def _column_ranges(row):
        pos = {}
        for i, c in enumerate(row):
            n = _norm_header(c)
            if not n:
                continue
            if n.startswith("company"):
                pos.setdefault("company", i)
            elif n.startswith("typeofinvestment"):
                pos.setdefault("type", i)
            elif n.startswith("dateacquired"):
                pos.setdefault("date", i)
            elif n.startswith("equity"):
                pos.setdefault("equity", i)
            elif n.startswith("cost"):
                pos.setdefault("cost", i)
            elif n.startswith("fairvalue"):
                pos.setdefault("fairvalue", i)
            elif n.startswith("percent"):
                pos.setdefault("percent", i)
        ordered = sorted((pos[k], k) for k in _COLUMNS if k in pos)
        ranges = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _split_company(value):
        s = (value or "").strip()
        matches = list(re.finditer(r"([A-Z][A-Za-z .'-]+,\s*[A-Z]{2})\b", s))
        m = matches[-1] if matches else None
        name = s[:m.start()].strip() if m else s
        name = re.sub(r"\s*\([a-z]\)\s*$", "", name)
        name = re.sub(r"\s*\((?:[a-z]|[ivx]+|\d+)\)(?:\s*\((?:[a-z]|[ivx]+|\d+)\))*\s*$",
                      "", name, flags=re.I)
        name = re.sub(r"\s*\(f/k/a[^)]*\)", "", name, flags=re.I)
        industry = ""
        im = re.findall(r"\(([A-Z][A-Za-z &/-]{3,})\)", s)
        if im:
            industry = im[-1].strip()
        return name.strip().rstrip(","), industry

    @staticmethod
    def _map_type(value):
        s = (value or "").lower()
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s or "units" in s or "membership interest" in s:
            return "Common Equity"
        if "equity" in s:
            return "Other Equity"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "second" in s:
            return "Second Lien"
        if "note" in s or "loan" in s or "term" in s:
            return "First Lien"
        return "Other"

    @staticmethod
    def _rate(value):
        s = value or ""
        cash = pik = ref = spread = ""
        cm = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if cm:
            cash = cm.group(1) + "%"
        pm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:PIK|\(\+?)", s, re.I)
        if pm:
            pik = pm.group(1) + "%"
        rm = re.search(r"(SOFR|LIBOR|PRIME|BSBY)", s, re.I)
        if rm:
            ref = rm.group(1).upper()
        sm = re.search(r"(?:SOFR|LIBOR|PRIME|BSBY)\s*\+\s*([\d.]+)\s*%", s, re.I)
        if sm:
            spread = sm.group(1) + "%"
        return cash, pik, ref, spread

    @staticmethod
    def _extract_maturity(value):
        m = re.search(
            r"due\s*(January|February|March|April|May|June|July|August|September|"
            r"October|November|December)\s+(\d{1,2}),\s*(\d{4})", value or "", re.I)
        if not m:
            return ""
        try:
            return datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date().isoformat()
        except ValueError:
            return ""

    @staticmethod
    def _pct(value):
        m = re.search(r"(\d+(?:\.\d+)?)", value or "")
        return m.group(1) + "%" if m else ""

    @staticmethod
    def _scale(value):
        v = (value or "").replace(",", "").replace("$", "").strip()
        if not v or v == "-":
            return ""
        try:
            return str(int(round(float(v) / 1000.0)))
        except ValueError:
            return ""

    @staticmethod
    def _parse_date(value):
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", value or "")
        if not m:
            return ""
        y = int(m.group(3))
        if y < 100:
            y += 2000
        return f"{y:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    @staticmethod
    def _period_for(filing_result, table):
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
