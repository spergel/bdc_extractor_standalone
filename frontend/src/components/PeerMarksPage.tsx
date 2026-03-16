import { useState, useMemo } from 'react';
import { useBDCIndex, useAllBDCsLatestInvestments } from '../api/hooks';
import {
  getFV,
  getPrice,
  getPriceCategory,
  PRICE_CATEGORY_COLORS,
  PRICE_CATEGORY_LABELS,
  type PriceCategory,
} from '../utils/holdingsAnalytics';
import type { Holding } from '../data/adapter';
import { formatPeriodLabel } from '../utils/periodComparisons';
import { formatThousandsAsCurrency } from '../utils/formatCurrency';

// ─── Types ────────────────────────────────────────────────────────────────────

type BDCEntry = { ticker: string; name: string; periods: string[]; latest?: string };

type ScorecardRow = {
  ticker: string;
  name: string;
  period: string;
  totalFV: number;
  pricedFV: number;
  pctAtPar: number;
  pctBelow90: number;
  pctBelow80: number;
  countDeepDistressed: number;
  countTotal: number;
  countPriced: number;
};

type BDCMark = { ticker: string; price: number; fv: number };

type SharedBorrower = {
  companyDisplay: string;
  bdcMarks: BDCMark[];
  maxDivergence: number;
  highTicker: string;
  lowTicker: string;
  highPrice: number;
  lowPrice: number;
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function PriceBadge({ price }: { price: number }) {
  const cat = getPriceCategory(price);
  return (
    <span style={{ color: PRICE_CATEGORY_COLORS[cat], fontWeight: 600, fontSize: 11 }}>
      {Math.round(price * 100)}¢
    </span>
  );
}

function MiniBar({ pctAtPar, pctBelow90, pctBelow80 }: { pctAtPar: number; pctBelow90: number; pctBelow80: number }) {
  const parColor = PRICE_CATEGORY_COLORS.par;
  const yellowColor = PRICE_CATEGORY_COLORS.slight;
  const badColor = PRICE_CATEGORY_COLORS.moderate;
  return (
    <div className="flex h-3 w-24" title={`Par: ${pctAtPar.toFixed(0)}% | 90–98¢: ${(pctBelow90 - pctBelow80).toFixed(0)}% | <80¢: ${pctBelow80.toFixed(0)}%`}>
      <div style={{ width: `${Math.max(0, 100 - pctBelow90)}%`, backgroundColor: parColor }} />
      <div style={{ width: `${Math.max(0, pctBelow90 - pctBelow80)}%`, backgroundColor: yellowColor }} />
      <div style={{ width: `${Math.max(0, pctBelow80)}%`, backgroundColor: badColor }} />
    </div>
  );
}

// ─── Compute ──────────────────────────────────────────────────────────────────

function computeScorecard(
  rows: Array<{ ticker: string; period: string; data: { investments: Holding[] } | null }>,
  bdcMap: Map<string, BDCEntry>,
): ScorecardRow[] {
  return rows
    .filter((r) => r.data?.investments?.length)
    .map(({ ticker, period, data }) => {
      let totalFV = 0;
      let pricedFV = 0;
      const catFV: Record<PriceCategory, number> = { par: 0, slight: 0, moderate: 0, deep: 0, distressed: 0 };
      let countPriced = 0;
      let countTotal = 0;

      for (const h of data!.investments as Holding[]) {
        const fv = getFV(h);
        totalFV += fv;
        countTotal++;
        const price = getPrice(h);
        if (price !== null) {
          catFV[getPriceCategory(price)] += fv;
          pricedFV += fv;
          countPriced++;
        }
      }

      const pctBelow90 = pricedFV > 0 ? ((catFV.slight + catFV.moderate + catFV.deep + catFV.distressed) / pricedFV) * 100 : 0;
      const pctBelow80 = pricedFV > 0 ? ((catFV.moderate + catFV.deep + catFV.distressed) / pricedFV) * 100 : 0;
      const pctAtPar = pricedFV > 0 ? (catFV.par / pricedFV) * 100 : 0;
      const countDeepDistressed = (data!.investments as Holding[]).filter((h) => {
        const p = getPrice(h);
        return p !== null && p < 0.80;
      }).length;

      return {
        ticker,
        name: bdcMap.get(ticker)?.name ?? ticker,
        period,
        totalFV,
        pricedFV,
        pctAtPar,
        pctBelow90,
        pctBelow80,
        countDeepDistressed,
        countTotal,
        countPriced,
      };
    });
}

function computeSharedBorrowers(
  rows: Array<{ ticker: string; data: { investments: Holding[] } | null }>,
): SharedBorrower[] {
  type HoldingEntry = { ticker: string; price: number; fv: number; companyDisplay: string };
  const byCompany = new Map<string, HoldingEntry[]>();

  for (const { ticker, data } of rows) {
    if (!data?.investments) continue;
    for (const h of data.investments as Holding[]) {
      const display = (h.company_name_clean ?? h.company_name ?? '').trim();
      if (!display) continue;
      const price = getPrice(h);
      if (price === null) continue;
      const fv = getFV(h);
      const key = display.toLowerCase();
      if (!byCompany.has(key)) byCompany.set(key, []);
      byCompany.get(key)!.push({ ticker, price, fv, companyDisplay: display });
    }
  }

  const result: SharedBorrower[] = [];
  for (const entries of byCompany.values()) {
    const tickers = new Set(entries.map((e) => e.ticker));
    if (tickers.size < 2) continue;

    // Aggregate per-BDC: weighted avg price
    const byTicker = new Map<string, { sumPrice: number; sumFV: number; companyDisplay: string }>();
    for (const e of entries) {
      if (!byTicker.has(e.ticker)) byTicker.set(e.ticker, { sumPrice: 0, sumFV: 0, companyDisplay: e.companyDisplay });
      const agg = byTicker.get(e.ticker)!;
      agg.sumPrice += e.price * e.fv;
      agg.sumFV += e.fv;
    }

    const bdcMarks: BDCMark[] = [];
    for (const [ticker, agg] of byTicker) {
      if (agg.sumFV > 0) {
        bdcMarks.push({ ticker, price: agg.sumPrice / agg.sumFV, fv: agg.sumFV });
      }
    }
    if (bdcMarks.length < 2) continue;

    const prices = bdcMarks.map((m) => m.price);
    const high = Math.max(...prices);
    const low = Math.min(...prices);
    const divergence = high - low;

    result.push({
      companyDisplay: entries[0].companyDisplay,
      bdcMarks: bdcMarks.sort((a, b) => b.price - a.price),
      maxDivergence: divergence,
      highTicker: bdcMarks.find((m) => m.price === high)!.ticker,
      lowTicker: bdcMarks.find((m) => m.price === low)!.ticker,
      highPrice: high,
      lowPrice: low,
    });
  }

  return result.sort((a, b) => b.maxDivergence - a.maxDivergence);
}

// ─── Sub-components ───────────────────────────────────────────────────────────

type SortKey = 'ticker' | 'pctAtPar' | 'pctBelow90' | 'pctBelow80' | 'countDeepDistressed' | 'totalFV';

function ScorecardTable({ rows }: { rows: ScorecardRow[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('pctBelow90');
  const [sortAsc, setSortAsc] = useState(false);

  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      const av = a[sortKey as keyof ScorecardRow] as number | string;
      const bv = b[sortKey as keyof ScorecardRow] as number | string;
      const cmp = typeof av === 'string' ? av.localeCompare(bv as string) : (av as number) - (bv as number);
      return sortAsc ? cmp : -cmp;
    });
  }, [rows, sortKey, sortAsc]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(key === 'ticker'); }
  };

  const Th = ({ k, label, right }: { k: SortKey; label: string; right?: boolean }) => (
    <th
      className={`py-1 pr-2 text-[#808080] font-semibold text-[10px] cursor-pointer hover:text-black ${right ? 'text-right' : 'text-left'}`}
      onClick={() => toggleSort(k)}
    >
      {label}{sortKey === k ? (sortAsc ? ' ↑' : ' ↓') : ''}
    </th>
  );

  return (
    <div className="overflow-x-auto">
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 11 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid #808080' }}>
            <Th k="ticker" label="BDC" />
            <th className="text-left py-1 pr-2 text-[#808080] font-semibold text-[10px]">Period</th>
            <Th k="totalFV" label="Total FV" right />
            <th className="text-right py-1 pr-2 text-[#808080] font-semibold text-[10px]">Health</th>
            <Th k="pctAtPar" label="% At Par" right />
            <Th k="pctBelow90" label="% <90¢" right />
            <Th k="pctBelow80" label="% <80¢" right />
            <Th k="countDeepDistressed" label="# <80¢" right />
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.ticker} style={{ borderBottom: '1px solid #e0e0e0' }}>
              <td className="py-1 pr-2 font-semibold text-black" style={{ whiteSpace: 'nowrap' }}>{row.ticker}</td>
              <td className="py-1 pr-2 text-[10px] text-[#808080]">{formatPeriodLabel(row.period)}</td>
              <td className="py-1 pr-2 text-right text-[10px] text-black">{formatThousandsAsCurrency(row.totalFV)}</td>
              <td className="py-1 pr-2 text-right">
                <div className="flex justify-end">
                  <MiniBar pctAtPar={row.pctAtPar} pctBelow90={row.pctBelow90} pctBelow80={row.pctBelow80} />
                </div>
              </td>
              <td className="py-1 pr-2 text-right text-[10px] text-black">{row.pctAtPar.toFixed(1)}%</td>
              <td className="py-1 pr-2 text-right text-[10px]" style={{ color: row.pctBelow90 > 5 ? '#B22222' : '#808080' }}>
                {row.pctBelow90.toFixed(1)}%
              </td>
              <td className="py-1 pr-2 text-right text-[10px]" style={{ color: row.pctBelow80 > 2 ? '#B22222' : '#808080' }}>
                {row.pctBelow80.toFixed(1)}%
              </td>
              <td className="py-1 text-right text-[10px]" style={{ color: row.countDeepDistressed > 0 ? '#B22222' : '#808080' }}>
                {row.countDeepDistressed}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SharedBorrowersTable({ rows }: { rows: SharedBorrower[] }) {
  const [search, setSearch] = useState('');
  const [threshold, setThreshold] = useState(5);

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      const matchSearch = !search || r.companyDisplay.toLowerCase().includes(search.toLowerCase());
      const matchThreshold = r.maxDivergence * 100 >= threshold;
      return matchSearch && matchThreshold;
    });
  }, [rows, search, threshold]);

  if (rows.length === 0) {
    return (
      <div className="text-xs text-[#808080]">
        No companies found held by 2+ BDCs with pricing data. (Name matching is exact — company name must match exactly across BDCs.)
      </div>
    );
  }

  return (
    <div>
      <div className="flex gap-3 mb-2 flex-wrap items-center">
        <input
          className="input text-xs"
          placeholder="Search company..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: 200 }}
        />
        <div className="flex items-center gap-1 text-xs text-[#808080]">
          <span>Min divergence:</span>
          <select className="input text-xs" value={threshold} onChange={(e) => setThreshold(Number(e.target.value))}>
            <option value={0}>All</option>
            <option value={2}>≥2¢</option>
            <option value={5}>≥5¢ (default)</option>
            <option value={10}>≥10¢</option>
            <option value={20}>≥20¢</option>
          </select>
        </div>
        <span className="text-[10px] text-[#808080]">{filtered.length} of {rows.length} borrowers</span>
      </div>
      <div className="overflow-x-auto">
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #808080' }}>
              <th className="text-left py-1 pr-2 text-[#808080] font-semibold text-[10px]">Company</th>
              <th className="text-right py-1 pr-2 text-[#808080] font-semibold text-[10px]">Divergence</th>
              <th className="text-left py-1 pr-2 text-[#808080] font-semibold text-[10px]">High Mark</th>
              <th className="text-left py-1 pr-2 text-[#808080] font-semibold text-[10px]">Low Mark</th>
              <th className="text-left py-1 text-[#808080] font-semibold text-[10px]">All BDC Marks</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((row) => (
              <tr key={row.companyDisplay} style={{ borderBottom: '1px solid #e0e0e0' }}>
                <td className="py-1 pr-2 text-black" style={{ maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.companyDisplay}>
                  {row.companyDisplay}
                </td>
                <td className="py-1 pr-2 text-right">
                  <span style={{ fontWeight: 600, color: row.maxDivergence > 0.10 ? '#B22222' : row.maxDivergence > 0.05 ? '#DAA520' : '#808080', fontSize: 11 }}>
                    {Math.round(row.maxDivergence * 100)}¢
                  </span>
                </td>
                <td className="py-1 pr-2" style={{ whiteSpace: 'nowrap' }}>
                  <span className="text-[10px] text-[#808080]">{row.highTicker} </span>
                  <PriceBadge price={row.highPrice} />
                </td>
                <td className="py-1 pr-2" style={{ whiteSpace: 'nowrap' }}>
                  <span className="text-[10px] text-[#808080]">{row.lowTicker} </span>
                  <PriceBadge price={row.lowPrice} />
                </td>
                <td className="py-1">
                  <div className="flex gap-2 flex-wrap">
                    {row.bdcMarks.map((m) => (
                      <span key={m.ticker} className="text-[10px]" style={{ whiteSpace: 'nowrap' }}>
                        <span className="text-[#808080]">{m.ticker} </span>
                        <PriceBadge price={m.price} />
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Portfolio Health Legend ──────────────────────────────────────────────────

function HealthLegend() {
  return (
    <div className="flex gap-3 flex-wrap mb-2">
      {(['par', 'slight', 'moderate'] as PriceCategory[]).map((cat) => (
        <div key={cat} className="flex items-center gap-1">
          <div style={{ width: 10, height: 10, backgroundColor: PRICE_CATEGORY_COLORS[cat], flexShrink: 0 }} />
          <span className="text-[10px] text-[#808080]">{PRICE_CATEGORY_LABELS[cat]}</span>
        </div>
      ))}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function PeerMarksPage() {
  const { data: index } = useBDCIndex();
  const bdcs: BDCEntry[] = index?.bdcs ?? [];
  const bdcMap = useMemo(() => new Map(bdcs.map((b) => [b.ticker, b])), [bdcs]);

  const allLatest = useAllBDCsLatestInvestments(bdcs);
  const loadedCount = allLatest.filter((r) => !r.isLoading).length;
  const isLoading = allLatest.some((r) => r.isLoading);

  const scorecardRows = useMemo(
    () => computeScorecard(allLatest as any, bdcMap),
    [allLatest, bdcMap],
  );

  const sharedBorrowers = useMemo(
    () => computeSharedBorrowers(allLatest as any),
    [allLatest],
  );

  return (
    <div className="flex flex-col h-full min-h-0 overflow-y-auto space-y-4 p-3">
      {/* Header */}
      <div className="window p-3">
        <div className="text-sm font-semibold text-black mb-1">Peer Marks — Cross-BDC Valuation Comparison</div>
        <div className="text-xs text-[#808080]">
          Compares how each BDC marks its loan portfolio. Lower % at par may indicate more honest (or more troubled) marks.
          Shared Borrowers shows loans held by multiple BDCs — large divergence is a red flag.
        </div>
        {isLoading && (
          <div className="text-[10px] text-[#808080] mt-1">
            Loading… {loadedCount}/{bdcs.length} BDCs ready
          </div>
        )}
      </div>

      {/* Scorecard */}
      <div className="window p-3">
        <div className="text-xs font-semibold text-black mb-2">BDC Scorecard (Latest Period, Debt Instruments)</div>
        <HealthLegend />
        {scorecardRows.length === 0 ? (
          <div className="text-xs text-[#808080]">Loading BDC data…</div>
        ) : (
          <ScorecardTable rows={scorecardRows} />
        )}
      </div>

      {/* Shared Borrowers */}
      <div className="window p-3">
        <div className="text-xs font-semibold text-black mb-1">
          Shared Borrowers — Same Company, Different BDC Marks
        </div>
        <div className="text-[10px] text-[#808080] mb-2">
          Companies held by 2+ BDCs with pricing data. Exact name match only. Sorted by divergence.
        </div>
        <SharedBorrowersTable rows={sharedBorrowers} />
      </div>
    </div>
  );
}
