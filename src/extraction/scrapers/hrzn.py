"""
Custom scraper for HRZN (Horizon Technology Finance Corp).

HRZN files two SOI layouts that share the same company/sector/type prefix:

    Debt:
      Portfolio Company | Sector | Type of Investment | Cash Rate | Index |
      Margin | Floor | Ceiling | ETP | Maturity Date | Principal Amount |
      Cost of Investments | Fair Value
    Equity:
      Portfolio Company | Sector | Type of Investment | Number of Shares |
      Cost of Investments | Fair Value

Because the header is split across two rows ("Cost of" / "Investments",
"Fair" / "Value"), each table's header is built by joining the labels found
in the first rows of the table before normalizing.

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Section rows ("Non-Affiliate Debt Investments - Life Science -73.2%")
    are skipped; the type comes from the per-row instrument
    ("Term Loan", "Revolver", "Warrant", "Preferred Stock").
  * Continuation rows omit the company and sector; the previous values are
    reused.
  * Maturity dates are written out ("March 1, 2028").
  * When the "Non-Affiliate Investments" section appears a second time the
    prior year-end schedule begins and parsing stops.
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
    "portfoliocompany", "sector", "typeofinvestment", "cashrate", "index",
    "margin", "floor", "ceiling", "etp", "maturitydate", "principalamount",
    "numberofshares", "costofinvestments", "fairvalue", "cost", "fair",
]

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
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


class HRZNScraper(BaseCompanyScraper):
    ticker = "HRZN"
    dedupe_rows = False  # identical tranches repeat legitimately

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid:
                continue
            head = " ".join((c or "") for r in grid[:3] for c in r).lower()
            if "portfolio company" not in head:
                continue
            if "fair" not in head:
                continue
            if "cost" not in head:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("HRZN: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_company = ""
        current_sector = ""
        current_instrument = "Term Loan"
        seen_main_section = False
        equity_seen = set()

        for t in soi_tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid:
                continue
            ranges, header_rows = self._merged_ranges(grid)
            if "portfoliocompany" not in ranges or "fairvalue" not in ranges:
                continue

            for row in grid[header_rows:]:
                company = _cell_range(row, *ranges["portfoliocompany"])
                sector = _cell_range(row, *ranges["sector"]) \
                    if "sector" in ranges else ""
                instrument = _cell_range(row, *ranges["typeofinvestment"]) \
                    if "typeofinvestment" in ranges else ""
                low = company.lower()
                amounts = self._flow_amounts(row)
                has_amounts = len(amounts) >= 2

                if "non-affiliate investments" in low or \
                        low.startswith("affiliate investment"):
                    if seen_main_section:
                        logger.info("HRZN: parsed %d investment rows", len(rows))
                        return rows
                    seen_main_section = True
                    continue
                if low.startswith("total") or low.startswith("subtotal"):
                    continue

                if has_amounts and (company or current_company):
                    record = self._row_to_record(
                        row,
                        company or current_company,
                        sector or current_sector,
                        instrument or current_instrument,
                        ranges)
                    if len(grid[0]) < 30:
                        # Equity/warrant tables repeat rows across pages.
                        key = (record["company_name"], record["investment_type"],
                               record["shares"], record["cost"],
                               record["fair_value"])
                        if key in equity_seen:
                            continue
                        equity_seen.add(key)
                    rows.append(record)
                    if company:
                        current_company = company
                    if sector:
                        current_sector = sector
                    if instrument:
                        current_instrument = instrument
                    continue

                # Header / section rows.
                if instrument:
                    current_instrument = instrument
                if company and not instrument:
                    continue

        logger.info("HRZN: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str, sector: str,
                       instrument: str, ranges: Dict[str, tuple]) -> Dict[str, str]:
        def grp(key: str) -> str:
            rng = ranges.get(key)
            if not rng:
                return ""
            return _cell_range(row, *rng)

        inv_type = self._map_type(instrument)

        cash = self._parse_percent(grp("cashrate"))
        index_raw = grp("index").strip()
        ref_map = {"PRIME": "Prime", "SOFR": "SOFR", "LIBOR": "LIBOR",
                   "PRIME RATE": "Prime", "3-MONTH SOFR": "SOFR"}
        reference = ref_map.get(index_raw.upper(), index_raw)
        margin = self._parse_percent(grp("margin"))
        floor = self._parse_percent(grp("floor"))

        amounts = self._flow_amounts(row)
        if inv_type in _EQUITY_TYPES:
            shares = amounts[0] if amounts else ""
            principal = ""
            if len(amounts) >= 3:
                cost, fair = amounts[-2], amounts[-1]
            elif len(amounts) == 2:
                cost, fair = amounts[0], amounts[1]
                shares = ""
            else:
                cost = fair = ""
        else:
            shares = ""
            if len(amounts) >= 3:
                principal, cost, fair = amounts[-3], amounts[-2], amounts[-1]
            elif len(amounts) == 2:
                principal, cost, fair = "", amounts[0], amounts[1]
            elif len(amounts) == 1:
                principal, cost, fair = amounts[0], "", ""
            else:
                principal = cost = fair = ""

        return {
            "company_name": re.sub(r"\s*(\(\d+\))+\s*$", "", company).strip(),
            "investment_type": inv_type,
            "industry": sector,
            "cash_rate": cash,
            "pik_rate": "",
            "reference_rate": reference,
            "spread": margin,
            "floor_rate": floor,
            "acquisition_date": "",
            "maturity_date": self._parse_date(grp("maturitydate")),
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
    @classmethod
    def _merged_ranges(cls, grid: List[List[str]]):
        """Return (column ranges, header rows) for either SOI layout.

        Debt tables are 39 columns wide; equity tables are 17.  Both start
        with Portfolio Company | Sector | Type of Investment but the value
        columns sit at different offsets.
        """
        width = max(len(r) for r in grid[:3])
        header_rows = 1
        for i, r in enumerate(grid[:3]):
            if "portfolio company" in " ".join((c or "") for c in r).lower():
                header_rows = i + 1

        if width >= 30:
            ranges = {
                "portfoliocompany": (0, 2),
                "sector": (2, 4),
                "typeofinvestment": (4, 6),
                "cashrate": (6, 9),
                "index": (9, 11),
                "margin": (11, 15),
                "floor": (15, 19),
                "ceiling": (19, 23),
                "etp": (23, 26),
                "maturitydate": (26, 28),
                "principalamount": (28, 32),
                "costofinvestments": (32, 36),
                "fairvalue": (36, 39),
            }
        elif width <= 18:
            ranges = {
                "portfoliocompany": (0, 2),
                "sector": (2, 4),
                "typeofinvestment": (4, 6),
                "numberofshares": (6, 10),
                "costofinvestments": (10, 14),
                "fairvalue": (14, width),
            }
        else:
            # Unusual widths (affiliate tables): read the merged labels.
            merged = [""] * width
            for r in grid[:header_rows]:
                for i, c in enumerate(r):
                    if (c or "").strip() and i < width:
                        merged[i] = (merged[i] + " " + c.strip()).strip()
            labels = [_norm_header(c) for c in merged]
            pos: Dict[str, int] = {}
            for i, n in enumerate(labels):
                if not n or i in pos.values():
                    continue
                for key in _COLUMN_KEYS:
                    if key in n:
                        pos.setdefault(key, i)
                        break
            if "costofinvestments" in pos:
                pos.pop("cost", None)
            ordered = sorted((v, k) for k, v in pos.items())
            ranges = {}
            for idx, (start_i, key) in enumerate(ordered):
                end_i = ordered[idx + 1][0] if idx + 1 < len(ordered) else width
                ranges[key] = (start_i, end_i)
            if "portfoliocompany" not in ranges:
                ranges["portfoliocompany"] = (0, 2)
            if "fairvalue" not in ranges:
                ranges["fairvalue"] = (width - 3, width)
        return ranges, header_rows

    def _flow_amounts(self, row: List[str]) -> List[str]:
        """Numeric cells in order (percentages and dates excluded).

        Colspan differences across HRZN's tables make fixed columns
        unreliable, but the amounts always trail the row: (principal,) cost,
        fair value - or (shares,) cost, fair value for equity rows.
        """
        out: List[str] = []
        for cell in row:
            v = (cell or "").strip()
            if not v or "%" in v:
                continue
            if re.fullmatch(r"\(?[\d,]{1,15}\)?", v) and any(ch.isdigit() for ch in v):
                neg = v.startswith("(")
                num = v.strip("()").replace(",", "")
                if neg:
                    num = "-" + num
                out.append(num)
        return out

    @staticmethod
    def _map_type(instrument: str) -> str:
        s = (instrument or "").lower()
        if "term loan" in s or "first lien" in s:
            return "First Lien"
        if "second lien" in s:
            return "Second Lien"
        if "revolver" in s or "line of credit" in s:
            return "Revolver"
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s:
            return "Common Equity"
        if "equity" in s or "unit" in s:
            return "Other Equity"
        return "Other"

    @staticmethod
    def _parse_percent(value: str) -> str:
        if not value:
            return ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", value)
        if m:
            return m.group(1) + "%"
        if re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
            return value.strip() + "%"
        return ""

    @staticmethod
    def _parse_date(value: str) -> str:
        v = (value or "").strip()
        m = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", v)
        if m:
            month = _MONTHS.get(m.group(1).lower())
            if month:
                return f"{m.group(3)}-{month:02d}-{int(m.group(2)):02d}"
        return ""
