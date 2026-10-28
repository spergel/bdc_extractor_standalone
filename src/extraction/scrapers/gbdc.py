"""
Custom scraper for GBDC (Golub Capital BDC, Inc.).

GBDC files its consolidated schedule of investments as HTML tables whose
headers vary slightly by section, but always contain these groups:

    Investment Type | SpreadAboveIndex (6 cols) | InterestRate |
    [Acquisition Date] | MaturityDate | Principal ($)/Shares |
    Amortized Cost | Percentage of Net Assets | Fair Value

The company name sits in the 3 columns immediately before the Investment
Type group (there is no "Company" header label).

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * The company name is repeated on every data row.
  * Industry rows ("Textiles, Apparel & Luxury Goods") and section headers
    ("Equity investments(22)") fill the company cell but leave Investment
    Type empty.
  * The InterestRate group may contain the all-in rate plus a "cash/ X% PIK"
    split (e.g. "10.51%  cash/ 1.25% PIK"); cash_rate gets the first
    percentage and pik_rate the percentage near "PIK".
  * 10-Qs include a prior year-end comparative SOI; headings read
    "…Schedule of Investments (unaudited) - (continued) <date>".
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_REFERENCE_MAP = {
    "SF+": "SOFR",
    "SF +": "SOFR",
    "SF": "SOFR",
    "S+": "SOFR",
    "S": "SOFR",
    "L+": "LIBOR",
    "L": "LIBOR",
    "E+": "Euribor",
    "E": "Euribor",
    "P+": "Prime",
    "P": "Prime",
    "SONIA+": "SONIA",
    "SONIA": "SONIA",
    "CORRA+": "CORRA",
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
    return re.sub(r"[^a-z0-9/%$]", "", s)


def _cell_range(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip()
        if v and v not in ("$", "€", "%"):
            return v
    return ""


class GBDCScraper(BaseCompanyScraper):
    ticker = "GBDC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    _TYPE_KEYWORDS = (
        "one stop", "senior secured", "second lien", "subordinated",
        "unsecured", "common stock", "preferred stock", "llc interest",
        "lp interest", "warrant",
    )

    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        """
        Keep tables that repeat the SOI header row, plus headerless
        continuation tables that sit between them.  GBDC repeats the header
        only on the first table of a page run; continuation tables start
        directly with data rows.
        """
        soi: List[Dict[str, Any]] = []
        header_positions: List[int] = []
        seen_ids = set()
        for t in tables:
            if "Investment Type" not in t["text"][:800] and "SpreadAboveIndex" not in t["text"][:1200]:
                continue
            grid = self.parse_table_to_rows(t["html"])
            for row in grid[:4]:
                labels = {_norm_header(c) for c in row if c.strip()}
                if "investmenttype" in labels and "fairvalue" in labels:
                    soi.append(t)
                    seen_ids.add(id(t))
                    header_positions.append(t.get("pos") or 0)
                    break

        if header_positions:
            min_pos, max_pos = min(header_positions), max(header_positions)
            for t in tables:
                if id(t) in seen_ids:
                    continue
                pos = t.get("pos") or 0
                if not (min_pos < pos < max_pos):
                    continue
                text_low = t["text"][:3000].lower()
                if any(k in text_low for k in self._TYPE_KEYWORDS):
                    soi.append(t)

        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("GBDC: found %d SOI tables", len(soi))
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
        logger.info("GBDC: current SOI heading: %r", current_heading)

        last_pos: Optional[Dict[str, int]] = None
        last_spread_rng: Optional[tuple] = None
        last_ref_rng: Optional[tuple] = None

        for t in soi_tables:
            if current_heading:
                heading = self._heading_before(filing_result, t)
                if heading and heading != current_heading:
                    continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is not None:
                pos = self._header_positions(grid[header_idx])
                if "investmenttype" not in pos or "fairvalue" not in pos:
                    pos = None
            else:
                pos = None

            # Headerless continuation tables inherit the column layout from
            # the previous table that had a header row.
            if pos is not None:
                last_pos = pos
                spread_start = pos.get("spreadaboveindex")
                if spread_start is not None:
                    spread_span = self._span(grid[header_idx], spread_start)
                    last_ref_rng = (spread_start, spread_start + 3)
                    last_spread_rng = (spread_start + 3, spread_start + spread_span)
                else:
                    last_ref_rng = last_spread_rng = None
                data_start = header_idx + 1
            else:
                if last_pos is None:
                    continue
                pos = last_pos
                data_start = 0

            type_pos = pos["investmenttype"]
            company_rng = (type_pos - 3, type_pos) if type_pos >= 3 else (0, 3)
            rate_pos = pos.get("interestrate")

            next_positions = []
            if pos.get("acquisitiondate") is not None:
                next_positions.append(pos["acquisitiondate"])
            if pos.get("maturitydate") is not None:
                next_positions.append(pos["maturitydate"])
            interest_end = min(next_positions) if next_positions else (
                (rate_pos + 12) if rate_pos is not None else None)

            for row in grid[data_start:]:
                company = self._strip_footnote_refs(
                    _cell_range(row, *company_rng))
                type_raw = _cell_range(row, type_pos, type_pos + 3)

                if not company and not type_raw:
                    # Subtotal / continuation row.
                    continue

                low = company.lower()
                if low.startswith("total"):
                    continue

                # Industry/section rows fill the company cell and leave the
                # Investment Type empty, or span the whole row (in which
                # case the type cell repeats the company text).
                type_clean = self._strip_footnote_refs(type_raw)
                if not type_raw or type_clean == company or type_raw == company:
                    if "investments" in low:
                        continue
                    current_industry = company
                    continue

                type_low = type_raw.lower()
                if "money market" in type_low:
                    continue

                record = self._row_to_record(
                    row, company, type_raw, current_industry,
                    last_ref_rng, last_spread_rng, rate_pos, interest_end, pos)
                rows.append(record)

        logger.info("GBDC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, type_raw: str,
                       industry: str,
                       ref_rng: Optional[tuple], spread_rng: Optional[tuple],
                       rate_pos: Optional[int], interest_end: Optional[int],
                       pos: Dict[str, int]) -> Dict[str, str]:
        inv_type = self._map_type(type_raw)

        ref_raw = _cell_range(row, *ref_rng) if ref_rng else ""
        spread = self._parse_percent_cell(
            _cell_range(row, *spread_rng)) if spread_rng else ""

        interest = ""
        if rate_pos is not None and interest_end is not None:
            interest = " ".join(
                (row[i] or "").strip() for i in range(
                    max(0, rate_pos), min(interest_end, len(row))))

        cash, pik = self._split_interest_rate(interest)

        if pos.get("acquisitiondate") is not None:
            acquisition = self._parse_date(
                _cell_range(row, pos["acquisitiondate"], pos["acquisitiondate"] + 3))
        else:
            acquisition = ""

        def grp(key: str) -> str:
            p = pos.get(key)
            if p is None:
                return ""
            return _cell_range(row, p, p + 3)

        maturity = self._parse_date(grp("maturitydate"))
        par = grp("principal$/shares")
        cost = self._clean_number(grp("amortizedcost"))
        pct = self._parse_percent_cell(grp("percentageofnetassets"))
        fair = self._clean_number(grp("fairvalue"))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", self._clean_number(par)
        else:
            principal, shares = self._clean_number(par), ""

        ref_clean = (ref_raw or "").strip().upper()
        reference = _REFERENCE_MAP.get(ref_clean, ref_raw)

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
            if any(_norm_header(c) == "investmenttype" for c in row):
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
        s = re.sub(r"\s*\+\s*(?:\(\d+\)\s*)+$", "", value.strip())
        return s.rstrip(" *+").strip()

    @staticmethod
    def _map_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "one stop" in s:
            return "First Lien"
        if "second lien" in s:
            return "Second Lien"
        if "senior secured" in s or "first lien" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "revolv" in s:
            return "Revolver"
        if "delayed draw" in s:
            return "Delayed Draw"
        if "common stock" in s or "common equity" in s:
            return "Common Equity"
        if "preferred" in s:
            return "Preferred Equity"
        if "lp interest" in s or "lp units" in s or "lp" in s:
            return "Partnership Interest"
        if "llc interest" in s:
            return "Other Equity"
        if "warrant" in s:
            return "Warrants"
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
    def _split_interest_rate(cls, interest: str) -> tuple:
        if not interest:
            return "", ""
        pcts = re.findall(r"\d+(?:\.\d+)?%", interest)
        if not pcts:
            return "", ""
        cash = pcts[0]
        pik = ""
        if "pik" in interest.lower():
            pik = pcts[-1] if len(pcts) >= 2 else ""
        return cash, pik

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
