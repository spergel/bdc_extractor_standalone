"""
Base class for per-company custom scrapers.

Every BDC reports its schedule of investments slightly differently, so each
ticker gets its own scraper module in this package.  The base class handles
everything that is the same across companies:

  * SEC document discovery + download (reuses SECAPIClient, the same download
    path the rest of the repo already uses)
  * HTML table extraction from filing documents
  * Parsing an HTML <table> into a rectangular grid of cell strings
    (colspan expansion, whitespace/entity normalization)
  * Filing iteration (single filing or historical 10-Q + 10-K) with
    skip-if-exists behaviour
  * Writing the standard output CSV in `output/custom_scraper_TICKER_date.csv`

Subclasses only implement `parse_soi_tables` (turning parsed tables into rows
of the standard schema) and any table-detection overrides they need.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from sec_api_client import SECAPIClient

logger = logging.getLogger(__name__)

# Standard extraction schema.  Mirrors the XBRL extractor's output so the
# existing post-processing + consolidation steps keep working unchanged.
OUTPUT_COLUMNS = [
    "company_name", "investment_type", "industry", "cash_rate", "pik_rate",
    "reference_rate", "spread", "floor_rate", "acquisition_date", "maturity_date",
    "principal_amount", "amortized_cost", "fair_value", "percent_of_net_assets",
    "cost", "commitment_limit", "undrawn_commitment", "rate_cap", "shares",
]

HEADER_CSV = ",".join(OUTPUT_COLUMNS) + "\n"

# CIKs missing from the bundled company_tickers map.
CIK_OVERRIDES = {
    "MRCC": {"cik_str": 1512931, "ticker": "MRCC", "title": "Monroe Capital Corporation"},
    "BCRED": {"cik_str": 1803498, "ticker": "BCRED", "title": "Blackstone Private Credit Fund"},
    "SSSS": {"cik_str": 1509470, "ticker": "SSSS", "title": "Neostellar Capital Corp."},
    "LRFC": {"cik_str": 1278752, "ticker": "LRFC", "title": "Logan Ridge Finance Corp"},
}


class BaseCompanyScraper:
    """Common scaffolding for a per-ticker custom scraper."""

    ticker: str = ""
    # Only these document extensions are examined for tables.
    document_types: List[str] = [".htm", ".html"]
    # By default only fetch the main filing document (skip exhibit .htm files).
    main_document_only: bool = True

    def __init__(self, data_dir: str = "data", output_dir: str = "output",
                 user_agent: Optional[str] = None):
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sec_client = SECAPIClient(
            data_dir=str(self.data_dir),
            user_agent=user_agent,
        )
        for ticker, info in CIK_OVERRIDES.items():
            self.sec_client._company_tickers.setdefault(ticker.upper(), info)

    # ------------------------------------------------------------------
    # Filing discovery / download (reuses the existing SEC download path)
    # ------------------------------------------------------------------
    def list_historical_filings(self, years_back: int) -> List[Dict[str, Any]]:
        """Return 10-Q + 10-K filings for the last N years, sorted by date."""
        qs = self.sec_client.get_historical_10q_filings(self.ticker, years_back=years_back)
        ks = self.sec_client.get_historical_10k_filings(self.ticker, years_back=years_back)
        filings: List[Dict[str, Any]] = []
        for f in qs:
            f = dict(f)
            f["filing_type"] = "10-Q"
            filings.append(f)
        for f in ks:
            f = dict(f)
            f["filing_type"] = "10-K"
            filings.append(f)
        filings.sort(key=lambda x: x.get("date", ""))
        return filings

    def get_filing_index_url(self, filing_type: str, year: Optional[int] = None,
                             quarter: Optional[str] = None) -> Optional[str]:
        return self.sec_client.get_filing_index_url(
            ticker=self.ticker,
            filing_type=filing_type,
            year=year,
            quarter=quarter if filing_type == "10-Q" else None,
        )

    def fetch_filing(self, filing_type: str, index_url: str) -> Any:
        """Download a filing (documents + text map) via SECAPIClient."""
        return self.sec_client.fetch_filing_by_index_url(
            index_url=index_url,
            ticker=self.ticker,
            filing_type=filing_type,
            save_to_file=False,
            document_types=self.document_types,
            main_document_only=self.main_document_only,
        )

    # ------------------------------------------------------------------
    # Table extraction
    # ------------------------------------------------------------------
    def extract_tables(self, filing_result: Any) -> List[Dict[str, Any]]:
        """
        Extract every HTML <table> from the filing's HTML documents.

        Returns a list of dicts with keys: html, text, num, id, doc.
        """
        tables: List[Dict[str, Any]] = []
        text_map = getattr(filing_result, "text_map", None) or {}
        for doc in filing_result.documents:
            if not (doc.filename.endswith(".htm") or doc.filename.endswith(".html")):
                continue
            html = text_map.get(doc.filename)
            if html is None:
                try:
                    resp = requests.get(doc.url, headers=self.sec_client.headers, timeout=90)
                    resp.raise_for_status()
                    html = resp.text
                except Exception as exc:
                    logger.warning("Failed to fetch %s: %s", doc.filename, exc)
                    continue
            try:
                soup = BeautifulSoup(html, "html.parser")
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", doc.filename, exc)
                continue
            # Positions of each <table ...> tag so scrapers can compute the
            # text that precedes a table (section captions, dates, etc.).
            table_starts = [m.start() for m in re.finditer(r"<table[\s>]", html, re.I)]
            for i, table in enumerate(soup.find_all("table")):
                table_html = str(table)
                table_text = table.get_text(" ", strip=True)
                if len(table_text) < 50:
                    continue
                tables.append({
                    "html": table_html,
                    "text": table_text,
                    "num": len(tables),
                    "id": f"{doc.filename}_table_{i}",
                    "doc": doc.filename,
                    "idx": i,
                    "pos": table_starts[i] if i < len(table_starts) else None,
                })
        logger.info("Extracted %d tables from %s filing", len(tables), self.ticker)
        return tables

    # ------------------------------------------------------------------
    # HTML table -> rectangular grid
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_cell_text(text: str) -> str:
        text = text.replace("\u00a0", " ")
        text = text.replace("\u2014", "-").replace("\u2013", "-")
        text = text.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
        return " ".join(text.split())

    def parse_table_to_rows(self, table_html: str) -> List[List[str]]:
        """
        Parse an HTML table into a rectangular grid of strings.

        * inline formatting tags are flattened to text
        * `colspan` is expanded by repeating the cell value
        * all rows are padded to the same number of columns

        (ARCC and most BDC SOI tables do not use meaningful `rowspan`; if a
        future scraper needs it, extend this method.)
        """
        soup = BeautifulSoup(table_html, "html.parser")
        table = soup.find("table")
        if not table:
            return []

        for element in table.find_all(style=True):
            del element["style"]
        for element in table.find_all(["ix:nonfraction", "ix:nonnumeric", "span",
                                       "div", "font", "sup", "sub", "br"]):
            element.replace_with(element.get_text())
        for tag in table.find_all(True):
            keep = {a: v for a, v in tag.attrs.items() if a in ("colspan", "rowspan")}
            tag.attrs = keep

        grid: List[List[str]] = []
        for tr in table.find_all("tr"):
            row: List[str] = []
            for cell in tr.find_all(["th", "td"]):
                text = self._normalize_cell_text(cell.get_text(strip=True))
                colspan = int(cell.get("colspan", 1) or 1)
                row.extend([text] * colspan)
            if row and any(c.strip() for c in row):
                grid.append(row)

        if not grid:
            return []
        ncols = max(len(r) for r in grid)
        for r in grid:
            r.extend([""] * (ncols - len(r)))
        return grid

    # ------------------------------------------------------------------
    # Shared field-cleaning helpers (used by ticker scrapers)
    # ------------------------------------------------------------------
    @staticmethod
    def _strip_footnote_refs(value: str) -> str:
        """Remove trailing footnote markers like (13), (2)(9), (2)(6)(11)."""
        if not value:
            return ""
        return re.sub(r"\s*(?:\(\d+\)\s*)+$", "", value.strip()).strip()

    @staticmethod
    def _clean_number(value: str) -> str:
        """Return the numeric part of a cell, or '' when not numeric.

        Accounting parentheses denote negatives ("(41)" -> "-41").
        """
        if not value:
            return ""
        s = value.strip().replace(",", "").replace("$", "").replace("%", "").strip()
        if s in ("", "-", "\u2014", "\u2013"):
            return ""
        negative = False
        if s.startswith("(") and s.endswith(")"):
            negative = True
            s = s[1:-1].strip()
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        if not m:
            return ""
        num = m.group(0)
        if negative and not num.startswith("-"):
            num = "-" + num
        return num

    @staticmethod
    def _fmt_number(value: float) -> str:
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.6f}".rstrip("0").rstrip(".")

    def _to_thousands(self, value: str) -> str:
        """Convert a dollar amount expressed in millions to thousands."""
        num = self._clean_number(value)
        if not num:
            return ""
        try:
            return self._fmt_number(float(num) * 1000.0)
        except ValueError:
            return ""

    def _parse_date(self, value: str) -> str:
        """Normalize dates to YYYY-MM-DD. Handles MM/DD/YYYY, M/D/YY, MM/YYYY."""
        if not value:
            return ""
        s = value.strip()
        if s in ("-", "\u2014", "\u2013"):
            return ""
        # Already ISO
        m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", s)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        # MM/DD/YYYY or M/D/YYYY
        m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", s)
        if m:
            mo, d, y = m.group(1), m.group(2), m.group(3)
            if len(y) == 2:
                y = "20" + y
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        # MM/YYYY
        m = re.search(r"\b(\d{1,2})/(\d{4})\b", s)
        if m:
            return f"{m.group(2)}-{int(m.group(1)):02d}-01"
        return s

    # ------------------------------------------------------------------
    # CSV output
    # ------------------------------------------------------------------
    def rows_to_csv(self, rows: List[Dict[str, str]]) -> str:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in OUTPUT_COLUMNS})
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Filing processing
    # ------------------------------------------------------------------
    def output_path_for(self, filing_date: str) -> Path:
        return self.output_dir / f"custom_scraper_{self.ticker}_{filing_date}.csv"

    def process_filing(self, filing_type: str, index_url: str, force: bool = False,
                       filing_date: Optional[str] = None) -> bool:
        """Download + parse one filing and write the standard CSV."""
        # When the filing date is already known (historical runs), skip without
        # downloading the filing again.
        if filing_date:
            out_path = self.output_path_for(filing_date)
            if not force and out_path.exists() and out_path.stat().st_size > len(HEADER_CSV):
                logger.info("SKIP (exists): %s", out_path.name)
                return True

        filing_result = self.fetch_filing(filing_type, index_url)
        if not filing_result:
            logger.warning("Failed to fetch %s", index_url)
            return False

        filing_date = filing_result.filing_date
        out_path = self.output_path_for(filing_date)
        if not force and out_path.exists() and out_path.stat().st_size > len(HEADER_CSV):
            logger.info("SKIP (exists): %s", out_path.name)
            return True

        tables = self.extract_tables(filing_result)
        if not tables:
            logger.warning("No tables found in %s %s", self.ticker, filing_date)
            out_path.write_text(HEADER_CSV, encoding="utf-8")
            return True

        rows = self.parse_soi_tables(tables, filing_result)
        csv_content = self.rows_to_csv(rows)

        if not rows:
            out_path.write_text(HEADER_CSV, encoding="utf-8")
            logger.info("Wrote header-only CSV: %s", out_path)
            return True

        # Deduplicate identical rows (scrapers whose filings legitimately
        # repeat identical tranches can opt out with dedupe_rows = False).
        unique: List[Dict[str, str]] = rows
        if getattr(self, "dedupe_rows", True):
            seen = set()
            kept: List[Dict[str, str]] = []
            for row in rows:
                key = tuple(row.get(k, "") for k in OUTPUT_COLUMNS)
                if key not in seen:
                    seen.add(key)
                    kept.append(row)
            unique = kept

        csv_content = self.rows_to_csv(unique)
        out_path.write_text(csv_content, encoding="utf-8")
        logger.info("Wrote %d rows to %s", len(unique), out_path)
        return True

    # Subclass interface -------------------------------------------------
    def find_soi_tables(self, tables: List[Dict[str, Any]],
                        filing_result: Any) -> List[Dict[str, Any]]:
        """
        Return the subset of tables that are schedule-of-investments tables.
        Default heuristic; subclasses may override.
        """
        return [t for t in tables
                if "Company" in t["text"][:600] and "Fair Value" in t["text"][:2000]]

    def parse_soi_tables(self, tables: List[Dict[str, Any]],
                         filing_result: Any) -> List[Dict[str, str]]:
        """Parse SOI tables into standard-schema rows. Implement per ticker."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Runners
    # ------------------------------------------------------------------
    def run_single_filing(self, filing_type: str = "10-Q", year: Optional[int] = None,
                          quarter: Optional[str] = None, force: bool = False) -> int:
        index_url = self.get_filing_index_url(filing_type, year=year, quarter=quarter)
        if not index_url:
            logger.error("No %s filing found for %s", filing_type, self.ticker)
            return 1
        return 0 if self.process_filing(filing_type, index_url, force=force) else 1

    def run_historical(self, years_back: int, force: bool = False) -> int:
        if years_back <= 0:
            # "--years-back 0" is the documented "latest quarter only" mode.
            return self.run_single_filing("10-Q", force=force)
        filings = self.list_historical_filings(years_back)
        if not filings:
            logger.error("No historical filings found for %s (years_back=%s)",
                         self.ticker, years_back)
            return 1
        logger.info("Processing %d filings for %s (last %s years)",
                    len(filings), self.ticker, years_back)
        ok = 0
        for f in filings:
            logger.info("--- %s %s ---", f["filing_type"], f["date"])
            if self.process_filing(f["filing_type"], f["index_url"], force=force,
                                   filing_date=f["date"]):
                ok += 1
        logger.info("Done: %d/%d filings written to %s", ok, len(filings), self.output_dir)
        return 0 if ok else 1
