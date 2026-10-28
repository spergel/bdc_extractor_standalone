"""
Custom scraper for MRCC (Monroe Capital Corporation).

MRCC's consolidated schedule of investments uses a grouped header:

    Portfolio Company | Footnotes | Index | Spread | Interest Rate |
    Acquisition Date | Maturity | Principal | Amortized Cost | Fair Value |
    % of Net Assets

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * The header cells are repeated three times by colspan, so column ranges
    are built from the first occurrence of each unique label.
  * Section rows ("Senior Secured Loans", "Equity Securities") set the type;
    industry rows are carried down.
  * Company cells include the instrument/shares in parentheses, e.g.
    "Express Wash Acquisition Company, LLC (31 Class A common units)".
  * The prior year-end schedule follows the current one; its continuation
    pages have no repeated heading, so block state is carried forward.
  * The separate MRCC Senior Loan Fund I, LLC schedule is excluded.
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
    "portfoliocompany", "footnotes", "index", "spread", "interestrate",
    "acquisitiondate", "maturity", "principal", "amortizedcost",
    "fairvalue", "netassets",
]


def _norm_header(label: str) -> str:
    s = re.sub(r"\s*\([a-zA-Z0-9]+\)\s*", "", label or "")
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9]", "", s)


def _cell_range(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip()
        if v and v not in ("$", "€", "%", "£", "—", "-", "n/a", "N/A"):
            return v
    return ""


class MRCCScraper(BaseCompanyScraper):
    ticker = "MRCC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            ctx = self._context_before(filing_result, t)
            if "monroe capital corporation" not in ctx.lower():
                continue
            if "senior loan fund" in ctx.lower():
                continue
            grid = self.parse_table_to_rows(t["html"])
            if not grid or len(grid[0]) < 40:
                continue
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("MRCC: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        if not soi_tables:
            return []

        first_period = next(
            (p for p in (self._period_for(filing_result, t) for t in soi_tables) if p),
            None,
        )
        logger.info("MRCC: current SOI period: %s", first_period)
        rows: List[Dict[str, str]] = []
        current_type = "First Lien"
        current_industry = ""
        for t in soi_tables:
            period = self._period_for(filing_result, t)
            if first_period and period and period != first_period:
                # The prior year-end schedule follows the current one; stop
                # before its pages and any later note-level tables.
                break

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[header_idx])
            if "portfoliocompany" not in ranges or "fairvalue" not in ranges:
                continue

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, *ranges["portfoliocompany"])
                if not company:
                    continue
                low = company.lower()
                if low.startswith("total") or low.startswith("subtotal") \
                        or low.startswith("sub-total"):
                    continue

                fair = self._clean_number(_cell_range(row, *ranges["fairvalue"]))
                cost = self._clean_number(_cell_range(row, *ranges["amortizedcost"])) \
                    if "amortizedcost" in ranges else ""
                principal = _cell_range(row, *ranges["principal"]) \
                    if "principal" in ranges else ""
                rate_text = _cell_range(row, *ranges["interestrate"]) \
                    if "interestrate" in ranges else ""

                has_values = bool(fair or cost or principal)

                if not has_values:
                    if "equity securities" in low or "warrant" in low:
                        current_type = "Other Equity"
                    elif "senior secured" in low:
                        current_type = "Second Lien" if "second" in low else "First Lien"
                    elif "subordinated" in low:
                        current_type = "Subordinated Debt"
                    elif "unsecured" in low or "notes" in low:
                        current_type = "Unsecured Debt"
                    elif "investment" in low:
                        continue
                    else:
                        current_industry = self._clean_industry(company)
                    continue

                inv_type, shares = self._classify(company, low, current_type)
                company_name = self._clean_company(company)
                reference, spread, cash, pik = self._parse_rate(
                    _cell_range(row, *ranges["index"]) if "index" in ranges else "",
                    _cell_range(row, *ranges["spread"]) if "spread" in ranges else "",
                    rate_text,
                )
                maturity = self._parse_date(
                    _cell_range(row, *ranges["maturity"])) if "maturity" in ranges else ""
                acquisition = self._parse_date(
                    _cell_range(row, *ranges["acquisitiondate"])) \
                    if "acquisitiondate" in ranges else ""
                pct_raw = _cell_range(row, *ranges["netassets"]) \
                    if "netassets" in ranges else ""
                pct = self._parse_pct(pct_raw)

                principal_amount = self._clean_number(principal)
                if inv_type in _EQUITY_TYPES:
                    shares = shares or principal
                    principal_amount = ""

                rows.append({
                    "company_name": company_name,
                    "investment_type": inv_type,
                    "industry": current_industry,
                    "cash_rate": cash,
                    "pik_rate": pik,
                    "reference_rate": reference,
                    "spread": spread,
                    "floor_rate": "",
                    "acquisition_date": acquisition,
                    "maturity_date": maturity,
                    "principal_amount": principal_amount,
                    "amortized_cost": cost,
                    "fair_value": fair,
                    "percent_of_net_assets": pct,
                    "cost": cost,
                    "commitment_limit": "",
                    "undrawn_commitment": "",
                    "rate_cap": "",
                    "shares": shares,
                })

        logger.info("MRCC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Helpers
    # --------------------------------------------------------------
    @staticmethod
    def _find_header_row(grid: List[List[str]]) -> Optional[int]:
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
            if n and n not in pos:
                pos[n] = i
        if "netassets" not in pos:
            for key in pos:
                if key.endswith("netassets"):
                    pos["netassets"] = pos[key]
                    break
        ordered = sorted((pos[k], k) for k in _COLUMN_KEYS if k in pos)
        ranges: Dict[str, tuple] = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _clean_industry(value: str) -> str:
        s = re.sub(r"\s*\(\d+\)\s*", "", value or "")
        s = re.sub(r"\s*[-\u2013]\s*[\d.]+%?\s*$", "", s)
        return s.strip()

    @staticmethod
    def _clean_company(value: str) -> str:
        s = re.sub(r"\s*\(\d+\)(?:\s*\(\d+\))*\s*$", "", value or "").strip()
        s = re.sub(r"\s*\((?:fka|formerly)[^)]*\)", "", s, flags=re.I)
        # Drop the instrument/share detail from the company name.
        s = re.sub(r"\s*\((?=[^)]*(?:shares?|units?|%|preferred|common|class))"
                   r"[^)]*\)\s*$", "", s, flags=re.I)
        s = re.sub(r"\s*[-–]\s*(?:Series\s+[A-Z0-9-]+\s+)?"
                   r"(?:Preferred|Common)\s+(?:Stock|Units?|Equity)\s*$", "", s,
                   flags=re.I)
        return s.strip().rstrip(",")

    @staticmethod
    def _classify(company: str, low: str, current_type: str) -> tuple:
        shares = ""
        m = re.search(r"\(([\d,]+(?:\.\d+)?)\s*(?:shares?|units?)\)", company, re.I)
        if m:
            shares = m.group(1).replace(",", "")
        if "revolver" in low:
            return "Revolver", shares
        if "delayed draw" in low:
            return "Delayed Draw", shares
        if "warrant" in low:
            return "Warrants", shares
        if "preferred" in low:
            return "Preferred Equity", shares
        if "common" in low or "class a" in low or "class b" in low:
            return "Common Equity", shares
        if any(k in low for k in ("units", "membership", "equity", "shares")):
            return "Other Equity", shares
        if "second lien" in low:
            return "Second Lien", shares
        if "first lien" in low or "term loan" in low:
            return "First Lien", shares
        return current_type, shares

    @staticmethod
    def _parse_rate(index_raw: str, spread_raw: str, rate_raw: str) -> tuple:
        ref_map = {
            "SF": "SOFR", "S": "SOFR", "SOFR": "SOFR",
            "L": "LIBOR", "LIBOR": "LIBOR",
            "P": "Prime", "PRIME": "Prime",
        }
        reference = ref_map.get((index_raw or "").strip().upper(), "")
        spread = ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%?", spread_raw or "")
        if m:
            spread = m.group(1) + "%"

        rate = (rate_raw or "").replace("\u00a0", " ").strip()
        cash = pik = ""
        cash_m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*Cash", rate, re.I)
        pik_m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", rate, re.I)
        if cash_m:
            cash = cash_m.group(1) + "%"
        if pik_m:
            pik = pik_m.group(1) + "%"
        if not cash and not pik:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", rate)
            if m:
                cash = m.group(1) + "%"
        return reference, spread, cash, pik

    @staticmethod
    def _parse_pct(value: str) -> str:
        m = re.search(r"(\d+(?:\.\d+)?)", value or "")
        return m.group(1) + "%" if m else ""

    @staticmethod
    def _parse_date(value: str) -> str:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", (value or "").strip())
        if m:
            return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
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
            r"October|November|December)\s+(\d{1,2}),\s*(\d{4})",
            raw, re.I)
        if not matches:
            return None
        month, day, year = matches[-1]
        try:
            return datetime.strptime(
                f"{month} {day}, {year}", "%B %d, %Y"
            ).date().isoformat()
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
