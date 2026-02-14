"""Table detection for schedule-of-investments in SEC filings."""

from .detection import (
    Table,
    fallback_table_detection,
    is_excluded_non_schedule_table,
    is_investment_table,
    is_year_end_table,
    select_current_quarter_tables,
)

__all__ = [
    "Table",
    "fallback_table_detection",
    "is_excluded_non_schedule_table",
    "is_investment_table",
    "is_year_end_table",
    "select_current_quarter_tables",
]
