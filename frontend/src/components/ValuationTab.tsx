import { useState, useMemo, Fragment } from 'react';
import { useBDCInvestmentsMultiple } from '../api/hooks';
import {
  getFV,
  getCost,
  getPrincipal,
  getInvType,
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

type LoadedPeriod = { period: string; holdings: Holding[] };

type PriceBuckets = Record<PriceCategory, { fv: number; count: number }>;

type PeriodBucket = {
  period: string;
  buckets: PriceBuckets;
  totalFV: number;
  pricedFV: number;
};

type StressedRow = {
  key: string;
  companyName: string;
  invType: string;
  firstBelowParPeriod: string;
  periodsBelow: number;
  currentPrice: number | null;
  currentPeriod: string | null;
  lowestPrice: number;
  lowestPricePeriod: string;
  priceHistory: Array<{ period: string; price: number; fv: number }>;
  trend: 'up' | 'down' | 'flat' | 'recovered';
};

type NonAccrualRow = {
  key: string;
  companyName: string;
  invType: string;
  changePeriod: string;
  rateBefore: string;
  rateAfter: string;
  priceAtChange: number | null;
  pricePrior: number | null;
  lag: 'same_period' | 'lag' | 'no_markdown' | 'unknown';
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

const PRICE_CATS: PriceCategory[] = ['par', 'slight', 'moderate', 'deep', 'distressed'];

function emptyBuckets(): PriceBuckets {
  return { par: { fv: 0, count: 0 }, slight: { fv: 0, count: 0 }, moderate: { fv: 0, count: 0 }, deep: { fv: 0, count: 0 }, distressed: { fv: 0, count: 0 } };
}

function getCompanyKey(h: Holding): string {
  const name = (h.company_name_clean ?? h.company_name ?? '').trim().toLowerCase();
  const type = getInvType(h).toLowerCase();
  return `${name}||${type}`;
}

function getDisplayName(h: Holding): string {
  return (h.company_name_clean ?? h.company_name ?? '').trim();
}

function getCashRatePct(h: Holding): number | null {
  const raw = (h as Record<string, unknown>).cash_rate;
  if (raw !== undefined && raw !== null && raw !== '') {
    const n = typeof raw === 'number' ? raw : parseFloat(String(raw));
    if (!isNaN(n)) return n;
  }
  if (h.total_interest_rate != null) {
    const v = h.total_interest_rate;
    // stored as decimal (0.105) or already percent (10.5)?
    return v > 0 && v < 1 ? v * 100 : v;
  }
  return null;
}

function isDebtInstrument(h: Holding): boolean {
  return getCost(h) > 0 || getPrincipal(h) > 0;
}

// ─── Compute functions ────────────────────────────────────────────────────────

function computePeriodBuckets(loaded: LoadedPeriod[]): PeriodBucket[] {
  return loaded.map(({ period, holdings }) => {
    const buckets = emptyBuckets();
    let totalFV = 0;
    let pricedFV = 0;
    for (const h of holdings) {
      const fv = getFV(h);
      totalFV += fv;
      const price = getPrice(h);
      if (price !== null) {
        const cat = getPriceCategory(price);
        buckets[cat].fv += fv;
        buckets[cat].count++;
        pricedFV += fv;
      }
    }
    return { period, buckets, totalFV, pricedFV };
  });
}

function computeStressed(loaded: LoadedPeriod[]): StressedRow[] {
  const history = new Map<string, {
    companyName: string;
    invType: string;
    points: Array<{ period: string; price: number; fv: number }>;
  }>();

  for (const { period, holdings } of loaded) {
    for (const h of holdings) {
      const price = getPrice(h);
      if (price === null) continue;
      const fv = getFV(h);
      if (fv <= 0) continue;
      const name = getDisplayName(h);
      if (!name) continue;
      const key = getCompanyKey(h);
      if (!history.has(key)) {
        history.set(key, { companyName: name, invType: getInvType(h), points: [] });
      }
      history.get(key)!.points.push({ period, price, fv });
    }
  }

  const rows: StressedRow[] = [];
  for (const [key, item] of history) {
    const sorted = [...item.points].sort((a, b) => a.period.localeCompare(b.period));
    const belowPar = sorted.filter((p) => p.price < 0.98);
    if (belowPar.length === 0) continue;

    const firstBelow = sorted.find((p) => p.price < 0.98)!;
    const lowest = sorted.reduce((a, b) => (a.price < b.price ? a : b));
    const latest = sorted[sorted.length - 1];
    const prev = sorted.length >= 2 ? sorted[sorted.length - 2] : null;

    let trend: StressedRow['trend'] = 'flat';
    if (latest.price >= 0.98) {
      trend = 'recovered';
    } else if (prev && latest.price > prev.price + 0.02) {
      trend = 'up';
    } else if (prev && latest.price < prev.price - 0.02) {
      trend = 'down';
    }

    rows.push({
      key,
      companyName: item.companyName,
      invType: item.invType,
      firstBelowParPeriod: firstBelow.period,
      periodsBelow: belowPar.length,
      currentPrice: latest.price,
      currentPeriod: latest.period,
      lowestPrice: lowest.price,
      lowestPricePeriod: lowest.period,
      priceHistory: sorted,
      trend,
    });
  }

  return rows.sort((a, b) => {
    // Most distressed first; recovered last
    if (a.trend === 'recovered' && b.trend !== 'recovered') return 1;
    if (b.trend === 'recovered' && a.trend !== 'recovered') return -1;
    const ap = a.currentPrice ?? 1;
    const bp = b.currentPrice ?? 1;
    return ap - bp;
  });
}

function computeNonAccrual(loaded: LoadedPeriod[]): NonAccrualRow[] {
  const history = new Map<string, {
    companyName: string;
    invType: string;
    points: Array<{ period: string; cashRate: number | null; price: number | null }>;
  }>();

  for (const { period, holdings } of loaded) {
    for (const h of holdings) {
      if (!isDebtInstrument(h)) continue;
      const name = getDisplayName(h);
      if (!name) continue;
      const key = getCompanyKey(h);
      if (!history.has(key)) {
        history.set(key, { companyName: name, invType: getInvType(h), points: [] });
      }
      history.get(key)!.points.push({ period, cashRate: getCashRatePct(h), price: getPrice(h) });
    }
  }

  const rows: NonAccrualRow[] = [];
  for (const [key, item] of history) {
    const sorted = [...item.points].sort((a, b) => a.period.localeCompare(b.period));
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1];
      const curr = sorted[i];
      const hadRate = prev.cashRate !== null && prev.cashRate > 0.5;
      const lostRate = curr.cashRate === null || curr.cashRate <= 0.5;
      if (!hadRate || !lostRate) continue;

      let lag: NonAccrualRow['lag'] = 'unknown';
      if (prev.price !== null && curr.price !== null) {
        const drop = prev.price - curr.price;
        if (drop > 0.05) lag = 'same_period';
        else if (drop <= 0.01) lag = 'no_markdown';
        else lag = 'lag';
      }

      rows.push({
        key,
        companyName: item.companyName,
        invType: item.invType,
        changePeriod: curr.period,
        rateBefore: prev.cashRate != null ? `${prev.cashRate.toFixed(2)}%` : '—',
        rateAfter: curr.cashRate != null && curr.cashRate > 0 ? `${curr.cashRate.toFixed(2)}%` : '0% / non-accrual',
        priceAtChange: curr.price,
        pricePrior: prev.price,
        lag,
      });
    }
  }

  return rows.sort((a, b) => b.changePeriod.localeCompare(a.changePeriod));
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function PriceHistoryChart({ row, onClose }: { row: StressedRow; onClose: () => void }) {
  if (row.priceHistory.length < 2) return null;
  const W = 480, H = 130;
  const padL = 36, padR = 12, padT = 12, padB = 28;
  const chartW = W - padL - padR;
  const chartH = H - padT - padB;
  const prices = row.priceHistory.map((p) => p.price);
  const minP = Math.min(0.5, ...prices) - 0.02;
  const maxP = Math.max(1.05, ...prices) + 0.01;
  const n = row.priceHistory.length;
  const xs = (i: number) => padL + (n === 1 ? chartW / 2 : (i / (n - 1)) * chartW);
  const ys = (p: number) => padT + (1 - (p - minP) / (maxP - minP)) * chartH;

  const pathD = row.priceHistory.map((pt, i) => `${i === 0 ? 'M' : 'L'}${xs(i).toFixed(1)},${ys(pt.price).toFixed(1)}`).join(' ');
  const parY = ys(0.98);

  return (
    <div className="window p-2 mt-1 mb-2">
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs font-semibold text-black">{row.companyName} — Price (FV/Cost) Over Time</div>
        <button className="btn text-xs px-1" onClick={onClose}>✕</button>
      </div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ maxWidth: W, display: 'block' }}>
        {/* par threshold line */}
        <line x1={padL} y1={parY} x2={W - padR} y2={parY} stroke="#228B22" strokeWidth="1" strokeDasharray="4,2" opacity="0.7" />
        <text x={W - padR - 2} y={parY - 3} fontSize="8" fill="#228B22" textAnchor="end">98¢ par</text>
        {/* price path */}
        <path d={pathD} fill="none" stroke="#0000cc" strokeWidth="1.5" />
        {/* data points */}
        {row.priceHistory.map((pt, i) => (
          <circle key={i} cx={xs(i)} cy={ys(pt.price)} r="3" fill={pt.price < 0.98 ? PRICE_CATEGORY_COLORS[getPriceCategory(pt.price)] : '#228B22'} />
        ))}
        {/* x-axis period labels */}
        {row.priceHistory.map((pt, i) => (
          <text key={i} x={xs(i)} y={H - 4} fontSize="7" fill="#808080" textAnchor="middle">{pt.period.slice(2, 7)}</text>
        ))}
        {/* y-axis labels */}
        {[0.6, 0.7, 0.8, 0.9, 0.98, 1.0].filter((v) => v >= minP && v <= maxP).map((v, i) => (
          <text key={i} x={padL - 3} y={ys(v) + 3} fontSize="8" fill="#808080" textAnchor="end">{Math.round(v * 100)}¢</text>
        ))}
      </svg>
    </div>
  );
}

function PortfolioHealthSection({ data }: { data: PeriodBucket[] }) {
  if (data.length === 0) return null;
  return (
    <div className="window p-3">
      <div className="text-xs font-semibold text-black mb-2">Portfolio Health By Period (Debt Instruments, FV-Weighted)</div>
      {/* Legend */}
      <div className="flex gap-3 mb-3 flex-wrap">
        {PRICE_CATS.map((cat) => (
          <div key={cat} className="flex items-center gap-1">
            <div style={{ width: 10, height: 10, backgroundColor: PRICE_CATEGORY_COLORS[cat], flexShrink: 0 }} />
            <span className="text-[10px] text-[#808080]">{PRICE_CATEGORY_LABELS[cat]}</span>
          </div>
        ))}
      </div>
      {/* Bars */}
      <div className="space-y-1">
        {data.map(({ period, buckets, pricedFV }) => (
          <div key={period} className="flex items-center gap-2">
            <div className="w-16 text-[10px] text-[#808080] text-right flex-shrink-0">{formatPeriodLabel(period)}</div>
            <div className="flex-1 flex h-4" style={{ minWidth: 0 }}>
              {PRICE_CATS.map((cat) => {
                const pct = pricedFV > 0 ? (buckets[cat].fv / pricedFV) * 100 : 0;
                if (pct < 0.05) return null;
                return (
                  <div
                    key={cat}
                    style={{ width: `${pct}%`, backgroundColor: PRICE_CATEGORY_COLORS[cat], flexShrink: 0 }}
                    title={`${PRICE_CATEGORY_LABELS[cat]}: ${pct.toFixed(1)}% (${buckets[cat].count} positions)`}
                  />
                );
              })}
            </div>
            <div className="w-20 text-[10px] text-[#808080] text-right flex-shrink-0">
              {formatThousandsAsCurrency(pricedFV)}
            </div>
          </div>
        ))}
      </div>
      {data.length === 0 && <div className="text-xs text-[#808080]">No data</div>}
    </div>
  );
}

function TrendBadge({ trend }: { trend: StressedRow['trend'] }) {
  const MAP = { up: { label: '↑ Improving', color: '#228B22' }, down: { label: '↓ Worsening', color: '#B22222' }, flat: { label: '→ Flat', color: '#808080' }, recovered: { label: '↑ Recovered', color: '#228B22' } };
  const { label, color } = MAP[trend];
  return <span style={{ color, fontSize: 10 }}>{label}</span>;
}

function LagBadge({ lag }: { lag: NonAccrualRow['lag'] }) {
  if (lag === 'same_period') return <span className="text-[10px]" style={{ color: '#228B22' }}>Same quarter ✓</span>;
  if (lag === 'lag')         return <span className="text-[10px]" style={{ color: '#DAA520' }}>Small markdown</span>;
  if (lag === 'no_markdown') return <span className="text-[10px]" style={{ color: '#B22222' }}>No markdown !</span>;
  return <span className="text-[10px] text-[#808080]">Unknown</span>;
}

function PriceBadge({ price }: { price: number | null }) {
  if (price === null) return <span className="text-[10px] text-[#808080]">—</span>;
  const cat = getPriceCategory(price);
  return <span style={{ color: PRICE_CATEGORY_COLORS[cat], fontSize: 10, fontWeight: 600 }}>{Math.round(price * 100)}¢</span>;
}

function StressedSection({ data }: { data: StressedRow[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const current = data.filter((r) => r.trend !== 'recovered');
  const recovered = data.filter((r) => r.trend === 'recovered');
  const display = showAll ? data : current;

  if (data.length === 0) {
    return (
      <div className="window p-3">
        <div className="text-xs font-semibold text-black mb-1">Stressed Investments (Ever Below Par)</div>
        <div className="text-xs text-[#808080]">No investments marked below 98¢ in any period.</div>
      </div>
    );
  }

  return (
    <div className="window p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-semibold text-black">
          Stressed Investments — {current.length} current, {recovered.length} recovered
        </div>
        {recovered.length > 0 && (
          <button className="btn text-[10px]" onClick={() => setShowAll(!showAll)}>
            {showAll ? 'Hide Recovered' : 'Show Recovered'}
          </button>
        )}
      </div>
      <div className="overflow-x-auto">
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #808080' }}>
              <th className="text-left py-1 pr-2 text-[#808080] font-semibold text-[10px]">Company</th>
              <th className="text-left py-1 pr-2 text-[#808080] font-semibold text-[10px]">Type</th>
              <th className="text-right py-1 pr-2 text-[#808080] font-semibold text-[10px]">Current</th>
              <th className="text-right py-1 pr-2 text-[#808080] font-semibold text-[10px]">Lowest</th>
              <th className="text-right py-1 pr-2 text-[#808080] font-semibold text-[10px]">Qtrs Below Par</th>
              <th className="text-left py-1 pr-2 text-[#808080] font-semibold text-[10px]">Trend</th>
              <th className="text-left py-1 text-[#808080] font-semibold text-[10px]">First Below Par</th>
            </tr>
          </thead>
          <tbody>
            {display.map((row) => (
              <Fragment key={row.key}>
                <tr
                  style={{ borderBottom: '1px solid #e0e0e0', cursor: 'pointer', background: expanded === row.key ? '#f0f0f0' : 'transparent' }}
                  onClick={() => setExpanded(expanded === row.key ? null : row.key)}
                >
                  <td className="py-1 pr-2 text-black" style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.companyName}>
                    {row.companyName}
                  </td>
                  <td className="py-1 pr-2 text-[#808080]" style={{ whiteSpace: 'nowrap' }}>{row.invType}</td>
                  <td className="py-1 pr-2 text-right"><PriceBadge price={row.currentPrice} /></td>
                  <td className="py-1 pr-2 text-right"><PriceBadge price={row.lowestPrice} /></td>
                  <td className="py-1 pr-2 text-right text-[10px] text-black">{row.periodsBelow}</td>
                  <td className="py-1 pr-2"><TrendBadge trend={row.trend} /></td>
                  <td className="py-1 text-[10px] text-[#808080]">{formatPeriodLabel(row.firstBelowParPeriod)}</td>
                </tr>
                {expanded === row.key && (
                  <tr>
                    <td colSpan={7} className="pb-2">
                      <PriceHistoryChart row={row} onClose={() => setExpanded(null)} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {display.length === 0 && <div className="text-xs text-[#808080] mt-1">No current stressed positions.</div>}
    </div>
  );
}

function NonAccrualSection({ data }: { data: NonAccrualRow[] }) {
  if (data.length === 0) {
    return (
      <div className="window p-3">
        <div className="text-xs font-semibold text-black mb-1">Non-Accrual Signals</div>
        <div className="text-xs text-[#808080]">No rate-drop events detected across available periods.</div>
      </div>
    );
  }

  return (
    <div className="window p-3">
      <div className="text-xs font-semibold text-black mb-1">
        Non-Accrual Signals — {data.length} rate-drop event{data.length !== 1 ? 's' : ''} detected
      </div>
      <div className="text-[10px] text-[#808080] mb-2">
        When a loan stops paying interest, did the BDC mark down fair value in the same quarter?
      </div>
      <div className="overflow-x-auto">
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #808080' }}>
              <th className="text-left py-1 pr-2 text-[#808080] font-semibold text-[10px]">Company</th>
              <th className="text-left py-1 pr-2 text-[#808080] font-semibold text-[10px]">Type</th>
              <th className="text-left py-1 pr-2 text-[#808080] font-semibold text-[10px]">Period</th>
              <th className="text-right py-1 pr-2 text-[#808080] font-semibold text-[10px]">Rate Before</th>
              <th className="text-right py-1 pr-2 text-[#808080] font-semibold text-[10px]">Rate After</th>
              <th className="text-right py-1 pr-2 text-[#808080] font-semibold text-[10px]">Price Before</th>
              <th className="text-right py-1 pr-2 text-[#808080] font-semibold text-[10px]">Price After</th>
              <th className="text-left py-1 text-[#808080] font-semibold text-[10px]">Markdown Timing</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.key + row.changePeriod} style={{ borderBottom: '1px solid #e0e0e0' }}>
                <td className="py-1 pr-2 text-black" style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.companyName}>
                  {row.companyName}
                </td>
                <td className="py-1 pr-2 text-[#808080]" style={{ whiteSpace: 'nowrap' }}>{row.invType}</td>
                <td className="py-1 pr-2 text-[10px] text-[#808080]">{formatPeriodLabel(row.changePeriod)}</td>
                <td className="py-1 pr-2 text-right text-[10px] text-black">{row.rateBefore}</td>
                <td className="py-1 pr-2 text-right text-[10px] text-[#808080]">{row.rateAfter}</td>
                <td className="py-1 pr-2 text-right"><PriceBadge price={row.pricePrior} /></td>
                <td className="py-1 pr-2 text-right"><PriceBadge price={row.priceAtChange} /></td>
                <td className="py-1"><LagBadge lag={row.lag} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function ValuationTab({ ticker, periods }: { ticker: string; periods: string[] }) {
  const allData = useBDCInvestmentsMultiple(ticker, periods);

  const loaded = useMemo<LoadedPeriod[]>(
    () =>
      allData
        .filter((d) => d.data?.investments?.length)
        .map((d) => ({ period: d.period, holdings: d.data!.investments as Holding[] }))
        .sort((a, b) => a.period.localeCompare(b.period)),
    [allData],
  );

  const loadingCount = allData.filter((d) => d.isLoading).length;
  const isLoading = loadingCount > 0;

  const periodBuckets = useMemo(() => computePeriodBuckets(loaded), [loaded]);
  const stressedRows = useMemo(() => computeStressed(loaded), [loaded]);
  const nonAccrualRows = useMemo(() => computeNonAccrual(loaded), [loaded]);

  return (
    <div className="flex flex-col h-full min-h-0 overflow-y-auto space-y-3 p-2">
      {isLoading && (
        <div className="text-[10px] text-[#808080]">
          Loading period data… ({periods.length - loadingCount}/{periods.length} ready)
        </div>
      )}
      {loaded.length === 0 && !isLoading && (
        <div className="window p-4 text-xs text-[#808080]">No investment data loaded.</div>
      )}
      {loaded.length > 0 && (
        <>
          <PortfolioHealthSection data={periodBuckets} />
          <StressedSection data={stressedRows} />
          <NonAccrualSection data={nonAccrualRows} />
        </>
      )}
    </div>
  );
}
