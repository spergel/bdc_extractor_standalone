import logging
import re
from datetime import datetime
from typing import Any, List, Optional, Tuple


logger = logging.getLogger(__name__)

Table = Tuple[str, str, int, str]  # (table_html, table_text, table_num, table_id)


def is_investment_table(table_text: str) -> bool:
    """
    Heuristic: determine if a table is an investment table based on content.
    Uses the same expanded criteria that were previously embedded in
    LLMTableScraper._is_investment_table.
    """
    # Primary indicators (any of these strongly suggests investment table)
    primary_indicators = [
        "portfolio company",
        "company name",
        "portfolio company name",
        "investment company",
        "issuer name",
        "issuer",
        "borrower",
        "obligor",
        "schedule of investments",
        "investments at fair value",
    ]

    # Secondary indicators (need multiple)
    secondary_indicators = [
        "principal",
        "fair value",
        "amortized cost",
        "interest rate",
        "maturity",
        "acquisition date",
        "senior secured",
        "first lien",
        "common equity",
        "preferred equity",
        "spread",
        "reference rate",
        "sofr",
        "libor",
    ]

    text = table_text.lower()

    has_primary = any(indicator in text for indicator in primary_indicators)
    secondary_count = sum(1 for indicator in secondary_indicators if indicator in text)
    has_secondary = secondary_count >= 2

    has_financial_data = (
        ("$" in text or "percent" in text or "%" in text)
        and any(term in text for term in ["llc", "inc.", "corp", "ltd", "lp", "holdings", "acquisition"])
    )

    is_investment = has_primary or (has_secondary and has_financial_data)

    if is_investment:
        logger.debug(
            "Table identified as investment table "
            f"(primary={has_primary}, secondary_count={secondary_count}, financial={has_financial_data})"
        )

    return is_investment


def is_excluded_non_schedule_table(table_text: str) -> bool:
    """
    Exclude tables that mention investments but are NOT the schedule of investments:
    - Cash flow statement (operating/financing/investing activities)
    - Unrealized appreciation/depreciation summary (portfolio company + three/nine months columns)
    - Fair value hierarchy disclosure (Level 1 / Level 2 / Level 3 breakdown)
    - Investment activity rollforward (purchases, sales/payoffs, transfers by period)
    """
    text = table_text.lower()
    # Cash flow statement
    if "cash flows from operating activities" in text and "net cash provided by" in text:
        return True
    if "net cash provided by (used for) operating activities" in text:
        return True
    # Unrealized appreciation summary (rollforward by portfolio company, not the schedule)
    if "net change in unrealized appreciation" in text or "net change in unrealized depreciation" in text:
        if "for the three months ended" in text and "for the nine months ended" in text:
            if "($ in millions)" in text or "portfolio company" in text:
                return True
    # Fair value hierarchy tables — have Level 1/2/3 structure but duplicate all investment names
    if "level 1" in text and "level 2" in text and "level 3" in text:
        return True
    # Investment activity rollforward (10-K: purchases/sales schedule, not current holdings)
    if ("purchases" in text and ("sales or payoffs" in text or "pay-offs" in text)
            and ("beginning of period" in text or "end of period" in text)):
        return True
    return False


def is_year_end_table(
    table_html: str,
    table_text: str,
    filing_date: str,
    context_before: str = "",
    filing_type: str = "10-Q",
    period_end_date: Optional[str] = None,
) -> bool:
    """
    Check if this table is ONLY prior year-end (so we should skip it).

    We want ONLY current quarter's holdings; filings often have two blocks:
    (1) current quarter tables, (2) prior year-end comparative tables.
    Return True only when we're sure this table is in block (2).

    - If the table mentions the CURRENT PERIOD date anywhere → KEEP (return False).
    - If it only has December 31 of a PRIOR year (no current period) → SKIP (return True).
    - 10-K: apply same logic (10-Ks can include a full comparative SOI for the prior year).
    """

    try:
        # Parse the period end date (the actual date this filing covers)
        if period_end_date:
            period_dt = datetime.strptime(period_end_date, "%Y-%m-%d")
            period_year = period_dt.year
            period_month = period_dt.month
            period_day = period_dt.day
        else:
            filing_dt = datetime.strptime(filing_date, "%Y-%m-%d")
            period_year = filing_dt.year
            period_month = filing_dt.month
            period_day = filing_dt.day

        # Combine table content with context (header often has the date)
        text_lower = (table_text + " " + (context_before or "")).lower()
        html_lower = table_html.lower()
        combined_text = text_lower + " " + html_lower
        combined_text = re.sub(r"[\xa0\u2013\u2014\u00a0]", " ", combined_text)

        # Use a LARGE window so we see both current and prior-year dates in comparative tables.
        # Q4 tables often have "December 31, 2025" and "December 31, 2024" - we must keep them.
        # Search full table for current period (avoids dropping main schedule when date is late in header).
        search_text = combined_text[:2500]
        search_text_full = combined_text[:8000]  # wider window for current-period check

        month_names = {
            1: "january",
            2: "february",
            3: "march",
            4: "april",
            5: "may",
            6: "june",
            7: "july",
            8: "august",
            9: "september",
            10: "october",
            11: "november",
            12: "december",
        }
        period_month_name = month_names.get(period_month, "")

        # 1) CURRENT PERIOD: if we see the filing's period date, this is current → KEEP
        #
        # For 10-K filings (and any Dec 31 period-end), bare date matches are unreliable
        # because maturity dates in the SOI often fall on December 31 of the current year.
        # Require the date to appear in "as of" or "at" context (table-period marker).
        # For 10-Q filings with non-year-end periods, bare date matches are fine since
        # maturity dates rarely coincide with e.g. September 30 or June 30.
        as_of_patterns = [
            rf"as\s+of\s+{re.escape(period_month_name)}\s+{period_day}[,\s]+{period_year}",
            rf"at\s+{re.escape(period_month_name)}\s+{period_day}[,\s]+{period_year}",
        ]
        bare_date_patterns = [
            rf"{re.escape(period_month_name)}\s+{period_day}[,\s]+{period_year}",
            rf"{period_month_name[:3]}[.]?\s+{period_day}[,\s]+{period_year}",
            rf"\b{period_month}/{period_day}/{period_year}\b",
            rf"\b{period_month:02d}/{period_day:02d}/{period_year}\b",
            rf"\b{period_year}-{period_month:02d}-{period_day:02d}\b",
        ]
        # "As of" patterns are always reliable
        for pattern in as_of_patterns:
            if re.search(pattern, search_text_full, re.I):
                return False  # current period -> keep
        # Bare date patterns: reliable only for non-Dec-31 periods
        # (Dec 31 maturity dates are common and would cause false positives)
        use_bare = (period_month != 12 or period_day != 31)
        if use_bare:
            for pattern in bare_date_patterns:
                if re.search(pattern, search_text_full, re.I):
                    return False  # current period -> keep

        # 2) PRIOR YEAR-END: only skip if we see Dec 31 of a prior year AND we did not see current period above
        dec31_match = re.search(r"(?:december|dec\.?)\s+31[,\s]+(\d{4})", search_text, re.I)
        if dec31_match:
            year_in_table = int(dec31_match.group(1))
            if year_in_table < period_year:
                logger.info(
                    "Detected prior year-end table: December 31, %s (no current period date in table)",
                    year_in_table,
                )
                return True

        dec31_numeric = re.search(r"\b12[/-]31[/-](\d{4})\b", search_text)
        if dec31_numeric:
            year_in_table = int(dec31_numeric.group(1))
            if year_in_table < period_year:
                logger.info(
                    "Detected prior year-end table: 12/31/%s (no current period date in table)",
                    year_in_table,
                )
                return True

        # Unclear → keep (don't skip)
        return False

    except Exception as e:
        logger.debug("Error checking year-end status: %s", e)
        return False


def fallback_table_detection(
    tables: List[Table],
    filing_date: Optional[str] = None,
    filing_type: str = "10-Q",
    period_end_date: Optional[str] = None,
) -> List[Table]:
    """
    Simple content-based detection with expanded criteria.

    Uses is_investment_table + is_year_end_table to decide which tables to keep.
    """
    investment_tables: List[Table] = []
    for html, text, table_num, table_id in tables:
        text_lower = text.lower()
        if not is_investment_table(text_lower):
            continue
        if is_excluded_non_schedule_table(text_lower):
            logger.info("Fallback: Skipping non-schedule table %s (cash flow or unrealized summary)", table_num)
            continue

        # Filter out prior-year-end tables for 10-Q if we have a filing date
        if filing_date and is_year_end_table(html, text, filing_date, "", filing_type, period_end_date):
            logger.info("Fallback: Skipping year-end table %s", table_num)
            continue

        investment_tables.append((html, text, table_num, table_id))
        logger.info("Fallback: Added table %s with investment content", table_num)

    return investment_tables


def select_current_quarter_tables(
    tables: List[Table],
    filing_date: str,
    filing_type: str,
    period_end_date: Optional[str] = None,
) -> Tuple[List[Table], bool]:
    """
    Given a list of candidate investment tables (already filtered by content),
    apply the year-end transition rule:

    - For 10-Q: walk in order, keep tables until we hit the first prior year-end table.
      Everything after that is treated as prior year-end.
    - For 10-K: keep everything.

    Returns:
        (current_quarter_tables, year_end_transition_found)
    """
    if filing_type != "10-Q":
        return list(tables), False

    current_quarter_tables: List[Table] = []
    year_end_transition_found = False

    for i, (html, text, num, table_id) in enumerate(tables):
        if is_year_end_table(html, text, filing_date, "", filing_type, period_end_date):
            logger.info("🛑 Year-end transition detected at table %s (%s)", num, table_id)
            logger.info("   Stopping here - all remaining %s tables are year-end", len(tables) - i)
            year_end_transition_found = True
            break
        current_quarter_tables.append((html, text, num, table_id))

    if not year_end_transition_found:
        logger.info("✅ No year-end transition detected - all tables are current period")
    else:
        logger.info("✅ Identified %s current quarter tables (before year-end)", len(current_quarter_tables))

    if not current_quarter_tables:
        logger.warning("No current quarter tables found. Using all tables as fallback.")
        return list(tables), year_end_transition_found

    return current_quarter_tables, year_end_transition_found
