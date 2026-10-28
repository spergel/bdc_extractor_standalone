"""
Custom scraper for FDUS (Fidus Investment Corporation).

FDUS files its schedule of investments in a compact two-line-per-position
layout.  The header splits across two rows:

    Portfolio Company (a)(b) / Investment Type (c) | Industry |
    Variable Index Rate (e) Spread / Floor (d) | Cash/PIK |
    Investment Date (f) | Maturity | Principal Amount | Cost | Fair Value |
    Percent of Net Assets

Rows:
  * Section rows: "Control Investments (t)", "Affiliate Investments (l)",
    "Non-Control/Non-Affiliate Investments…" -> skipped.
  * Company rows: company in col 0, industry in col 1.
  * Instrument rows: instrument in col 0 ("First Lien Debt (am)",
    "Common Equity (2,522units)", "Warrant (…)", "Preferred Equity (…)")
    with rates, dates, principal, cost and fair value in the columns
    to the right.
  * Company subtotal rows (only cost/fair/percent) -> skipped because the
    company column is empty.

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Shares/units appear inside the instrument text: "Common Equity (2,522units)".
  * Spread cell packs reference and floor: "(S+8.50%) / (4.00%)".
  * Cash/PIK cell: "12.50%/0.00%" (a zero PIK is stored as blank).
  * The filing contains a second copy of the schedule for the prior
    year-end; only the first SOI table is parsed.
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


class FDUSScraper(BaseCompanyScraper):
    ticker = "FDUS"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Portfolio Company" not in t["text"][:300]:
                continue
            if "Investment Type" not in t["text"][:900]:
                continue
            if "Fair" not in t["text"][:900]:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("FDUS: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        if not soi_tables:
            return []

        # The current schedule is split across the header table and the
        # following headerless continuation tables, up to the prior-year copy.
        first_pos = soi_tables[0].get("pos") or 0
        prior_pos = soi_tables[1].get("pos") if len(soi_tables) > 1 else None
        candidates = [
            t for t in tables
            if (t.get("pos") or 0) >= first_pos
            and (prior_pos is None or (t.get("pos") or 0) < prior_pos)
        ]
        candidates.sort(key=lambda t: t.get("pos") or 0)

        rows: List[Dict[str, str]] = []
        current_company = ""
        current_industry = ""

        for t in candidates:
            rows.extend(self._parse_table(t, current_company, current_industry))
            if rows:
                current_company = rows[-1]["company_name"] or current_company
                current_industry = rows[-1]["industry"] or current_industry

        logger.info("FDUS: parsed %d investment rows", len(rows))
        return rows

    def _parse_table(self, t: Dict[str, Any], current_company: str,
                     current_industry: str) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        grid = self.parse_table_to_rows(t["html"])
        for row in grid:
            company = _cell_range(row, 0, 2)
            industry = _cell_range(row, 1, 2)
            low = company.lower()

            if not company:
                continue
            if low.startswith("total") or low.startswith("subtotal"):
                continue
            if "investments" in low and ("control" in low or "affiliate" in low):
                continue
            if low.startswith("portfolio company") or low.startswith("investment type"):
                continue
            # Footnote / prose tables.
            if re.match(r"^\([a-z]\)", company) or "see note" in low:
                continue

            # Company row: company + industry, no financial values.
            fair = _cell_range(row, 14, 17)
            cost = _cell_range(row, 10, 14)
            principal = _cell_range(row, 6, 10)
            if industry and not fair and not cost and not principal:
                current_company = re.sub(r"\s*\([a-z]\)\s*$", "", company).strip()
                current_industry = industry
                continue

            # Subtotal row: company cell empty handled above; rows with only
            # cost/fair and no instrument keyword are skipped.
            inv_type = self._map_type(company)
            if not inv_type:
                continue

            rows.append(self._row_to_record(row, current_company,
                                            current_industry, company, inv_type))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, industry: str,
                       instrument: str, inv_type: str) -> Dict[str, str]:
        spread_raw = _cell_range(row, 2, 3)
        reference, spread, floor = self._parse_spread_floor(spread_raw)
        cash, pik = self._parse_cash_pik(_cell_range(row, 3, 4))

        principal = self._clean_number(_cell_range(row, 6, 10))
        cost = self._clean_number(_cell_range(row, 10, 14))
        fair = self._clean_number(_cell_range(row, 14, 17))
        pct = self._parse_percent_cell(_cell_range(row, 17, 20))

        shares = self._parse_units(instrument)
        if inv_type in _EQUITY_TYPES:
            principal, shares = "", shares
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
            "acquisition_date": self._parse_date(_cell_range(row, 4, 5)),
            "maturity_date": self._parse_date(_cell_range(row, 5, 6)),
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
    def _map_type(instrument: str) -> str:
        s = (instrument or "").lower()
        if "first lien" in s:
            return "First Lien"
        if "second lien" in s:
            return "Second Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common equity" in s or "common stock" in s:
            return "Common Equity"
        if "unit" in s or "equity" in s or "partnership" in s:
            return "Other Equity"
        return ""

    @staticmethod
    def _parse_spread_floor(value: str) -> tuple:
        if not value:
            return "", "", ""
        ref_map = {"S": "SOFR", "SOFR": "SOFR", "L": "LIBOR", "P": "Prime",
                   "E": "Euribor"}
        reference = ""
        spread = ""
        floor = ""
        m = re.search(r"\(\s*([A-Za-z]+)\s*\+\s*(\d+(?:\.\d+)?)\s*%\s*\)", value)
        if m:
            reference = ref_map.get(m.group(1).upper(), m.group(1).upper())
            spread = m.group(2) + "%"
        fm = re.search(r"/\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)", value)
        if fm:
            floor = fm.group(1) + "%"
        else:
            fm2 = re.search(r"floor[^0-9]*(\d+(?:\.\d+)?)\s*%", value, re.IGNORECASE)
            if fm2:
                floor = fm2.group(1) + "%"
        return reference, spread, floor

    @staticmethod
    def _parse_cash_pik(value: str) -> tuple:
        if not value:
            return "", ""
        parts = re.findall(r"(\d+(?:\.\d+)?)\s*%", value)
        cash = parts[0] + "%" if parts else ""
        pik = ""
        if len(parts) >= 2 and float(parts[1]) > 0:
            pik = parts[1] + "%"
        return cash, pik

    @staticmethod
    def _parse_units(instrument: str) -> str:
        m = re.search(r"\(\s*([\d,]+)\s*units?\s*\)", instrument or "", re.IGNORECASE)
        if not m:
            return ""
        return m.group(1).replace(",", "")

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
        return ""
