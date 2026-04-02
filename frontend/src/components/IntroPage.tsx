import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useBDCIndex, useCompanyExposures, useAllBDCProfiles } from '../api/hooks';
import { API_BASE } from '../api/client-csv';

type NavCardProps = {
  title: string;
  icon: string;
  description: string;
  detail: string;
  onClick: () => void;
};

function NavCard({ title, icon, description, detail, onClick }: NavCardProps) {
  return (
    <button
      onClick={onClick}
      className="window text-left p-4 flex flex-col gap-2 hover:bg-gray-50 active:bg-gray-100 w-full cursor-pointer group"
      style={{ minHeight: 140 }}
    >
      <div className="flex items-center gap-2">
        <span className="text-2xl">{icon}</span>
        <span className="text-sm font-bold text-[#000080]">{title}</span>
      </div>
      <p className="text-xs text-gray-700 flex-1">{description}</p>
      <div className="flex items-center justify-between mt-1">
        <span className="text-xs text-gray-500">{detail}</span>
        <span
          className="btn text-xs px-2 py-0.5"
          style={{ fontSize: 11 }}
        >
          Open →
        </span>
      </div>
    </button>
  );
}

type StatTileProps = {
  label: string;
  value: string | number;
  sub?: string;
};

function StatTile({ label, value, sub }: StatTileProps) {
  return (
    <div
      className="flex flex-col items-center justify-center p-3 gap-1"
      style={{
        background: '#ffffff',
        border: '2px inset #c0c0c0',
        borderTop: '2px solid #808080',
        borderLeft: '2px solid #808080',
        borderRight: '2px solid #ffffff',
        borderBottom: '2px solid #ffffff',
      }}
    >
      <span className="text-xl font-bold text-[#000080]">{value}</span>
      <span className="text-xs text-gray-700 text-center">{label}</span>
      {sub && <span className="text-[10px] text-gray-400 text-center">{sub}</span>}
    </div>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getRaw(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === 'number') return isFinite(v) ? v : null;
  if (typeof v === 'object' && 'raw' in (v as object)) {
    const n = (v as Record<string, unknown>).raw;
    return typeof n === 'number' && isFinite(n) ? n : null;
  }
  return null;
}

type NAVRow = {
  ticker: string;
  name: string;
  price: number;
  nav: number;
  discount: number; // (price/nav - 1), negative = discount
  pricesAsOf: string;
};

function DiscountBar({ discount }: { discount: number }) {
  // Centered bar: negative = red left, positive = blue right, scale capped ±40%
  const MAX = 40;
  const pct = Math.min(Math.abs(discount * 100), MAX);
  const isDiscount = discount < 0;
  return (
    <div className="flex items-center" style={{ width: 80, height: 10, gap: 0 }}>
      {/* left half */}
      <div style={{ width: 40, display: 'flex', justifyContent: 'flex-end' }}>
        {isDiscount && (
          <div style={{ width: `${(pct / MAX) * 100}%`, height: 8, backgroundColor: '#B22222' }} />
        )}
      </div>
      {/* center tick */}
      <div style={{ width: 1, height: 10, backgroundColor: '#808080', flexShrink: 0 }} />
      {/* right half */}
      <div style={{ width: 40 }}>
        {!isDiscount && (
          <div style={{ width: `${(pct / MAX) * 100}%`, height: 8, backgroundColor: '#000080' }} />
        )}
      </div>
    </div>
  );
}

function NAVTable({ rows }: { rows: NAVRow[] }) {
  const sorted = useMemo(() => [...rows].sort((a, b) => a.discount - b.discount), [rows]);
  const pricesAsOf = rows[0]?.pricesAsOf ?? '—';

  return (
    <div className="window">
      <div className="titlebar">
        <span>Discount / Premium to NAV</span>
      </div>
      <div className="p-3">
        <div className="text-[10px] text-[#808080] mb-2">
          Market price vs. net asset value per share. Negative = trading below book (discount). Prices as of {pricesAsOf}.
        </div>
        <div className="overflow-x-auto">
          <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 11 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #808080' }}>
                <th className="text-left py-1 pr-3 text-[#808080] font-semibold text-[10px]">BDC</th>
                <th className="text-right py-1 pr-3 text-[#808080] font-semibold text-[10px]">Price</th>
                <th className="text-right py-1 pr-3 text-[#808080] font-semibold text-[10px]">NAV/share</th>
                <th className="text-right py-1 pr-3 text-[#808080] font-semibold text-[10px]">Disc/Prem</th>
                <th className="py-1 text-[#808080] font-semibold text-[10px]" style={{ minWidth: 88 }}></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => {
                const pct = row.discount * 100;
                const isDiscount = pct < 0;
                const color = isDiscount ? '#B22222' : '#000080';
                return (
                  <tr key={row.ticker} style={{ borderBottom: '1px solid #f0f0f0' }}>
                    <td className="py-0.5 pr-3 font-semibold text-black" style={{ whiteSpace: 'nowrap' }}>
                      {row.ticker}
                      <span className="font-normal text-[#808080] ml-1 text-[9px]">{row.name}</span>
                    </td>
                    <td className="py-0.5 pr-3 text-right text-black">${row.price.toFixed(2)}</td>
                    <td className="py-0.5 pr-3 text-right text-[#808080]">${row.nav.toFixed(2)}</td>
                    <td className="py-0.5 pr-3 text-right font-semibold" style={{ color }}>
                      {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                    </td>
                    <td className="py-0.5"><DiscountBar discount={row.discount} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────

export function IntroPage() {
  const navigate = useNavigate();
  const { data: index } = useBDCIndex();
  const { data: exposures } = useCompanyExposures();

  const tickers = useMemo(() => index?.bdcs?.map((b) => b.ticker) ?? [], [index]);
  const allProfiles = useAllBDCProfiles(tickers);

  /** NAV/share from consolidated SEC XBRL (first NetAssetValuePerShare per latest filing). Yahoo bookValue is often wrong for BDCs. */
  const { data: navLatest } = useQuery({
    queryKey: ['nav-latest-sec'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/financials/nav_latest.json`, { cache: 'no-store' });
      if (!res.ok) return null;
      return res.json() as Promise<{ generated_at?: string; nav_per_share?: Record<string, number> }>;
    },
    staleTime: 24 * 60 * 60 * 1000,
  });

  const navRows = useMemo<NAVRow[]>(() => {
    const bdcMap = new Map((index?.bdcs ?? []).map((b) => [b.ticker, b.name]));
    const secNav = navLatest?.nav_per_share ?? {};
    const rows: NAVRow[] = [];
    for (const { ticker, data } of allProfiles) {
      if (!data) continue;
      const price = getRaw(data.price?.regularMarketPrice);
      const yahooNav = getRaw(data.summaryDetail?.bookValue);
      const nav = secNav[ticker] ?? yahooNav;
      if (!price || !nav || nav <= 0) continue;
      const discount = price / nav - 1;
      const rawDate = data.generated_at ? new Date(data.generated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
      rows.push({ ticker, name: bdcMap.get(ticker) ?? '', price, nav, discount, pricesAsOf: rawDate });
    }
    return rows;
  }, [allProfiles, index, navLatest]);

  const stats = useMemo(() => {
    const bdcCount = index?.bdcs?.length ?? 0;
    const rows = (exposures ?? []) as { company_id?: string; primary_industry?: string; total_exposure_millions?: number }[];
    const companyCount = new Set(rows.map((e) => e.company_id).filter(Boolean)).size;
    const sectorCount = new Set(rows.map((e) => e.primary_industry).filter(Boolean)).size;
    const lastUpdated = index?.generated_at
      ? new Date(index.generated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
      : '—';
    const totalExposure = rows.reduce((sum, r) => sum + (Number(r.total_exposure_millions) || 0), 0);
    const totalExposureStr =
      totalExposure >= 1000
        ? `$${(totalExposure / 1000).toFixed(1)}T`
        : `$${totalExposure.toFixed(0)}B`;
    return { bdcCount, companyCount, sectorCount, lastUpdated, totalExposureStr };
  }, [index, exposures]);

  return (
    <div className="flex items-start justify-center w-full h-full overflow-auto">
      <div className="w-full max-w-2xl flex flex-col gap-3 p-2 sm:p-4">

        {/* Title window */}
        <div className="window">
          <div className="titlebar">
            <span>BDC Extractor — Portfolio Intelligence</span>
          </div>
          <div className="p-4 flex flex-col gap-2">
            <p className="text-xs text-gray-700 leading-relaxed">
              Track Schedule of Investments filings across Business Development Companies.
              Analyze portfolio holdings, cross-BDC company exposure, sector allocations, and
              period-over-period changes — sourced directly from SEC EDGAR.
            </p>
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <StatTile label="BDCs tracked" value={stats.bdcCount || '—'} />
          <StatTile label="Portfolio companies" value={stats.companyCount ? stats.companyCount.toLocaleString() : '—'} />
          <StatTile label="Industries" value={stats.sectorCount || '—'} />
          <StatTile label="Last updated" value={stats.lastUpdated} />
        </div>

        {/* Navigation cards */}
        <div className="window">
          <div className="titlebar">
            <span>Navigate</span>
          </div>
          <div className="p-3 grid grid-cols-1 sm:grid-cols-3 gap-3">
            <NavCard
              title="BDC View"
              icon="🏦"
              description="Browse holdings for each BDC. Filter by investment type, sector, or period. Compare period-over-period changes."
              detail={stats.bdcCount ? `${stats.bdcCount} BDCs` : ''}
              onClick={() => navigate('/bdc')}
            />
            <NavCard
              title="Companies"
              icon="🏢"
              description="Look up any portfolio company across all BDCs. See total exposure, which lenders hold the debt, and maturity breakdown."
              detail={stats.companyCount ? `${stats.companyCount.toLocaleString()} companies` : ''}
              onClick={() => navigate('/companies')}
            />
            <NavCard
              title="Sectors"
              icon="📊"
              description="Analyze portfolio composition by industry sector. See BDC concentration, maturity ladders, and top holdings per sector."
              detail={stats.sectorCount ? `${stats.sectorCount} sectors` : ''}
              onClick={() => navigate('/sectors')}
            />
          </div>
        </div>

        {/* NAV discount table */}
        {navRows.length > 0 && <NAVTable rows={navRows} />}

        {/* Footer note */}
        <p className="text-[10px] text-gray-400 text-center px-2">
          Data sourced from SEC EDGAR filings. Values in thousands USD unless otherwise noted.
          Not financial advice.
        </p>

      </div>
    </div>
  );
}
