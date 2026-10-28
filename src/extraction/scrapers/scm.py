"""
Custom scraper for SCM (Stellus Capital Investment Corp).

The schedule of investments puts each portfolio company on its own line and
its instruments on the following lines:

    Cerebro Buyer, LLC                         Columbia, SC
    Term Loan (SBIC)  (4)(11)  First Lien  3M SOFR+ 6.00% 1.00%  9.85% 9/1/2021 9/1/2026
                               Media: ...  $ 13,152,991  13,137,594  13,087,227  3.57%

Column map (32 columns):

    investments  0      footnotes    2      security(type)  4
    coupon ref   6      coupon %     7      floor %         9
    cash %      11      pik %       13      acquired       15
    maturity    17      industry    19      principal      22
    cost        25      fair value  28      % of assets    30

Layout notes:
  * Amounts are in whole dollars and are converted to thousands.
  * Company rows carry the city in the industry column and no amounts; the
    industry label appears on the instrument rows, so it is captured there.
  * Per-company "Total" rows are skipped.
  * The prior year-end copy of the schedule follows the current one; a
    repeated company name ends parsing.
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
    s = s.replace("\u200b", " ").lower().strip()
    return re.sub(r"[^a-z0-9/%$]", "", s)


def _cell(row: List[str], idx: int) -> str:
    if idx >= len(row):
        return ""
    v = (row[idx] or "").strip().replace("\u200b", "").strip()
    if v in ("$", "%", "—", "-"):
        return ""
    return v


def _any(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip().replace("\u200b", "").strip()
        if v and v not in ("$", "%", "—", "-"):
            return v
    return ""


class SCMScraper(BaseCompanyScraper):
    ticker = "SCM"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid or len(grid[0]) < 25:
                continue
            head = " ".join((c or "") for r in grid[:4] for c in r).lower()
            if "maturity" not in head:
                continue
            if "coupon" not in head and "interest rate" not in head:
                continue
            if "value" not in head:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("SCM: found %d SOI tables", len(soi))
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
        first_company = ""
        table_index = -1
        done = False

        for t in soi_tables:
            if done:
                break
            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._header_row(grid)
            if header_idx is None:
                continue
            table_index += 1

            table_first = self._first_company(grid, header_idx)
            if not first_company and table_first:
                first_company = table_first
            elif table_index > 0 and table_first and table_first == first_company:
                # Prior year-end copy of the schedule starts here.
                break

            for row in grid[header_idx + 1:]:
                row_text = " ".join(c for c in row if c).lower()
                row_flat = re.sub(r"[^a-z]", "", row_text)
                if "totalinvestments" in row_flat \
                        or "totalportfolioinvestments" in row_flat:
                    done = True
                    break
                name = _cell(row, 0)
                if not name:
                    continue
                low = name.lower()
                if low.startswith("total investments") \
                        or low.startswith("total portfolio investments"):
                    # End of the current schedule; later tables are the
                    # prior-year copy or notes.
                    done = True
                    break
                if low.startswith("total") or low.startswith("subtotal") \
                        or low.startswith("net asset") \
                        or low.startswith("liabilities"):
                    continue

                fair = _any(row, 27, 30)
                cost = _any(row, 24, 27)
                has_amounts = bool(fair or cost)

                if not has_amounts:
                    # Portfolio company line (city sits in the industry col).
                    current_company = name
                    continue

                if not current_company:
                    continue

                record = self._row_to_record(row, current_company, name)
                if record["industry"]:
                    current_industry = record["industry"]
                elif current_industry:
                    record["industry"] = current_industry
                rows.append(record)
            if done:
                break

        # Guard against partially-parsed older filings (different layouts).
        total = 0.0
        for r in rows:
            try:
                total += float(r.get("fair_value") or 0)
            except ValueError:
                pass
        if rows and total < 200000:  # SCM never ran below ~$500M
            logger.warning("SCM: implausible total %.0f (thousand) - layout "
                           "not supported; skipping filing", total)
            return []

        logger.info("SCM: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], company: str,
                       instrument: str) -> Dict[str, str]:
        type_raw = _any(row, 4, 6)
        inv_type = self._map_type(type_raw, instrument)

        reference = ""
        spread = ""
        coupon_ref = _any(row, 6, 7)
        if coupon_ref:
            m = re.search(r"(SOFR|LIBOR|PRIME|BSBY|SONIA|EURIBOR)", coupon_ref,
                          re.IGNORECASE)
            if m:
                reference = {"SOFR": "SOFR", "LIBOR": "LIBOR", "PRIME": "Prime",
                             "BSBY": "BSBY", "SONIA": "SONIA",
                             "EURIBOR": "Euribor"}[m.group(1).upper()]
                spread = _any(row, 7, 9)
                if spread:
                    spread = spread.rstrip("%") + "%"

        floor = _any(row, 9, 11)
        if floor:
            floor = floor.rstrip("%") + "%"
        cash = _any(row, 11, 13)
        if cash:
            cash = cash.rstrip("%") + "%"
        pik = _any(row, 13, 15)
        if pik:
            pik = pik.rstrip("%") + "%"

        principal = self._clean_number(self._scale(_any(row, 21, 23)))
        cost = self._clean_number(self._scale(_any(row, 24, 27)))
        fair = self._clean_number(self._scale(_any(row, 27, 30)))

        pct = _any(row, 30, 32)
        percent = pct.rstrip("%") + "%" if pct else ""

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", principal
        else:
            shares = ""

        return {
            "company_name": company,
            "investment_type": inv_type,
            "industry": _any(row, 19, 21),
            "cash_rate": cash,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": floor,
            "acquisition_date": self._parse_date(_any(row, 15, 17)),
            "maturity_date": self._parse_date(_any(row, 17, 19)),
            "principal_amount": principal,
            "amortized_cost": cost,
            "fair_value": fair,
            "percent_of_net_assets": percent,
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
    def _header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid[:6]):
            joined = " ".join((c or "") for c in row).lower()
            if "coupon" in joined and "maturity" in joined:
                return i
        return None

    @staticmethod
    def _map_type(type_raw: str, instrument: str) -> str:
        s = (type_raw or "").lower()
        i = (instrument or "").lower()
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s:
            return "First Lien"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "equity" in s:
            if "warrant" in i or "warrant" in s:
                return "Warrants"
            if "preferred" in i:
                return "Preferred Equity"
            if "common" in i or "units" in i or "interests" in i:
                return "Common Equity"
            return "Other Equity"
        if "warrant" in i:
            return "Warrants"
        if "revolver" in s or "revolver" in i:
            return "Revolver"
        return "Other"

    @staticmethod
    def _first_company(grid: List[List[str]], header_idx: int) -> str:
        for row in grid[header_idx + 1:]:
            name = _cell(row, 0)
            if not name:
                continue
            low = name.lower()
            if low.startswith(("total", "subtotal", "net asset", "liabilities")):
                continue
            if _any(row, 24, 27) or _any(row, 27, 30):
                continue
            return name
        return ""

    @staticmethod
    def _parse_date(value: str) -> str:
        v = (value or "").strip()
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", v)
        if m:
            return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        return ""

    @staticmethod
    def _scale(value: str) -> str:
        v = (value or "").replace(",", "").replace("$", "").strip()
        if not v or v in ("-", ""):
            return ""
        try:
            return str(int(round(float(v) / 1000.0)))
        except ValueError:
            return ""
