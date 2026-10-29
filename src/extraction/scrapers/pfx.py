"""
Custom scraper for PFX (PhenixFIN Corporation).

PFX's schedule uses:

    Company | Industry | Type of Investment | Maturity |
    Par Amount/Shares/Units | Amortized Cost | Fair Value | % of Net Assets

Amounts are in whole dollars and are converted to thousands. Company rows are
carried across instrument rows; section rows span the full table.
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

_COLUMNS = ["company", "industry", "type", "maturity", "par", "cost",
            "fairvalue", "percent"]


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


class PFXScraper(BaseCompanyScraper):
    ticker = "PFX"

    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid or len(grid[0]) < 15:
                continue
            if self._header_row(grid) is None:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("PFX: found %d SOI tables", len(soi))
        return soi

    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi = self.find_soi_tables(tables, filing_result)
        if not soi:
            return []
        first_period = next((p for p in (self._period_for(filing_result, t) for t in soi) if p), None)
        logger.info("PFX: current SOI period: %s", first_period)
        rows: List[Dict[str, str]] = []
        company = ""
        industry = ""
        done = False

        for t in soi:
            if done:
                break
            period = self._period_for(filing_result, t)
            logger.info("PFX table %s period=%s first=%s", t.get("id"), period, first_period)
            if first_period and period and period != first_period:
                break
            grid = self.parse_table_to_rows(t["html"])
            hi = self._header_row(grid)
            if hi is None:
                continue
            ranges = self._column_ranges(grid[hi])
            for row in grid[hi + 1:]:
                name = _cell(row, *ranges["company"])
                inv = _cell(row, *ranges["type"]) if "type" in ranges else ""
                low = name.lower()
                nonempty = [c.strip() for c in row if c.strip()]
                full = bool(nonempty) and len(set(nonempty)) == 1
                if low.startswith(("total", "subtotal")):
                    if re.match(r"total\s+(portfolio\s+)?investments\b", low):
                        done = True
                        break
                    continue
                if not name and not inv:
                    continue
                if not name and inv.lower().startswith(("total", "subtotal")):
                    continue
                if full:
                    continue
                if name and not inv and not _cell(row, *ranges["fairvalue"]):
                    company = self._clean(name)
                    industry = _cell(row, *ranges["industry"]) or industry
                    continue
                if name:
                    company = self._clean(name)
                    industry = _cell(row, *ranges["industry"]) or industry
                if not company:
                    continue
                if not inv and not _cell(row, *ranges["fairvalue"]):
                    continue

                inv_type = self._map_type(inv)
                cash, pik, ref, spread, floor = self._rate(inv)
                principal, shares = "", ""
                par = (_cell(row, *ranges["par"]) or "").replace(",", "")
                if re.search(r"shares?|units?", par, re.I):
                    m = re.search(r"([\d.]+)", par)
                    shares = m.group(1) if m else ""
                else:
                    principal = self._clean_number(par)
                if inv_type in _EQUITY_TYPES:
                    shares = shares or principal
                    principal = ""

                rows.append({
                    "company_name": company,
                    "investment_type": inv_type,
                    "industry": industry,
                    "cash_rate": cash,
                    "pik_rate": pik,
                    "reference_rate": ref,
                    "spread": spread,
                    "floor_rate": floor,
                    "acquisition_date": "",
                    "maturity_date": self._parse_date(_cell(row, *ranges["maturity"]))
                    if "maturity" in ranges else "",
                    "principal_amount": principal,
                    "amortized_cost": self._scale(_cell(row, *ranges["cost"]))
                    if "cost" in ranges else "",
                    "fair_value": self._scale(_cell(row, *ranges["fairvalue"])),
                    "percent_of_net_assets": self._pct(_cell(row, *ranges["percent"]))
                    if "percent" in ranges else "",
                    "cost": self._scale(_cell(row, *ranges["cost"]))
                    if "cost" in ranges else "",
                    "commitment_limit": "",
                    "undrawn_commitment": "",
                    "rate_cap": "",
                    "shares": shares,
                })
            if done:
                break
        logger.info("PFX: parsed %d investment rows", len(rows))
        return rows

    @staticmethod
    def _header_row(grid):
        for i, row in enumerate(grid[:3]):
            labels = {_norm_header(c) for c in row}
            if "company" in labels and "fairvalue" in labels:
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
            elif n.startswith("industry"):
                pos.setdefault("industry", i)
            elif n.startswith("typeofinvestment"):
                pos.setdefault("type", i)
            elif n.startswith("maturity"):
                pos.setdefault("maturity", i)
            elif n.startswith("paramount"):
                pos.setdefault("par", i)
            elif n.startswith("amortizedcost"):
                pos.setdefault("cost", i)
            elif n.startswith("fairvalue"):
                pos.setdefault("fairvalue", i)
            elif n.startswith("ofnetassets") or n.startswith("percent"):
                pos.setdefault("percent", i)
        ordered = sorted((pos[k], k) for k in _COLUMNS if k in pos)
        ranges = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _clean(value):
        s = re.sub(r"\s*\(\d+\)(?:\s*\(\d+\))*\s*$", "", value or "")
        s = re.sub(r"\s*\(dba[^)]*\)", "", s, flags=re.I)
        return s.strip().rstrip(",")

    @staticmethod
    def _map_type(value):
        s = (value or "").lower()
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s or "units" in s:
            return "Common Equity"
        if "equity" in s:
            return "Other Equity"
        if "delayed draw" in s:
            return "Delayed Draw"
        if "revolver" in s:
            return "Revolver"
        if "second lien" in s or "second out" in s:
            return "Second Lien"
        if "first lien" in s or "first out" in s or "term loan" in s:
            return "First Lien"
        return "Other"

    @staticmethod
    def _rate(value):
        s = value or ""
        cash = pik = ref = spread = floor = ""
        cm = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if cm:
            cash = cm.group(1) + "%"
        pm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", s, re.I)
        if pm:
            pik = pm.group(1) + "%"
        rm = re.search(r"(SOFR|LIBOR|PRIME|BSBY)", s, re.I)
        if rm:
            ref = rm.group(1).upper()
        sm = re.search(r"(?:SOFR|LIBOR|PRIME|BSBY)\s*\+\s*([\d.]+)\s*%", s, re.I)
        if sm:
            spread = sm.group(1) + "%"
        fm = re.search(r"([\d.]+)\s*%\s*Floor", s, re.I)
        if fm:
            floor = fm.group(1) + "%"
        return cash, pik, ref, spread, floor

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
        m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", value or "")
        if not m:
            return ""
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

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
