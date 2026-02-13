import { useMemo, useEffect } from 'react';
import type { Holding } from '../data/adapter';
import { toPercent, getFV, getCost, getPrincipal, getMaturityDateStr, isVariableRate } from '../utils/holdingsAnalytics';
import { useDebounce } from '../hooks/useDebounce';
import { calculateCurrentYield } from '../utils/referenceRates';

export type FilterState = {
  industry: string;    // 'all' or specific industry
  type: string;        // 'all' or specific type
};

export const defaultFilterState: FilterState = {
  industry: 'all',
  type: 'all',
};

export type AdvancedFilterState = {
  // Multi-select classification
  industries: string[];
  types: string[];
  // Numeric expression filters
  spread: string;
  fv: string;
  principal: string;
  fvCost: string;
  fvPrin: string;
  // Range filters (min/max)
  percentOfBookMin: string;
  percentOfBookMax: string;
  yieldMin: string;
  yieldMax: string;
  // Term/maturity
  remainingTerm: string;
  maturityAfter: string;
  maturityBefore: string;
  // Characteristics
  pikFilter: 'all' | 'pik' | 'non-pik';
  rateType: 'all' | 'floating' | 'fixed';
};

export const defaultAdvancedFilterState: AdvancedFilterState = {
  industries: [],
  types: [],
  spread: '',
  fv: '',
  principal: '',
  fvCost: '',
  fvPrin: '',
  percentOfBookMin: '',
  percentOfBookMax: '',
  yieldMin: '',
  yieldMax: '',
  remainingTerm: '',
  maturityAfter: '',
  maturityBefore: '',
  pikFilter: 'all',
  rateType: 'all',
};

export function hasActiveAdvancedFilters(adv: AdvancedFilterState): boolean {
  return adv.industries.length > 0 || adv.types.length > 0 ||
    adv.spread !== '' || adv.fv !== '' || adv.principal !== '' ||
    adv.fvCost !== '' || adv.fvPrin !== '' ||
    adv.percentOfBookMin !== '' || adv.percentOfBookMax !== '' ||
    adv.yieldMin !== '' || adv.yieldMax !== '' ||
    adv.remainingTerm !== '' || adv.maturityAfter !== '' ||
    adv.maturityBefore !== '' || adv.pikFilter !== 'all' ||
    adv.rateType !== 'all';
}

// All available filter kinds for the modular UI
export type FilterKind =
  | 'industries' | 'types'
  | 'spread' | 'fv' | 'principal' | 'fvCost' | 'fvPrin'
  | 'percentOfBook' | 'yield'
  | 'remainingTerm' | 'maturity'
  | 'pikFilter' | 'rateType';

export const FILTER_DEFS: Record<FilterKind, { label: string }> = {
  industries: { label: 'Industry' },
  types: { label: 'Type' },
  spread: { label: 'Spread %' },
  fv: { label: 'Fair Value ($K)' },
  principal: { label: 'Principal ($K)' },
  fvCost: { label: 'FV / Cost' },
  fvPrin: { label: 'FV / Principal' },
  percentOfBook: { label: '% of Book' },
  yield: { label: 'Yield %' },
  remainingTerm: { label: 'Term (yrs)' },
  maturity: { label: 'Maturity Date' },
  pikFilter: { label: 'PIK' },
  rateType: { label: 'Rate Type' },
};

export const ALL_FILTER_KINDS: FilterKind[] = Object.keys(FILTER_DEFS) as FilterKind[];

export function isKindActive(kind: FilterKind, state: AdvancedFilterState): boolean {
  switch (kind) {
    case 'industries': return state.industries.length > 0;
    case 'types': return state.types.length > 0;
    case 'spread': return state.spread !== '';
    case 'fv': return state.fv !== '';
    case 'principal': return state.principal !== '';
    case 'fvCost': return state.fvCost !== '';
    case 'fvPrin': return state.fvPrin !== '';
    case 'percentOfBook': return state.percentOfBookMin !== '' || state.percentOfBookMax !== '';
    case 'yield': return state.yieldMin !== '' || state.yieldMax !== '';
    case 'remainingTerm': return state.remainingTerm !== '';
    case 'maturity': return state.maturityAfter !== '' || state.maturityBefore !== '';
    case 'pikFilter': return state.pikFilter !== 'all';
    case 'rateType': return state.rateType !== 'all';
  }
}

export function resetKind(kind: FilterKind): Partial<AdvancedFilterState> {
  switch (kind) {
    case 'industries': return { industries: [] };
    case 'types': return { types: [] };
    case 'spread': return { spread: '' };
    case 'fv': return { fv: '' };
    case 'principal': return { principal: '' };
    case 'fvCost': return { fvCost: '' };
    case 'fvPrin': return { fvPrin: '' };
    case 'percentOfBook': return { percentOfBookMin: '', percentOfBookMax: '' };
    case 'yield': return { yieldMin: '', yieldMax: '' };
    case 'remainingTerm': return { remainingTerm: '' };
    case 'maturity': return { maturityAfter: '', maturityBefore: '' };
    case 'pikFilter': return { pikFilter: 'all' };
    case 'rateType': return { rateType: 'all' };
  }
}

function getHoldingYield(h: Holding): number | null {
  // Try calculated yield from spread + ref rate first
  const cy = calculateCurrentYield(h.spread ?? h.spread_clean, h.reference_rate);
  if (cy !== null) return cy;
  // Fallback to total_interest_rate (stored as decimal)
  if (h.total_interest_rate) return h.total_interest_rate * 100;
  // Fallback to effective_yield_pct
  if (h.effective_yield_pct != null) return h.effective_yield_pct;
  return null;
}

function getPercentOfBook(h: Holding): number | null {
  const pct = h.percent_of_net_assets;
  if (pct == null) return null;
  // If < 1 in absolute value, treat as decimal (0.02 → 2%)
  if (Math.abs(pct) < 1) return pct * 100;
  return pct;
}

function parseNum(s: string): number | null {
  const v = parseFloat(s);
  return Number.isNaN(v) ? null : v;
}

export function filterHoldingsAdvanced(data: Holding[], adv: AdvancedFilterState): Holding[] {
  if (!hasActiveAdvancedFilters(adv)) return data;

  // Expression-based tests (moved from basic bar)
  const spreadTest = parseExpr(adv.spread);
  const fvTest = parseExpr(adv.fv);
  const principalTest = parseExpr(adv.principal);
  const fvCostTest = parseExpr(adv.fvCost);
  const fvPrinTest = parseExpr(adv.fvPrin);
  const termTest = parseExpr(adv.remainingTerm);

  // Range filters (simple min/max)
  const pctMin = parseNum(adv.percentOfBookMin);
  const pctMax = parseNum(adv.percentOfBookMax);
  const yieldMin = parseNum(adv.yieldMin);
  const yieldMax = parseNum(adv.yieldMax);

  const matAfter = adv.maturityAfter ? Date.parse(adv.maturityAfter) : null;
  const matBefore = adv.maturityBefore ? Date.parse(adv.maturityBefore) : null;

  return data.filter(h => {
    // Expression filters (moved from basic bar)
    if (spreadTest) {
      const spread = toPercent(h.spread);
      if (!spreadTest(spread)) return false;
    }

    if (fvTest) {
      if (!fvTest(getFV(h))) return false;
    }

    if (principalTest) {
      if (!principalTest(getPrincipal(h))) return false;
    }

    if (fvCostTest) {
      const fv = getFV(h);
      const cost = getCost(h);
      if (cost <= 0 || fv <= 0) return false;
      if (!fvCostTest(fv / cost)) return false;
    }

    if (fvPrinTest) {
      const fv = getFV(h);
      const prin = getPrincipal(h);
      if (prin <= 0 || fv <= 0) return false;
      if (!fvPrinTest(fv / prin)) return false;
    }

    // Range: % of book
    if (pctMin !== null || pctMax !== null) {
      const pct = getPercentOfBook(h);
      if (pct === null) return false;
      if (pctMin !== null && pct < pctMin) return false;
      if (pctMax !== null && pct > pctMax) return false;
    }

    // Range: yield
    if (yieldMin !== null || yieldMax !== null) {
      const y = getHoldingYield(h);
      if (y === null) return false;
      if (yieldMin !== null && y < yieldMin) return false;
      if (yieldMax !== null && y > yieldMax) return false;
    }

    // Remaining term
    if (termTest) {
      const term = h.remaining_term_years;
      if (term == null) return false;
      if (!termTest(term)) return false;
    }

    // Maturity date range
    const matStr = getMaturityDateStr(h);
    if (matAfter !== null && !Number.isNaN(matAfter)) {
      if (!matStr) return false;
      const mat = Date.parse(matStr);
      if (Number.isNaN(mat) || mat < matAfter) return false;
    }

    if (matBefore !== null && !Number.isNaN(matBefore)) {
      if (!matStr) return false;
      const mat = Date.parse(matStr);
      if (Number.isNaN(mat) || mat > matBefore) return false;
    }

    // PIK
    if (adv.pikFilter === 'pik') {
      if (!h.is_pik) return false;
    } else if (adv.pikFilter === 'non-pik') {
      if (h.is_pik) return false;
    }

    // Rate type (use same logic as analytics: variable = has SOFR/LIBOR/etc.)
    if (adv.rateType === 'floating') {
      if (!isVariableRate(h)) return false;
    } else if (adv.rateType === 'fixed') {
      if (isVariableRate(h)) return false;
    }

    return true;
  });
}

// Parse suffixes like 1m, 500k, 1.5b into raw numbers
function parseSuffix(s: string): number {
  const cleaned = s.replace(/[$,\s%]/g, '').toLowerCase();
  const match = cleaned.match(/^([0-9.]+)(k|m|b)?$/);
  if (!match) return NaN;
  let n = parseFloat(match[1]);
  if (Number.isNaN(n)) return NaN;
  switch (match[2]) {
    case 'k': n *= 1_000; break;
    case 'm': n *= 1_000_000; break;
    case 'b': n *= 1_000_000_000; break;
  }
  return n;
}

type NumericTest = (value: number) => boolean;

// Parse an expression string into a test function
// Supported: >5, >=5, <3, <=3, 3-7, 3..7, =5, 5 (bare number = >=)
export function parseExpr(expr: string): NumericTest | null {
  const s = expr.trim();
  if (!s) return null;

  // Range: 3-7 or 3..7  (but not negative like -5)
  const rangeMatch = s.match(/^([0-9.$kKmMbB]+)\s*[-–..]+\s*([0-9.$kKmMbB]+)$/);
  if (rangeMatch) {
    const lo = parseSuffix(rangeMatch[1]);
    const hi = parseSuffix(rangeMatch[2]);
    if (!Number.isNaN(lo) && !Number.isNaN(hi)) {
      return v => v >= lo && v <= hi;
    }
  }

  // Operators: >=, <=, >, <, =, !=
  const opMatch = s.match(/^([><!]=?)\s*([0-9.$kKmMbB]+)$/);
  if (opMatch) {
    const n = parseSuffix(opMatch[2]);
    if (Number.isNaN(n)) return null;
    switch (opMatch[1]) {
      case '>':  return v => v > n;
      case '>=': return v => v >= n;
      case '<':  return v => v < n;
      case '<=': return v => v <= n;
      case '=':  return v => Math.abs(v - n) < 0.001;
      case '!=': return v => Math.abs(v - n) >= 0.001;
    }
  }

  // Bare number = treat as >=
  const bare = parseSuffix(s);
  if (!Number.isNaN(bare)) {
    return v => v >= bare;
  }

  return null;
}

export function filterHoldings(data: Holding[], filters: FilterState): Holding[] {
  return data.filter(h => {
    if (filters.industry !== 'all') {
      if ((h.industry || 'Unknown') !== filters.industry) return false;
    }

    if (filters.type !== 'all') {
      if ((h.investment_type || 'Unknown') !== filters.type) return false;
    }

    return true;
  });
}

// Debounced expression input - displays immediately, fires onChange after delay
export function ExprInput({ value, onChange, className, placeholder, title }: {
  value: string;
  onChange: (val: string) => void;
  className: string;
  placeholder: string;
  title?: string;
}) {
  const { displayValue, debouncedValue, setDisplayValue, flushNow, reset } = useDebounce(value, 400);

  // Sync from parent when value resets externally (e.g. Clear All)
  useEffect(() => {
    if (value !== displayValue && value !== debouncedValue) {
      reset(value);
    }
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fire parent onChange when debounced value changes
  useEffect(() => {
    if (debouncedValue !== value) {
      onChange(debouncedValue);
    }
  }, [debouncedValue]); // eslint-disable-line react-hooks/exhaustive-deps

  // Compute validation class from display value (instant feedback)
  const exprClass = () => {
    if (!displayValue.trim()) return className;
    const valid = parseExpr(displayValue) !== null;
    return `${className} ${valid ? 'border-[#000080]' : 'border-[#ff0000]'}`;
  };

  return (
    <input
      className={exprClass()}
      value={displayValue}
      onChange={e => setDisplayValue(e.target.value)}
      onKeyDown={e => { if (e.key === 'Enter') flushNow(); }}
      placeholder={placeholder}
      title={title}
    />
  );
}

type Props = {
  holdings: Holding[];
  filters: FilterState;
  onChange: (filters: FilterState) => void;
};

export function HoldingsFilterBar({ holdings, filters, onChange }: Props) {
  const industries = useMemo(() => {
    const set = new Set<string>();
    holdings.forEach(h => set.add(h.industry || 'Unknown'));
    return Array.from(set).sort();
  }, [holdings]);

  const types = useMemo(() => {
    const set = new Set<string>();
    holdings.forEach(h => set.add(h.investment_type || 'Unknown'));
    return Array.from(set).sort();
  }, [holdings]);

  const update = (partial: Partial<FilterState>) => {
    onChange({ ...filters, ...partial });
  };

  const hasActiveFilters = filters.industry !== 'all' || filters.type !== 'all';

  return (
    <div className="flex items-center gap-2 text-xs text-[#808080] flex-wrap">
      <select
        className="input text-xs py-0.5"
        value={filters.industry}
        onChange={e => update({ industry: e.target.value })}
        title="Filter by industry"
      >
        <option value="all">Industry: All</option>
        {industries.map(ind => (
          <option key={ind} value={ind}>{ind}</option>
        ))}
      </select>

      <select
        className="input text-xs py-0.5"
        value={filters.type}
        onChange={e => update({ type: e.target.value })}
        title="Filter by investment type"
      >
        <option value="all">Type: All</option>
        {types.map(t => (
          <option key={t} value={t}>{t}</option>
        ))}
      </select>

      {hasActiveFilters && (
        <button
          className="px-1.5 py-0.5 text-xs window"
          onClick={() => onChange(defaultFilterState)}
          title="Clear all filters"
        >
          Clear
        </button>
      )}
    </div>
  );
}
