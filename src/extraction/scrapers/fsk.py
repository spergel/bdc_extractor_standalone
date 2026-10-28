"""
Custom scraper for FSK (FS KKR Capital Corp).

FSK files its consolidated schedule of investments as a series of HTML
tables, one per page, each repeating the same column header row:

    Portfolio Company | Footnotes | Industry | Rate | Floor | Maturity |
    Principal Amount (or Number of Shares) | Amortized Cost | Fair Value

Layout notes:
  * The `Rate` group contains sub-columns: reference, '+', spread, and an
    optional PIK marker like "(0.0% PIK / 2.6% PIK)".
  * The position column is `Principal Amount` for debt tables,
    `Number of Shares` for equity tables, or `Principal Amount / Shares`
    for the mixed Asset Based Finance tables (decided per row by the
    presence of a '$'/'€' unit cell).
  * Investment type comes from section header rows
    (e.g. "Senior Secured Loans-First Lien-131.0%") plus the instrument
    suffix in the company cell (e.g. "..., Common Stock").
  * 10-Q filings include a comparative prior-year-end SOI section, which is
    skipped the same way as ARCC.
  * Dollar amounts are in millions and converted to thousands.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

# Reference-rate abbreviations used in the Rate column.
_REFERENCE_MAP = {
    "SF": "SOFR",
    "S": "SOFR",
    "E": "Euribor",
    "L": "LIBOR",
    "P": "Prime",
    "SONIA": "SONIA",
    "CORRA": "CORRA",
}

# Section header prefix -> investment type ('' means "decide per row").
_SECTION_TYPES: Tuple[Tuple[str, Optional[str]], ...] = (
    ("senior secured loans-second lien", "Second Lien"),
    ("senior secured loans-first lien", "First Lien"),
    ("other senior secured debt", "First Lien"),
    ("subordinated debt", "Subordinated Debt"),
    ("credit opportunities partners jv", "Partnership Interest"),
    ("asset based finance", None),
    ("equity/other", None),
)


def _norm_header(label: str) -> str:
    """Normalize a header cell, e.g. 'Portfolio Company(a)' -> 'portfoliocompany'."""
    s = re.sub(r"\s*\([a-zA-Z0-9]+\)\s*", "", label or "")
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9/%]", "", s)


def _build_groups(header_row: List[str]) -> List[Dict[str, Any]]:
    """Split a header row into runs of equal cells -> (label, start, end) groups."""
    groups: List[Dict[str, Any]] = []
    prev: Optional[str] = None
    start = 0
    for i, cell in enumerate(header_row):
        if cell != prev:
            if prev is not None:
                groups.append({"label": _norm_header(prev), "start": start, "end": i})
            prev = cell
            start = i
    if prev is not None:
        groups.append({"label": _norm_header(prev), "start": start, "end": len(header_row)})
    return groups


class FSKScraper(BaseCompanyScraper):
    ticker = "FSK"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        """
        Keep tables whose header row is the FSK SOI header.  The rollforward
        tables (Fair Value at ... / Gross Additions ...) have no Industry or
        Rate header and are excluded.
        """
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if "Portfolio Company" not in t["text"][:800]:
                continue
            grid = self.parse_table_to_rows(t["html"])
            for row in grid[:4]:
                labels = {_norm_header(c) for c in row if c.strip()}
                if ("portfoliocompany" in labels and "industry" in labels
                        and "rate" in labels):
                    soi.append(t)
                    break
        logger.info("FSK: found %d SOI tables", len(soi))
        return soi

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_section_name = ""

        in_prior_year_section = False
        prior_heading, current_heading = self._soi_heading_markers(filing_result)

        for t in soi_tables:
            if prior_heading:
                ctx = self._context_before(t, filing_result).lower()
                if (not in_prior_year_section
                        and prior_heading in ctx
                        and (not current_heading or current_heading not in ctx)):
                    in_prior_year_section = True
                    logger.info("FSK: prior year-end SOI section reached at table %s; skipping rest",
                                t["id"])
                if in_prior_year_section:
                    continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            groups = _build_groups(grid[header_idx])
            by_label = {g["label"]: g for g in groups}

            position_kind = self._position_kind(by_label)
            if position_kind is None:
                continue
            # Map the position group so _parse_row can read it via 'pos'.
            if position_kind == "mixed":
                by_label["pos"] = by_label.get("principalamount/shares")
            elif position_kind == "principal":
                by_label["pos"] = by_label.get("principalamount")
            else:
                by_label["pos"] = by_label.get("numberofshares")

            for row in grid[header_idx + 1:]:
                record = self._parse_row(row, by_label, position_kind,
                                         current_section_name)
                if record is None:
                    continue
                # Section header rows only update the section context.
                if record.get("_section_name") is not None:
                    current_section_name = record.pop("_section_name")
                    continue
                rows.append(record)

        logger.info("FSK: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _parse_row(self, row: List[str], by_label: Dict[str, Any],
                   position_kind: str, current_section_name: str) -> Optional[Dict[str, Any]]:
        company_cell = self._cell(row, by_label.get("portfoliocompany"))
        industry = self._cell(row, by_label.get("industry"))
        company_cell = self._strip_footnote_refs(company_cell)

        # --- Totals / net asset / money market rows: never holdings ---
        if self._is_non_holding_row(company_cell):
            return None

        # --- Section header row: e.g. "Senior Secured Loans-First Lien-131.0%" ---
        if not industry and self._looks_like_section_header(company_cell):
            if self._is_major_total(company_cell):
                return None
            return {"_section_name": company_cell}

        # --- Data rows must have an industry cell. ---
        if not industry:
            # Money-market / subtotal rows have no industry; skip them.
            return None

        if not company_cell:
            return None

        company, suffix = self._split_company_and_suffix(company_cell)
        if not company:
            return None

        # Rate group -------------------------------------------------
        rate_cells = self._unique_cells(row, by_label.get("rate"))
        reference, spread, pik, cash = self._parse_rate(rate_cells)

        # Position group ---------------------------------------------
        pos_cells = self._unique_cells(row, by_label.get("pos"))
        principal = ""
        shares = ""
        if position_kind == "principal":
            principal = self._to_thousands(self._monetary_cell(pos_cells))
        elif position_kind == "shares":
            shares = self._clean_number(pos_cells[0] if pos_cells else "")
        else:  # mixed: '$'/'€' present -> principal, otherwise shares
            if any(c in ("$", "€") for c in pos_cells):
                principal = self._to_thousands(self._monetary_cell(pos_cells))
            else:
                shares = self._clean_number(pos_cells[0] if pos_cells else "")

        floor_raw = self._cell(row, by_label.get("floor"))
        floor_rate = self._parse_percent(floor_raw) + "%" if self._parse_percent(floor_raw) else ""

        maturity = self._parse_date(self._cell(row, by_label.get("maturity")))
        amortized = self._to_thousands(self._monetary_cell(
            self._unique_cells(row, by_label.get("amortizedcost"))))
        fair = self._to_thousands(self._monetary_cell(
            self._unique_cells(row, by_label.get("fairvalue"))))

        inv_type = self._row_type(suffix, current_section_name)

        return {
            "company_name": company,
            "investment_type": inv_type,
            "industry": industry,
            "cash_rate": cash,
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": floor_rate,
            "acquisition_date": "",
            "maturity_date": maturity,
            "principal_amount": principal,
            "amortized_cost": amortized,
            "fair_value": fair,
            "percent_of_net_assets": "",
            "cost": "",
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
    def _position_kind(by_label: Dict[str, Any]) -> Optional[str]:
        labels = list(by_label.keys())
        if "principalamount/shares" in labels:
            return "mixed"
        if "principalamount" in labels:
            return "principal"
        if "numberofshares" in labels:
            return "shares"
        return None

    @staticmethod
    def _cell(row: List[str], group: Optional[Dict[str, Any]]) -> str:
        if group is None:
            return ""
        for i in range(group["start"], min(group["end"], len(row))):
            cell = (row[i] or "").strip()
            if cell:
                return cell
        return ""

    @staticmethod
    def _unique_cells(row: List[str], group: Optional[Dict[str, Any]]) -> List[str]:
        """Distinct non-empty cell values within a group, in document order."""
        if group is None:
            return []
        seen = set()
        out = []
        for i in range(group["start"], min(group["end"], len(row))):
            cell = (row[i] or "").strip()
            if cell and cell not in seen:
                seen.add(cell)
                out.append(cell)
        return out

    @staticmethod
    def _monetary_cell(cells: List[str]) -> str:
        """Return the first non-currency cell (skip '$' and '€')."""
        for c in cells:
            if c not in ("$", "€", "%"):
                return c
        return ""

    @staticmethod
    def _strip_footnote_refs(value: str) -> str:
        if not value:
            return ""
        return re.sub(r"\s*(?:\(\d+\)\s*)+$", "", value.strip()).strip()

    @staticmethod
    def _is_non_holding_row(company: str) -> bool:
        """True for total/liability/net-asset/money-market summary rows."""
        if not company:
            return False
        s = company.strip().lower()
        return bool(re.match(r"^(total|net|unfunded|total investments|net assets|"
                             r"liabilities|money market funds)\b", s))

    @staticmethod
    def _looks_like_section_header(company: str) -> bool:
        if not company:
            return False
        return bool(re.search(r"-\s*\d+\.?\d*\s*%\s*(\([a-z]+\))?\s*$", company, re.I)
                    or re.search(r"-\s*\(\d+\.?\d*\s*%\s*\)\s*$", company, re.I))

    @staticmethod
    def _is_major_total(company: str) -> bool:
        return bool(re.match(r"^(total investments|net assets|liabilities in excess|total money market)",
                             company.strip(), re.I))

    @staticmethod
    def _section_type(company: str) -> Optional[str]:
        s = company.strip().lower()
        for prefix, inv_type in _SECTION_TYPES:
            if s.startswith(prefix):
                return inv_type
        return None

    @staticmethod
    def _split_company_and_suffix(company: str) -> Tuple[str, str]:
        """Split an instrument suffix like ', Common Stock' off the company name."""
        if "," not in company:
            return company, ""
        base, suffix = company.rsplit(",", 1)
        suffix = suffix.strip()
        base = base.strip()
        suffix_lower = suffix.lower()
        instrument_keywords = ("common", "preferred", "equity", "units", "warrant",
                               "trade claim", "mezzanine", "abf", "term loan",
                               "stock", "shares", "private")
        if any(k in suffix_lower for k in instrument_keywords):
            return base, suffix
        return company, ""

    @staticmethod
    def _suffix_type(suffix: str) -> str:
        s = suffix.lower()
        if "warrant" in s:
            return "Warrants"
        if "common" in s:
            return "Common Equity"
        if "preferred" in s:
            return "Preferred Equity"
        if "private equity" in s or "abf equity" in s:
            return "Other Equity"
        if "mezzanine" in s:
            return "Subordinated Debt"
        if "term loan" in s:
            return "First Lien"
        if "equity" in s:
            return "Other Equity"
        if "trade claim" in s:
            return "Other"
        return "Other"

    def _row_type(self, suffix: str, section_name: str) -> str:
        if suffix:
            return self._suffix_type(suffix)
        s = (section_name or "").lower().strip()
        if s.startswith("asset based finance"):
            return "Other Debt"
        if s.startswith("equity/other"):
            return "Other Equity"
        return self._section_type(section_name) or "Other"

    def _parse_rate(self, cells: List[str]) -> Tuple[str, str, str, str]:
        """
        Parse the Rate group cells into (reference, spread, pik_rate, cash_rate).
        Handles "SF + 6.0%", "SF + 4.2% (0.0% PIK / 2.6% PIK)",
        "12.0% PIK" (fixed PIK), "11.0% (3.0% PIK / 3.0% PIK)", and "E + 5.0%".
        """
        reference = ""
        for c in cells:
            up = c.upper()
            if up in _REFERENCE_MAP and not reference:
                reference = _REFERENCE_MAP[up]

        pct_cells = [c for c in cells if "%" in c]
        non_pik_pcts = [c for c in pct_cells if "PIK" not in c.upper()]
        pik_markers = [c for c in cells if "PIK" in c.upper()]
        pik_pcts = [float(m.group(1))
                    for c in pik_markers
                    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", c)]

        spread = ""
        pik = ""
        cash = ""
        if reference:
            if non_pik_pcts:
                spread = self._parse_percent(non_pik_pcts[0]) + "%"
            if pik_pcts:
                pik = self._fmt_pct(max(pik_pcts))
        else:
            # Fixed-rate row: the non-PIK percent is cash only when the PIK
            # marker carries its own percent(s); otherwise it is the PIK rate.
            if pik_pcts:
                if non_pik_pcts:
                    cash = self._parse_percent(non_pik_pcts[0]) + "%"
                pik = self._fmt_pct(max(pik_pcts))
            elif non_pik_pcts:
                pik = self._parse_percent(non_pik_pcts[0]) + "%"
        return reference, spread, pik, cash

    @staticmethod
    def _parse_percent(value: str) -> str:
        if not value:
            return ""
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", value)
        return m.group(1) if m else ""

    @staticmethod
    def _fmt_pct(value: float) -> str:
        if abs(value - round(value)) < 1e-9:
            return f"{int(round(value))}%"
        return f"{value:.6f}".rstrip("0").rstrip(".") + "%"

    def _soi_heading_markers(self, filing_result: Any) -> Tuple[str, str]:
        """Return (prior_year_heading, current_heading) for 10-Q prior-year skip."""
        filing_type = getattr(filing_result, "filing_type", "10-Q")
        period_end = getattr(filing_result, "period_end_date", None)
        if filing_type != "10-Q" or not period_end:
            return "", ""
        try:
            dt = datetime.strptime(str(period_end)[:10], "%Y-%m-%d")
        except ValueError:
            return "", ""
        months = {1: "january", 2: "february", 3: "march", 4: "april",
                  5: "may", 6: "june", 7: "july", 8: "august",
                  9: "september", 10: "october", 11: "november", 12: "december"}
        current = f"as of {months.get(dt.month, '')} {dt.day}, {dt.year}"
        prior = f"as of december 31, {dt.year - 1}"
        return prior, current

    def _context_before(self, table: Dict[str, Any], filing_result: Any,
                        chars: int = 700) -> str:
        html = (getattr(filing_result, "text_map", None) or {}).get(table.get("doc", ""), "")
        pos = table.get("pos")
        if not html or pos is None:
            return ""
        raw = html[max(0, pos - chars * 4):pos]
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"&#160;|&nbsp;", " ", text, flags=re.I)
        text = re.sub(r"\s+", " ", text)
        return text[-chars:]
