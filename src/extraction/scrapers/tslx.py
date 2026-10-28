"""
Custom scraper for TSLX (Sixth Street Specialty Lending, Inc.).

TSLX files its consolidated schedule of investments as one large table per
period with this header layout:

    Company | Investment | Initial Acquisition Date |
    Reference Rate and Spread | Interest Rate | Amortized Cost |
    Fair Value | Percentage of Net Assets

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * The Investment cell packs the instrument, par/shares and maturity,
    e.g. "First-lien loan ($38,241 par, due 11/2029)",
    "First-lien revolving loan ($4,669 par, due 11/2029)",
    "Class A Units (1,584 units)".  Par/shares are extracted from it.
  * Company names carry footnote refs attached ("Artisan Bidco, Inc.(3)")
    and are stripped; continuation rows repeat the investment but not the
    company, so the company is carried down.
  * Reference cell: "SOFR+7.00%", "E+7.00%" (Euribor).
  * The Interest Rate cell may be a plain number with a separate "%" cell
    or "11.17% PIK" (full PIK).
  * Fair Value cells may carry an FX note ("19,275 (EUR 16,859)") - the
    USD amount comes first.
  * Section rows ("Debt Investments"), industry rows ("Business Services")
    and totals are skipped; industry is carried down.
  * The 10-Q also contains the prior year-end SOI; only the section with
    the current heading ("as of June 30, 2026") is kept.
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

_HEADING_RE = re.compile(
    r"(?:As\s+of\s+)?(?:Consolidated\s+)?Schedule\s+of\s+Investments[^\n]{0,80}?"
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),\s+(\d{4})",
    re.IGNORECASE,
)

_COLUMN_KEYS = [
    "company", "investment", "initialacquisitiondate",
    "referencerateandspread", "interestrate", "amortizedcost",
    "fairvalue", "percentageofnetassets",
]


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


class TSLXScraper(BaseCompanyScraper):
    ticker = "TSLX"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Company" not in t["text"][:400]:
                continue
            if "Reference Rate and Spread" not in t["text"][:1200]:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)

        # The SOI is one giant table rendered as a series of headerless
        # continuation tables.  Include every table between the current
        # header table and the next header table (the prior year-end SOI).
        if len(soi) >= 2:
            current = soi[0]
            prior = soi[1]
            start = current.get("pos") or 0
            end = prior.get("pos") or (start + 1)
            between = [t for t in tables
                       if (t.get("pos") or 0) > start and (t.get("pos") or 0) < end]
            soi = [current] + sorted(between, key=lambda t: t.get("pos") or 0)
        logger.info("TSLX: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_company = ""
        current_industry = ""

        current_heading = None
        for t in soi_tables:
            heading = self._heading_before(filing_result, t)
            if heading:
                current_heading = heading
                break
        logger.info("TSLX: current SOI heading: %r", current_heading)

        ranges: Optional[Dict[str, tuple]] = None
        last_heading = None
        for t in soi_tables:
            heading = self._heading_before(filing_result, t)
            if heading:
                last_heading = heading
            effective = heading or last_heading
            if current_heading is None or effective != current_heading:
                continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            start_row = 0
            if header_idx is not None:
                ranges = self._column_ranges(grid[header_idx])
                start_row = header_idx + 1
            if not ranges or "company" not in ranges or "investment" not in ranges:
                continue

            for row in grid[start_row:]:
                company = _cell_range(row, *ranges["company"])
                investment = _cell_range(row, *ranges["investment"])
                low = company.lower()

                if not company and not investment:
                    continue

                if not investment:
                    if low.startswith("total") or low.startswith("subtotal"):
                        continue
                    if "schedule of investments" in low:
                        continue
                    if ("interest rate swap" in low or "derivative" in low
                            or "foreign currency" in low):
                        continue
                    if low in ("debt investments", "equity investments",
                               "investments", "cash and cash equivalents",
                               "cash & cash equivalents"):
                        continue
                    current_industry = self._clean_company(company)
                    continue

                if low.startswith("interest rate swap") or "derivative" in low:
                    continue

                if company:
                    company = self._clean_company(company)
                    current_company = company
                else:
                    company = current_company

                record = self._row_to_record(row, company, investment,
                                             current_industry, ranges)
                rows.append(record)

        logger.info("TSLX: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, investment: str,
                       industry: str, ranges: Dict[str, tuple]) -> Dict[str, str]:
        inv_type = self._map_type(investment)
        principal, shares, _ = self._parse_investment_cell(investment, inv_type)

        def grp(key: str) -> str:
            rng = ranges.get(key)
            if not rng:
                return ""
            return _cell_range(row, *rng)

        acquisition = self._parse_date(grp("initialacquisitiondate"))
        reference, spread = self._parse_ref_spread(grp("referencerateandspread"))
        cash, pik = self._parse_interest(grp("interestrate"), row, ranges)
        cost = self._clean_number(grp("amortizedcost"))
        fair = self._clean_number(grp("fairvalue"))
        pct = self._parse_percent_cell(grp("percentageofnetassets"))

        if inv_type in _EQUITY_TYPES:
            principal = ""
        else:
            shares = ""

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
            "maturity_date": "",
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
            if any(_norm_header(c) == "company" for c in row):
                return i
        return None

    @staticmethod
    def _column_ranges(row: List[str]) -> Dict[str, tuple]:
        pos: Dict[str, int] = {}
        for i, c in enumerate(row):
            n = _norm_header(c)
            if n and n not in pos:
                pos[n] = i
        ordered = sorted((pos[k], k) for k in _COLUMN_KEYS if k in pos)
        ranges: Dict[str, tuple] = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _clean_company(value: str) -> str:
        s = re.sub(r"\s*(\(\d+\))+\s*$", "", (value or "").strip())
        return s.strip()

    @staticmethod
    def _map_type(investment: str) -> str:
        s = (investment or "").lower().strip()
        if not s:
            return ""
        if "second-lien" in s or "second lien" in s:
            return "Second Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "revolving" in s:
            return "Revolver"
        if "first-lien" in s or "first lien" in s:
            return "First Lien"
        if "common stock" in s:
            return "Common Equity"
        if "preferred" in s:
            return "Preferred Equity"
        if "unit" in s or "llc interest" in s or "equity" in s:
            return "Other Equity"
        if "warrant" in s:
            return "Warrants"
        return "Other"

    @staticmethod
    def _parse_investment_cell(s: str, inv_type: str) -> tuple:
        principal, shares = "", ""
        if inv_type in _EQUITY_TYPES:
            m = re.search(r"\(\s*([\d,]+)\s*(?:shares|units)", s, re.IGNORECASE)
            if m:
                shares = m.group(1).replace(",", "")
        else:
            m = re.search(r"\(\s*([\d,]+)\s*par", s, re.IGNORECASE)
            if m:
                principal = m.group(1).replace(",", "")
        return principal, shares, ""

    @staticmethod
    def _parse_ref_spread(s: str) -> tuple:
        if not s:
            return "", ""
        m = re.search(r"(SOFR|EURIBOR|SONIA|LIBOR|CDOR|E|S|L)\s*\+\s*(\d+(?:\.\d+)?)%", s, re.IGNORECASE)
        if not m:
            return "", ""
        ref_raw = m.group(1).upper()
        ref_map = {"S": "SOFR", "E": "Euribor", "L": "LIBOR",
                   "SOFR": "SOFR", "EURIBOR": "Euribor", "SONIA": "SONIA",
                   "LIBOR": "LIBOR", "CDOR": "CDOR"}
        return ref_map.get(ref_raw, ref_raw), m.group(2) + "%"

    @classmethod
    def _parse_interest(cls, cell_text: str, row: List[str],
                        ranges: Dict[str, tuple]) -> tuple:
        """Return (cash, pik). Handles '10.68' + '%' and '11.17% PIK'."""
        if not cell_text:
            return "", ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", cell_text)
        number = None
        if m:
            number = m.group(1)
        else:
            m2 = re.fullmatch(r"(\d+(?:\.\d+)?)", cell_text.strip())
            if m2:
                number = m2.group(1)
                rng = ranges.get("interestrate")
                if rng:
                    if any("%" in (c or "") for c in row[rng[0]:rng[1]]):
                        number += "%"
        if number is None:
            return "", ""
        if "pik" in cell_text.lower():
            return "", number if number.endswith("%") else number + "%"
        return number if number.endswith("%") else number + "%", ""

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
