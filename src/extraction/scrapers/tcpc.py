"""
Custom scraper for TCPC (BlackRock TCP Capital Corp.).

TCPC files its schedule of investments inside the notes with this header:

    Issuer | Instrument | Ref | Floor | Spread | Total Coupon | Maturity |
    Principal | Cost | Fair Value | % of Total Cash and Investments | Notes

Layout notes:
  * Amounts are in WHOLE DOLLARS (13,653,092) and converted to thousands
    to match the standard schema.
  * Industry rows are title case ("Aerospace & Defense") and carried down;
    section rows ("Debt Investments(A)") are skipped.
  * Instrument column carries the type ("First Lien Term Loan",
    "First Lien Delayed Draw Term Loan B", "Common Stock", "Warrants").
  * Spread may pack PIK ("2.75% Cash +2.75% PIK").
  * The prior year-end (December 31, 2025) SOI follows the current one and
    is excluded by stopping at the first repeated "Debt Investments"
    section after the first table.
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


class TCPCScraper(BaseCompanyScraper):
    ticker = "TCPC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Issuer" not in t["text"][:300]:
                continue
            if "Total Coupon" not in t["text"][:800]:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("TCPC: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_industry = ""
        seen_debt_section = False
        rows_finished = False

        for t in soi_tables:
            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[header_idx])
            if "issuer" not in ranges or "instrument" not in ranges:
                continue

            for row in grid[header_idx + 1:]:
                issuer = _cell_range(row, *ranges["issuer"])
                instrument = _cell_range(row, *ranges["instrument"])
                low = issuer.lower()

                if not issuer:
                    continue

                # Total/cash rows must be skipped even when their label
                # spills into the instrument range.
                if low.startswith("total") or low.startswith("subtotal") \
                        or "cash and cash equivalents" in low \
                        or "cash and investments" in low:
                    continue

                # Section rows: "Debt Investments(A)" / "Equity Investments".
                if not instrument:
                    if "debt investments" in low:
                        # The prior year-end block repeats the sections.
                        if seen_debt_section:
                            rows_finished = True
                            break
                        seen_debt_section = True
                        continue
                    if "equity investments" in low or "cash" in low:
                        continue
                    if issuer == issuer.upper() and not any(ch.isdigit() for ch in issuer):
                        continue
                    current_industry = issuer
                    continue

                record = self._row_to_record(row, issuer, instrument,
                                             current_industry, ranges)
                rows.append(record)

            if rows_finished:
                break

        logger.info("TCPC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], issuer: str, instrument: str,
                       industry: str, ranges: Dict[str, tuple]) -> Dict[str, str]:
        def grp(key: str) -> str:
            rng = ranges.get(key)
            if not rng:
                return ""
            return _cell_range(row, *rng)

        inv_type = self._map_type(instrument)
        ref_raw = grp("ref").strip().upper()
        ref_raw = re.sub(r"\s*\((M|Q|T)\)\s*$", "", ref_raw)
        ref_map = {"SOFR": "SOFR", "LIBOR": "LIBOR", "EURIBOR": "Euribor",
                   "SONIA": "SONIA", "FIXED": ""}
        reference = ref_map.get(ref_raw, ref_raw)

        spread_raw = grp("spread")
        spread, pik = self._parse_spread_pik(spread_raw)

        principal = self._scale(grp("principal"))
        cost = self._scale(grp("cost"))
        fair = self._scale(grp("fairvalue"))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", principal
        else:
            shares = ""

        return {
            "company_name": issuer,
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": self._parse_percent_cell(grp("totalcoupon")),
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": self._parse_percent_cell(grp("floor")),
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
            if any(_norm_header(c) == "issuer" for c in row):
                return i
        return None

    @staticmethod
    def _column_ranges(row: List[str]) -> Dict[str, tuple]:
        keys = ["issuer", "instrument", "ref", "floor", "spread",
                "totalcoupon", "maturity", "principal", "cost",
                "fairvalue"]
        pos: Dict[str, int] = {}
        for i, c in enumerate(row):
            n = _norm_header(c)
            if n and n not in pos:
                pos[n] = i
        ordered = sorted((pos[k], k) for k in keys if k in pos)
        ranges: Dict[str, tuple] = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _map_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s or "term loan" in s or "delayed draw" in s \
                or "senior" in s:
            return "First Lien"
        if "revolver" in s or "revolving" in s:
            return "Revolver"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s or "note" in s:
            return "Unsecured Debt"
        if "common stock" in s or "common equity" in s:
            return "Common Equity"
        if "preferred" in s:
            return "Preferred Equity"
        if "warrant" in s:
            return "Warrants"
        if "equity" in s or "unit" in s or "membership" in s:
            return "Other Equity"
        return "Other"

    @staticmethod
    def _parse_spread_pik(s: str) -> tuple:
        if not s:
            return "", ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*Cash\s*\+\s*(\d+(?:\.\d+)?)\s*%\s*PIK", s, re.IGNORECASE)
        if m:
            return m.group(1) + "%", m.group(2) + "%"
        m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", s, re.IGNORECASE)
        if m:
            return "", m.group(1) + "%"
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
        if m:
            return m.group(1) + "%", ""
        return "", ""

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

    @staticmethod
    def _scale(value: str) -> str:
        v = (value or "").replace(",", "").strip()
        if not v or v in ("-", ""):
            return ""
        try:
            num = float(v) / 1000.0
        except ValueError:
            return ""
        if num == int(num):
            return str(int(num))
        return f"{num:.3f}".rstrip("0").rstrip(".")
