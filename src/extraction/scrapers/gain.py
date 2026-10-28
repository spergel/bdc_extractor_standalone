"""
Custom scraper for GAIN (Gladstone Investment Corporation).

GAIN uses the same compact "Company and Investment" schedule as GLAD
(same advisor), with slightly different wording:

    Company and Investment(A)(B)(D)(E) | Principal/Shares/Units |
    Cost | Fair Value

Example row text:

    Detroit Defense, Inc.(K) - Term Debt
        (SOFR+9.0%, 13.0% Cash, Due 12/2029)(J)

Layout notes:
  * Amounts are in thousands (no scaling needed).
  * Section rows set the type; industry rows are carried down.
  * The instrument split tolerates both " - Term Debt" and "-Term Debt".
  * Reference rates may be written as "SOFR+9.0%" or "(S + 6.4%)".
  * The current schedule starts with "NON-CONTROL/NON-AFFILIATE
    INVESTMENTS"; when that section appears a second time the prior
    year-end copy begins and parsing stops.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .glad import (GLADScraper, _SECTION_KEYWORDS, _cell_range,
                   _norm_header)

logger = logging.getLogger(__name__)

_SPLIT_RE = re.compile(
    r"\s+-\s*|-(?=Term|Line|Common|Preferred|Warrant|Equity|Subordinated|"
    r"Senior|Second|First|Note|Unit)",
)


class GAINScraper(GLADScraper):
    ticker = "GAIN"

    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        soi: List[Dict[str, Any]] = []
        for t in tables:
            grid = self.parse_table_to_rows(t["html"])
            if not grid:
                continue
            head = " ".join((c or "") for r in grid[:2] for c in r).lower()
            if "company and investment" not in head:
                continue
            if "fair value" not in head:
                continue
            soi.append(t)
        soi.sort(key=lambda t: t.get("pos") or 0)
        logger.info("GAIN: found %d SOI tables", len(soi))
        return soi

    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        soi_tables = self.find_soi_tables(tables, filing_result)
        rows: List[Dict[str, str]] = []
        current_type = "First Lien"
        current_industry = ""
        seen_main_section = False

        for t in soi_tables:
            grid = self.parse_table_to_rows(t["html"])
            header_idx = self._find_header_row(grid)
            if header_idx is None:
                continue
            ranges = self._column_ranges(grid[self._best_header_row(grid, header_idx)])
            if "principal/shares/units" not in ranges \
                    or "fairvalue" not in ranges:
                # Older filings use a layout this scraper does not model yet.
                continue

            for row in grid[header_idx + 1:]:
                text = _cell_range(row, *ranges["companyandinvestment"])
                low = text.lower()

                if not text:
                    continue
                if low.startswith("total") or low.startswith("subtotal"):
                    continue

                principal = _cell_range(row, *ranges["principal/shares/units"])
                cost = _cell_range(row, *ranges["cost"])
                fair = _cell_range(row, *ranges["fairvalue"])
                has_values = bool(principal or cost or fair)

                if not has_values:
                    if "non-control/non-affiliate investments" in low \
                            or "non-control, non-affiliate investments" in low:
                        if seen_main_section:
                            return rows
                        seen_main_section = True
                        continue
                    if any(k in low for k in _SECTION_KEYWORDS):
                        sec_type = self._map_section_type(text)
                        if sec_type:
                            current_type = sec_type
                    else:
                        current_industry = re.sub(
                            r"\s*-\s*[\d.]+%?\s*$", "", text).strip()
                    continue

                if not _SPLIT_RE.search(text):
                    continue

                parts = _SPLIT_RE.split(text, maxsplit=1)
                company = re.sub(r"\([A-Z]\)", "", parts[0]).strip()
                instrument = parts[1] if len(parts) > 1 else ""
                inv_type = self._map_instrument(instrument) or current_type
                rows.append(self._row_to_record(
                    row, company, instrument, current_industry, inv_type, ranges))

        logger.info("GAIN: parsed %d investment rows", len(rows))
        return rows

    @staticmethod
    def _parse_terms(instrument: str) -> tuple:
        s = instrument or ""
        reference = ""
        spread = ""
        cash = ""
        pik = ""
        maturity = ""

        m = re.search(r"(SOFR|LIBOR|PRIME|EURIBOR)\s*\+\s*(\d+(?:\.\d+)?)\s*%",
                      s, re.IGNORECASE)
        if m:
            ref_map = {"SOFR": "SOFR", "LIBOR": "LIBOR", "PRIME": "Prime",
                       "EURIBOR": "Euribor"}
            reference = ref_map.get(m.group(1).upper(), m.group(1).upper())
            spread = m.group(2) + "%"
        else:
            m2 = re.search(r"\(\s*([A-Z])\s*\+\s*(\d+(?:\.\d+)?)\s*%", s)
            if m2:
                ref_map = {"S": "SOFR", "L": "LIBOR", "P": "Prime",
                           "E": "Euribor"}
                reference = ref_map.get(m2.group(1).upper(), m2.group(1).upper())
                spread = m2.group(2) + "%"

        cm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*Cash", s)
        if cm:
            cash = cm.group(1) + "%"

        pm = re.search(r"(\d+(?:\.\d+)?)\s*%\s*PIK", s)
        if pm:
            pik = pm.group(1) + "%"

        dm = re.search(r"Due\s+(\d{1,2})/(\d{4})", s, re.IGNORECASE)
        if dm:
            maturity = f"{dm.group(2)}-{int(dm.group(1)):02d}-01"

        return reference, spread, cash, pik, maturity
