import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useBDCIndex, useCompanyExposures } from '../api/hooks';

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

export function IntroPage() {
  const navigate = useNavigate();
  const { data: index } = useBDCIndex();
  const { data: exposures } = useCompanyExposures();

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

        {/* Footer note */}
        <p className="text-[10px] text-gray-400 text-center px-2">
          Data sourced from SEC EDGAR filings. Values in thousands USD unless otherwise noted.
          Not financial advice.
        </p>

      </div>
    </div>
  );
}
