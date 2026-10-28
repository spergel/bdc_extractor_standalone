"""
Custom scraper for PSEC (Prospect Capital Corporation).

PSEC files its consolidated schedule of investments with this header layout:

    Portfolio Company | Industry | Investments | Acquisition Date |
    All-in Rate | Ref. Rate | Spread | PIK | Floor | Legal Maturity |
    Shares/Units | Commitment | Principal Value | Amortized Cost |
    Fair Value | % of Net Assets

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Rows come in three shapes:
      - Standard debt rows carry company + industry + instrument.
      - Continuation tranche rows shift left by one column group (the
        instrument lands in the company column and the next cells hold
        dates/rates).
      - Equity rows ("Class P Units", "Common Stock", "Warrants") shift
        differently and hold shares/units.
    The parser detects the shape per row and maps columns accordingly.
  * Company names carry footnote refs ("Belnick, LLC (...) (41)") which are
    stripped from the end.
  * Reference "3M SOFR" -> SOFR.  All-in Rate -> cash rate.
  * Section rows, industry rows and totals are skipped; the industry is
    carried down from industry rows.
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

_EQUITY_KEYWORDS = ("units", "common stock", "preferred", "warrant",
                    "equity", "llc interest", "membership", "series")

_DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")


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


class PSECScraper(BaseCompanyScraper):
    ticker = "PSEC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Portfolio" not in t["text"][:500]:
                continue
            if "All-in Rate" not in t["text"][:2000]:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("PSEC: found %d SOI tables", len(soi))
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

        for t in soi_tables:
            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            rows_finished = False
            pending: Optional[Dict[str, str]] = None

            for row in grid[header_idx + 1:]:
                comp = _cell_range(row, 0, 3)
                ind = _cell_range(row, 3, 6)
                inv = _cell_range(row, 6, 9)
                low = comp.lower()

                if not comp:
                    # Non-control tables split each position into a terms
                    # row and a separate values row (principal/cost/fair).
                    vals = (_cell_range(row, 36, 39) + _cell_range(row, 39, 42)
                            + _cell_range(row, 42, 45))
                    if pending and vals:
                        pending["principal_amount"] = self._clean_number(
                            _cell_range(row, 36, 39)) or pending["principal_amount"]
                        pending["amortized_cost"] = self._clean_number(
                            _cell_range(row, 39, 42)) or pending["amortized_cost"]
                        pending["cost"] = pending["amortized_cost"]
                        pending["fair_value"] = self._clean_number(
                            _cell_range(row, 42, 45)) or pending["fair_value"]
                        pending["percent_of_net_assets"] = self._parse_percent_cell(
                            _cell_range(row, 45, 48)) or pending["percent_of_net_assets"]
                        rows.append(pending)
                        pending = None
                    continue

                # Stop at the grand-total row; skip section/subtotal rows.
                if low.startswith("total"):
                    if low == "total portfolio investments":
                        rows_finished = True
                        break
                    continue

                # ---- section / industry / company header rows ----
                if not ind and not inv:
                    if low.startswith("total") or low.startswith("subtotal"):
                        continue
                    if low.startswith("control investments") or \
                            low.startswith("affiliate investments") or \
                            low.startswith("non-control") or \
                            low.startswith("debt investments") or \
                            low.startswith("equity investments") or \
                            "cash" in low:
                        continue
                    if re.search(r"\(\d+\)\s*$", comp):
                        current_company = self._clean_company(comp)
                    else:
                        current_industry = comp
                    continue

                # ---- shifted continuation/equity rows ----
                if _DATE_RE.match(inv) or not inv:
                    if self._is_equity_instrument(comp):
                        record = self._equity_row(row, current_company,
                                                  current_industry, comp)
                    else:
                        record = self._shifted_debt_row(row, current_company,
                                                        current_industry, comp)
                    if record:
                        if record["fair_value"] or record["principal_amount"] \
                                or record["amortized_cost"]:
                            rows.append(record)
                        else:
                            pending = record
                    continue

                # ---- standard row ----
                if ind:
                    current_industry = ind
                if re.search(r"\(\d+\)\s*$", comp) or not self._is_equity_instrument(inv):
                    if re.search(r"\(\d+\)\s*$", comp):
                        current_company = self._clean_company(comp)
                    record = self._standard_row(row, current_company,
                                                current_industry, inv)
                    if record:
                        if record["fair_value"] or record["principal_amount"] \
                                or record["amortized_cost"]:
                            rows.append(record)
                        else:
                            pending = record
                    continue

            if rows_finished:
                break

        logger.info("PSEC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row shapes
    # --------------------------------------------------------------
    def _standard_row(self, row: List[str], company: str, industry: str,
                      inv: str) -> Optional[Dict[str, str]]:
        inv_type = self._map_type(inv)
        return self._build(row, company, industry, inv_type, {
            "acq": (9, 12), "allin": (12, 15), "ref": (15, 18),
            "spread": (18, 21), "pik": (21, 24), "floor": (24, 27),
            "maturity": (27, 30), "shares": (30, 33), "commitment": (33, 36),
            "principal": (36, 39), "cost": (39, 42), "fair": (42, 45),
            "pct": (45, 48),
        })

    def _shifted_debt_row(self, row: List[str], company: str, industry: str,
                          inv: str) -> Optional[Dict[str, str]]:
        inv_type = self._map_type(inv)
        return self._build(row, company, industry, inv_type, {
            "acq": (3, 6), "allin": (6, 9), "ref": (9, 12),
            "spread": (12, 15), "pik": (15, 18), "floor": (18, 21),
            "maturity": (21, 24), "shares": (24, 27), "commitment": (27, 30),
            "principal": (30, 33), "cost": (33, 36), "fair": (36, 39),
            "pct": (39, 42),
        })

    def _equity_row(self, row: List[str], company: str, industry: str,
                    inv: str) -> Optional[Dict[str, str]]:
        inv_type = self._map_type(inv)
        return self._build(row, company, industry, inv_type, {
            "acq": (3, 6), "allin": (6, 9), "ref": (9, 12),
            "spread": (15, 18), "pik": (21, 24), "floor": (18, 21),
            "maturity": (21, 24), "shares": (24, 27), "commitment": (33, 36),
            "principal": (36, 39), "cost": (33, 36), "fair": (36, 39),
            "pct": (39, 42),
        })

    def _build(self, row: List[str], company: str, industry: str,
               inv_type: str, rng: Dict[str, tuple]) -> Optional[Dict[str, str]]:
        if not inv_type:
            return None

        def grp(key: str) -> str:
            r = rng.get(key)
            if not r:
                return ""
            return _cell_range(row, *r)

        ref_raw = grp("ref").strip().upper()
        ref_map = {"3M SOFR": "SOFR", "SOFR": "SOFR", "LIBOR": "LIBOR",
                   "EURIBOR": "Euribor", "SONIA": "SONIA", "FIXED": ""}
        reference = ref_map.get(ref_raw, ref_raw)

        shares = self._clean_number(grp("shares"))
        principal = self._clean_number(grp("principal"))
        if inv_type in _EQUITY_TYPES:
            if not shares and principal:
                shares = principal
                principal = ""
            principal = ""

        return {
            "company_name": company,
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": self._parse_percent_cell(grp("allin")),
            "pik_rate": self._parse_percent_cell(grp("pik")),
            "reference_rate": reference,
            "spread": self._parse_percent_cell(grp("spread")),
            "floor_rate": self._parse_percent_cell(grp("floor")),
            "acquisition_date": self._parse_date(grp("acq")),
            "maturity_date": self._parse_date(grp("maturity")),
            "principal_amount": principal,
            "amortized_cost": self._clean_number(grp("cost")),
            "fair_value": self._clean_number(grp("fair")),
            "percent_of_net_assets": self._parse_percent_cell(grp("pct")),
            "cost": self._clean_number(grp("cost")),
            "commitment_limit": self._clean_number(grp("commitment")),
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
            if any(_norm_header(c).startswith("portfoliocompany") for c in row):
                return i
        return None

    @staticmethod
    def _is_equity_instrument(s: str) -> bool:
        low = (s or "").lower()
        return any(k in low for k in _EQUITY_KEYWORDS)

    @staticmethod
    def _clean_company(value: str) -> str:
        s = re.sub(r"\s*\(\d+\)\s*$", "", (value or "").strip())
        return s.strip()

    @staticmethod
    def _map_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s or "unitranche" in s or "term loan" in s \
                or "delayed draw" in s or "revolver" in s or "revolving" in s:
            return "First Lien"
        if "subordinated" in s or "mezzanine" in s:
            return "Subordinated Debt"
        if "unsecured" in s or "note" in s:
            return "Unsecured Debt"
        if "common stock" in s or "common equity" in s:
            return "Common Equity"
        if "preferred" in s:
            return "Preferred Equity"
        if "warrant" in s:
            return "Warrants"
        if "equity" in s or "units" in s or "membership" in s \
                or "llc interest" in s or "series" in s:
            return "Other Equity"
        return "Other"

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
