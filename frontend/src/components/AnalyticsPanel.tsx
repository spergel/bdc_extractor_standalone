import { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import type { Holding } from '../data/adapter';
import {
  getIndustryDistribution,
  getInvestmentTypeDistribution,
  getRateStructure,
  getPIKAnalysis,
  getMaturityLadder,
  getSpreadStats,
  getSpreadDistribution,
  getFloorRateAnalysis,
  getAverageSpreadByIndustry,
  getAverageSpreadByInvestmentType,
  getTopHoldings,
  getFVRatioStats,
  getFVRatioDistribution,
  getFV,
  getPrincipal,
  getCost,
  checkRedFlags,
  type RedFlag,
} from '../utils/holdingsAnalytics';
import { type DrillDownSelection, getFilteredHoldings } from '../utils/drillDownFilters';
import { DrillDownTable } from './DrillDownTable';
import { PieChart, MaturityLadderChart } from './Charts';

type Props = {
  holdings: Holding[];
  period?: string;
};

// Histogram bar chart component
function HistogramChart({ data, title, onBucketClick, selectedBucket, rangeControls }: {
  data: Array<{ range: string; count: number; percentage: number }>;
  title: string;
  onBucketClick?: (idx: number, range: string) => void;
  selectedBucket?: number | null;
  rangeControls?: {
    range: [number, number];
    onRangeChange: (range: [number, number]) => void;
    outlierCount: number;
  };
}) {
  const [hovered, setHovered] = useState<{ item: typeof data[0]; x: number; y: number } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  if (data.length === 0 && !rangeControls) {
    return (
      <div className="window p-3">
        <div className="text-xs text-[#808080]">{title}: No data</div>
      </div>
    );
  }

  const maxCount = Math.max(...data.map(d => d.count), 1);

  const handleMouseEnter = useCallback((e: React.MouseEvent<HTMLDivElement>, item: typeof data[0]) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    setHovered({ item, x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, []);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>, item: typeof data[0]) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    setHovered({ item, x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, []);

  const handleMouseLeave = useCallback(() => setHovered(null), []);

  return (
    <div ref={containerRef} className="window p-3 relative">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <div className="text-xs font-semibold text-black">{title}</div>
        {rangeControls && (
          <>
            <input
              type="number"
              step="0.05"
              className="input text-xs w-16 px-1 py-0"
              value={rangeControls.range[0]}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                if (!isNaN(v)) rangeControls.onRangeChange([v, rangeControls.range[1]]);
              }}
            />
            <span className="text-xs text-[#808080]">to</span>
            <input
              type="number"
              step="0.05"
              className="input text-xs w-16 px-1 py-0"
              value={rangeControls.range[1]}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                if (!isNaN(v)) rangeControls.onRangeChange([rangeControls.range[0], v]);
              }}
            />
            <button className="btn text-[10px] px-1 py-0" onClick={() => rangeControls.onRangeChange([0.90, 1.10])}>Tight</button>
            <button className="btn text-[10px] px-1 py-0" onClick={() => rangeControls.onRangeChange([0.50, 1.50])}>Wide</button>
            <button className="btn text-[10px] px-1 py-0" onClick={() => rangeControls.onRangeChange([0, 99])}>All</button>
            {rangeControls.outlierCount > 0 && (
              <span className="text-[10px] text-[#808080]">({rangeControls.outlierCount} outside range)</span>
            )}
          </>
        )}
      </div>
      {data.length === 0 ? (
        <div className="text-xs text-[#808080]">No holdings in this range</div>
      ) : (
        <div className="space-y-1">
          {data.map((item, i) => (
            <div
              key={i}
              className="flex items-center gap-2"
              onMouseEnter={(e) => handleMouseEnter(e, item)}
              onMouseMove={(e) => handleMouseMove(e, item)}
              onMouseLeave={handleMouseLeave}
              onClick={() => onBucketClick?.(i, item.range)}
              style={{ cursor: onBucketClick ? 'pointer' : 'default' }}
            >
              <div className="w-24 text-xs text-[#808080] truncate" title={item.range}>{item.range}</div>
              <div className="flex-1 bg-[#c0c0c0] h-4 overflow-hidden">
                <div
                  className="h-full transition-opacity"
                  style={{
                    width: `${(item.count / maxCount) * 100}%`,
                    backgroundColor: selectedBucket === i ? '#000080' : '#0000ff',
                    opacity: hovered?.item.range === item.range || selectedBucket === i ? 1 : hovered ? 0.5 : 1,
                  }}
                />
              </div>
              <div className="w-20 text-xs text-[#808080] text-right">
                {item.count} ({item.percentage.toFixed(1)}%)
              </div>
            </div>
          ))}
        </div>
      )}
      {hovered && (
        <div
          className="absolute pointer-events-none z-10 bg-white border-2 border-[#000000] px-2 py-1 text-xs text-black"
              style={{
                left: `${hovered.x + 10}px`,
                top: `${hovered.y - 10}px`,
              }}
        >
          <div className="text-black font-medium">{hovered.item.range}</div>
          <div className="text-black">{hovered.item.count} holdings</div>
          <div className="text-black">{hovered.item.percentage.toFixed(1)}%</div>
        </div>
      )}
    </div>
  );
}

export function AnalyticsPanel({ holdings, period }: Props) {
  const [redFlagFilter, setRedFlagFilter] = useState<RedFlag['type'] | 'all'>('all');
  const [drillDown, setDrillDown] = useState<DrillDownSelection>(null);

  // Clear drill-down when holdings change (new ticker/period)
  useEffect(() => { setDrillDown(null); }, [holdings]);

  const drillDownHoldings = useMemo(
    () => drillDown ? getFilteredHoldings(holdings, drillDown) : [],
    [holdings, drillDown]
  );

  const drillDownTitle = useMemo(() => {
    if (!drillDown) return '';
    switch (drillDown.source) {
      case 'spread': return `Spread: ${drillDown.range}`;
      case 'fv-principal': return `FV/Principal: ${drillDown.range}`;
      case 'fv-cost': return `FV/Cost: ${drillDown.range}`;
      case 'industry': return `Industry: ${drillDown.category}`;
      case 'type': return `Type: ${drillDown.category}`;
    }
  }, [drillDown]);

  // Toggle helpers - clicking same element again clears drill-down
  const handleSpreadClick = useCallback((idx: number, range: string) => {
    setDrillDown(prev =>
      prev?.source === 'spread' && prev.bucketIndex === idx
        ? null
        : { source: 'spread', bucketIndex: idx, range }
    );
  }, []);

  const handleFVPrincipalClick = useCallback((idx: number, range: string) => {
    setDrillDown(prev =>
      prev?.source === 'fv-principal' && prev.bucketIndex === idx
        ? null
        : { source: 'fv-principal', bucketIndex: idx, range }
    );
  }, []);

  const handleFVCostClick = useCallback((idx: number, range: string) => {
    setDrillDown(prev =>
      prev?.source === 'fv-cost' && prev.bucketIndex === idx
        ? null
        : { source: 'fv-cost', bucketIndex: idx, range }
    );
  }, []);

  const handleIndustryClick = useCallback((category: string) => {
    setDrillDown(prev =>
      prev?.source === 'industry' && prev.category === category
        ? null
        : { source: 'industry', category }
    );
  }, []);

  const handleTypeClick = useCallback((category: string) => {
    setDrillDown(prev =>
      prev?.source === 'type' && prev.category === category
        ? null
        : { source: 'type', category }
    );
  }, []);

  const industryDist = useMemo(() => getIndustryDistribution(holdings), [holdings]);
  const typeDist = useMemo(() => getInvestmentTypeDistribution(holdings), [holdings]);
  const rateStruct = useMemo(() => getRateStructure(holdings), [holdings]);
  const ratePieData = useMemo(
    () => [
      {
        category: 'Variable',
        count: rateStruct.variable.count,
        fairValue: rateStruct.variable.fairValue,
        percentage: rateStruct.variable.percentage,
      },
      {
        category: 'Fixed',
        count: rateStruct.fixed.count,
        fairValue: rateStruct.fixed.fairValue,
        percentage: rateStruct.fixed.percentage,
      },
    ],
    [rateStruct],
  );
  const pikAnalysis = useMemo(() => getPIKAnalysis(holdings), [holdings]);
  const maturityLadder = useMemo(() => getMaturityLadder(holdings), [holdings]);
  const spreadStats = useMemo(() => getSpreadStats(holdings), [holdings]);
  const spreadDistribution = useMemo(() => getSpreadDistribution(holdings), [holdings]);
  const floorAnalysis = useMemo(() => getFloorRateAnalysis(holdings), [holdings]);
  const avgSpreadByIndustry = useMemo(() => getAverageSpreadByIndustry(holdings), [holdings]);
  const avgSpreadByType = useMemo(() => getAverageSpreadByInvestmentType(holdings), [holdings]);
  const topHoldings = useMemo(() => getTopHoldings(holdings, 10), [holdings]);
  const fvRatios = useMemo(() => getFVRatioStats(holdings), [holdings]);

  const [fvPrincipalRange, setFvPrincipalRange] = useState<[number, number]>([0.85, 1.15]);
  const [fvCostRange, setFvCostRange] = useState<[number, number]>([0.85, 1.15]);

  const fvPrincipalRawRatios = useMemo(() => {
    const ratios: number[] = [];
    holdings.forEach(h => {
      const fv = getFV(h);
      const principal = getPrincipal(h);
      if (principal > 0 && fv > 0) {
        const ratio = fv / principal;
        if (ratio >= 0.01 && ratio <= 5) ratios.push(ratio);
      }
    });
    return ratios;
  }, [holdings]);
  const fvPrincipalRatios = useMemo(
    () => getFVRatioDistribution(fvPrincipalRawRatios, fvPrincipalRange[0], fvPrincipalRange[1]),
    [fvPrincipalRawRatios, fvPrincipalRange]
  );
  const fvPrincipalOutliers = useMemo(
    () => fvPrincipalRawRatios.filter(r => r < fvPrincipalRange[0] || r > fvPrincipalRange[1]).length,
    [fvPrincipalRawRatios, fvPrincipalRange]
  );

  const fvCostRawRatios = useMemo(() => {
    const ratios: number[] = [];
    holdings.forEach(h => {
      const fv = getFV(h);
      const cost = getCost(h);
      if (cost > 0 && fv > 0) {
        const ratio = fv / cost;
        if (ratio >= 0.01 && ratio <= 5) ratios.push(ratio);
      }
    });
    return ratios;
  }, [holdings]);
  const fvCostRatios = useMemo(
    () => getFVRatioDistribution(fvCostRawRatios, fvCostRange[0], fvCostRange[1]),
    [fvCostRawRatios, fvCostRange]
  );
  const fvCostOutliers = useMemo(
    () => fvCostRawRatios.filter(r => r < fvCostRange[0] || r > fvCostRange[1]).length,
    [fvCostRawRatios, fvCostRange]
  );
  // Red flags
  const redFlags = useMemo(() => {
    return holdings.map(h => ({
      holding: h,
      flags: checkRedFlags(h, period || ''),
    })).filter(item => item.flags.length > 0);
  }, [holdings, period]);
  
  const filteredRedFlags = useMemo(() => {
    if (redFlagFilter === 'all') return redFlags;
    return redFlags.filter(item => item.flags.some(f => f.type === redFlagFilter));
  }, [redFlags, redFlagFilter]);

  return (
    <div className="space-y-4 overflow-auto">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <PieChart data={industryDist} title="Industry Distribution" byValue onSliceClick={handleIndustryClick} selectedCategory={drillDown?.source === 'industry' ? drillDown.category : null} />
        <PieChart data={typeDist} title="Investment Type Distribution" byValue onSliceClick={handleTypeClick} selectedCategory={drillDown?.source === 'type' ? drillDown.category : null} />
        <PieChart data={ratePieData} title="Variable vs Fixed Rate" />
        <MaturityLadderChart data={maturityLadder} />
      </div>
      
      {/* Spread Distribution and Floor Rate */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <HistogramChart data={spreadDistribution} title="Spread Distribution" onBucketClick={handleSpreadClick} selectedBucket={drillDown?.source === 'spread' ? drillDown.bucketIndex : null} />
        <div className="window p-3">
          <div className="text-xs font-semibold mb-2 text-black">Floor Rate Analysis</div>
          <div className="space-y-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-[#808080]">With Floor:</span>
              <span className="text-black">{floorAnalysis.withFloor.count} ({floorAnalysis.withFloor.percentage.toFixed(1)}%)</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[#808080]">Without Floor:</span>
              <span className="text-black">{floorAnalysis.withoutFloor.count} ({floorAnalysis.withoutFloor.percentage.toFixed(1)}%)</span>
            </div>
            {floorAnalysis.withFloor.count > 0 && (
              <>
                <div className="pt-2 border-t border-[#808080]">
                  <div className="text-[#808080] mb-1">Floor Statistics:</div>
                  <div className="space-y-1 text-[#808080]">
                    <div>Avg: {floorAnalysis.averageFloor.toFixed(2)}%</div>
                    <div>Min: {floorAnalysis.minFloor.toFixed(2)}%</div>
                    <div>Max: {floorAnalysis.maxFloor.toFixed(2)}%</div>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
      
      {/* Average Spread by Category */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="window p-3">
          <div className="text-xs font-semibold mb-2 text-black">Average Spread by Industry</div>
          {avgSpreadByIndustry.length === 0 ? (
            <div className="text-xs text-[#808080]">No spread data available</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[#808080]">
                    <th className="text-left py-1 text-[#808080]">Industry</th>
                    <th className="text-right py-1 text-[#808080]">Avg Spread</th>
                    <th className="text-right py-1 text-[#808080]">Count</th>
                  </tr>
                </thead>
                <tbody>
                  {avgSpreadByIndustry.slice(0, 10).map((item, i) => (
                    <tr key={i} className="border-b border-[#c0c0c0]">
                      <td className="py-1 text-black truncate max-w-[200px]" title={item.category}>{item.category}</td>
                      <td className="text-right py-1 text-[#808080]">{item.averageSpread.toFixed(2)}%</td>
                      <td className="text-right py-1 text-[#808080]">{item.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div className="window p-3">
          <div className="text-xs font-semibold mb-2 text-black">Average Spread by Investment Type</div>
          {avgSpreadByType.length === 0 ? (
            <div className="text-xs text-[#808080]">No spread data available</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[#808080]">
                    <th className="text-left py-1 text-[#808080]">Type</th>
                    <th className="text-right py-1 text-[#808080]">Avg Spread</th>
                    <th className="text-right py-1 text-[#808080]">Count</th>
                  </tr>
                </thead>
                <tbody>
                  {avgSpreadByType.slice(0, 10).map((item, i) => (
                    <tr key={i} className="border-b border-[#c0c0c0]">
                      <td className="py-1 text-black truncate max-w-[200px]" title={item.category}>{item.category}</td>
                      <td className="text-right py-1 text-[#808080]">{item.averageSpread.toFixed(2)}%</td>
                      <td className="text-right py-1 text-[#808080]">{item.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      
      {/* FV Ratio Distributions */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <HistogramChart
          data={fvPrincipalRatios}
          title="FV/Principal Ratio Distribution"
          onBucketClick={handleFVPrincipalClick}
          selectedBucket={drillDown?.source === 'fv-principal' ? drillDown.bucketIndex : null}
          rangeControls={{
            range: fvPrincipalRange,
            onRangeChange: setFvPrincipalRange,
            outlierCount: fvPrincipalOutliers,
          }}
        />
        <HistogramChart
          data={fvCostRatios}
          title="FV/Cost Ratio Distribution"
          onBucketClick={handleFVCostClick}
          selectedBucket={drillDown?.source === 'fv-cost' ? drillDown.bucketIndex : null}
          rangeControls={{
            range: fvCostRange,
            onRangeChange: setFvCostRange,
            outlierCount: fvCostOutliers,
          }}
        />
      </div>
      
      {/* Drill-Down Table */}
      {drillDown && drillDownHoldings.length > 0 && (
        <DrillDownTable
          holdings={drillDownHoldings}
          title={drillDownTitle}
          onClose={() => setDrillDown(null)}
        />
      )}

      {/* Statistics Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="window p-3">
          <div className="text-xs font-semibold mb-2 text-black">Spread Statistics</div>
          <div className="space-y-1 text-xs text-[#808080]">
            <div>Avg: {spreadStats.average.toFixed(2)}%</div>
            <div>Min: {spreadStats.min.toFixed(2)}%</div>
            <div>Max: {spreadStats.max.toFixed(2)}%</div>
            <div>Median: {spreadStats.median.toFixed(2)}%</div>
            <div className="pt-1 border-t border-[#808080]">With Spread: {spreadStats.withSpread}</div>
            <div>Without Spread: {spreadStats.withoutSpread}</div>
          </div>
        </div>
        
        <div className="window p-3">
          <div className="text-xs font-semibold mb-2 text-black">PIK Analysis</div>
          <div className="space-y-1 text-xs text-[#808080]">
            <div>PIK Count: {pikAnalysis.pikCount}</div>
            <div>PIK FV: ${(pikAnalysis.pikFairValue / 1000).toFixed(1)}M</div>
            <div>PIK %: {pikAnalysis.pikPercentage.toFixed(1)}%</div>
            <div className="pt-1 border-t border-[#808080]">Avg PIK Rate: {pikAnalysis.averagePikRate.toFixed(2)}%</div>
          </div>
        </div>
        
        <div className="window p-3">
          <div className="text-xs font-semibold mb-2 text-black">FV Ratios</div>
          <div className="space-y-1 text-xs text-[#808080]">
            <div>FV/Principal Avg: {fvRatios.fvPrincipal.average.toFixed(3)}</div>
            <div>Range: {fvRatios.fvPrincipal.min.toFixed(3)} - {fvRatios.fvPrincipal.max.toFixed(3)}</div>
            <div className="pt-1 border-t border-[#808080]">FV/Cost Avg: {fvRatios.fvCost.average.toFixed(3)}</div>
            <div>Range: {fvRatios.fvCost.min.toFixed(3)} - {fvRatios.fvCost.max.toFixed(3)}</div>
          </div>
        </div>
      </div>
      
      {/* Top Holdings */}
      <div className="window p-3">
        <div className="text-xs font-semibold mb-2 text-black">Top 10 Holdings by Fair Value</div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[#808080]">
                <th className="text-left py-1 text-[#808080]">Company</th>
                <th className="text-right py-1 text-[#808080]">Fair Value</th>
                <th className="text-right py-1 text-[#808080]">% of Portfolio</th>
                <th className="text-left py-1 text-[#808080]">Type</th>
              </tr>
            </thead>
            <tbody>
              {topHoldings.map((h, i) => (
                <tr key={i} className="border-b border-[#c0c0c0]">
                  <td className="py-1 text-black">{h.company_name}</td>
                  <td className="text-right py-1 text-[#808080]">${(h.fair_value / 1000).toFixed(1)}M</td>
                  <td className="text-right py-1 text-[#808080]">{h.percentage.toFixed(2)}%</td>
                  <td className="py-1 text-[#808080]">{h.investment_type}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      
      {/* Red Flags Watchlist */}
      <div className="window p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs font-semibold text-black">Red Flags Watchlist ({filteredRedFlags.length})</div>
          <select
            className="input text-xs"
            value={redFlagFilter}
            onChange={(e) => setRedFlagFilter(e.target.value as RedFlag['type'] | 'all')}
          >
            <option value="all">All Flags</option>
            <option value="fv_equals_principal">FV ≈ Principal</option>
            <option value="fv_below_principal">FV &lt; Principal</option>
            <option value="fv_below_cost">FV &lt; Cost</option>
            <option value="has_pik">Has PIK</option>
            <option value="near_maturity">Near Maturity</option>
          </select>
        </div>
        {filteredRedFlags.length === 0 ? (
          <div className="text-xs text-[#808080]">No red flags found</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[#808080]">
                  <th className="text-left py-1 text-[#808080]">Company</th>
                  <th className="text-left py-1 text-[#808080]">Flags</th>
                  <th className="text-right py-1 text-[#808080]">Fair Value</th>
                  <th className="text-right py-1 text-[#808080]">Principal</th>
                  <th className="text-right py-1 text-[#808080]">Cost</th>
                </tr>
              </thead>
              <tbody>
                {filteredRedFlags.map((item, i) => (
                  <tr key={i} className="border-b border-[#c0c0c0]">
                    <td className="py-1 text-black">{item.holding.company_name_clean || item.holding.company_name}</td>
                    <td className="py-1">
                      <div className="flex flex-wrap gap-1">
                        {item.flags.map((flag, j) => (
                          <span
                            key={j}
                            className={`badge ${
                              flag.severity === 'high' ? 'badge-danger' :
                              flag.severity === 'medium' ? 'badge-warn' : 'badge-ok'
                            }`}
                            title={flag.message}
                          >
                            {flag.type.replace(/_/g, ' ')}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="text-right py-1 text-[#808080]">${(getFV(item.holding) / 1000).toFixed(1)}M</td>
                    <td className="text-right py-1 text-[#808080]">${(getPrincipal(item.holding) / 1000).toFixed(1)}M</td>
                    <td className="text-right py-1 text-[#808080]">${(getCost(item.holding) / 1000).toFixed(1)}M</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

