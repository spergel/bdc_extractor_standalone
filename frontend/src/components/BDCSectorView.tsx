import { useMemo, useState, useEffect } from 'react';
import type { Holding } from '../data/adapter';
import { getIndustryDistribution, getIndustry, getFV, getInvType, getMaturityDateStr, getInvestmentTypeDistribution, getMaturityLadder } from '../utils/holdingsAnalytics';
import { playClickSound } from '../utils/sounds';
import { formatThousandsAsCurrency, CURRENCY_M_LABEL } from '../utils/formatCurrency';
import { PieChart, MaturityLadderChart } from './Charts';
import type { PieChartDatum, MaturityLadderDatum } from './Charts';

type Props = {
  holdings: Holding[];
  period?: string;
  ticker?: string;
  onCompanyClick?: (companyId: string) => void;
};

export function BDCSectorView({ holdings, period, onCompanyClick }: Props) {
  const sectors = useMemo(() => getIndustryDistribution(holdings), [holdings]);
  const [selectedSector, setSelectedSector] = useState<string | null>(null);

  useEffect(() => {
    if (sectors.length === 0) return;
    const names = new Set(sectors.map((s) => s.category));
    if (!selectedSector || !names.has(selectedSector)) {
      setSelectedSector(sectors[0].category);
    }
  }, [sectors, selectedSector]);

  const holdingsInSector = useMemo(() => {
    if (!selectedSector) return [];
    return holdings.filter((h) => getIndustry(h) === selectedSector);
  }, [holdings, selectedSector]);

  const totalFV = useMemo(() => holdings.reduce((s, h) => s + getFV(h), 0), [holdings]);

  const sectorPieData: PieChartDatum[] = useMemo(
    () =>
      sectors.map((s) => ({
        category: s.category,
        count: s.count,
        fairValue: s.fairValue,
        percentage: s.percentage,
      })),
    [sectors]
  );

  const typeDistForSector = useMemo(
    () => getInvestmentTypeDistribution(holdingsInSector),
    [holdingsInSector]
  );
  const maturityLadderForSector = useMemo(
    () => getMaturityLadder(holdingsInSector),
    [holdingsInSector]
  );
  const maturityLadderData: MaturityLadderDatum[] = useMemo(
    () =>
      maturityLadderForSector
        .filter((d) => d.fairValue > 0)
        .map((d) => ({ bucket: d.bucket, count: d.count, fairValue: d.fairValue, percentage: d.percentage })),
    [maturityLadderForSector]
  );

  if (holdings.length === 0) {
    return (
      <div className="window p-6">
        <div className="text-sm text-[#808080]">No holdings data for this period.</div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      <div className="mb-2 flex items-center gap-2 flex-wrap flex-shrink-0 text-xs text-[#808080]">
        <span>View this BDC&apos;s portfolio by sector (industry). Amounts in $M unless noted.</span>
        {period && <span>Period: {period}</span>}
      </div>

      {sectorPieData.length > 0 && (
        <div className="flex-shrink-0 mb-2">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            <PieChart
              data={sectorPieData}
              title="Sector distribution ($M)"
              byValue
              onSliceClick={(cat) => setSelectedSector(cat)}
              selectedCategory={selectedSector}
            />
            {selectedSector && typeDistForSector.length > 0 && (
              <PieChart data={typeDistForSector} title={`Investment type: ${selectedSector} ($M)`} byValue />
            )}
            {selectedSector && maturityLadderData.length > 0 && (
              <MaturityLadderChart data={maturityLadderData} />
            )}
          </div>
        </div>
      )}

      <div className="flex flex-1 min-h-0 gap-3 overflow-hidden">
        <div className="window flex-shrink-0 w-56 flex flex-col min-h-0 overflow-hidden">
          <div className="p-2 border-b border-[#808080] text-xs font-semibold text-black">Sectors</div>
          <div className="overflow-y-auto flex-1 min-h-0 p-1">
            {sectors.map((s) => {
              const isSelected = selectedSector === s.category;
              const expStr = formatThousandsAsCurrency(s.fairValue);
              return (
                <button
                  key={s.category}
                  type="button"
                  className={`w-full text-left px-2 py-1.5 text-xs rounded-sm mb-0.5 ${isSelected ? 'bg-[#0000ff] text-white' : 'hover:bg-[#e0e0e0] text-black'}`}
                  onClick={() => {
                    playClickSound();
                    setSelectedSector(s.category);
                  }}
                >
                  <div className="font-medium truncate" title={s.category}>
                    {s.category}
                  </div>
                  <div className={isSelected ? 'text-[#c0c0c0]' : 'text-[#808080]'}>
                    {s.count} pos · {expStr} ({s.percentage.toFixed(1)}%)
                  </div>
                </button>
              );
            })}
          </div>
        </div>
        <div className="window flex-1 min-h-0 overflow-hidden flex flex-col">
          {selectedSector ? (
            <>
              <div className="p-2 border-b border-[#808080] flex items-center justify-between flex-wrap gap-1">
                <span className="text-xs font-semibold text-black">{selectedSector}</span>
                <span className="text-xs text-[#808080]">
                  {holdingsInSector.length} positions · {formatThousandsAsCurrency(holdingsInSector.reduce((s, h) => s + getFV(h), 0))}
                  {totalFV > 0 && (
                    <> ({(holdingsInSector.reduce((s, h) => s + getFV(h), 0) / totalFV * 100).toFixed(1)}%)</>
                  )}
                </span>
              </div>
              <div className="overflow-auto flex-1 min-h-0">
                <table className="w-full text-xs border-collapse">
                  <thead className="sticky top-0 bg-[#c0c0c0]">
                    <tr>
                      <th className="text-left px-2 py-1.5 border border-[#808080]">Company</th>
                      <th className="text-left px-2 py-1.5 border border-[#808080]">Type</th>
                      <th className="text-right px-2 py-1.5 border border-[#808080]">{CURRENCY_M_LABEL}</th>
                      <th className="text-right px-2 py-1.5 border border-[#808080]">% of portfolio</th>
                      <th className="text-left px-2 py-1.5 border border-[#808080]">Maturity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {holdingsInSector.map((h, i) => {
                      const fv = getFV(h);
                      const pct = totalFV > 0 ? (fv / totalFV) * 100 : 0;
                      const name = h.company_name_clean || h.company_name || '—';
                      const companyId = h.company_id;
                      return (
                        <tr key={i} className="hover:bg-[#e8e8e8]">
                          <td className="px-2 py-1.5 border border-[#808080]">
                            {companyId && onCompanyClick ? (
                              <button
                                type="button"
                                className="text-left text-[#0000ff] hover:underline font-medium"
                                onClick={() => {
                                  playClickSound();
                                  onCompanyClick(companyId);
                                }}
                              >
                                {name}
                              </button>
                            ) : (
                              <span>{name}</span>
                            )}
                          </td>
                          <td className="px-2 py-1.5 border border-[#808080]">{getInvType(h)}</td>
                          <td className="px-2 py-1.5 border border-[#808080] text-right">{formatThousandsAsCurrency(fv)}</td>
                          <td className="px-2 py-1.5 border border-[#808080] text-right">{pct.toFixed(2)}%</td>
                          <td className="px-2 py-1.5 border border-[#808080]">{getMaturityDateStr(h) ?? '—'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="flex items-center justify-center h-full text-xs text-[#808080] p-4">
              Select a sector from the list.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
