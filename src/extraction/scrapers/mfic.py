"""
Custom scraper for MFIC (MidCap Financial Investment Corporation).

MFIC's consolidated schedule uses grouped headers:

    Industry/Company | Investment Type | Interest Rate | Maturity Date |
    Par/Shares | Cost | Fair Value

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * A company is often a standalone header row, followed by tranche rows that
    leave the company cell blank; the last company is carried forward.
  * Industry and section headers span the full row.
  * Negative values are split across cells as "(20" + ")".
  * The prior year-end schedule follows the current one; parsing stops at the
    first different period heading.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_EQUITY_TYPES = {
    "Common Equity", "Preferred Equity", "Other Equity",
    "Partnership Interest", "Warrants",
}

_COLUMN_KEYS = [
    "industrycompany", "investmenttype", "interestrate", "maturitydate",
    "parshares", "cost", "fairvalue",
]


def _norm_header(label: str) -> str:
    s = re.sub(r"\s*\([a-zA-Z0-9]+\)\s*", "", label or "")
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9]", "", s)


def _cell_range(row: List[str], start: int, end: int) -> str:
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip()
        if v and v not in ("$", "€", "%", "£", "—", "-", "n/a", "N/A"):
            return v
    return ""


def _money(row: List[str], start: int, end: int) -> str:
    """Numeric cell, handling accounting parentheses split across cells."""
    for i in range(max(0, start), min(end, len(row))):
        v = (row[i] or "").strip()
        if not v or v in ("$", "€", "%", "£", "—", "-", "n/a", "N/A"):
            continue
        s = v.replace(",", "").replace("$", "").strip()
        if s in ("(", ")"):
            continue
        if s.startswith("(") and not s.endswith(")"):
            # The closing paren is usually the next non-empty cell.
            if any((row[j] or "").strip() == ")" for j in range(i + 1, min(end + 2, len(row)))):
                s = s.lstrip("(").strip()
                m = re.search(r"\d+(?:\.\d+)?", s)
                return "-" + m.group(0) if m else ""
        if s.startswith("(") and s.endswith(")"):
            m = re.search(r"\d+(?:\.\d+)?", s)
            return "-" + m.group(0) if m else ""
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if m:
            return m.group(0)
    return ""


class MFICScraper(BaseCompanyScraper):
    ticker = "MFIC"

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
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("MFIC: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        if not soi_tables:
            return []

        first_period = next(
            (p for p in (self._period_for(filing_result, t) for t in soi_tables) if p),
            None,
        )
        logger.info("MFIC: current SOI period: %s", first_period)
        rows: List[Dict[str, str]] = []
        current_type = ""
        current_industry = ""
        last_company = ""

        for t in soi_tables:
            period = self._period_for(filing_result, t)
            if first_period and period and period != first_period:
                break

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[header_idx])
            if "industrycompany" not in ranges or "fairvalue" not in ranges:
                continue

            data = grid[header_idx + 1:]
            for ri, row in enumerate(data):
                company = _cell_range(row, *ranges["industrycompany"])
                investment = _cell_range(row, *ranges["investmenttype"]) \
                    if "investmenttype" in ranges else ""
                interest = _cell_range(row, *ranges["interestrate"]) \
                    if "interestrate" in ranges else ""
                maturity = _cell_range(row, *ranges["maturitydate"]) \
                    if "maturitydate" in ranges else ""
                par = _cell_range(row, *ranges["parshares"]) \
                    if "parshares" in ranges else ""
                cost = _money(row, *ranges["cost"]) if "cost" in ranges else ""
                fair = _money(row, *ranges["fairvalue"])
                low = company.lower()

                has_values = bool(cost or fair)
                nonempty = [c.strip() for c in row if c.strip()]
                full_span = bool(nonempty) and len(set(nonempty)) == 1

                if not company and not investment and not has_values:
                    continue
                if low.startswith(("total", "subtotal", "sub-total")):
                    continue
                if not company and not investment:
                    row_text = " ".join(c for c in row if c).lower()
                    if "total" in row_text or "subtotal" in row_text:
                        continue
                    # Unlabeled subtotal: only cost/fair, no rate, maturity or par.
                    if has_values and not interest and not maturity and not par:
                        continue

                # Full-row span: industry or company header.
                if full_span and not investment and not has_values:
                    nxt = data[ri + 1] if ri + 1 < len(data) else []
                    nxt_inv = _cell_range(nxt, *ranges["investmenttype"]) \
                        if "investmenttype" in ranges else ""
                    nxt_company = _cell_range(nxt, *ranges["industrycompany"])
                    if nxt_inv and not nxt_company:
                        last_company = self._clean_company(company)
                    elif self._is_section(company):
                        current_type = self._map_type(company) or current_type
                    else:
                        current_industry = self._clean_industry(company)
                    continue

                # Company-only row: carry it into the following tranche rows.
                if company and not investment and not has_values:
                    if self._is_section(company):
                        current_type = self._map_type(company) or current_type
                    else:
                        last_company = self._clean_company(company)
                    continue

                if not company:
                    company = last_company
                else:
                    company = self._clean_company(company)
                    last_company = company
                if not company:
                    continue

                if not investment and not has_values and not interest:
                    continue

                inv_type = self._map_type(investment) or current_type or "Other"
                reference, spread, floor, cash = self._parse_rate(interest)

                shares = ""
                principal = ""
                par_clean = (par or "").replace(",", "")
                if re.search(r"shares?|units?", par_clean, re.I):
                    m = re.search(r"([\d.]+)", par_clean)
                    shares = m.group(1) if m else ""
                else:
                    principal = self._clean_number(par)

                if inv_type in _EQUITY_TYPES:
                    shares = shares or principal
                    principal = ""

                rows.append({
                    "company_name": company,
                    "investment_type": inv_type,
                    "industry": current_industry,
                    "cash_rate": cash,
                    "pik_rate": "",
                    "reference_rate": reference,
                    "spread": spread,
                    "floor_rate": floor,
                    "acquisition_date": "",
                    "maturity_date": self._parse_date(maturity),
                    "principal_amount": principal,
                    "amortized_cost": cost,
                    "fair_value": fair,
                    "percent_of_net_assets": "",
                    "cost": cost,
                    "commitment_limit": "",
                    "undrawn_commitment": "",
                    "rate_cap": "",
                    "shares": shares,
                })

        logger.info("MFIC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Helpers
    # --------------------------------------------------------------
    @staticmethod
    def _find_header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid[:3]):
            labels = {_norm_header(c) for c in row}
            if "industrycompany" in labels and "fairvalue" in labels:
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
    def _is_section(value: str) -> bool:
        s = (value or "").lower()
        return any(k in s for k in (
            "first lien", "second lien", "subordinated", "unsecured",
            "equity", "warrant", "debt investments", "investments",
        ))

    @staticmethod
    def _clean_industry(value: str) -> str:
        s = re.sub(r"\s*\(\d+\)\s*", "", value or "")
        return s.strip()

    @staticmethod
    def _clean_company(value: str) -> str:
        s = re.sub(r"\s*\((?:fka|formerly)[^)]*\)", "", value or "", flags=re.I)
        s = re.sub(r"\s*\(\d+\)(?:\s*\(\d+\))*\s*$", "", s)
        return s.strip().rstrip(",")

    @staticmethod
    def _map_type(value: str) -> str:
        s = (value or "").lower()
        if "revolver" in s:
            return "Revolver"
        if "delayed draw" in s:
            return "Delayed Draw"
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s or "term loan" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s:
            return "Common Equity"
        if "equity" in s or "unit" in s:
            return "Other Equity"
        return ""

    @staticmethod
    def _parse_rate(value: str) -> tuple:
        s = (value or "").replace("\u00a0", " ").strip()
        if not s or s.upper() in ("N/A", "NA"):
            return "", "", "", ""
        ref_map = {"S": "SOFR", "SOFR": "SOFR", "L": "LIBOR", "LIBOR": "LIBOR",
                   "P": "Prime", "PRIME": "Prime"}
        reference = spread = floor = cash = ""
        m = re.search(r"(SOFR|LIBOR|S|L|PRIME|P)\s*\+\s*([\d.]+)", s, re.I)
        if m:
            reference = ref_map.get(m.group(1).upper(), m.group(1).upper())
            val = float(m.group(2))
            if val > 50:
                val /= 100.0
            spread = f"{val:g}%"
        fm = re.search(r"([\d.]+)\s*%\s*Floor", s, re.I)
        if fm:
            floor = fm.group(1) + "%"
        if not reference and not spread:
            cm = re.search(r"([\d.]+)\s*%", s)
            if cm:
                cash = cm.group(1) + "%"
        return reference, spread, floor, cash

    @staticmethod
    def _parse_date(value: str) -> str:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", (value or "").strip())
        if not m:
            return ""
        year = int(m.group(3))
        if year < 100:
            year += 2000
        return f"{year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    @staticmethod
    def _period_for(filing_result: Any, table: Dict[str, Any]) -> Optional[str]:
        html = (filing_result.text_map or {}).get(table.get("doc") or "", "")
        if not html:
            return None
        pos = table.get("pos") or 0
        raw = re.sub(r"<[^>]+>", " ", html[max(0, pos - 6000):pos])
        raw = re.sub(r"&#160;|&nbsp;", " ", raw, flags=re.I)
        raw = re.sub(r"\s+", " ", raw)
        matches = re.findall(
            r"(January|February|March|April|May|June|July|August|September|"
            r"October|November|December)\s+(\d{1,2}),\s*(\d{4})", raw, re.I)
        if not matches:
            return None
        month, day, year = matches[-1]
        try:
            return datetime.strptime(f"{month} {day}, {year}", "%B %d, %Y").date().isoformat()
        except ValueError:
            return None
