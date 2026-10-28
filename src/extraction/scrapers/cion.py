"""
Custom scraper for CION (CION Investment Corporation).

CION files its consolidated schedule of investments with this header:

    Portfolio Company(a) | Interest(b) | Maturity | Industry |
    Principal/Par Amount/Units(e) | Cost(d) | Fair Value(c)

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Investment type comes from section rows
    ("Senior Secured First Lien Debt - 195.2%", "Equity - 50.0%").
  * Equity rows embed the instrument in the company cell
    ("ACS Holdings LLC, Class A-1 Membership Units"); the instrument is
    stripped and used for the investment type.
  * Interest cells pack reference, spread and floor
    ("S+725, 1.00% SOFR Floor" -> SOFR + 7.25%, floor 1.00%).
  * The prior year-end schedule follows the current one; parsing stops when
    a section row repeats.
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

_COLUMN_KEYS = [
    "portfoliocompany", "interest", "maturity", "industry",
    "principal/paramount/units", "cost", "fairvalue",
]

_INSTRUMENT_RE = re.compile(
    r"[,;]?\s*((?:Class\s+[A-Z0-9\-]+\s+)?(?:Membership\s+Units?|Class\s+[A-Z0-9\-]+\s+Units?|"
    r"Preferred\s+(?:Units?|Stock|Equity)|Common\s+(?:Units?|Stock|Equity)|"
    r"LLC\s+Units?|Units?|Warrants?(?:\s+to\s+Purchase\s+Common\s+Stock)?|"
    r"Term\s+Loan(?:\s+[A-Z])?|Revolver|Delayed\s+Draw(?:\s+Term\s+Loan)?|"
    r"First\s+Lien(?:\s+Term\s+Loan)?|Second\s+Lien(?:\s+Term\s+Loan)?|"
    r"Subordinated\s+(?:Debt|Notes?)|Notes?))\s*$",
    re.IGNORECASE,
)


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


class CIONScraper(BaseCompanyScraper):
    ticker = "CION"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Portfolio Company" not in t["text"][:300]:
                continue
            if "Fair Value" not in t["text"][:600]:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("CION: found %d SOI tables", len(soi))
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
        seen_sections = {}

        for t in soi_tables:
            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[header_idx])
            if "portfoliocompany" not in ranges:
                continue

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, *ranges["portfoliocompany"])
                industry = _cell_range(row, *ranges["industry"]) \
                    if "industry" in ranges else ""
                low = company.lower()

                if not company:
                    continue
                if low.startswith("total") or low.startswith("subtotal"):
                    continue
                if "investments" in low and ("non-controlled" in low
                                             or "affiliated" in low):
                    continue
                if low.startswith("net assets") or "treasury" in low \
                        or "cash equivalent" in low:
                    continue

                fair_cell = _cell_range(row, *ranges["fairvalue"]) \
                    if "fairvalue" in ranges else ""
                cost_cell = _cell_range(row, *ranges["cost"]) \
                    if "cost" in ranges else ""

                if not industry and (fair_cell or cost_cell):
                    # Continuation tranche row: industry carried down.
                    raw_type = current_type
                    company_name, instrument_type = self._split_instrument(company)
                    inv_type = instrument_type or raw_type or "Other"
                    rows.append(self._row_to_record(row, company_name,
                                                    current_industry,
                                                    inv_type, ranges))
                    continue

                if not industry:
                    # Section row, e.g. "Senior Secured First Lien Debt - 195.2%"
                    if re.search(r"first lien|second lien|subordinated|"
                                 r"unsecured|equity|note", low):
                        key = re.sub(r"[\d.,%\s\-]+$", "", low).strip()
                        pct_m = re.search(r"([\d.]+)\s*%\s*$", company)
                        pct = pct_m.group(1) if pct_m else ""
                        if key in seen_sections and seen_sections[key]                                 and pct and seen_sections[key] != pct:
                            # Prior year-end schedule restarts with new %.
                            return rows
                        seen_sections[key] = pct or seen_sections.get(key, "")
                        current_type = self._map_type(low)
                    continue

                current_industry = industry
                raw_type = current_type
                company_name, instrument_type = self._split_instrument(company)
                inv_type = instrument_type or raw_type or "Other"

                rows.append(self._row_to_record(row, company_name, industry,
                                                inv_type, ranges))
            logger.info("CION: %s -> %d rows total", t["id"].rsplit("_", 1)[-1], len(rows))

        logger.info("CION: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, industry: str,
                       inv_type: str, ranges: Dict[str, tuple]) -> Dict[str, str]:
        def grp(key: str) -> str:
            rng = ranges.get(key)
            if not rng:
                return ""
            return _cell_range(row, *rng)

        reference, spread, floor, cash, pik = self._parse_interest(grp("interest"))

        principal = self._clean_number(grp("principal/paramount/units"))
        cost = self._clean_number(grp("cost"))
        fair = self._clean_number(grp("fairvalue"))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", principal
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
            "floor_rate": floor,
            "acquisition_date": "",
            "maturity_date": self._parse_date(grp("maturity")),
            "principal_amount": principal,
            "amortized_cost": cost,
            "fair_value": fair,
            "percent_of_net_assets": "",
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
    def _split_instrument(value: str) -> tuple:
        s = (value or "").strip()
        s = re.sub(r"\s*(\(\d+\))+\s*$", "", s)   # footnote refs
        s = re.sub(r"\s*\([a-z]{1,3}\)\s*$", "", s)
        m = _INSTRUMENT_RE.search(s)
        if m and m.start() > 0:
            company = s[:m.start()].strip().rstrip(",").strip()
            instrument = m.group(1)
            return company, CIONScraper._map_type(instrument)
        return s.strip(), ""

    @staticmethod
    def _map_type(raw: str) -> str:
        s = (raw or "").lower()
        if "first lien" in s or "senior secured" in s and "second" not in s:
            return "First Lien"
        if "second lien" in s:
            return "Second Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s or "note" in s:
            return "Unsecured Debt"
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s:
            return "Common Equity"
        if "equity" in s or "unit" in s or "membership" in s:
            return "Other Equity"
        if "term loan" in s or "revolver" in s or "delayed draw" in s:
            return "First Lien"
        return ""

    @staticmethod
    def _parse_interest(value: str) -> tuple:
        """Return (reference, spread, floor, cash, pik)."""
        if not value:
            return "", "", "", "", ""
        s = value.replace("\u00a0", " ")

        pik = ""
        pik_m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", s, re.IGNORECASE)
        if pik_m:
            pik = pik_m.group(1) + "%"

        floor = ""
        floor_m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:SOFR\s*)?Floor", s, re.IGNORECASE)
        if floor_m:
            floor = floor_m.group(1) + "%"

        ref_map = {"S": "SOFR", "SOFR": "SOFR", "L": "LIBOR", "P": "Prime",
                   "E": "Euribor", "SONIA": "SONIA"}
        ref_m = re.search(r"\b(S|SOFR|L|LIBOR|P|Prime|E|EURIBOR|SONIA)\s*\+\s*(\d+(?:\.\d+)?)", s, re.IGNORECASE)
        if ref_m:
            reference = ref_map.get(ref_m.group(1).upper(), ref_m.group(1).upper())
            spread = f"{float(ref_m.group(2)) / 100.0:g}%"
            return reference, spread, floor, "", pik

        # Fixed rate, e.g. "9.49%" or "0.50% Unfunded".
        fixed_m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if fixed_m:
            return "", "", floor, fixed_m.group(1) + "%", pik
        return "", "", floor, "", pik

    @staticmethod
    def _parse_date(value: str) -> str:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", (value or "").strip())
        if m:
            return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
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
