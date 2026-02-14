/**
 * Standard currency formatting for the app.
 *
 * CONVENTION:
 * - Holdings and company_detail store amounts in THOUSANDS (e.g. fair_value = 50000 means $50M).
 * - We display everything as millions ($M) or billions ($B) with an explicit unit so it's never ambiguous.
 * - Use these helpers everywhere so labels stay consistent.
 */

/** Format a value stored in thousands as display string with explicit unit: "$1.5 M", "$500 M", "$1.2 B" */
export function formatThousandsAsCurrency(thousands: number): string {
  if (thousands >= 1e6) return `$${(thousands / 1e6).toFixed(2)} B`;
  if (thousands >= 1e3) return `$${(thousands / 1e3).toFixed(2)} B`;
  return `$${thousands.toFixed(1)} M`;
}

/** Format a value already in millions (e.g. company_exposures total_exposure_millions) as "$1.5 M", "$500 M", "$1.2 B" */
export function formatMillionsAsCurrency(millions: number): string {
  if (millions >= 1e6) return `$${(millions / 1e6).toFixed(2)} B`;
  if (millions >= 1e3) return `$${(millions / 1e3).toFixed(2)} B`;
  return `$${millions.toFixed(1)} M`;
}

/** Column header suffix for dollar amounts that are displayed in millions */
export const CURRENCY_M_LABEL = 'Fair value ($M)';

/** Column header suffix when the column is "% of portfolio" or "% NAV" */
export const PCT_NAV_LABEL = '% of portfolio';
