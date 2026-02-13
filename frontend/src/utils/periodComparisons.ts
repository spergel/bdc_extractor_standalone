/**
 * Utility functions for period and quarter comparisons
 */

export interface QuarterInfo {
  year: number;
  quarter: 1 | 2 | 3 | 4;
  date: Date;
}

/**
 * Parse a period string (YYYY-MM-DD) and extract quarter information
 */
export function parsePeriod(period: string): QuarterInfo | null {
  try {
    const date = new Date(period);
    if (isNaN(date.getTime())) {
      return null;
    }
    
    const year = date.getFullYear();
    const month = date.getMonth() + 1; // 1-12
    
    // Determine quarter based on month
    // Q1: Jan-Mar (1-3), Q2: Apr-Jun (4-6), Q3: Jul-Sep (7-9), Q4: Oct-Dec (10-12)
    let quarter: 1 | 2 | 3 | 4;
    if (month >= 1 && month <= 3) {
      quarter = 1;
    } else if (month >= 4 && month <= 6) {
      quarter = 2;
    } else if (month >= 7 && month <= 9) {
      quarter = 3;
    } else {
      quarter = 4;
    }
    
    return { year, quarter, date };
  } catch {
    return null;
  }
}

/**
 * Format quarter info as a readable string (e.g., "Q3 2025")
 */
export function formatQuarter(q: QuarterInfo): string {
  return `Q${q.quarter} ${q.year}`;
}

/**
 * Map SEC filing date (YYYY-MM-DD) to the quarter that filing represents.
 * 10-Q: May→Q1, Aug→Q2, Nov→Q3. 10-K: Feb/Mar→Q4 (prior year).
 */
function filingDateToQuarter(period: string): { year: number; quarter: 1 | 2 | 3 | 4 } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(period);
  if (!match) return null;
  const year = parseInt(match[1], 10);
  const month = parseInt(match[2], 10);
  if (month >= 2 && month <= 3) return { year: year - 1, quarter: 4 };  // 10-K → prior year Q4
  if (month >= 4 && month <= 6) return { year, quarter: 1 };
  if (month >= 7 && month <= 9) return { year, quarter: 2 };
  if (month >= 10 && month <= 12) return { year, quarter: 3 };
  if (month === 1) return { year: year - 1, quarter: 4 };  // late 10-K in Jan
  return null;
}

/**
 * Format a raw period string (YYYY-MM-DD filing date) as a quarter label (e.g., "Q3 2025").
 * Uses SEC filing-date convention (May=Q1, Aug=Q2, Nov=Q3, Feb/Mar=Q4 prior year).
 */
export function formatPeriodLabel(period: string | undefined | null): string {
  if (!period) return '';
  const q = filingDateToQuarter(period);
  if (q) return `Q${q.quarter} ${q.year}`;
  const fallback = parsePeriod(period);
  if (fallback) return formatQuarter(fallback);
  return period;
}

/**
 * Get the quarter-end date for a given year and quarter
 */
function getQuarterEndDate(year: number, quarter: 1 | 2 | 3 | 4): Date {
  // Quarter end dates: Q1=Mar 31, Q2=Jun 30, Q3=Sep 30, Q4=Dec 31
  const month = quarter * 3; // 3, 6, 9, 12
  const day = month === 12 ? 31 : 30; // Dec 31, others are 30
  return new Date(year, month - 1, day);
}

/**
 * Find the closest period in a list of periods to a target date
 */
export function findClosestPeriod(periods: string[], targetDate: Date): string | null {
  if (periods.length === 0) return null;
  
  const targetTime = targetDate.getTime();
  let closestPeriod: string | null = null;
  let minDiff = Infinity;
  
  for (const period of periods) {
    const periodDate = new Date(period);
    if (isNaN(periodDate.getTime())) continue;
    
    // Prefer periods on or before the target date
    const diff = targetTime - periodDate.getTime();
    if (diff >= 0 && diff < minDiff) {
      minDiff = diff;
      closestPeriod = period;
    }
  }
  
  // If no period before target, use the earliest available
  if (!closestPeriod) {
    const sorted = [...periods].sort((a, b) => 
      new Date(a).getTime() - new Date(b).getTime()
    );
    return sorted[0];
  }
  
  return closestPeriod;
}

/**
 * Get the previous quarter (QoQ comparison)
 * Returns the period string for the previous quarter, or null if not found
 */
export function getPreviousQuarter(period: string, availablePeriods: string[]): string | null {
  const current = parsePeriod(period);
  if (!current) return null;
  
  let prevYear = current.year;
  let prevQuarter: 1 | 2 | 3 | 4;
  
  if (current.quarter === 1) {
    prevYear = current.year - 1;
    prevQuarter = 4;
  } else {
    prevQuarter = (current.quarter - 1) as 1 | 2 | 3 | 4;
  }
  
  const targetDate = getQuarterEndDate(prevYear, prevQuarter);
  return findClosestPeriod(availablePeriods, targetDate);
}

/**
 * Get the same quarter from the previous year (YoY comparison)
 * Returns the period string for the same quarter last year, or null if not found
 */
export function getYearOverYear(period: string, availablePeriods: string[]): string | null {
  const current = parsePeriod(period);
  if (!current) return null;
  
  const prevYear = current.year - 1;
  const targetDate = getQuarterEndDate(prevYear, current.quarter);
  return findClosestPeriod(availablePeriods, targetDate);
}

/**
 * Get Q4 of the previous year (Year-End vs Now comparison)
 * If the current period is Q4, returns Q4 of the previous year
 * If the current period is Q1-Q3, returns Q4 of the previous year
 * Returns the period string, or null if not found
 */
export function getYearEndComparison(period: string, availablePeriods: string[]): string | null {
  const current = parsePeriod(period);
  if (!current) return null;
  
  // Always compare to Q4 of the previous year
  const prevYear = current.year - 1;
  const targetDate = getQuarterEndDate(prevYear, 4);
  return findClosestPeriod(availablePeriods, targetDate);
}

/**
 * Get comparison label for display
 */
export function getComparisonLabel(
  type: 'qoq' | 'yoy' | 'ye',
  currentPeriod: string
): string {
  const current = parsePeriod(currentPeriod);
  if (!current) return '';
  
  switch (type) {
    case 'qoq': {
      let prevYear = current.year;
      let prevQuarter: 1 | 2 | 3 | 4;
      if (current.quarter === 1) {
        prevYear = current.year - 1;
        prevQuarter = 4;
      } else {
        prevQuarter = (current.quarter - 1) as 1 | 2 | 3 | 4;
      }
      return `${formatQuarter({ year: prevYear, quarter: prevQuarter, date: new Date() })} vs ${formatQuarter(current)}`;
    }
    case 'yoy': {
      const prevYear = current.year - 1;
      return `${formatQuarter({ year: prevYear, quarter: current.quarter, date: new Date() })} vs ${formatQuarter(current)}`;
    }
    case 'ye': {
      const prevYear = current.year - 1;
      if (current.quarter === 4) {
        return `${formatQuarter({ year: prevYear, quarter: 4, date: new Date() })} vs ${formatQuarter(current)} (Previous Year)`;
      } else {
        return `Q4 ${prevYear} vs ${formatQuarter(current)}`;
      }
    }
  }
}































