"""
Custom scraper for SAR (Saratoga Investment Corp).

SAR files its schedule of investments with this header:

    Company | Industry | Investment Interest Rate/ Maturity |
    Original Acquisition Date | Principal/Number of Shares | Cost |
    Fair Value | % of Net Assets

Layout notes:
  * Amounts are in WHOLE DOLLARS and are converted to thousands.
  * The rate, instrument and maturity are packed into one cell, e.g.
    "First Lien Term Loan (3M USD TERM SOFR+8.08%), 11.88% Cash, 7/18/2027"
    or "Series A-1 Preferred Shares".
  * Section rows ("Non-control/Non-affiliate investments - 299.4%") and
    industry subtotals ("Total Alternative Investment Management Software",
    which carry amounts but no company) are skipped.
  * When the main section appears a second time the prior year-end
    schedule begins and parsing stops.
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


class SARScraper(BaseCompanyScraper):
    ticker = "SAR"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid or len(grid[0]) < 15:
                continue
            head = " ".join((c or "") for c in grid[0]).lower()
            if "investment interest rate" not in head and "company" not in head:
                continue
            if "fair value" not in head:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("SAR: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        main_pct: Optional[str] = None

        current_period = self._period_for(filing_result, soi_tables[0])
        logger.info("SAR: current SOI period: %s", current_period)

        for t in soi_tables:
            period = self._period_for(filing_result, t)
            if current_period:
                # Skip the prior period and note-level duplicates that repeat
                # the affiliate/control schedules without a period heading.
                if period != current_period:
                    continue
            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[header_idx])
            if "company" not in ranges or "fairvalue" not in ranges:
                continue

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, *ranges["company"])
                industry = _cell_range(row, *ranges["industry"]) \
                    if "industry" in ranges else ""
                packed = _cell_range(row, *ranges["investment"]) \
                    if "investment" in ranges else ""
                low = company.lower()

                if low.startswith("control investments") or \
                        low.startswith("affiliate investments"):
                    continue
                if "non-control/non-affiliate investments" in low:
                    m = re.search(r"([\d.]+)\s*%", company)
                    pct = m.group(1) if m else ""
                    if pct and main_pct and pct != main_pct:
                        # Prior-period schedule (different % of net assets).
                        logger.info("SAR: parsed %d investment rows", len(rows))
                        return rows
                    if pct and not main_pct:
                        main_pct = pct
                    continue
                if "total" in low:
                    # "Total …", "Sub Total Affiliate investments", etc.
                    continue
                if not company:
                    # Industry subtotal rows carry amounts but no company.
                    continue

                fair = self._clean_number(
                    self._scale(_cell_range(row, *ranges["fairvalue"])))
                cost = self._clean_number(
                    self._scale(_cell_range(row, *ranges["cost"]))) \
                    if "cost" in ranges else ""
                par = _cell_range(row, *ranges["principal"]) \
                    if "principal" in ranges else ""

                if not fair and not cost:
                    continue

                rows.append(self._row_to_record(row, company, industry, packed,
                                                cost, fair, par, ranges))

        logger.info("SAR: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, industry: str,
                       packed: str, cost: str, fair: str, par_raw: str,
                       ranges: Dict[str, tuple]) -> Dict[str, str]:
        inv_type = self._map_type(packed)
        reference, spread, cash, pik, maturity = self._parse_terms(packed)

        par = self._clean_number(self._scale(par_raw))
        if inv_type in _EQUITY_TYPES:
            principal, shares = "", par
        else:
            principal, shares = par, ""

        pct = ""
        if "percent" in ranges:
            raw = _cell_range(row, *ranges["percent"])
            m = re.search(r"(\d+(?:\.\d+)?)\s*%?", raw or "")
            if m:
                pct = m.group(1) + "%"

        acq = ""
        if "acquisition" in ranges:
            acq = self._parse_date(_cell_range(row, *ranges["acquisition"]))

        return {
            "company_name": re.sub(r"\s*(\(\w+\))+\s*$", "", company).strip(),
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": cash,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": "",
            "acquisition_date": acq,
            "maturity_date": maturity,
            "principal_amount": principal,
            "amortized_cost": cost,
            "fair_value": fair,
            "percent_of_net_assets": pct,
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
        for i, row in enumerate(grid[:3]):
            if any(_norm_header(c) == "company" for c in row):
                return i
        return None

    @staticmethod
    def _column_ranges(row: List[str]) -> Dict[str, tuple]:
        keys = ["company", "industry", "investment", "acquisition",
                "principal", "cost", "fairvalue", "percent"]
        pos: Dict[str, int] = {}
        for i, c in enumerate(row):
            n = _norm_header(c)
            if not n or n in pos:
                continue
            if n.startswith("company"):
                pos.setdefault("company", i)
            elif n.startswith("industry"):
                pos.setdefault("industry", i)
            elif n.startswith("investment"):
                pos.setdefault("investment", i)
            elif "acquisition" in n:
                pos.setdefault("acquisition", i)
            elif n.startswith("principal"):
                pos.setdefault("principal", i)
            elif n == "cost":
                pos.setdefault("cost", i)
            elif n.startswith("fairvalue"):
                pos.setdefault("fairvalue", i)
            elif n.startswith("%ofnetassets"):
                pos.setdefault("percent", i)
        ordered = sorted((v, k) for k, v in pos.items())
        ranges: Dict[str, tuple] = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _map_type(packed: str) -> str:
        s = (packed or "").lower()
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s or "term loan" in s or "senior secured" in s:
            return "First Lien"
        if "delayed draw" in s or "revolver" in s or "line of credit" in s:
            return "Revolver"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "structured finance" in s:
            return "Other Debt"
        if "unsecured" in s or "notes" in s:
            return "Unsecured Debt"
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s:
            return "Common Equity"
        if "equity" in s or "unit" in s or "membership" in s:
            return "Other Equity"
        return "Other"

    @staticmethod
    def _parse_terms(packed: str) -> tuple:
        """Return (reference, spread, cash, pik, maturity)."""
        s = (packed or "").replace("\u00a0", " ")
        reference = ""
        spread = ""
        cash = ""
        pik = ""
        maturity = ""

        m = re.search(r"(SOFR|LIBOR|PRIME|EURIBOR)\s*\+\s*(\d+(?:\.\d+)?)\s*%",
                      s, re.IGNORECASE)
        if m:
            ref_map = {"SOFR": "SOFR", "LIBOR": "LIBOR", "PRIME": "Prime",
                       "EURIBOR": "Euribor"}
            reference = ref_map.get(m.group(1).upper(), m.group(1).upper())
            spread = m.group(2) + "%"

        cm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*Cash", s)
        if cm:
            cash = cm.group(1) + "%"

        pm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", s)
        if pm:
            pik = pm.group(1) + "%"

        dm = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s*$", s)
        if dm:
            maturity = (f"{dm.group(3)}-{int(dm.group(1)):02d}-"
                        f"{int(dm.group(2)):02d}")

        return reference, spread, cash, pik, maturity

    @staticmethod
    def _parse_date(value: str) -> str:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", (value or "").strip())
        if m:
            return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        return ""

    @staticmethod
    def _scale(value: str) -> str:
        v = (value or "").replace(",", "").strip()
        if not v or v in ("-", ""):
            return ""
        try:
            num = float(v) / 1000.0
        except ValueError:
            return ""
        return str(int(round(num)))

    @staticmethod
    def _period_for(filing_result: Any, table: Dict[str, Any]) -> Optional[str]:
        html = (filing_result.text_map or {}).get(table.get("doc") or "", "")
        if not html:
            return None
        pos = table.get("pos") or 0
        raw = re.sub(r"<[^>]+>", " ", html[max(0, pos - 8000):pos])
        raw = re.sub(r"&#160;|&nbsp;", " ", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s+", " ", raw)
        m = re.search(r"Schedule\s+of\s+Investments\s+"
                      r"(January|February|March|April|May|June|July|August|"
                      r"September|October|November|December)\s+(\d{1,2}),\s*(\d{4})",
                      raw, re.IGNORECASE)
        if not m:
            return None
        return f"{m.group(1).upper()} {m.group(2)}, {m.group(3)}"
