"""
Custom scraper for BCSF (Bain Capital Specialty Finance, Inc.).

BCSF files its consolidated schedule of investments with this header layout
(single-width cells):

    Portfolio Company | Investment Type | Index | Floor | Spread |
    Interest Rate | Maturity Date | Principal/Shares | Cost |
    Market Value | % of Net Assets

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Company names carry footnote refs in the same cell ("ATS (4)(12)"),
    which are stripped.
  * Investment Type is a per-row column ("First Lien Senior Secured Loan",
    "- Revolver", "- Delayed Draw", "Equity Interest", "Preferred Equity").
  * The Spread cell may be PIK ("10.00 % PIK").
  * Equity rows use the Principal/Shares column for shares.
  * Section rows ("Non-Controlled/Non-Affiliate Investments"), industry rows
    ("Aerospace & Defense") and totals are skipped; industry is carried down.
  * The 10-Q's prior year-end SOI has a different header (no Floor column)
    and is excluded by detection.
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


class BCSFScraper(BaseCompanyScraper):
    ticker = "BCSF"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Portfolio Company" not in t["text"][:600]:
                continue
            grid = self.parse_table_to_rows(t["html"])
            for row in grid[:4]:
                labels = {_norm_header(c) for c in row if c.strip()}
                # The current-period SOI has a Floor column; the prior
                # year-end schedule does not.
                if ("portfoliocompany" in labels and "investmenttype" in labels
                        and ("marketvalue" in labels or "fairvalue" in labels)):
                    soi.append(t)
                    break
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("BCSF: found %d SOI tables", len(soi))
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
        logger.info("BCSF: current SOI heading: %r", current_heading)

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
            if header_idx is None:
                continue
            pos = self._header_positions(grid[header_idx])
            if "portfoliocompany" not in pos or "investmenttype" not in pos:
                continue

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, pos["portfoliocompany"],
                                      pos["portfoliocompany"] + 2)
                type_raw = _cell_range(row, pos["investmenttype"],
                                       pos["investmenttype"] + 2)
                low = company.lower()

                if not company:
                    continue

                if not type_raw:
                    if low.startswith("total") or low.startswith("subtotal"):
                        continue
                    if low in ("investments", "non-controlled/non-affiliate investments",
                               "non-controlled/affiliate investments",
                               "controlled investments",
                               "cash and cash equivalents", "cash & cash equivalents"):
                        continue
                    current_industry = self._clean_company(company)
                    continue

                record = self._row_to_record(row, self._clean_company(company),
                                             type_raw, current_industry, pos)
                rows.append(record)

        logger.info("BCSF: parsed %d investment rows", len(rows))
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
            return _cell_range(row, p, p + 2)

        ref_raw = grp("index").strip().upper()
        ref_map = {"SOFR": "SOFR", "SONIA": "SONIA", "BKBM": "BKBM",
                   "EURIBOR": "Euribor", "LIBOR": "LIBOR", "CDOR": "CDOR"}
        reference = ref_map.get(ref_raw, ref_raw if ref_raw not in ("—", "-") else "")

        floor = self._parse_percent_cell(grp("floor"))
        spread_raw = grp("spread")
        pcts = re.findall(r"(\d+(?:\.\d+)?)\s*%", spread_raw)
        spread = pcts[0] + "%" if pcts else ""
        pik_m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", spread_raw, re.IGNORECASE)
        pik = pik_m.group(1) + "%" if pik_m else ""

        cash = self._parse_percent_cell(grp("interestrate"))
        maturity = self._parse_date(grp("maturitydate"))
        par = self._clean_number(grp("principal/shares"))
        cost = self._clean_number(grp("cost"))
        fair = self._clean_number(grp("marketvalue") or grp("fairvalue"))
        pct = self._parse_percent_cell(grp("%ofnetassets") or grp("%ofnav"))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", par
        else:
            principal, shares = par, ""

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
    def _clean_company(value: str) -> str:
        s = re.sub(r"\s*(\(\d+\))+\s*$", "", (value or "").strip())
        return s.strip()

    @staticmethod
    def _map_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s or "unitranche" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "common stock" in s or "common equity" in s:
            return "Common Equity"
        if "preferred equity" in s or "preferred stock" in s:
            return "Preferred Equity"
        if "equity interest" in s:
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
