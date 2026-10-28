"""
Custom scraper for WHF (WhiteHorse Finance, Inc.).

The schedule of investments lists one line per instrument:

    Issuer | Investment Type | Floor | Reference Rate | Spread Above Index |
    Interest Rate | Acquisition Date | Maturity Date | Principal/Share Amount |
    Amortized Cost | Fair Value | % of Net Assets

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Section rows ("Debt Investments") set the coarse type; industry rows
    ("Advertising") are carried down to the instruments beneath them.
  * Multi-tranche companies render the amounts on a *values-only* row whose
    issuer cell is blank; those rows are render artefacts of the previous
    instrument and are skipped (the instrument row already carries its own
    amounts).
  * The interest-rate cell can pack cash and PIK, e.g.
    "9.64% (9.14% Cash +0.50% PIK)" or "12.38% PIK".
  * Dates are MM/DD/YY.
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
    s = (label or "").replace("\u200b", " ")
    s = re.sub(r"\s*\([a-zA-Z0-9]+\)\s*", "", s)
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9/%$]", "", s)


def _clean(value: str) -> str:
    return (value or "").replace("\u200b", "").strip()


def _cell_range(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = _clean(row[i])
        if v and v not in ("$", "€", "%", "£", "—", "-"):
            return v
    return ""


class WHFScraper(BaseCompanyScraper):
    ticker = "WHF"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid or len(grid[0]) < 20:
                continue
            head = " ".join(_norm_header(c) for r in grid[:3] for c in r)
            if "issuer" not in head:
                continue
            if "fairvalue" not in head:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("WHF: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_industry = ""
        current_section = ""
        seen_issuers = set()

        current_month = self._month_before(filing_result, soi_tables[0]) \
            if soi_tables else ""

        for t in soi_tables:
            month = self._month_before(filing_result, t)
            if current_month and month and month != current_month:
                # Prior year-end copy of the schedule.
                continue
            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[header_idx])

            # A table whose first issuer was already captured belongs to the
            # repeated (prior year-end) copy of the schedule.
            first_issuer = ""
            for row in grid[header_idx + 1:]:
                cand = _cell_range(row, *ranges.get("issuer", (0, 2)))
                if cand and not cand.lower().startswith("total"):
                    first_issuer = cand
                    break
            if first_issuer and first_issuer in seen_issuers:
                continue

            for row in grid[header_idx + 1:]:
                issuer = _cell_range(row, *ranges.get("issuer", (0, 2)))
                if not issuer:
                    # Values-only artefact rows carry amounts but no issuer.
                    continue
                low = issuer.lower()
                if low.startswith("total investments"):
                    # End of the current schedule; later copies are repeats.
                    logger.info("WHF: parsed %d investment rows", len(rows))
                    return rows
                if low.startswith("total"):
                    continue

                inv_type = _cell_range(row, *ranges["type"]) \
                    if "type" in ranges else ""
                fair_raw = _cell_range(row, *ranges["fairvalue"]) \
                    if "fairvalue" in ranges else ""
                principal = _cell_range(row, *ranges["principal"]) \
                    if "principal" in ranges else ""

                if not inv_type and not fair_raw and not principal:
                    # Section header ("Debt Investments") or industry row.
                    if low in ("debt investments", "equity investments",
                               "investment funds", "structured products"):
                        current_section = issuer
                    else:
                        current_industry = issuer
                    continue

                seen_issuers.add(issuer)

                rows.append(self._row_to_record(row, issuer, inv_type,
                                                current_industry, ranges))

        # The notes repeat the affiliate/controlled schedules; collapse rows
        # that describe the same position twice.
        unique: List[Dict[str, str]] = []
        seen = set()
        for r in rows:
            key = (r["company_name"], r["investment_type"],
                   r["principal_amount"], r["amortized_cost"], r["fair_value"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(r)

        logger.info("WHF: parsed %d investment rows (%d unique)",
                    len(rows), len(unique))
        return unique

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _row_to_record(self, row: List[str], issuer: str, inv_type_raw: str,
                       industry: str, ranges: Dict[str, tuple]) -> Dict[str, str]:
        def grp(key: str) -> str:
            rng = ranges.get(key)
            return _cell_range(row, *rng) if rng else ""

        inv_type = self._map_type(inv_type_raw)

        reference_raw = grp("referencerate")
        reference = ""
        m = re.search(r"(SOFR|LIBOR|PRIME|BSBY|SONIA|EURIBOR)", reference_raw,
                      re.IGNORECASE)
        if m:
            reference = {"SOFR": "SOFR", "LIBOR": "LIBOR", "PRIME": "Prime",
                         "BSBY": "BSBY", "SONIA": "SONIA",
                         "EURIBOR": "Euribor"}[m.group(1).upper()]

        spread = self._pct(grp("spread"))
        floor = self._pct(grp("floor"))
        cash, pik = self._parse_interest(grp("interest"))

        principal = self._clean_number(grp("principal"))
        cost = self._clean_number(grp("amortizedcost"))
        fair = self._clean_number(grp("fairvalue"))

        if inv_type in _EQUITY_TYPES:
            principal, shares = "", principal
        else:
            shares = ""

        pct = self._pct(grp("percent"))

        return {
            "company_name": self._clean_issuer(issuer),
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": cash,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": floor,
            "acquisition_date": self._parse_date(grp("acquisition")),
            "maturity_date": self._parse_date(grp("maturity")),
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
    def _header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid[:4]):
            joined = " ".join(_norm_header(c) for c in row)
            if "issuer" in joined and "fairvalue" in joined:
                return i
        return None

    @staticmethod
    def _column_ranges(row: List[str]) -> Dict[str, tuple]:
        keys = ["issuer", "type", "floor", "referencerate", "spread",
                "interest", "acquisition", "maturity", "principal",
                "amortizedcost", "fairvalue", "percent"]
        pos: Dict[str, int] = {}
        for i, c in enumerate(row):
            n = _norm_header(c)
            if not n or i in pos.values():
                continue
            if n.startswith("issuer"):
                pos.setdefault("issuer", i)
            elif n.startswith("investmenttype"):
                pos.setdefault("type", i)
            elif n.startswith("floor"):
                pos.setdefault("floor", i)
            elif n.startswith("referencerate"):
                pos.setdefault("referencerate", i)
            elif n.startswith("spread"):
                pos.setdefault("spread", i)
            elif n.startswith("interestrate"):
                pos.setdefault("interest", i)
            elif n.startswith("acquisition"):
                pos.setdefault("acquisition", i)
            elif n.startswith("maturity"):
                pos.setdefault("maturity", i)
            elif n.startswith("principal"):
                pos.setdefault("principal", i)
            elif n.startswith("amortizedcost"):
                pos.setdefault("amortizedcost", i)
            elif n.startswith("fairvalue"):
                pos.setdefault("fairvalue", i)
            elif "netassets" in n:
                pos.setdefault("percent", i)
        ordered = sorted((v, k) for k, v in pos.items())
        ranges: Dict[str, tuple] = {}
        for idx, (start, key) in enumerate(ordered):
            end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(row)
            ranges[key] = (start, end)
        return ranges

    @staticmethod
    def _map_type(raw: str) -> str:
        s = (raw or "").lower()
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s:
            return "First Lien"
        if "revolv" in s:
            return "Revolver"
        if "delay" in s:
            return "Revolver"
        if "subordinat" in s:
            return "Subordinated Debt"
        if "unsecured" in s or "note" in s:
            return "Unsecured Debt"
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s:
            return "Common Equity"
        if "equity" in s or "unit" in s or "interest" in s:
            return "Other Equity"
        return "Other"

    @staticmethod
    def _clean_issuer(value: str) -> str:
        s = _clean(value)
        s = re.sub(r"(\s*\([^)]*\))+$", "", s)
        return s.strip().strip(",").strip()

    @staticmethod
    def _pct(value: str) -> str:
        v = _clean(value)
        m = re.search(r"(\d+(?:\.\d+)?)\s*%?", v)
        return m.group(1) + "%" if m else ""

    @staticmethod
    def _parse_interest(value: str) -> tuple:
        """Return (cash, pik) from the interest-rate cell."""
        s = _clean(value)
        if not s:
            return "", ""
        pik = ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", s, re.IGNORECASE)
        if m:
            pik = m.group(1) + "%"
        cash = ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*Cash", s, re.IGNORECASE)
        if m:
            cash = m.group(1) + "%"
        elif not pik:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
            if m:
                cash = m.group(1) + "%"
        elif "cash" not in s.lower():
            # e.g. "12.38% PIK" -> no cash component
            pass
        return cash, pik

    @staticmethod
    def _parse_date(value: str) -> str:
        v = _clean(value)
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", v)
        if not m:
            return ""
        year = m.group(3)
        if len(year) == 2:
            year = "20" + year
        return f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    @staticmethod
    def _month_before(filing_result: Any, table: Dict[str, Any]) -> str:
        html = (filing_result.text_map or {}).get(table.get("doc") or "", "")
        if not html:
            return ""
        pos = table.get("pos") or 0
        raw = re.sub(r"<[^>]+>", " ", html[max(0, pos - 9000):pos])
        raw = re.sub(r"&#160;|&nbsp;|\u200b", " ", raw)
        raw = re.sub(r"\s+", " ", raw)
        ms = re.findall(r"\b(January|February|March|April|May|June|July|August|"
                        r"September|October|November|December)\b", raw)
        return ms[-1] if ms else ""
