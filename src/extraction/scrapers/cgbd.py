"""
Custom scraper for CGBD (Carlyle Secured Lending, Inc.).

CGBD files its consolidated schedule of investments as HTML tables with this
header layout (each header cell spans 3 columns):

    Investments… | Footnotes | Industry | Reference Rate | Spread |
    Interest Rate | Acquisition Date | Maturity Date | Par/Principal |
    Amortized Cost | Fair Value | % of Net Assets

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * There is no per-row Investment Type column.  Type comes from section
    headers ("First Lien Debt (78.9% of fair value)",
    "Equity Investments (…)", "Investment Funds (…)"), which span the row.
  * Total rows ("First Lien Debt Total", "Total investments") are skipped.
  * Some tables (equity variants) omit the Acquisition Date column, so all
    columns are located by header label rather than fixed offsets.
  * 10-Qs include the current SOI ("As of June 30, 2026") and the prior
    year-end SOI ("As of December 31, 2025"); continuation pages without a
    page heading inherit the previous heading.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_REFERENCE_MAP = {
    "SOFR": "SOFR",
    "EURIBOR": "Euribor",
    "LIBOR": "LIBOR",
    "PRIME": "Prime",
    "SONIA": "SONIA",
    "CORRA": "CORRA",
}

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
        if v and v not in ("$", "€", "%"):
            return v
    return ""


class CGBDScraper(BaseCompanyScraper):
    ticker = "CGBD"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Industry" not in t["text"][:600] and "Reference Rate" not in t["text"][:1000]:
                continue
            grid = self.parse_table_to_rows(t["html"])
            for row in grid[:4]:
                labels = {_norm_header(c) for c in row if c.strip()}
                # SOI tables carry Industry + Fair Value plus position-level
                # columns (par/principal, acquisition or maturity date).
                # Industry concentration summaries only have
                # Industry/Cost/Fair Value/% of Fair Value.
                if ("industry" in labels and "fairvalue" in labels
                        and ("par/principalamount" in labels
                             or "acquisitiondate" in labels
                             or "maturitydate" in labels)):
                    soi.append(t)
                    break
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("CGBD: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_type = ""

        current_heading = None
        for t in soi_tables:
            heading = self._heading_before(filing_result, t)
            if heading:
                current_heading = heading
                break
        logger.info("CGBD: current SOI heading: %r", current_heading)

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
            if "industry" not in pos or "fairvalue" not in pos:
                continue

            for row in grid[header_idx + 1:]:
                company = _cell_range(row, 0, 3)
                industry = _cell_range(row, pos["industry"], pos["industry"] + 3)
                low = company.lower()

                if not company:
                    continue

                if not industry:
                    # Section header or total row spanning the row.
                    if "total" in low:
                        continue
                    sec_type = self._map_section_type(company)
                    if sec_type:
                        current_type = sec_type
                    continue

                record = self._row_to_record(row, company, industry,
                                             current_type, pos)
                rows.append(record)

        logger.info("CGBD: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, industry: str,
                       sec_type: str, pos: Dict[str, int]) -> Dict[str, str]:
        def grp(key: str) -> str:
            p = pos.get(key)
            if p is None:
                return ""
            return _cell_range(row, p, p + 3)

        ref_raw = grp("referencerate")
        ref_clean = (ref_raw or "").strip().upper()
        if ref_clean == "FIXED":
            reference = ""
        else:
            reference = _REFERENCE_MAP.get(ref_clean, ref_raw)
        spread_raw = grp("spread")
        pcts = re.findall(r"(\d+(?:\.\d+)?)%", spread_raw)
        spread = pcts[0] + "%" if pcts else ""
        pik_m = re.search(r"(\d+(?:\.\d+)?)%\s*PIK", spread_raw, re.IGNORECASE)
        pik = pik_m.group(1) + "%" if pik_m else ""
        cash = self._parse_percent_cell(grp("interestrate"))

        acquisition = self._parse_date(grp("acquisitiondate"))
        maturity = self._parse_date(grp("maturitydate"))

        par = self._clean_number(grp("par/principalamount"))
        cost = self._clean_number(grp("amortizedcost"))
        fair = self._clean_number(grp("fairvalue"))
        pct = self._parse_percent_cell(grp("%ofnetassets"))

        if sec_type in _EQUITY_TYPES:
            principal, shares = "", par
        else:
            principal, shares = par, ""

        return {
            "company_name": company,
            "investment_type": sec_type,
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
            if any(_norm_header(c) == "industry" for c in row):
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
    def _map_section_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "investment fund" in s:
            return "Partnership Interest"
        if "equity" in s:
            return "Other Equity"
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
