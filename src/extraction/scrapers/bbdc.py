"""
Custom scraper for BBDC (Barings BDC, Inc.).

BBDC files its consolidated schedule of investments as HTML tables with a
consistent header layout (each header cell spans 3 columns):

    Portfolio Company | Investment Type | Interest | Acq. Date |
    Maturity Date | Principal Amount | Cost | Fair Value |
    % of Net Assets | Notes

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Every data row carries the company name, type, rates and amounts.
  * The Interest cell encodes reference/spread/cash/PIK, e.g.
    "SOFR + 4.75%, 8.6% Cash", "SONIA + 8.30%, 12.0% PIK",
    "1.9% Cash, 12.4% PIK", or "15.5% Cash".
  * Dates use MM/YY format ("04/22" -> 2022-04-01).
  * Industry rows ("Aerospace & Defense") fill the company cell and leave
    Investment Type empty; section headers ("Debt Investments",
    "Non-Control / Non-Affiliate Investments:") and subtotal rows
    ("Subtotal ...") are skipped.
  * 10-Qs include a prior year-end comparative SOI; headings read
    "Consolidated Schedule of Investments — (Continued) <date>".
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_REFERENCE_MAP = {
    "SOFR": "SOFR",
    "SONIA": "SONIA",
    "EURIBOR": "Euribor",
    "LIBOR": "LIBOR",
    "PRIME": "Prime",
    "CORRA": "CORRA",
}

_HEADING_RE = re.compile(
    r"Schedule\s+of\s+Investments.*?"
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),\s+(\d{4})",
    re.IGNORECASE | re.DOTALL,
)

_EQUITY_TYPES = {
    "Common Equity", "Preferred Equity", "Other Equity",
    "Partnership Interest", "Warrants",
}


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


class BBDCScraper(BaseCompanyScraper):
    ticker = "BBDC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Portfolio Company" not in t["text"][:600] and "Investment Type" not in t["text"][:1000]:
                continue
            grid = self.parse_table_to_rows(t["html"])
            for row in grid[:4]:
                labels = {_norm_header(c) for c in row if c.strip()}
                if "portfoliocompany" in labels and "investmenttype" in labels:
                    soi.append(t)
                    break
        logger.info("BBDC: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_industry = ""

        current_heading = None
        for t in soi_tables:
            heading = self._heading_before(filing_result, t)
            if heading:
                current_heading = heading
                break
        logger.info("BBDC: current SOI heading: %r", current_heading)

        for t in soi_tables:
            if current_heading:
                heading = self._heading_before(filing_result, t)
                if heading and heading != current_heading:
                    continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            pos = self._header_positions(grid[header_idx])
            if "portfoliocompany" not in pos or "investmenttype" not in pos:
                continue

            company_rng = (pos["portfoliocompany"], pos["portfoliocompany"] + 3)
            type_rng = (pos["investmenttype"], pos["investmenttype"] + 3)

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, *company_rng)
                type_raw = _cell_range(row, *type_rng)
                low = company.lower()

                if not company and not type_raw:
                    continue
                if low.startswith("total") or low.startswith("subtotal"):
                    continue
                # Repeated header rows appear occasionally inside tables.
                if _norm_header(company) in ("portfoliocompany", "investmenttype"):
                    continue

                type_clean = _norm_header(type_raw)
                if not type_raw or _norm_header(company) == type_clean:
                    # Section header or industry row.
                    if "investment" in low:
                        continue
                    current_industry = company
                    continue

                record = self._row_to_record(row, company, type_raw,
                                             current_industry, pos)
                rows.append(record)

        logger.info("BBDC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, type_raw: str,
                       industry: str, pos: Dict[str, int]) -> Dict[str, str]:
        inv_type = self._map_type(type_raw)

        def grp(key: str) -> str:
            p = pos.get(key)
            if p is None:
                return ""
            return _cell_range(row, p, p + 3)

        interest = grp("interest")
        reference, spread, cash, pik = self._parse_interest(interest)

        acquisition = self._parse_short_date(grp("acqdate"))
        maturity = self._parse_short_date(grp("maturitydate"))
        par_key = ("principalamount" if "principalamount" in pos
                   else "units/shares")
        par = grp(par_key)
        cost = self._clean_number(grp("cost"))
        fair = self._clean_number(grp("fairvalue"))
        pct = self._parse_percent_cell(grp("%ofnetassets"))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", self._clean_number(par)
        else:
            principal, shares = self._clean_number(par), ""

        return {
            "company_name": company,
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": cash,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": "",
            "acquisition_date": acquisition,
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
    def _parse_interest(interest: str) -> tuple:
        if not interest:
            return "", "", "", ""
        reference = ""
        spread = ""
        cash = ""
        pik = ""
        m = re.match(r"^([A-Za-z]+)\s*\+", interest.strip())
        if m:
            reference = _REFERENCE_MAP.get(m.group(1).upper(), m.group(1).upper())
            m2 = re.search(r"(\d+(?:\.\d+)?)%", interest)
            if m2:
                spread = m2.group(1) + "%"
        m3 = re.search(r"(\d+(?:\.\d+)?)%\s*Cash", interest, re.IGNORECASE)
        if m3:
            cash = m3.group(1) + "%"
        m4 = re.search(r"(\d+(?:\.\d+)?)%\s*PIK", interest, re.IGNORECASE)
        if m4:
            pik = m4.group(1) + "%"
        if not reference and not cash and not pik:
            m5 = re.search(r"(\d+(?:\.\d+)?)%", interest)
            if m5:
                cash = m5.group(1) + "%"
        return reference, spread, cash, pik

    @staticmethod
    def _parse_short_date(value: str) -> str:
        if not value:
            return ""
        value = value.strip()
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
        if m:
            return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        m = re.fullmatch(r"(\d{1,2})/(\d{2})", value)
        if m:
            return f"20{m.group(2)}-{int(m.group(1)):02d}-01"
        return ""

    @staticmethod
    def _map_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "revolv" in s:
            return "Revolver"
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s or "super senior" in s:
            return "First Lien"
        if "first out" in s or "last in first out" in s or "second out" in s or "third out" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "structured" in s or "note" in s:
            return "Other Debt"
        if "common stock" in s or "common equity" in s or "ordinary share" in s:
            return "Common Equity"
        if "preferred" in s:
            return "Preferred Equity"
        if "llc interest" in s or "llc units" in s:
            return "Other Equity"
        if "lp interest" in s or "lp units" in s or "partnership unit" in s:
            return "Partnership Interest"
        if "warrant" in s:
            return "Warrants"
        if "unit" in s:
            return "Other Equity"
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
