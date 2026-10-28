"""
Custom scraper for ARCC (Ares Capital Corporation).

ARCC files its consolidated schedule of investments as a series of HTML
tables, one per page, each repeating the same column header row:

    Company | Business Description | Investment | Coupon | Reference |
    Spread | Acquisition Date | Maturity Date | Shares/Units |
    Principal | Amortized Cost | Fair Value | % of Net Assets

Dollar amounts are reported in millions and converted to thousands to match
the standard pipeline scale.

The scraper downloads filings exactly like the rest of the repo (SECAPIClient),
then column-maps each table by its header row so year-to-year layout changes
are handled by the same code path.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

# Header label (normalized) -> cell key for the ARCC SOI layout.
_COLUMN_LABELS: Tuple[Tuple[str, str], ...] = (
    ("company", "company"),
    ("desc", "businessdescription"),
    ("investment", "investment"),
    ("coupon", "coupon"),
    ("reference", "reference"),
    ("spread", "spread"),
    ("acq", "acquisitiondate"),
    ("maturity", "maturitydate"),
    ("shares", "shares/units"),
    ("principal", "principal"),
    ("amortized", "amortizedcost"),
    ("fair", "fairvalue"),
    ("pct", "%ofnetassets"),
)


def _norm_header(label: str) -> str:
    """Normalize a header cell for group matching, e.g. 'Company (1)' -> 'company'."""
    s = re.sub(r"\s*\(\d+\)\s*", "", label or "")
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9/%]", "", s)


def _build_groups(header_row: List[str]) -> List[Dict[str, Any]]:
    """Split a header row into runs of equal cells -> (label, start, end) groups."""
    groups: List[Dict[str, Any]] = []
    prev: Optional[str] = None
    start = 0
    for i, cell in enumerate(header_row):
        if cell != prev:
            if prev is not None:
                groups.append({"label": _norm_header(prev), "start": start, "end": i})
            prev = cell
            start = i
    if prev is not None:
        groups.append({"label": _norm_header(prev), "start": start, "end": len(header_row)})
    return groups


class ARCCScraper(BaseCompanyScraper):
    ticker = "ARCC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        """Keep only tables whose header row is the ARCC SOI header."""
        soi: List[Dict[str, Any]] = []
        for t in tables:
            txt = t["text"]
            if "Company" not in txt[:800]:
                continue
            if "Fair Value" not in txt[:2000]:
                continue
            grid = self.parse_table_to_rows(t["html"])
            for row in grid[:4]:
                labels = {_norm_header(c) for c in row if c.strip()}
                if ("company" in labels and "businessdescription" in labels
                        and "coupon" in labels):
                    soi.append(t)
                    break
        logger.info("ARCC: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_industry = ""
        last_company = ""

        # 10-Q filings contain a comparative prior-year-end SOI section.
        # We only want the current period, so stop once that section starts.
        in_prior_year_section = False
        prior_heading, current_heading = self._soi_heading_markers(filing_result)

        for t in soi_tables:
            if prior_heading:
                ctx = self._context_before(t, filing_result).lower()
                if (not in_prior_year_section
                        and prior_heading in ctx
                        and (not current_heading or current_heading not in ctx)):
                    in_prior_year_section = True
                    logger.info("ARCC: prior year-end SOI section reached at table %s; skipping rest",
                                t["id"])
                if in_prior_year_section:
                    continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            groups = _build_groups(grid[header_idx])
            by_label = {g["label"]: g for g in groups}

            # Ignore tables whose header doesn't include the ARCC columns.
            if "company" not in by_label or "coupon" not in by_label:
                continue

            for row in grid[header_idx + 1:]:
                cells = {key: self._cell_value(row, by_label.get(label))
                         for key, label in _COLUMN_LABELS}
                company = self._strip_footnote_refs(cells.get("company", ""))

                if not company:
                    if self._is_continuation(cells) and last_company:
                        company = last_company
                    else:
                        continue
                else:
                    if self._is_section_header(cells):
                        current_industry = company
                        continue
                    if re.match(r"^(total|subtotal)\b", company, re.I):
                        continue
                    last_company = company

                rows.append(self._cells_to_record(company, current_industry, cells))

        logger.info("ARCC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Helpers
    # --------------------------------------------------------------
    @staticmethod
    def _find_header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid):
            if any(_norm_header(c) == "company" for c in row):
                return i
        return None

    def _soi_heading_markers(self, filing_result: Any) -> Tuple[str, str]:
        """
        Return (prior_year_heading, current_period_heading) lowercase marker
        strings for 10-Q filings, so the prior year-end comparative SOI can be
        skipped.  Returns ("", "") for 10-K (year-end data is what we want).
        """
        filing_type = getattr(filing_result, "filing_type", "10-Q")
        period_end = getattr(filing_result, "period_end_date", None)
        if filing_type != "10-Q" or not period_end:
            return "", ""
        try:
            dt = datetime.strptime(str(period_end)[:10], "%Y-%m-%d")
        except ValueError:
            return "", ""
        months = {1: "january", 2: "february", 3: "march", 4: "april",
                  5: "may", 6: "june", 7: "july", 8: "august",
                  9: "september", 10: "october", 11: "november", 12: "december"}
        current = f"as of {months.get(dt.month, '')} {dt.day}, {dt.year}"
        prior = f"as of december 31, {dt.year - 1}"
        return prior, current

    def _context_before(self, table: Dict[str, Any], filing_result: Any,
                        chars: int = 700) -> str:
        """Return the text immediately preceding a table in its document."""
        html = (getattr(filing_result, "text_map", None) or {}).get(table.get("doc", ""), "")
        pos = table.get("pos")
        if not html or pos is None:
            return ""
        raw = html[max(0, pos - chars * 4):pos]
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"&#160;|&nbsp;", " ", text, flags=re.I)
        text = re.sub(r"\s+", " ", text)
        return text[-chars:]

    @staticmethod
    def _cell_value(row: List[str], group: Optional[Dict[str, Any]]) -> str:
        if group is None:
            return ""
        for i in range(group["start"], min(group["end"], len(row))):
            cell = (row[i] or "").strip()
            if cell and cell not in ("$", "%"):
                return cell
        return ""

    @staticmethod
    def _is_section_header(cells: Dict[str, str]) -> bool:
        keys = ("desc", "investment", "coupon", "reference", "spread",
                "acq", "maturity", "shares", "principal", "amortized", "fair")
        return not any(cells.get(k) for k in keys)

    @staticmethod
    def _is_continuation(cells: Dict[str, str]) -> bool:
        return any(cells.get(k) for k in ("investment", "coupon", "reference",
                                          "spread", "maturity", "shares"))

    def _cells_to_record(self, company: str, industry: str,
                         cells: Dict[str, str]) -> Dict[str, str]:
        cash, pik = self._parse_coupon(cells.get("coupon", ""))
        return {
            "company_name": company,
            "investment_type": self._map_investment_type(cells.get("investment", "")),
            "industry": industry,
            "cash_rate": cash,
            "pik_rate": pik,
            "reference_rate": " ".join(cells.get("reference", "").split()),
            "spread": self._parse_spread(cells.get("spread", "")),
            "floor_rate": "",
            "acquisition_date": self._parse_date(cells.get("acq", "")),
            "maturity_date": self._parse_date(cells.get("maturity", "")),
            "principal_amount": self._to_thousands(cells.get("principal", "")),
            "amortized_cost": self._to_thousands(cells.get("amortized", "")),
            "fair_value": self._to_thousands(cells.get("fair", "")),
            "percent_of_net_assets": self._parse_percent(cells.get("pct", "")),
            "cost": "",
            "commitment_limit": "",
            "undrawn_commitment": "",
            "rate_cap": "",
            "shares": self._clean_number(cells.get("shares", "")),
        }

    # --------------------------------------------------------------
    # Field parsers
    # --------------------------------------------------------------
    @staticmethod
    def _parse_percent(value: str) -> str:
        if not value:
            return ""
        s = value.strip()
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", s)
        if m:
            return m.group(1)
        if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
            return s
        return ""

    def _parse_coupon(self, value: str) -> Tuple[str, str]:
        """Split a coupon cell like '9.66 % ( 3.00 % PIK)' into cash/pik."""
        if not value:
            return "", ""
        s = value.strip()
        up = s.upper()
        cash = ""
        pik = ""
        if "PIK" in up:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", up)
            if m:
                pik = m.group(1) + "%"
            pcts = re.findall(r"(\d+(?:\.\d+)?)\s*%", s)
            others = [p for p in pcts if (m is None or p != m.group(1))]
            if others:
                cash = others[0] + "%"
        else:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
            if m:
                cash = m.group(1) + "%"
        return cash, pik

    @staticmethod
    def _parse_spread(value: str) -> str:
        if not value:
            return ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", value)
        return m.group(1) + "%" if m else ""

    @staticmethod
    def _map_investment_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
        if not s:
            return ""
        if "warrant" in s:
            return "Warrants"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "second lien" in s:
            return "Second Lien"
        if "revolving" in s:
            return "Revolver"
        if "first lien" in s:
            return "First Lien"
        if "senior secured" in s:
            return "First Lien"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s:
            return "Common Equity"
        if "ordinary" in s:
            return "Common Equity"
        if "redeemable" in s:
            return "Common Equity"
        if any(k in s for k in ("limited partner", "limited liability", "membership", "partner")):
            return "Partnership Interest"
        if "units" in s:
            return "Partnership Interest"
        if "shares" in s:
            return "Common Equity"
        if "interest" in s:
            return "Partnership Interest"
        return "Other"
