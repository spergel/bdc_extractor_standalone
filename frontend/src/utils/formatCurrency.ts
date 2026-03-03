/**
 * Standard currency formatting for the app. Single source of truth for dollar display.
 *
 * USE THESE EVERYWHERE for currency so numbers match across views:
 * - formatThousandsAsCurrency(n) — when the value is in THOUSANDS (holdings, analytics).
 * - formatMillionsAsCurrency(n)  — when the value is in MILLIONS (exposures, company_detail, sectors).
 *
 * DATA UNITS (do not mix):
 * | Data / view              | Unit     | Example raw value | Display              |
 * |--------------------------|----------|-------------------|----------------------|
 * | Holdings (fair_value, etc.) | THOUSANDS | 8750            | formatThousandsAsCurrency → "$8.75 M" |
 * | Analytics total FV, charts  | THOUSANDS | 1631944         | formatThousandsAsCurrency → "$1,631.94 M" |
 * | company_exposures.total_exposure_millions | MILLIONS | 608   | formatMillionsAsCurrency → "$608.00 M" |
 * | company_detail (by_bdc, by_maturity, etc.) | MILLIONS | 50.5 | formatMillionsAsCurrency → "$50.50 M" |
 * | Sectors sidebar exposure    | MILLIONS (sum of total_exposure_millions) | 1200 | formatMillionsAsCurrency → "$1.20 B" |
 * | portfolio_summaries.*_millions | MILLIONS | —            | formatMillionsAsCurrency |
 *
 * CSV/backend: investment CSVs must emit fair_value, principal_amount, cost, amortized_cost in THOUSANDS
 * so the frontend (adapter + these formatters) shows correct $M/$B everywhere.
 */

/** Format a value stored in THOUSANDS as display string in MILLIONS: "$0.50 M", "$50.00 M", "$1,631.94 M" */
export function formatThousandsAsCurrency(thousands: number): string {
  if (typeof thousands !== 'number' || Number.isNaN(thousands)) return '—';
  const sign = thousands < 0 ? '-' : '';
  const millions = Math.abs(thousands) / 1_000;
  return `${sign}$${millions.toFixed(2)} M`;
}

/** Format a value already in MILLIONS (company_exposures, company_detail, portfolio_summaries, sectors) as "$1.50 M", "$1.20 B" */
export function formatMillionsAsCurrency(millions: number): string {
  if (typeof millions !== 'number' || Number.isNaN(millions)) return '—';
  const abs = Math.abs(millions);
  const sign = millions < 0 ? '-' : '';
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(2)} B`;
  return `${sign}$${abs.toFixed(2)} M`;
}

/** Column header suffix for dollar amounts that are displayed in millions */
export const CURRENCY_M_LABEL = 'Fair value ($M)';

/** Column header suffix when the column is "% of portfolio" or "% NAV" */
export const PCT_NAV_LABEL = '% of portfolio';
