#!/usr/bin/env python3
"""
DSPy-based Schedule of Investments Extractor

Uses DSPy's structured typed prediction with Gemini to extract investment data from
BDC SEC filings.  Designed for tickers that fail with XBRL or the plain-HTML approach.

Target tickers (broken / incomplete): EQS, SSSS, BXSL, GSBD, LIEN, NEWT, OXSQ, PSBD, TPVG, ICMB

Key advantages over llm_table_scraper.py
-----------------------------------------
* Typed Pydantic output instead of raw CSV text – no comma-parsing bugs
* TypedChainOfThought adds a reasoning step that helps with unusual table layouts
* Per-field schema descriptions double as in-prompt instructions
* Modular DSPy signatures are independently testable / optimisable
* Section context is passed explicitly so investment_type is inferred correctly
  even when the column itself is missing (e.g. ICMB loans listed as "Other Equity")

Usage
-----
    from dspy_table_scraper import DSPyTableScraper

    scraper = DSPyTableScraper(output_dir="output")
    paths = scraper.scrape_ticker("BXSL", start_date="2023-01-01")
"""

import csv
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# DSPy + Pydantic
# ---------------------------------------------------------------------------
try:
    import dspy
    from pydantic import BaseModel, Field
    DSPY_AVAILABLE = True
except ImportError:
    DSPY_AVAILABLE = False

# Optional dotenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Local imports – resolve relative to repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC_EXTRACTION = _REPO_ROOT / "src" / "extraction"
if str(_SRC_EXTRACTION) not in sys.path:
    sys.path.insert(0, str(_SRC_EXTRACTION))

from sec_api_client import SECAPIClient
from llm_table_scraper import LLMTableScraper
from table_detection import fallback_table_detection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Tickers this scraper is designed to fix
TARGET_TICKERS = [
    "EQS", "SSSS", "BXSL", "GSBD", "LIEN",
    "NEWT", "OXSQ", "PSBD", "TPVG", "ICMB",
]

#: Standard investment types the model must choose from
INVESTMENT_TYPE_OPTIONS = (
    "First Lien", "Revolver", "Delayed Draw", "Second Lien",
    "Subordinated Debt", "Unsecured Debt", "Mezzanine",
    "Common Equity", "Preferred Equity", "Partnership Interest",
    "Warrants", "CLO", "Structured Note", "Money Market Fund",
    "Other Debt", "Other",
)

#: Standard industry buckets the model must choose from
INDUSTRY_OPTIONS = (
    "Software", "Healthcare Services", "Pharmaceuticals & Biotechnology",
    "Medical Devices & Equipment", "Healthcare Technology", "Financial Services",
    "Insurance", "Real Estate", "Business Services", "Manufacturing",
    "Industrial Services", "Aerospace & Defense", "Automotive",
    "Construction & Engineering", "Chemicals", "Metals & Mining",
    "Media & Entertainment", "Marketing & Advertising", "Education",
    "Legal Services", "Consumer Products", "Food & Beverage", "Retail",
    "Apparel & Textiles", "Hotels & Leisure", "Transportation & Logistics",
    "Airlines", "Oil & Gas", "Energy & Utilities", "Energy Services",
    "Packaging", "Environmental Services", "Telecommunications",
    "Technology Hardware", "Internet & Media", "Investment Vehicles",
    "Diversified", "Agriculture", "Other",
)

#: Standard reference rate tokens
REFERENCE_RATE_OPTIONS = (
    "SOFR", "SOFR (Q)", "SOFR (M)", "SOFR (S)",
    "LIBOR", "LIBOR (Q)", "LIBOR (M)",
    "Euribor", "Prime", "Prime (Q)", "SONIA", "CDOR", "BKBM", "Fixed", "N/A",
)

#: Output CSV columns – must match the pipeline standard exactly
OUTPUT_COLUMNS = [
    "company_name", "investment_type", "industry",
    "cash_rate", "pik_rate", "reference_rate", "spread", "floor_rate",
    "acquisition_date", "maturity_date",
    "principal_amount", "amortized_cost", "fair_value",
    "percent_of_net_assets", "cost",
    "commitment_limit", "undrawn_commitment",
    "rate_cap", "shares",
    "data_quality_flags", "ticker", "filing_date",
]

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class InvestmentRow(BaseModel):
    """A single investment position from a BDC Schedule of Investments."""

    company_name: str = Field(
        description=(
            "Portfolio company name. Replace any commas with spaces "
            "(e.g. 'Douglas Holdings, Inc.' → 'Douglas Holdings Inc'). Required."
        )
    )
    investment_type: str = Field(
        description=(
            "Instrument type. Must be one of: "
            + ", ".join(f'"{t}"' for t in INVESTMENT_TYPE_OPTIONS)
            + ". Infer from section header if the column is missing."
        )
    )
    industry: Optional[str] = Field(
        default=None,
        description=(
            "Business sector. Must be one of: "
            + ", ".join(f'"{i}"' for i in INDUSTRY_OPTIONS)
            + ". Fill down from the row above if blank."
        ),
    )
    cash_rate: Optional[str] = Field(
        default=None,
        description="Cash interest rate as a percentage string, e.g. '8.5%'. Blank if none.",
    )
    pik_rate: Optional[str] = Field(
        default=None,
        description="PIK rate as a percentage string, e.g. '2.0%'. Blank if none.",
    )
    reference_rate: Optional[str] = Field(
        default=None,
        description=(
            "Floating rate base. Standardize to one of: "
            + ", ".join(f'"{r}"' for r in REFERENCE_RATE_OPTIONS)
            + ". Use 'Fixed' for fixed-rate instruments, 'N/A' for equity."
        ),
    )
    spread: Optional[str] = Field(
        default=None,
        description="Spread over reference rate, e.g. '5.25%'.",
    )
    floor_rate: Optional[str] = Field(
        default=None,
        description="Interest rate floor, e.g. '1.0%'. Blank if not stated.",
    )
    acquisition_date: Optional[str] = Field(
        default=None,
        description="Date of investment in YYYY-MM-DD format. Blank if not available.",
    )
    maturity_date: Optional[str] = Field(
        default=None,
        description="Loan maturity date in YYYY-MM-DD format. Blank if not available or equity.",
    )
    principal_amount: Optional[str] = Field(
        default=None,
        description=(
            "Loan par / principal amount as a plain number (no $ or commas), "
            "in the unit scale given. Blank for equity."
        ),
    )
    amortized_cost: Optional[str] = Field(
        default=None,
        description="Book / amortized cost as a plain number. Blank if not shown.",
    )
    fair_value: Optional[str] = Field(
        default=None,
        description="Fair value as a plain number.",
    )
    percent_of_net_assets: Optional[str] = Field(
        default=None,
        description="% of net assets as a plain number (e.g. '2.5' not '2.5%').",
    )
    cost: Optional[str] = Field(
        default=None,
        description="Cost basis as a plain number. Use when separate from amortized_cost.",
    )
    commitment_limit: Optional[str] = Field(
        default=None,
        description="Total commitment / facility size as a plain number.",
    )
    undrawn_commitment: Optional[str] = Field(
        default=None,
        description="Unfunded / undrawn commitment as a plain number.",
    )


class ExtractedSOI(BaseModel):
    """Structured output from one table-chunk extraction call."""

    rows: List[InvestmentRow] = Field(
        description=(
            "Investment rows extracted from the table. "
            "Empty list if the table contains no investment positions "
            "(e.g. it's a totals-only table or footnote table)."
        )
    )
    extraction_notes: Optional[str] = Field(
        default=None,
        description=(
            "Brief note about any extraction challenges, ambiguous columns, "
            "or assumptions made. Optional."
        ),
    )


# ---------------------------------------------------------------------------
# DSPy Signature
# ---------------------------------------------------------------------------

class ExtractSOIRows(dspy.Signature):
    """Extract Schedule of Investments rows from a BDC SEC filing table.

    You are reading a raw table from a 10-Q or 10-K SEC filing.  The table
    lists portfolio company investments held by a Business Development Company
    (BDC).

    EXTRACTION RULES
    ----------------
    1. **Fill-down company_name and industry**: when a row continues a previous
       company (e.g. a second tranche or revolver), the company_name column may
       be blank.  Copy it from the row above.  Same for industry.
    2. **Never fill-down numeric or rate fields**: each row's rates, amounts and
       dates must come only from that specific row's cells.
    3. **Skip non-data rows**: skip column headers, section headers that have no
       company, subtotal rows (containing "Total" or "Subtotal"), footnotes,
       and blank rows.
    4. **investment_type from section context**: if the table has no explicit
       type column, use the section_context (e.g. "Senior Secured First Lien
       Debt") to set investment_type for all rows in that section.
    5. **Standardize values**: use the allowed lists from the field descriptions
       for investment_type, industry, and reference_rate.
    6. **No commas inside field values**: replace internal commas with spaces in
       company names and other text fields.
    7. **Numbers only**: strip $, commas, and % from numeric fields; keep % in
       rate fields (cash_rate, pik_rate, spread, floor_rate).
    8. **Dates**: convert to YYYY-MM-DD; leave blank if ambiguous.
    9. **Comparative period columns**: 10-K filings often show two date columns
       side-by-side, e.g. "December 31, 2024" (current) and "December 31, 2023"
       (prior year).  Extract ONLY the current period data (matching
       ``filing_period``).  Do NOT create a second row for the prior-year column.
       For each instrument, produce exactly ONE row using the current-period values.
    """

    table_text: str = dspy.InputField(
        desc=(
            "Raw table text: one row per line, cells tab-separated. "
            "First line is the column header."
        )
    )
    section_context: str = dspy.InputField(
        desc=(
            "Section header text immediately preceding this table in the filing, "
            "e.g. 'Senior Secured First Lien Debt' or 'Common Equity Investments'. "
            "Use this to infer investment_type when the column is absent."
        )
    )
    filing_period: str = dspy.InputField(
        desc=(
            "The reporting period-end date this filing covers, in YYYY-MM-DD format "
            "(e.g. '2024-12-31' for an annual 10-K).  When the table has multiple "
            "date columns, extract ONLY the column for this period."
        )
    )
    unit_scale: str = dspy.InputField(
        desc=(
            "Dollar unit scale: 'thousands' means numbers are in $000s, "
            "'millions' means $M, 'units' means raw dollars. "
            "Extract numbers exactly as shown – do NOT multiply or divide."
        )
    )

    result: ExtractedSOI = dspy.OutputField(
        desc="Structured extraction result containing investment rows."
    )


# ---------------------------------------------------------------------------
# DSPy Module
# ---------------------------------------------------------------------------

class InvestmentTableExtractor(dspy.Module):
    """DSPy module wrapping the ExtractSOIRows signature.

    Uses ``dspy.ChainOfThought`` (DSPy 3.x) for a reasoning step before the
    structured JSON output, which helps with unusual table layouts.
    """

    def __init__(self, use_chain_of_thought: bool = True):
        super().__init__()
        # DSPy 3.x: ChainOfThought / Predict work with Pydantic output fields directly
        if use_chain_of_thought:
            self.extract = dspy.ChainOfThought(ExtractSOIRows)
        else:
            self.extract = dspy.Predict(ExtractSOIRows)

    def forward(
        self,
        table_text: str,
        section_context: str,
        filing_period: str,
        unit_scale: str,
    ) -> ExtractedSOI:
        prediction = self.extract(
            table_text=table_text,
            section_context=section_context,
            filing_period=filing_period,
            unit_scale=unit_scale,
        )
        return prediction.result


# ---------------------------------------------------------------------------
# Main Scraper
# ---------------------------------------------------------------------------

class DSPyTableScraper:
    """DSPy-powered Schedule of Investments scraper.

    Fetches HTML filings from SEC EDGAR, detects SOI tables via the existing
    heuristics, then runs each table chunk through a DSPy TypedChainOfThought
    module to produce Pydantic-typed rows, which are saved as standard pipeline
    CSV files.

    Parameters
    ----------
    gemini_api_key:
        Google Gemini API key.  Falls back to ``GOOGLE_API_KEY`` env var.
    gemini_model:
        litellm-style model string for DSPy.  Default: ``"gemini/gemini-2.0-flash"``.
    output_dir:
        Directory where per-filing CSV files are written.
    data_dir:
        Directory used by :class:`SECAPIClient` for caching.
    use_chain_of_thought:
        Whether to use ``TypedChainOfThought`` (adds reasoning step, recommended)
        or ``TypedPredictor`` (faster, fewer tokens).
    rows_per_chunk:
        Max table rows per DSPy call.  Large tables are split automatically.
    """

    ROWS_PER_CHUNK = 80  # rows per DSPy call (keeps JSON output manageable)

    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        gemini_model: str = "gemini/gemini-2.0-flash",
        output_dir: str = "output",
        data_dir: str = "data",
        use_chain_of_thought: bool = True,
        rows_per_chunk: int = 80,
    ):
        if not DSPY_AVAILABLE:
            raise ImportError(
                "dspy-ai is not installed. Run: pip install dspy-ai"
            )

        # Configure DSPy LM
        api_key = gemini_api_key or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "Gemini API key required. Set GOOGLE_API_KEY env var or pass gemini_api_key."
            )

        lm = dspy.LM(
            gemini_model,
            api_key=api_key,
            temperature=0.1,
            max_tokens=16000,
        )
        dspy.configure(lm=lm)
        logger.info("DSPy configured with model: %s", gemini_model)

        self.extractor = InvestmentTableExtractor(
            use_chain_of_thought=use_chain_of_thought
        )
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rows_per_chunk = rows_per_chunk

        # Reuse LLMTableScraper for table detection / cleaning (LLM disabled)
        self._html_helper = LLMTableScraper(
            use_llm=False,
            data_dir=data_dir,
            output_dir=output_dir,
        )
        self.sec_client = SECAPIClient(data_dir=data_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scrape_ticker(
        self,
        ticker: str,
        filing_types: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_filings: Optional[int] = None,
        force: bool = False,
    ) -> List[str]:
        """Scrape all filings for *ticker* and write per-filing CSV files.

        Parameters
        ----------
        ticker:
            BDC ticker symbol (e.g. ``"BXSL"``).
        filing_types:
            List of SEC form types to process.  Defaults to ``["10-Q", "10-K"]``.
        start_date:
            Earliest filing date to process (``YYYY-MM-DD``).
        end_date:
            Latest filing date to process.  Defaults to today.
        max_filings:
            Cap on number of filings per form type (useful for testing).
        force:
            Re-extract even if output CSV already exists.  When False (default)
            existing files are skipped.

        Returns
        -------
        list[str]
            Paths of CSV files written.
        """
        from datetime import date as date_cls

        if filing_types is None:
            filing_types = ["10-Q", "10-K"]

        end_date = end_date or date_cls.today().isoformat()
        output_paths: List[str] = []

        for form in filing_types:
            if form == "10-Q":
                filings = self.sec_client.get_historical_10q_filings(
                    ticker, start_date=start_date, end_date=end_date, years_back=10
                )
            else:
                filings = self.sec_client.get_historical_10k_filings(
                    ticker, start_date=start_date, end_date=end_date, years_back=10
                )

            if max_filings:
                filings = filings[:max_filings]

            logger.info("Found %d %s filings for %s", len(filings), form, ticker)

            for filing_info in filings:
                period = filing_info["date"]
                expected_path = self.output_dir / f"{ticker}_investments_{period}.csv"
                if not force and expected_path.exists():
                    logger.info("Skipping %s %s — output already exists", ticker, period)
                    output_paths.append(str(expected_path))
                    continue

                try:
                    rows = self._scrape_filing(ticker, filing_info)
                    if rows:
                        out_path = self._save_csv(ticker, filing_info, rows)
                        output_paths.append(str(out_path))
                        logger.info(
                            "Saved %d rows to %s", len(rows), out_path
                        )
                    else:
                        logger.warning(
                            "No rows extracted for %s %s", ticker, filing_info["date"]
                        )
                except Exception:
                    logger.exception(
                        "Error scraping %s %s", ticker, filing_info["date"]
                    )

        return output_paths

    # ------------------------------------------------------------------
    # Filing-level extraction
    # ------------------------------------------------------------------

    def _scrape_filing(
        self,
        ticker: str,
        filing_info: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """Extract investment rows from one filing. Returns list of row dicts."""
        logger.info(
            "Scraping %s %s filed %s",
            ticker, filing_info["form"], filing_info["date"],
        )

        # 1. Fetch HTML filing
        filing_result = self.sec_client.fetch_filing_by_index_url(
            index_url=filing_info["index_url"],
            ticker=ticker,
            filing_type=filing_info["form"],
            save_to_file=False,
            main_document_only=True,
        )
        if not filing_result:
            logger.warning("Could not fetch filing for %s %s", ticker, filing_info["date"])
            return []

        # 2. Extract all HTML tables from the filing documents
        tables = self._html_helper.extract_tables_from_filing_documents(filing_result)
        if not tables:
            logger.warning("No tables found in %s %s", ticker, filing_info["date"])
            return []
        logger.info("Found %d raw tables", len(tables))

        # 3. Identify Schedule of Investments tables
        period_end = filing_info.get("period_end_date")
        soi_tables = self._html_helper.find_investment_schedule_tables(
            tables, filing_result, filing_info["form"], period_end
        )

        # Fallback: content-based detection if header-based finds nothing.
        # For 10-K filings where header-based finds only 1-2 tables but the filing has
        # many tables: the SOI is often split across dozens of per-page tables.
        # We use the header-found table as an anchor — only search for SOI tables at
        # or after that position to avoid picking up earlier summary/narrative tables.
        _try_fallback = (
            not soi_tables
            or (filing_info["form"] == "10-K" and len(soi_tables) <= 2 and len(tables) > 20)
        )
        if _try_fallback:
            if soi_tables:
                logger.info(
                    "10-K with only %d header-detected table(s); also trying content-based fallback",
                    len(soi_tables),
                )
                # Anchor: only consider tables from the first header-detected table onwards
                anchor_num = min(t[2] for t in soi_tables)
                candidate_tables = [(h, t, n, tid) for (h, t, n, tid) in tables if n >= anchor_num]
                logger.info(
                    "Anchoring fallback at table %d (%d candidate tables)",
                    anchor_num, len(candidate_tables),
                )
            else:
                logger.info("Falling back to content-based SOI table detection")
                candidate_tables = tables
            fallback = fallback_table_detection(
                candidate_tables,
                filing_date=filing_result.filing_date,
                filing_type=filing_info["form"],
                period_end_date=period_end,
            )
            if len(fallback) > len(soi_tables):
                logger.info(
                    "Using fallback (%d tables) over header-based (%d tables)",
                    len(fallback), len(soi_tables),
                )
                soi_tables = fallback

        if not soi_tables:
            logger.warning("No SOI tables found in %s %s", ticker, filing_info["date"])
            return []

        logger.info("Using %d SOI table(s)", len(soi_tables))

        # 4. Detect dollar unit scale (thousands / millions / units)
        unit_scale = self._html_helper._detect_unit_scale(soi_tables, filing_result)

        # 5. Build section-context strings for each table
        section_contexts = self._build_section_contexts(soi_tables, filing_result)

        # 6. Run DSPy extraction on each table (chunked)
        all_rows: List[Dict[str, str]] = []

        for idx, (table_html, table_text, table_num, table_id) in enumerate(soi_tables):
            section_ctx = section_contexts.get(idx, "")
            logger.info(
                "Extracting table %d (section: '%s')", table_num, section_ctx[:80]
            )

            chunks = self._chunk_table(table_text)

            # Fill-down state is reset per table: company names may not carry across
            # page-break sub-tables (especially in 10-K filings where the large SOI
            # is split at arbitrary page boundaries).
            _prev_company: Optional[str] = None
            _prev_industry: Optional[str] = None

            for chunk_idx, chunk_text in enumerate(chunks):
                if len(chunks) > 1:
                    logger.info("  Chunk %d / %d", chunk_idx + 1, len(chunks))

                try:
                    extracted: ExtractedSOI = self.extractor(
                        table_text=chunk_text,
                        section_context=section_ctx or "Unknown section",
                        filing_period=period_end or filing_result.filing_date,
                        unit_scale=unit_scale,
                    )
                except Exception:
                    logger.exception(
                        "DSPy extraction failed for table %d chunk %d",
                        table_num, chunk_idx + 1,
                    )
                    continue

                if not (extracted and extracted.rows):
                    if extracted and extracted.extraction_notes:
                        logger.info(
                            "  No rows (note: %s)", extracted.extraction_notes
                        )
                    continue

                logger.info(
                    "  Extracted %d rows from chunk %d",
                    len(extracted.rows), chunk_idx + 1,
                )

                # Apply fill-down within a table/chunk for company / industry
                for row in extracted.rows:
                    if row.company_name:
                        _prev_company = row.company_name
                        _prev_industry = row.industry or _prev_industry
                    else:
                        # Row has blank company – fill from previous within this table
                        if _prev_company:
                            row.company_name = _prev_company
                        if not row.industry and _prev_industry:
                            row.industry = _prev_industry

                    all_rows.append(
                        self._row_to_dict(row, ticker, filing_result.filing_date)
                    )

        # --- Post-extraction cleanup ---

        # 1. Filter rows with no meaningful financial data.
        #    JV subsidiary portfolio tables and comparative-period tables often produce
        #    rows that have only a principal_amount (commitment) but no fair_value and
        #    no rate data. Exclude them to avoid polluting the output.
        _RATE_FIELDS = ("cash_rate", "spread", "pik_rate", "reference_rate")
        filtered: List[Dict[str, str]] = []
        for r in all_rows:
            has_value = r.get("fair_value") or r.get("amortized_cost")
            has_rate = any(r.get(f) for f in _RATE_FIELDS)
            has_principal = r.get("principal_amount")
            if has_value or (has_rate and has_principal):
                filtered.append(r)
            else:
                logger.debug(
                    "Dropping data-poor row (no fv/cost, no rate+principal): %s",
                    r.get("company_name", ""),
                )
        dropped = len(all_rows) - len(filtered)
        if dropped:
            logger.info("Filtered %d data-poor rows (JV/comparative tables)", dropped)

        # 2. Dedup rows that appear identically across continuation sub-tables.
        #    Key: (company_name, investment_type, fair_value, principal_amount).
        #    Different tranches for the same company will have different amounts so
        #    they are correctly kept.
        seen_keys: set = set()
        deduped: List[Dict[str, str]] = []
        for r in filtered:
            key = (
                r.get("company_name", "").strip().lower(),
                r.get("investment_type", "").strip().lower(),
                r.get("fair_value", "").strip(),
                r.get("principal_amount", "").strip(),
            )
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(r)
        dupes = len(filtered) - len(deduped)
        if dupes:
            logger.info("Removed %d duplicate rows from continuation tables", dupes)

        # 3. Normalize all monetary fields to thousands (pipeline standard).
        #    Gemini extracts numbers exactly as shown, so we convert here.
        _MONETARY_FIELDS = (
            "principal_amount", "amortized_cost", "fair_value",
            "cost", "commitment_limit", "undrawn_commitment",
        )
        if unit_scale == "millions":
            # filing shows values in $M → multiply by 1000 to get $000s
            factor = 1000.0
        elif unit_scale == "units":
            # filing shows raw dollars → divide by 1000 to get $000s
            factor = 0.001
        else:
            factor = None  # already in thousands, no-op

        if factor is not None:
            logger.info(
                "Scaling monetary fields by %.4f (unit_scale=%s → thousands)",
                factor, unit_scale,
            )
            for r in deduped:
                for field in _MONETARY_FIELDS:
                    val = r.get(field, "").strip()
                    if val:
                        try:
                            r[field] = str(round(float(val) * factor, 4))
                        except ValueError:
                            pass

        return deduped

    # ------------------------------------------------------------------
    # Section-context helpers
    # ------------------------------------------------------------------

    def _build_section_contexts(
        self,
        tables: List[Tuple[str, str, int, str]],
        filing_result: Any,
    ) -> Dict[int, str]:
        """Return a dict mapping table-index → section header string."""
        contexts: Dict[int, str] = {}
        for i, (table_html, table_text, _, _) in enumerate(tables):
            contexts[i] = self._infer_section_context(table_html, table_text)
        return contexts

    def _infer_section_context(self, table_html: str, table_text: str) -> str:
        """Extract a section-type context string from the table or its rows.

        Looks first at the first few rows of the table for section-like content
        (short cells with investment-type keywords, usually spanning many columns).
        Falls back to scanning the first lines of the plain-text rendering.
        """
        from bs4 import BeautifulSoup

        _SECTION_KEYWORDS = (
            "first lien", "second lien", "senior secured", "subordinated",
            "common equity", "preferred equity", "warrant", "mezzanine",
            "unsecured", "revolver", "delayed draw", "unitranche", "clo",
            "structured", "junior", "debt investment", "equity investment",
        )

        try:
            soup = BeautifulSoup(table_html, "html.parser")
            for tr in soup.find_all("tr")[:5]:
                text = tr.get_text(" ", strip=True)
                if len(text) < 120 and any(kw in text.lower() for kw in _SECTION_KEYWORDS):
                    return text
        except Exception:
            pass

        # Fallback: scan first few plain-text lines
        for line in table_text.strip().splitlines()[:6]:
            stripped = line.strip()
            # Heuristic: a section header is short and has no tab separators
            if stripped and len(stripped) < 120 and "\t" not in stripped:
                if any(kw in stripped.lower() for kw in _SECTION_KEYWORDS):
                    return stripped

        return ""

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _chunk_table(self, table_text: str) -> List[str]:
        """Split a large table-text string into chunks of at most ROWS_PER_CHUNK rows.

        The header line is prepended to every chunk so each chunk is self-contained.
        """
        lines = table_text.strip().splitlines()
        if not lines:
            return [table_text]

        header = lines[0]
        data_lines = lines[1:]

        if len(data_lines) <= self.rows_per_chunk:
            return [table_text]

        chunks = []
        for start in range(0, len(data_lines), self.rows_per_chunk):
            chunk_lines = [header] + data_lines[start : start + self.rows_per_chunk]
            chunks.append("\n".join(chunk_lines))

        logger.info(
            "Chunked %d-row table into %d chunks of ≤%d rows",
            len(data_lines), len(chunks), self.rows_per_chunk,
        )
        return chunks

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_num(s: Optional[str]) -> str:
        """Strip $, commas, and whitespace from a numeric string (model sometimes ignores schema instructions)."""
        if not s:
            return ""
        return s.replace(",", "").replace("$", "").strip()

    def _row_to_dict(
        self, row: InvestmentRow, ticker: str, filing_date: str
    ) -> Dict[str, str]:
        """Convert a typed InvestmentRow to a flat string dict matching OUTPUT_COLUMNS."""
        sn = self._strip_num
        return {
            "company_name": row.company_name or "",
            "investment_type": row.investment_type or "",
            "industry": row.industry or "",
            "cash_rate": row.cash_rate or "",
            "pik_rate": row.pik_rate or "",
            "reference_rate": row.reference_rate or "",
            "spread": row.spread or "",
            "floor_rate": row.floor_rate or "",
            "acquisition_date": row.acquisition_date or "",
            "maturity_date": row.maturity_date or "",
            "principal_amount": sn(row.principal_amount),
            "amortized_cost": sn(row.amortized_cost),
            "fair_value": sn(row.fair_value),
            "percent_of_net_assets": row.percent_of_net_assets or "",
            "cost": sn(row.cost),
            "commitment_limit": sn(row.commitment_limit),
            "undrawn_commitment": sn(row.undrawn_commitment),
            "rate_cap": "",
            "shares": "",
            "data_quality_flags": "dspy_extracted",
            "ticker": ticker,
            "filing_date": filing_date,
        }

    def _save_csv(
        self,
        ticker: str,
        filing_info: Dict[str, Any],
        rows: List[Dict[str, str]],
    ) -> Path:
        """Write rows to a CSV file and return its path.

        Uses the standard pipeline naming ``{ticker}_investments_{period}.csv``
        so consolidation picks it up automatically.
        """
        period = filing_info["date"]
        out_path = self.output_dir / f"{ticker}_investments_{period}.csv"

        if out_path.exists():
            out_path.unlink()  # force clean overwrite

        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(rows)

        return out_path
