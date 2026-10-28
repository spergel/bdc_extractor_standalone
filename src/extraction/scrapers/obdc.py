"""
Custom scraper for OBDC (Blue Owl Capital Corporation).

OBDC files its consolidated schedule of investments as a series of HTML
tables, one per page, each repeating the same column header row:

    Company | Investment | Ref. Rate | Cash | PIK | Maturity Date | Par |
    Shares/Units | Amortized Cost | Fair Value | % of Net Assets

Layout notes:
  * Amounts are already in thousands (no scaling needed).
  * The Cash column is the spread over the reference rate
    (e.g. S+ / 4.25% -> reference SOFR, spread 4.25%).
  * Investment type comes from the Investment column
    ("First lien senior secured loan" -> First Lien, etc.).
  * Industry comes from section rows that only fill the Company cell
    (e.g. "Advertising and media").  Section headers like
    "Debt Investments(7)" / "Equity Investments" are skipped.
  * Subtotal rows (empty Company cell) are skipped.
  * Commitment tables (Commitment Type / Funded Commitment / Commitment)
    and rollforward tables have no Investment header and are ignored.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseCompanyScraper

logger = logging.getLogger(__name__)

_REFERENCE_MAP = {
    "S+": "SOFR",
    "S": "SOFR",
    "E+": "Euribor",
    "E": "Euribor",
    "L+": "LIBOR",
    "L": "LIBOR",
    "P+": "Prime",
    "P": "Prime",
    "SONIA+": "SONIA",
    "SONIA": "SONIA",
    "CORRA+": "CORRA",
    "CORRA": "CORRA",
}

_SECTION_KEYS = (
    "debt investment",
    "equity investment",
    "joint venture",
    "non-controlled",
    "non - controlled",
    "affiliate",
    "controlled",
    "total",
)

_COLUMN_LABELS: Tuple[Tuple[str, str], ...] = (
    ("company", "company"),
    ("investment", "investment"),
    ("ref", "refrate"),
    ("cash", "cash"),
    ("pik", "pik"),
    ("maturity", "maturitydate"),
    ("par", "par"),
    ("shares", "shares/units"),
    ("amortized", "amortizedcost"),
    ("fair", "fairvalue"),
    ("pct", "%ofnetassets"),
)


def _norm_header(label: str) -> str:
    s = re.sub(r"\s*\([a-zA-Z0-9]+\)\s*", "", label or "")
    s = s.lower().strip()
    return re.sub(r"[^a-z0-9/%]", "", s)


def _build_groups(header_row: List[str]) -> List[Dict[str, Any]]:
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


class OBDCScraper(BaseCompanyScraper):
    ticker = "OBDC"

    # --------------------------------------------------------------
    # SOI table detection
    # --------------------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        """
        Keep tables whose header row has Company + Investment + Fair Value.

        Only tables from the primary 10-Q/10-K document are kept; the filing
        also contains supplemental SOI schedules for subsidiaries (e.g.
        Blue Owl Credit SLF LLC / Blue Owl Leasing LLC) that should not be
        merged into OBDC's consolidated holdings.
        """
        main_doc = self._main_document_filename(filing_result)
        soi: List[Dict[str, Any]] = []
        for t in tables:
            if main_doc and t.get("doc") != main_doc:
                continue
            head = t["text"][:500]
            if "Company" not in head:
                continue
            context = self._context_before(filing_result, t)
            if re.search(r"senior\s+loan\s+fund|percentage\s+of\s+members", head + " " + context, re.I):
                logger.debug("OBDC: skip subsidiary/portfolio table: %r", head[:120])
                continue
            grid = self.parse_table_to_rows(t["html"])
            for row in grid[:4]:
                labels = {_norm_header(c) for c in row if c.strip()}
                if ("company" in labels and "investment" in labels
                        and "fairvalue" in labels):
                    soi.append(t)
                    break
        logger.info("OBDC: found %d SOI tables", len(soi))
        return soi

    @staticmethod
    def _main_document_filename(filing_result: Any) -> Optional[str]:
        docs = getattr(filing_result, "documents", None) or []
        filing_type = getattr(filing_result, "filing_type", "10-Q")
        for d in docs:
            if (d.exhibit_type or "").strip().upper() == filing_type:
                return d.filename
        return docs[0].filename if docs else None

    # --------------------------------------------------------------
    # Main entry point
    # --------------------------------------------------------------
    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_industry = ""

        # OBDC 10-Qs include both the current-period SOI and the prior
        # year-end comparative SOI.  Determine the current section heading
        # ("As of June 30, 2026") from the first SOI table and skip tables
        # belonging to a different "As of ..." section.
        current_heading = None
        for t in soi_tables:
            heading = self._heading_before(filing_result, t)
            if heading:
                current_heading = heading
                break
        logger.info("OBDC: current SOI heading: %r", current_heading)

        def norm_heading(value):
            return re.sub(r"\s+", " ", str(value or "")).strip().lower()

        current_heading_norm = norm_heading(current_heading)
        headings = [self._heading_before(filing_result, t) for t in soi_tables]
        first_current = 0
        if current_heading_norm:
            for i, h in enumerate(headings):
                if norm_heading(h) == current_heading_norm:
                    first_current = i
                    break

        for idx, t in enumerate(soi_tables):
            heading = headings[idx]
            if idx < first_current and not heading:
                # Pages before the current-period heading without a heading of
                # their own belong to the prior-year comparative schedule.
                logger.debug("OBDC: skip pre-current unlabeled table doc=%s first=%r", t.get("doc"), (t.get("text") or "")[:80])
                continue
            if current_heading_norm and heading and norm_heading(heading) != current_heading_norm:
                logger.debug("OBDC: skip table doc=%s heading=%r first=%r", t.get("doc"), heading, (t.get("text") or "")[:80])
                continue

            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                logger.debug("OBDC: no header doc=%s first=%r", t.get("doc"), (t.get("text") or "")[:80])
                continue
            groups = _build_groups(grid[header_idx])
            by_label = {g["label"]: g for g in groups}

            if "company" not in by_label or "investment" not in by_label:
                logger.debug("OBDC: missing labels %s doc=%s", sorted(by_label), t.get("doc"))
                continue

            before = len(rows)
            for row in grid[header_idx + 1:]:
                company = self._strip_footnote_refs(
                    self._cell(row, by_label.get("company")))
                investment = self._cell(row, by_label.get("investment"))

                if not company:
                    # Subtotal / total row.
                    continue

                # Company-only row: industry or section header.  Section
                # headers can span all columns (colspan), in which case the
                # investment cell equals the company cell (modulo footnote
                # refs like "(24)").
                inv_clean = self._strip_footnote_refs(investment)
                if not investment or inv_clean == company or investment == company:
                    if self._is_section_header(company):
                        continue
                    current_industry = company
                    continue

                record = self._cells_to_record(row, by_label, company,
                                               investment, current_industry)
                rows.append(record)
            logger.debug("OBDC: table doc=%s rows=%d total=%d heading=%r", t.get("doc"), len(rows) - before, len(rows), heading)

        logger.info("OBDC: parsed %d investment rows", len(rows))
        return rows

    # --------------------------------------------------------------
    # Row parsing
    # --------------------------------------------------------------
    def _cells_to_record(self, row: List[str], by_label: Dict[str, Any],
                         company: str, investment: str,
                         industry: str) -> Dict[str, str]:
        ref_raw = self._cell(row, by_label.get("refrate"))
        reference = _REFERENCE_MAP.get(ref_raw.upper(), ref_raw)
        spread = self._parse_percent_cell(self._cell(row, by_label.get("cash")))
        pik = self._parse_percent_cell(self._cell(row, by_label.get("pik")))
        maturity = self._parse_date(self._cell(row, by_label.get("maturitydate")))
        principal = self._clean_number(self._cell(row, by_label.get("par")))
        shares = self._clean_number(self._cell(row, by_label.get("shares/units")))
        amortized = self._clean_number(self._cell(row, by_label.get("amortizedcost")))
        fair = self._clean_number(self._cell(row, by_label.get("fairvalue")))
        pct = self._parse_percent_cell(self._cell(row, by_label.get("%ofnetassets")))

        return {
            "company_name": company,
            "investment_type": self._map_investment_type(investment),
            "industry": industry,
            "cash_rate": "",
            "pik_rate": pik,
            "reference_rate": reference,
            "spread": spread,
            "floor_rate": "",
            "acquisition_date": "",
            "maturity_date": maturity,
            "principal_amount": principal,
            "amortized_cost": amortized,
            "fair_value": fair,
            "percent_of_net_assets": pct,
            "cost": "",
            "commitment_limit": "",
            "undrawn_commitment": "",
            "rate_cap": "",
            "shares": shares,
        }

    # --------------------------------------------------------------
    # Helpers
    # --------------------------------------------------------------
    _AS_OF_RE = re.compile(
        r"As\s+of\s+"
        r"(January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{1,2},\s+\d{4}",
        re.IGNORECASE,
    )

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
        matches = list(cls._AS_OF_RE.finditer(text))
        if not matches:
            return None
        return matches[-1].group(0).strip()

    @staticmethod
    def _find_header_row(grid: List[List[str]]) -> Optional[int]:
        for i, row in enumerate(grid):
            if any(_norm_header(c) == "company" for c in row):
                return i
        return None

    @staticmethod
    def _cell(row: List[str], group: Optional[Dict[str, Any]]) -> str:
        if group is None:
            return ""
        for i in range(group["start"], min(group["end"], len(row))):
            cell = (row[i] or "").strip()
            if cell and cell not in ("$", "€", "%"):
                return cell
        return ""

    @staticmethod
    def _strip_footnote_refs(value: str) -> str:
        if not value:
            return ""
        return re.sub(r"\s*(?:\(\d+\)\s*)+$", "", value.strip()).strip()

    @staticmethod
    def _is_section_header(company: str) -> bool:
        s = company.lower().strip()
        return any(k in s for k in _SECTION_KEYS)

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
    def _map_investment_type(raw: str) -> str:
        s = (raw or "").lower().strip()
        if not s:
            return ""
        if "delayed draw" in s:
            return "Delayed Draw"
        if "revolv" in s:
            return "Revolver"
        if "second lien" in s:
            return "Second Lien"
        if "first lien" in s:
            return "First Lien"
        if "subordinated" in s:
            return "Subordinated Debt"
        if "unsecured" in s:
            return "Unsecured Debt"
        if "convertible" in s:
            return "Unsecured Debt"
        if "specialty finance" in s or "debt" in s:
            return "Other Debt"
        if "warrant" in s:
            return "Warrants"
        if "preferred" in s:
            return "Preferred Equity"
        if "common" in s:
            return "Common Equity"
        if "partnership" in s or "joint venture" in s:
            return "Partnership Interest"
        if re.search(r"\blp\b|\bl\.p\.\b", s):
            return "Partnership Interest"
        if "interest" in s:
            return "Partnership Interest"
        if "equity" in s:
            return "Other Equity"
        if "unit" in s:
            return "Partnership Interest"
        if "share" in s:
            return "Common Equity"
        return "Other"
