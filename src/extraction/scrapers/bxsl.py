"""
Custom scraper for BXSL (Blackstone Secured Lending Fund).

BXSL files its condensed consolidated schedule of investments as HTML tables
with this header layout (each header cell spans 3 columns):

    Investments | Footnotes | Reference Rate and Spread | Interest Rate |
    Acquisition Date | Maturity Date | Par Amount/Units | Cost | Fair Value |
    % of Net Assets

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * There is no per-row Investment column.  Investment type comes from the
    section headers ("First Lien Debt - non-controlled/non-affiliated",
    "Equity - ...", etc.), which span the whole row.
  * Industry comes from section rows like "Health Care Technology".
  * The Reference Rate and Spread group spans 6 columns: the first 3 hold the
    reference ("SOFR +"), the last 3 the spread ("5.75%").
  * The Interest Rate group is the all-in cash rate ("9.87%", or
    "13.75% PIK" for PIK-only positions).
  * 10-Qs include the current-period SOI and the prior year-end comparative
    SOI; the heading format is "…Schedule of Investments June 30, 2026…"
    (no "As of"), so prior-year sections are filtered on the date in that
    heading.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_REFERENCE_MAP = {
    "SOFR": "SOFR",
    "SOFR +": "SOFR",
    "S": "SOFR",
    "S +": "SOFR",
    "LIBOR": "LIBOR",
    "LIBOR +": "LIBOR",
    "L": "LIBOR",
    "L +": "LIBOR",
    "EURIBOR": "Euribor",
    "EURIBOR +": "Euribor",
    "E": "Euribor",
    "E +": "Euribor",
    "PRIME": "Prime",
    "PRIME +": "Prime",
    "P": "Prime",
    "P +": "Prime",
    "SONIA": "SONIA",
    "SONIA +": "SONIA",
    "CORRA": "CORRA",
    "CORRA +": "CORRA",
}

_HEADING_RE = re.compile(
    r"Schedule\s+of\s+Investments\s+"
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4}",
    re.IGNORECASE,
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


class BXSLScraper(BaseCompanyScraper):
    ticker = "BXSL"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Investments" not in t["text"][:500] and "Par Amount" not in t["text"][:800]:
                continue
            grid = self.parse_table_to_rows(t["html"])
            for row in grid[:6]:
                labels = {_norm_header(c) for c in row if c.strip()}
                if "investments" in labels and "paramount/units" in labels:
                    soi.append(t)
                    break
        logger.info("BXSL: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_type = ""
        current_industry = ""

        current_heading = None
        for t in soi_tables:
            heading = self._heading_before(filing_result, t)
            if heading:
                current_heading = heading
                break
        logger.info("BXSL: current SOI heading: %r", current_heading)

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
            if "investments" not in pos or "paramount/units" not in pos:
                continue
            company_rng = (pos["investments"], pos["investments"] + 3)
            ref_start = pos.get("referencerateandspread")
            rate_start = pos.get("interestrate")
            par_start = pos["paramount/units"]

            if ref_start is not None:
                ref_span = self._span(grid[header_idx], ref_start)
                ref_rng = (ref_start, ref_start + 3)
                spread_rng = (ref_start + 3, ref_start + ref_span)
            else:
                ref_rng = spread_rng = None
            rate_rng = (rate_start, rate_start + 3) if rate_start is not None else None

            for row in grid[header_idx + 1:]:
                company = self._strip_footnote_refs(
                    _cell_range(row, *company_rng))
                if not company:
                    continue
                if company.lower().startswith("total"):
                    continue

                par = _cell_range(row, par_start, par_start + 3)
                # Section/industry headers span the whole row, so the Par
                # cell repeats the company text instead of holding a value.
                if not par or par == company or self._strip_footnote_refs(par) == company:
                    sec_type = self._map_section_type(company)
                    if sec_type:
                        current_type = sec_type
                    else:
                        current_industry = company
                    continue

                row_type = current_type
                if row_type == "Other Equity" and "preferred" in company.lower():
                    row_type = "Preferred Equity"
                if row_type == "Other Equity" and "warrant" in company.lower():
                    row_type = "Warrants"

                record = self._row_to_record(
                    row, company, row_type, current_industry,
                    ref_rng, spread_rng, rate_rng, pos)
                rows.append(record)

        logger.info("BXSL: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str,
                       sec_type: str, industry: str,
                       ref_rng: Optional[tuple], spread_rng: Optional[tuple],
                       rate_rng: Optional[tuple],
                       pos: Dict[str, int]) -> Dict[str, str]:
        ref_raw = _cell_range(row, *ref_rng) if ref_rng else ""
        spread_raw = _cell_range(row, *spread_rng) if spread_rng else ""

        interest = _cell_range(row, *rate_rng) if rate_rng else ""

        cash, pik = self._split_interest_rate(interest)

        def grp(key: str) -> str:
            p = pos.get(key)
            if p is None:
                return ""
            return _cell_range(row, p, p + 3)

        acquisition = self._parse_date(grp("acquisitiondate"))
        maturity = self._parse_date(grp("maturitydate"))
        par = grp("paramount/units")
        cost_val = grp("cost")
        fair = grp("fairvalue")
        pct = self._parse_percent_cell(grp("%ofnetassets"))

        if sec_type in _EQUITY_TYPES:
            principal, shares = "", self._clean_number(par)
        else:
            principal, shares = self._clean_number(par), ""

        ref_clean = (ref_raw or "").strip().upper()
        reference = _REFERENCE_MAP.get(ref_clean, ref_raw)

        return {
            "company_name": company,
            "investment_type": sec_type,
            "industry": industry,
            "cash_rate": cash,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": self._parse_percent_cell(spread_raw),
            "floor_rate": "",
            "acquisition_date": acquisition,
            "maturity_date": maturity,
            "principal_amount": principal,
            "amortized_cost": self._clean_number(cost_val),
            "fair_value": self._clean_number(fair),
            "percent_of_net_assets": pct,
            "cost": self._clean_number(cost_val),
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
            if any(_norm_header(c) == "investments" for c in row):
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
    def _span(row: List[str], start: int) -> int:
        if start >= len(row):
            return 3
        cell = row[start]
        end = start
        while end < len(row) and row[end] == cell:
            end += 1
        return end - start

    @staticmethod
    def _strip_footnote_refs(value: str) -> str:
        if not value:
            return ""
        return re.sub(r"\s*(?:\(\d+\)\s*)+$", "", value.strip()).strip()

    @staticmethod
    def _map_section_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "revolv" in s:
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
        if "preferred equity" in s:
            return "Preferred Equity"
        if "common equity" in s:
            return "Common Equity"
        if "warrant" in s:
            return "Warrants"
        if "joint venture" in s:
            return "Partnership Interest"
        if "equity" in s:
            return "Other Equity"
        if "debt" in s:
            return "Other Debt"
        return ""

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
    def _split_interest_rate(cls, interest: str) -> tuple:
        if not interest:
            return "", ""
        pcts = re.findall(r"\d+(?:\.\d+)?%", interest)
        if "pik" in interest.lower():
            if len(pcts) >= 2:
                return pcts[0], pcts[-1]
            return "", (pcts[0] if pcts else "")
        return (pcts[0] if pcts else ""), ""

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
        return matches[-1].group(0).strip()
