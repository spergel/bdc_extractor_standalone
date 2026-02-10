import { useMemo } from 'react';
import type { Holding } from '../data/adapter';
import { toPercent, getFV, getCost, getPrincipal } from '../utils/holdingsAnalytics';

export type FilterState = {
  industry: string;    // 'all' or specific industry
  type: string;        // 'all' or specific type
  spread: string;      // expression: '>5', '<3', '3-7', '>=5', etc.
  fv: string;          // expression: '>1m', '<500k', '100k-5m', etc.
  fvCost: string;      // expression: '<0.9', '0.95-1.05', '>1.05', etc.
  fvPrin: string;      // expression on FV/Principal ratio
  principal: string;   // expression on principal amount
};

export const defaultFilterState: FilterState = {
  industry: 'all',
  type: 'all',
  spread: '',
  fv: '',
  fvCost: '',
  fvPrin: '',
  principal: '',
};

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
  const spreadTest = parseExpr(filters.spread);
  const fvTest = parseExpr(filters.fv);
  const fvCostTest = parseExpr(filters.fvCost);
  const fvPrinTest = parseExpr(filters.fvPrin);
  const principalTest = parseExpr(filters.principal);

  return data.filter(h => {
    if (filters.industry !== 'all') {
      if ((h.industry || 'Unknown') !== filters.industry) return false;
    }

    if (filters.type !== 'all') {
      if ((h.investment_type || 'Unknown') !== filters.type) return false;
    }

    if (spreadTest) {
      const spread = toPercent(h.spread);
      if (!spreadTest(spread)) return false;
    }

    if (fvTest) {
      if (!fvTest(getFV(h))) return false;
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

    if (principalTest) {
      if (!principalTest(getPrincipal(h))) return false;
    }

    return true;
  });
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

  const hasActiveFilters = filters.industry !== 'all' || filters.type !== 'all' ||
    filters.spread !== '' || filters.fv !== '' || filters.fvCost !== '' ||
    filters.fvPrin !== '' || filters.principal !== '';

  // Show red border when expression is invalid (non-empty but won't parse)
  const exprClass = (expr: string) => {
    if (!expr.trim()) return 'input text-xs py-0.5 w-20';
    const valid = parseExpr(expr) !== null;
    return `input text-xs py-0.5 w-20 ${valid ? 'border-[#000080]' : 'border-[#ff0000]'}`;
  };

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

      <label className="flex items-center gap-1" title="Spread %  (e.g. >5, 3-7, <10)">
        <span>Spread%</span>
        <input
          className={exprClass(filters.spread)}
          value={filters.spread}
          onChange={e => update({ spread: e.target.value })}
          placeholder=">5"
        />
      </label>

      <label className="flex items-center gap-1" title="Fair Value  (e.g. >1m, 100k-5m, <500k)">
        <span>FV</span>
        <input
          className={exprClass(filters.fv)}
          value={filters.fv}
          onChange={e => update({ fv: e.target.value })}
          placeholder=">1m"
        />
      </label>

      <label className="flex items-center gap-1" title="Principal  (e.g. >1m, <500k)">
        <span>Prin</span>
        <input
          className={exprClass(filters.principal)}
          value={filters.principal}
          onChange={e => update({ principal: e.target.value })}
          placeholder=">1m"
        />
      </label>

      <label className="flex items-center gap-1" title="FV/Cost ratio  (e.g. <0.9, 0.95-1.05, >1.05)">
        <span>FV/Cost</span>
        <input
          className={exprClass(filters.fvCost)}
          value={filters.fvCost}
          onChange={e => update({ fvCost: e.target.value })}
          placeholder="<0.9"
        />
      </label>

      <label className="flex items-center gap-1" title="FV/Principal ratio  (e.g. <0.95, >1)">
        <span>FV/Prin</span>
        <input
          className={exprClass(filters.fvPrin)}
          value={filters.fvPrin}
          onChange={e => update({ fvPrin: e.target.value })}
          placeholder="<0.95"
        />
      </label>

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
