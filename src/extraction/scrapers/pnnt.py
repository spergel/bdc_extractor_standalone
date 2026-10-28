"""
Custom scraper for PNNT (PennantPark Investment Corporation).

PNNT files its consolidated schedule of investments with this header layout
(single-width cells):

    Issuer Name | Acquisition | Maturity | Industry | Current Coupon |
    Basis Point Spread Above Index | Par / Shares | Cost | Fair Value

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Issuer cells carry tranche suffixes (" - Unfunded Term Loan (7)",
    " - Funded Revolver"), which override the section type.
  * Section headers ("First Lien Secured Debt - 86.7 % of Net Assets",
    "Preferred Equity", …) set the default type.
  * Spread cells pack the reference ("3M SOFR+ 550" -> SOFR + 5.50%).
  * Equity rows put shares/units in the Par / Shares column.
  * A "(PIK 0.50 %)" note may trail the fair value cell.
  * The current section ends at the "Net Assets - 100 %" table; the prior
    year-end SOI follows and is excluded.
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
    "issuername", "acquisition", "maturity", "industry", "currentcoupon",
    "basispointspreadaboveindex", "par/shares", "cost", "fairvalue",
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


class PNNTScraper(BaseCompanyScraper):
    ticker = "PNNT"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Issuer Name" not in t["text"][:400]:
                continue
            if "Industry" not in t["text"][:800]:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("PNNT: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_type = "First Lien"

        for t in soi_tables:
            # The current section ends with the "Net Assets - 100 %" table.
            if "Net Assets" in t["text"][:400] and "100" in t["text"][:600]:
                break

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[header_idx])
            if "issuername" not in ranges or "industry" not in ranges:
                continue

            for row in grid[header_idx + 1:]:
                issuer = _cell_range(row, *ranges["issuername"])
                industry = _cell_range(row, *ranges["industry"])
                low = issuer.lower()

                if not issuer:
                    continue

                if not industry:
                    if low.startswith("total") or low.startswith("subtotal"):
                        continue
                    sec_type = self._map_section_type(issuer)
                    if sec_type:
                        current_type = sec_type
                    continue

                # Cash equivalents / short-term government holdings are not
                # portfolio company investments.
                if "short-term" in industry.lower() or "u.s. treasury" in low \
                        or "cash equivalent" in low:
                    continue

                company, suffix_type = self._split_issuer(issuer)
                record = self._row_to_record(
                    row, company, industry,
                    suffix_type or current_type, ranges)
                rows.append(record)

        logger.info("PNNT: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, industry: str,
                       sec_type: str, ranges: Dict[str, tuple]) -> Dict[str, str]:
        def grp(key: str) -> str:
            rng = ranges.get(key)
            if not rng:
                return ""
            return _cell_range(row, *rng)

        coupon = self._parse_percent_cell(grp("currentcoupon"))
        reference, spread = self._parse_ref_spread(grp("basispointspreadaboveindex"))

        # PIK note may trail the fair value cell.
        pik = ""
        fv_rng = ranges.get("fairvalue")
        if fv_rng and fv_rng[1] < len(row):
            trailing = " ".join((c or "").strip() for c in row[fv_rng[1]:fv_rng[1] + 3])
            pik_m = re.search(r"PIK\s*(\d+(?:\.\d+)?)\s*%", trailing, re.IGNORECASE)
            if pik_m:
                pik = pik_m.group(1) + "%"

        par = self._clean_number(grp("par/shares"))
        cost = self._clean_number(grp("cost"))
        fair = self._clean_number(grp("fairvalue"))

        if sec_type in _EQUITY_TYPES:
            principal, shares = "", par
        else:
            principal, shares = par, ""

        return {
            "company_name": company,
            "investment_type": sec_type,
            "industry": industry,
            "cash_rate": coupon,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": "",
            "acquisition_date": self._parse_date(grp("acquisition")),
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
            if any(_norm_header(c) == "issuername" for c in row):
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
    def _split_issuer(value: str) -> tuple:
        s = (value or "").strip()
        m = re.search(r"\s*-\s*(Unfunded Revolver|Funded Revolver|"
                      r"Unfunded Term Loan|Funded Term Loan|Term Loan|"
                      r"Revolver|Delayed Draw)\s*(?:\(\d+\))?\s*$",
                      s, re.IGNORECASE)
        if m:
            suffix = m.group(1).lower()
            company = s[:m.start()].strip()
            if "revolver" in suffix or "delayed draw" in suffix:
                return company, "Revolver"
            return company, "First Lien"
        company = re.sub(r"\s*(\(\d+\))+\s*$", "", s)
        return company.strip(), ""

    @staticmethod
    def _map_section_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s or "secured debt" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "preferred equity" in s:
            return "Preferred Equity"
        if "common equity" in s or "equity" in s or "warrant" in s:
            return "Other Equity"
        return ""

    @staticmethod
    def _parse_ref_spread(s: str) -> tuple:
        if not s:
            return "", ""
        m = re.search(r"(\d+M\s+)?(SOFR|PRIME|LIBOR|EURIBOR|SONIA|CDOR)\s*\+\s*(\d+(?:\.\d+)?)", s, re.IGNORECASE)
        if not m:
            return "", ""
        ref_raw = m.group(2).upper()
        ref_map = {"SOFR": "SOFR", "PRIME": "Prime", "LIBOR": "LIBOR",
                   "EURIBOR": "Euribor", "SONIA": "SONIA", "CDOR": "CDOR"}
        bps = float(m.group(3))
        return ref_map.get(ref_raw, ref_raw), f"{bps / 100.0:g}%"

    @staticmethod
    def _parse_date(value: str) -> str:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", (value or "").strip())
        if m:
            return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
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
