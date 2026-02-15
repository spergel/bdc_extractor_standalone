/**
 * Standard currency formatting for the app.
 *
 * DATA UNITS (single source of truth):
 * - Investments/holdings CSVs and in-memory Holding: amounts in THOUSANDS (e.g. 50000 = $50M).
 * - company_exposures.csv: total_exposure_millions is in MILLIONS.
 * - company_detail.json (by_bdc, by_maturity, by_investment_type): values are in MILLIONS.
 * - portfolio_summaries / industry_summaries: total_fair_value_millions, etc. are in MILLIONS.
 *
 * We display as "$X.X M" or "$X.XX B" using these helpers so all views are consistent.
 */

/** Format a value stored in THOUSANDS as display string: "$0.5 M", "$50.00 M", "$1.20 B" */
export function formatThousandsAsCurrency(thousands: number): string {
  if (typeof thousands !== 'number' || Number.isNaN(thousands)) return '—';
  const abs = Math.abs(thousands);
  const sign = thousands < 0 ? '-' : '';
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)} B`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(2)} M`;
  return `${sign}$${(abs / 1e3).toFixed(2)} M`;
}

/** Format a value already in MILLIONS (company_exposures, company_detail, portfolio_summaries) as "$1.5 M", "$1.20 B" */
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
