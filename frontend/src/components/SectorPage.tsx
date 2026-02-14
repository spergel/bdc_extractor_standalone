import { useMemo, useState } from 'react';
import { useCompanyExposures, useCompanyDetail } from '../api/hooks';
import type { CompanyExposure } from '../data/adapter';
import { playClickSound } from '../utils/sounds';
import { formatMillionsAsCurrency } from '../utils/formatCurrency';
import { PieChart, MaturityLadderChart } from './Charts';
import type { PieChartDatum, MaturityLadderDatum } from './Charts';

type Props = {
  sector: string | undefined;
  onSelectCompany: (companyId: string) => void;
  onSwitchToCompanies: () => void;
};

export function SectorPage({ sector, onSelectCompany, onSwitchToCompanies }: Props) {
  const { data: exposures, isLoading, error } = useCompanyExposures();
  const { data: detailMap } = useCompanyDetail();

  const [view, setView] = useState<'charts' | 'table'>(() => {
    try {
      const saved = localStorage.getItem('sector_view');
      if (saved === 'charts' || saved === 'table') return saved;
    } catch { /* ignore */ }
    return 'charts';
  });

  const companies = useMemo(() => {
    if (!sector || !exposures?.length) return [];
    const arr = exposures as CompanyExposure[];
    return arr
      .filter((e) => ((e.primary_industry ?? 'Other').trim() || 'Other') === sector)
      .sort((a, b) => (Number(b.total_exposure_millions) || 0) - (Number(a.total_exposure_millions) || 0));
  }, [sector, exposures]);

  const sectorAnalysis = useMemo(() => {
    if (!companies.length) return null;
    const bdcCount: Record<string, number> = {};
    const byBdcExposure: Record<string, number> = {};
    let totalRate = 0;
    let rateCount = 0;
    const byMaturity: Record<string, number> = {};
    const byType: Record<string, number> = {};
    for (const c of companies) {
      const id = c.company_id ?? '';
      (c.bdcs_invested ?? '')
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean)
        .forEach((t) => {
          bdcCount[t] = (bdcCount[t] ?? 0) + 1;
        });
      const ar = Number(c.avg_interest_rate);
      if (!Number.isNaN(ar)) {
        totalRate += ar;
        rateCount += 1;
      }
      if (detailMap?.[id]) {
        Object.entries(detailMap[id].by_bdc || {}).forEach(([ticker, millions]) => {
          byBdcExposure[ticker] = (byBdcExposure[ticker] ?? 0) + millions;
        });
        Object.entries(detailMap[id].by_maturity || {}).forEach(([k, v]) => {
          byMaturity[k] = (byMaturity[k] ?? 0) + v;
        });
        Object.entries(detailMap[id].by_investment_type || {}).forEach(([k, v]) => {
          byType[k] = (byType[k] ?? 0) + v;
        });
      }
    }
    const topBdcs = Object.entries(bdcCount)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 12)
      .map(([ticker, count]) => ({ ticker, count }));
    return {
      topBdcs,
      byBdcExposure: Object.keys(byBdcExposure).length ? byBdcExposure : null,
      avgInterestRate: rateCount ? totalRate / rateCount : null,
      byMaturity: Object.keys(byMaturity).length ? byMaturity : null,
      byType: Object.keys(byType).length ? byType : null,
    };
  }, [companies, detailMap]);

  const maturityOrder = ['Before 2026', '2026', '2027', '2028', '2029', '2030', '2030+', 'No date'];

  const sectorChartData = useMemo(() => {
    if (!sectorAnalysis) return null;
    const { byBdcExposure, byMaturity, byType } = sectorAnalysis;
    const totalBdc = byBdcExposure ? Object.values(byBdcExposure).reduce((s, v) => s + v, 0) : 0;
    const totalType = byType ? Object.values(byType).reduce((s, v) => s + v, 0) : 0;
    const totalMat = byMaturity ? Object.values(byMaturity).reduce((s, v) => s + v, 0) : 0;

    const bdcPieData: PieChartDatum[] = byBdcExposure
      ? Object.entries(byBdcExposure)
          .map(([category, millions]) => ({
            category,
            count: 0,
            fairValue: millions * 1000,
            percentage: totalBdc > 0 ? (millions / totalBdc) * 100 : 0,
          }))
          .sort((a, b) => b.fairValue - a.fairValue)
      : [];

    const typePieData: PieChartDatum[] = byType
      ? Object.entries(byType)
          .map(([category, millions]) => ({
            category,
            count: 0,
            fairValue: millions * 1000,
            percentage: totalType > 0 ? (millions / totalType) * 100 : 0,
          }))
          .sort((a, b) => b.fairValue - a.fairValue)
      : [];

    const maturityLadderData: MaturityLadderDatum[] = byMaturity
      ? [...maturityOrder, ...Object.keys(byMaturity).filter((k) => !maturityOrder.includes(k))]
          .filter((bucket) => (byMaturity[bucket] ?? 0) > 0)
          .map((bucket) => {
            const millions = byMaturity[bucket] ?? 0;
            return {
              bucket,
              count: 0,
              fairValue: millions * 1000,
              percentage: totalMat > 0 ? (millions / totalMat) * 100 : 0,
            };
          })
      : [];

    return { bdcPieData, typePieData, maturityLadderData };
  }, [sectorAnalysis]);

  if (!sector) {
    return (
      <div className="window p-6">
        <div className="text-sm text-[#808080]">Select a sector from the sidebar.</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="window p-6">
        <div className="text-sm text-[#ff0000]">Error loading data: {error.message}</div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="window p-6">
        <div className="text-sm text-[#808080]">Loading…</div>
      </div>
    );
  }

  const totalExp = companies.reduce((sum, c) => sum + (Number(c.total_exposure_millions) || 0), 0);
  const expStr = formatMillionsAsCurrency(totalExp);

  const setViewAndSave = (v: 'charts' | 'table') => {
    playClickSound();
    setView(v);
    try {
      localStorage.setItem('sector_view', v);
    } catch { /* ignore */ }
  };

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden p-4">
      <div className="window p-4 flex-shrink-0 mb-2">
        <h2 className="text-base font-bold text-black">{sector}</h2>
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="text-xs text-[#808080]">
            {companies.length} companies • Total exposure {expStr}
            {sectorAnalysis?.avgInterestRate != null && (
              <> • Avg rate {sectorAnalysis.avgInterestRate.toFixed(2)}%</>
            )}
          </div>
          <div className="flex items-center gap-1 text-xs">
            <button
              type="button"
              className={`btn text-xs ${view === 'charts' ? 'pressed' : ''}`}
              onClick={() => setViewAndSave('charts')}
            >
              Charts
            </button>
            <button
              type="button"
              className={`btn text-xs ${view === 'table' ? 'pressed' : ''}`}
              onClick={() => setViewAndSave('table')}
            >
              Table
            </button>
          </div>
        </div>
      </div>

      {view === 'charts' && sectorChartData && (
        <div className="flex-1 min-h-0 overflow-y-auto">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {sectorChartData.bdcPieData.length > 0 && (
              <PieChart data={sectorChartData.bdcPieData} title="Exposure by BDC (in this sector)" byValue />
            )}
            {sectorChartData.typePieData.length > 0 && (
              <PieChart data={sectorChartData.typePieData} title="Investment type (sector total)" byValue />
            )}
            {sectorChartData.maturityLadderData.length > 0 && (
              <MaturityLadderChart data={sectorChartData.maturityLadderData} />
            )}
          </div>
        </div>
      )}

      {view === 'table' && (
        <>
          {sectorAnalysis && (sectorAnalysis.topBdcs.length > 0 || sectorAnalysis.byMaturity || sectorAnalysis.byType) && (
            <div className="window p-4 flex-shrink-0 mb-2 space-y-3">
              <div className="text-xs font-semibold text-black">Sector analysis</div>
              {sectorAnalysis.topBdcs.length > 0 && (
                <div>
                  <div className="text-[10px] text-[#808080] mb-1">BDCs with exposure in this sector (company count)</div>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
                    {sectorAnalysis.topBdcs.map(({ ticker, count }) => (
                      <span key={ticker}>
                        <span className="font-medium">{ticker}</span>
                        <span className="text-[#808080]"> ({count})</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {sectorAnalysis.byMaturity && (
                <div>
                  <div className="text-[10px] text-[#808080] mb-1">Maturity breakdown (sector total)</div>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
                    {[...maturityOrder, ...Object.keys(sectorAnalysis.byMaturity).filter((k) => !maturityOrder.includes(k))]
                      .filter((k) => (sectorAnalysis.byMaturity![k] ?? 0) > 0)
                      .map((k) => (
                        <span key={k}>
                          <span className="text-[#808080]">{k}:</span> {formatMillionsAsCurrency(sectorAnalysis.byMaturity![k])}
                        </span>
                      ))}
                  </div>
                </div>
              )}
              {sectorAnalysis.byType && (
                <div>
                  <div className="text-[10px] text-[#808080] mb-1">Investment type (sector total)</div>
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
                    {Object.entries(sectorAnalysis.byType)
                      .sort(([, a], [, b]) => b - a)
                      .slice(0, 8)
                      .map(([typeName, millions]) => (
                        <span key={typeName}>
                          <span className="text-[#808080]">{typeName}:</span> {formatMillionsAsCurrency(millions)}
                        </span>
                      ))}
                  </div>
                </div>
              )}
            </div>
          )}

          <div className="window flex-1 min-h-0 overflow-auto">
            <table className="w-full text-xs border-collapse">
          <thead className="sticky top-0 bg-[#c0c0c0]">
            <tr>
              <th className="text-left px-2 py-1.5 border border-[#808080]">Company</th>
              <th className="text-right px-2 py-1.5 border border-[#808080]">Exposure ($M)</th>
              <th className="text-right px-2 py-1.5 border border-[#808080]"># BDCs</th>
              <th className="text-left px-2 py-1.5 border border-[#808080]">BDCs</th>
            </tr>
          </thead>
          <tbody>
            {companies.map((e) => {
              const id = e.company_id ?? '';
              const exp = Number(e.total_exposure_millions) || 0;
              return (
                <tr key={id} className="hover:bg-[#e8e8e8]">
                  <td className="px-2 py-1.5 border border-[#808080]">
                    <button
                      type="button"
                      className="text-left text-[#0000ff] hover:underline font-medium"
                      onClick={() => {
                        playClickSound();
                        onSelectCompany(id);
                        onSwitchToCompanies();
                      }}
                    >
                      {e.company_name ?? id}
                    </button>
                  </td>
                  <td className="text-right px-2 py-1.5 border border-[#808080]">{formatMillionsAsCurrency(exp)}</td>
                  <td className="text-right px-2 py-1.5 border border-[#808080]">{e.num_bdcs_invested ?? 0}</td>
                  <td className="px-2 py-1.5 border border-[#808080] text-[#808080]">{e.bdcs_invested ?? '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
          </div>
        </>
      )}

      {view === 'charts' && (!sectorChartData || (sectorChartData.bdcPieData.length === 0 && sectorChartData.typePieData.length === 0 && sectorChartData.maturityLadderData.length === 0)) && (
        <div className="window p-4 flex-1 flex items-center justify-center text-xs text-[#808080]">
          No chart data for this sector. Switch to Table to see companies, or ensure company_detail.json is built.
        </div>
      )}
    </div>
  );
}
