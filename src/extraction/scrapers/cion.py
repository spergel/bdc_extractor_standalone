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
  * The prior year-end schedule follows the current one; tables are filtered
    by their normalized "Month DD, YYYY" schedule heading, and repeated
    body/exhibit copies are collapsed by table signature.
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
        current_first_pct = ""
        seen_signatures = set()

        month_re = (r"(?:January|February|March|April|May|June|July|August|"
                    r"September|October|November|December)\s+\d{1,2},\s+\d{4}")

        def period_heading(table: Dict[str, Any]) -> str:
            ctx = self._context_before(filing_result, table)
            matches = re.findall(month_re, ctx, re.I)
            if not matches:
                matches = re.findall(month_re, (table.get("text") or "")[:1000], re.I)
            if not matches:
                return ""
            try:
                return datetime.strptime(matches[-1], "%B %d, %Y").date().isoformat()
            except ValueError:
                return matches[-1].lower()

        headings = [period_heading(t) for t in soi_tables]
        current_period = next((h for h in headings if h), "")
        first_current = 0
        if current_period:
            for i, h in enumerate(headings):
                if h == current_period:
                    first_current = i
                    break

        # The 10-Q repeats the whole schedule (body + exhibit), so identical
        # rows are collapsed by the base scraper's dedupe.
        for idx, t in enumerate(soi_tables):
            heading = headings[idx]
            if idx < first_current and not heading:
                continue
            if current_period and heading and heading != current_period:
                continue
            table_text = (t.get("text") or "")[:6000].lower()
            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue

            table_pct = ""
            for r in grid[header_idx + 1:header_idx + 6]:
                c = _cell_range(r, 0, 3).lower()
                if c.startswith("senior secured first lien debt"):
                    m = re.search(r"([\d.]+)\s*%\s*$", c)
                    table_pct = m.group(1) if m else ""
                    break
            if table_pct:
                if not current_first_pct:
                    current_first_pct = table_pct
                elif table_pct != current_first_pct:
                    # Prior-period copy of the schedule.
                    continue

            # The filing repeats the whole schedule (body + exhibit); skip a
            # table whose leading positions were already captured.
            sig = []
            for r in grid[header_idx + 1:header_idx + 8]:
                c = _cell_range(r, 0, 3)
                f = _cell_range(r, 36, 39)
                if c and f:
                    sig.append((c.lower(), f))
            if len(sig) >= 3:
                key = tuple(sig[:3])
                if key in seen_signatures:
                    continue
                seen_signatures.add(key)
            ranges = self._column_ranges(grid[header_idx])
            if "portfoliocompany" not in ranges or "fairvalue" not in ranges:
                continue

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, *ranges["portfoliocompany"])
                industry = _cell_range(row, *ranges["industry"]) \
                    if "industry" in ranges else ""
                low = company.lower()

                if not company:
                    continue
                if low.startswith("total") or low.startswith("subtotal") \
                        or "sub-total" in low:
                    continue
                if "liabilities in excess" in low or "liabilities and" in low:
                    continue
                if "non-controlled" in low:
                    continue

                if not industry:
                    # Section row, e.g. "Senior Secured First Lien Debt - 195.2%"
                    if re.search(r"first lien|second lien|subordinated|unsecured|"
                                 r"equity|note|structured", low):
                        current_type = self._map_type(low)
                    continue

                company_name, instrument_type = self._split_instrument(company)
                inv_type = instrument_type or current_type or "Other"
                rows.append(self._row_to_record(row, company_name, industry,
                                                inv_type, ranges))

        # Collapse the duplicated copy of the schedule: identical positions
        # (same company, type, principal, cost and fair value) appear in both
        # the financial statements and the exhibit.
        unique: List[Dict[str, str]] = []
        seen_keys = set()
        for r in rows:
            key = (r["company_name"], r["investment_type"],
                   r["principal_amount"], r["amortized_cost"],
                   r["fair_value"], r["maturity_date"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique.append(r)

        logger.info("CION: parsed %d investment rows (%d unique)",
                    len(rows), len(unique))
        return unique

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
