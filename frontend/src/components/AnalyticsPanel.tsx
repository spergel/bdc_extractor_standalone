import { useMemo, useState, useCallback, useRef } from 'react';
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
  checkRedFlags,
  getHerfindahlIndex,
  type RedFlag,
} from '../utils/holdingsAnalytics';

type Props = {
  holdings: Holding[];
  period?: string;
};

// Pie chart component
function PieChart({ data, title, byValue = true }: { data: Array<{ category: string; count: number; fairValue: number; percentage: number }>; title: string; byValue?: boolean }) {
  const [hovered, setHovered] = useState<{ item: typeof data[0]; x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  if (data.length === 0) {
    return (
      <div className="window p-3">
        <div className="text-xs text-[#808080]">{title}: No data</div>
      </div>
    );
  }

  // Limit to top 10, group rest as "Other"
  const top = data.slice(0, 10);
  const rest = data.slice(10);
  const otherValue = rest.reduce((sum, item) => sum + (byValue ? item.fairValue : item.count), 0);
  const otherPercentage = rest.reduce((sum, item) => sum + item.percentage, 0);
  
  const displayData = otherValue > 0 
    ? [...top, { category: 'Other', count: rest.reduce((s, i) => s + i.count, 0), fairValue: otherValue, percentage: otherPercentage }]
    : top;

  const total = displayData.reduce((sum, item) => sum + (byValue ? item.fairValue : item.count), 0);
  
  // Windows 95 classic Excel colors
  const colors = [
    '#0000ff', '#ff0000', '#00ff00', '#ffff00', '#ff00ff',
    '#00ffff', '#808080', '#c0c0c0', '#800000', '#008000',
  ];

  let currentAngle = -90; // Start at top
  const paths = displayData.map((item, i) => {
    const value = byValue ? item.fairValue : item.count;
    const percentage = total > 0 ? (value / total) * 100 : 0;
    const angle = (percentage / 100) * 360;
    
    const startAngle = currentAngle;
    const endAngle = currentAngle + angle;
    currentAngle = endAngle;
    
    const largeArc = angle > 180 ? 1 : 0;
    const radius = 60;
    const centerX = 80;
    const centerY = 80;
    
    const x1 = centerX + radius * Math.cos((startAngle * Math.PI) / 180);
    const y1 = centerY + radius * Math.sin((startAngle * Math.PI) / 180);
    const x2 = centerX + radius * Math.cos((endAngle * Math.PI) / 180);
    const y2 = centerY + radius * Math.sin((endAngle * Math.PI) / 180);
    
    const path = `M ${centerX} ${centerY} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`;
    
    // Calculate midpoint for tooltip
    const midAngle = (startAngle + endAngle) / 2;
    const midX = centerX + (radius * 0.7) * Math.cos((midAngle * Math.PI) / 180);
    const midY = centerY + (radius * 0.7) * Math.sin((midAngle * Math.PI) / 180);
    
    return {
      path,
      color: colors[i % colors.length],
      item,
      percentage,
      midX,
      midY,
    };
  });

  const containerRef = useRef<HTMLDivElement>(null);

  const handleMouseMove = useCallback((e: React.MouseEvent<SVGSVGElement>, item: typeof displayData[0]) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    setHovered({ item, x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, []);

  const handleMouseLeave = useCallback(() => setHovered(null), []);

  return (
    <div className="window p-3 relative">
      <div className="text-xs font-semibold mb-2 text-black">{title}</div>
      <div className="flex items-start gap-4">
        <div ref={containerRef} className="flex-shrink-0 relative">
          <svg ref={svgRef} viewBox="0 0 160 160" className="w-32 h-32" onMouseLeave={handleMouseLeave}>
            {paths.map(({ path, color, item }, i) => (
              <path
                key={i}
                d={path}
                fill={color}
                stroke="#000000"
                strokeWidth="1"
                onMouseMove={(e) => handleMouseMove(e, item)}
                style={{ cursor: 'pointer' }}
                opacity={hovered?.item.category === item.category ? 1 : hovered ? 0.5 : 1}
              />
            ))}
          </svg>
          {hovered && (
            <div
              className="absolute pointer-events-none z-10 bg-white border-2 border-[#000000] px-2 py-1 text-xs text-black"
              style={{
                left: `${hovered.x + 10}px`,
                top: `${hovered.y - 10}px`,
                transform: hovered.x > 128 ? 'translateX(-100%)' : 'none',
              }}
            >
              <div className="text-black font-medium">{hovered.item.category}</div>
              <div className="text-black">{hovered.item.percentage.toFixed(1)}%</div>
              {byValue ? (
                <div className="text-black">${(hovered.item.fairValue / 1000).toFixed(0)}k</div>
              ) : (
                <div className="text-black">{hovered.item.count} holdings</div>
              )}
            </div>
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="space-y-1 text-xs">
            {displayData.slice(0, 8).map((item, i) => (
              <div key={i} className="flex items-center gap-2">
                <div
                  className="w-3 h-3 flex-shrink-0"
                  style={{ backgroundColor: colors[i % colors.length] }}
                />
                <div className="flex-1 min-w-0 truncate">{item.category}</div>
                <div className="text-[#808080]">{item.percentage.toFixed(1)}%</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// Maturity ladder bar chart
function MaturityLadderChart({ data }: { data: Array<{ bucket: string; count: number; fairValue: number; percentage: number }> }) {
  const [hovered, setHovered] = useState<{ item: typeof data[0]; x: number; y: number } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const maxFV = Math.max(...data.map(d => d.fairValue), 1);
  
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
      <div className="text-xs font-semibold mb-2 text-silver/90">Maturity Ladder</div>
      <div className="space-y-1">
        {data.map((item, i) => (
          <div
            key={i}
            className="flex items-center gap-2"
            onMouseEnter={(e) => handleMouseEnter(e, item)}
            onMouseMove={(e) => handleMouseMove(e, item)}
            onMouseLeave={handleMouseLeave}
            style={{ cursor: 'pointer' }}
          >
            <div className="w-20 text-xs text-[#808080]">{item.bucket}</div>
            <div className="flex-1 bg-[#c0c0c0] h-4 overflow-hidden relative">
              <div
                className="h-full bg-[#0000ff] transition-opacity"
                style={{
                  width: `${(item.fairValue / maxFV) * 100}%`,
                  opacity: hovered?.item.bucket === item.bucket ? 1 : hovered ? 0.5 : 1,
                }}
              />
            </div>
            <div className="w-24 text-xs text-[#808080] text-right">
              ${(item.fairValue / 1000).toFixed(0)}k ({item.percentage.toFixed(1)}%)
            </div>
          </div>
        ))}
      </div>
      {hovered && (
        <div
          className="absolute pointer-events-none z-10 bg-white border-2 border-[#000000] px-2 py-1 text-xs text-black"
              style={{
                left: `${hovered.x + 10}px`,
                top: `${hovered.y - 10}px`,
              }}
        >
          <div className="text-black font-medium">{hovered.item.bucket}</div>
          <div className="text-black">{hovered.item.count} holdings</div>
          <div className="text-black">${(hovered.item.fairValue / 1000).toFixed(0)}k</div>
          <div className="text-black">{hovered.item.percentage.toFixed(1)}%</div>
        </div>
      )}
    </div>
  );
}

// Histogram bar chart component
function HistogramChart({ data, title }: { data: Array<{ range: string; count: number; percentage: number }>; title: string }) {
  const [hovered, setHovered] = useState<{ item: typeof data[0]; x: number; y: number } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  if (data.length === 0) {
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
      <div className="text-xs font-semibold mb-2 text-black">{title}</div>
      <div className="space-y-1">
        {data.map((item, i) => (
          <div
            key={i}
            className="flex items-center gap-2"
            onMouseEnter={(e) => handleMouseEnter(e, item)}
            onMouseMove={(e) => handleMouseMove(e, item)}
            onMouseLeave={handleMouseLeave}
            style={{ cursor: 'pointer' }}
          >
            <div className="w-24 text-xs text-silver/70 truncate" title={item.range}>{item.range}</div>
            <div className="flex-1 bg-[#1b1f23]/50 rounded-sm h-4 overflow-hidden">
              <div
                className="h-full bg-cyan-400/50 transition-opacity"
                style={{
                  width: `${(item.count / maxCount) * 100}%`,
                  opacity: hovered?.item.range === item.range ? 1 : hovered ? 0.5 : 1,
                }}
              />
            </div>
            <div className="w-20 text-xs text-silver/70 text-right">
              {item.count} ({item.percentage.toFixed(1)}%)
            </div>
          </div>
        ))}
      </div>
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
  const fvPrincipalRatios = useMemo(() => {
    const ratios: number[] = [];
    holdings.forEach(h => {
      const fv = Number(h.fair_value || 0);
      const principal = Number(h.principal_amount || 0);
      if (principal > 0 && fv > 0) ratios.push(fv / principal);
    });
    return getFVRatioDistribution(ratios);
  }, [holdings]);
  const fvCostRatios = useMemo(() => {
    const ratios: number[] = [];
    holdings.forEach(h => {
      const fv = Number(h.fair_value || 0);
      const cost = Number(h.cost || h.amortized_cost || 0);
      if (cost > 0 && fv > 0) ratios.push(fv / cost);
    });
    return getFVRatioDistribution(ratios);
  }, [holdings]);
  const industryHerfindahl = useMemo(() => getHerfindahlIndex(industryDist), [industryDist]);
  const typeHerfindahl = useMemo(() => getHerfindahlIndex(typeDist), [typeDist]);
  
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
        <PieChart data={industryDist} title="Industry Distribution" byValue />
        <PieChart data={typeDist} title="Investment Type Distribution" byValue />
        <PieChart data={ratePieData} title="Variable vs Fixed Rate" />
        <MaturityLadderChart data={maturityLadder} />
      </div>
      
      {/* Spread Distribution and Floor Rate */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <HistogramChart data={spreadDistribution} title="Spread Distribution" />
        <div className="window p-3">
          <div className="text-xs font-semibold mb-2 text-silver/90">Floor Rate Analysis</div>
          <div className="space-y-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-silver/70">With Floor:</span>
              <span className="text-silver/90">{floorAnalysis.withFloor.count} ({floorAnalysis.withFloor.percentage.toFixed(1)}%)</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-silver/70">Without Floor:</span>
              <span className="text-silver/90">{floorAnalysis.withoutFloor.count} ({floorAnalysis.withoutFloor.percentage.toFixed(1)}%)</span>
            </div>
            {floorAnalysis.withFloor.count > 0 && (
              <>
                <div className="pt-2 border-t border-silver/20">
                  <div className="text-silver/70 mb-1">Floor Statistics:</div>
                  <div className="space-y-1 text-silver/60">
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
          <div className="text-xs font-semibold mb-2 text-silver/90">Average Spread by Industry</div>
          {avgSpreadByIndustry.length === 0 ? (
            <div className="text-xs text-silver/60">No spread data available</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-silver/20">
                    <th className="text-left py-1 text-silver/70">Industry</th>
                    <th className="text-right py-1 text-silver/70">Avg Spread</th>
                    <th className="text-right py-1 text-silver/70">Count</th>
                  </tr>
                </thead>
                <tbody>
                  {avgSpreadByIndustry.slice(0, 10).map((item, i) => (
                    <tr key={i} className="border-b border-silver/10">
                      <td className="py-1 text-silver/90 truncate max-w-[200px]" title={item.category}>{item.category}</td>
                      <td className="text-right py-1 text-silver/70">{item.averageSpread.toFixed(2)}%</td>
                      <td className="text-right py-1 text-silver/70">{item.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <div className="window p-3">
          <div className="text-xs font-semibold mb-2 text-silver/90">Average Spread by Investment Type</div>
          {avgSpreadByType.length === 0 ? (
            <div className="text-xs text-silver/60">No spread data available</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-silver/20">
                    <th className="text-left py-1 text-silver/70">Type</th>
                    <th className="text-right py-1 text-silver/70">Avg Spread</th>
                    <th className="text-right py-1 text-silver/70">Count</th>
                  </tr>
                </thead>
                <tbody>
                  {avgSpreadByType.slice(0, 10).map((item, i) => (
                    <tr key={i} className="border-b border-silver/10">
                      <td className="py-1 text-silver/90 truncate max-w-[200px]" title={item.category}>{item.category}</td>
                      <td className="text-right py-1 text-silver/70">{item.averageSpread.toFixed(2)}%</td>
                      <td className="text-right py-1 text-silver/70">{item.count}</td>
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
        <HistogramChart data={fvPrincipalRatios} title="FV/Principal Ratio Distribution" />
        <HistogramChart data={fvCostRatios} title="FV/Cost Ratio Distribution" />
      </div>
      
      {/* Statistics Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="window p-3">
          <div className="text-xs font-semibold mb-2 text-silver/90">Spread Statistics</div>
          <div className="space-y-1 text-xs text-silver/70">
            <div>Avg: {spreadStats.average.toFixed(2)}%</div>
            <div>Min: {spreadStats.min.toFixed(2)}%</div>
            <div>Max: {spreadStats.max.toFixed(2)}%</div>
            <div>Median: {spreadStats.median.toFixed(2)}%</div>
            <div className="pt-1 border-t border-silver/20">With Spread: {spreadStats.withSpread}</div>
            <div>Without Spread: {spreadStats.withoutSpread}</div>
          </div>
        </div>
        
        <div className="window p-3">
          <div className="text-xs font-semibold mb-2 text-silver/90">PIK Analysis</div>
          <div className="space-y-1 text-xs text-silver/70">
            <div>PIK Count: {pikAnalysis.pikCount}</div>
            <div>PIK FV: ${(pikAnalysis.pikFairValue / 1000).toFixed(0)}k</div>
            <div>PIK %: {pikAnalysis.pikPercentage.toFixed(1)}%</div>
            <div className="pt-1 border-t border-silver/20">Avg PIK Rate: {pikAnalysis.averagePikRate.toFixed(2)}%</div>
          </div>
        </div>
        
        <div className="window p-3">
          <div className="text-xs font-semibold mb-2 text-silver/90">FV Ratios</div>
          <div className="space-y-1 text-xs text-silver/70">
            <div>FV/Principal Avg: {fvRatios.fvPrincipal.average.toFixed(3)}</div>
            <div>Range: {fvRatios.fvPrincipal.min.toFixed(3)} - {fvRatios.fvPrincipal.max.toFixed(3)}</div>
            <div className="pt-1 border-t border-silver/20">FV/Cost Avg: {fvRatios.fvCost.average.toFixed(3)}</div>
            <div>Range: {fvRatios.fvCost.min.toFixed(3)} - {fvRatios.fvCost.max.toFixed(3)}</div>
          </div>
        </div>
      </div>
      
      {/* Concentration Metrics */}
      <div className="window p-3">
        <div className="text-xs font-semibold mb-2 text-silver/90">Concentration Metrics</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs text-silver/70">
          <div>
            <div>Industry Herfindahl: {industryHerfindahl.toFixed(0)}</div>
            <div className="text-silver/50 text-[10px] mt-1">(Higher = more concentrated)</div>
          </div>
          <div>
            <div>Type Herfindahl: {typeHerfindahl.toFixed(0)}</div>
            <div className="text-silver/50 text-[10px] mt-1">(Higher = more concentrated)</div>
          </div>
        </div>
      </div>
      
      {/* Top Holdings */}
      <div className="window p-3">
        <div className="text-xs font-semibold mb-2 text-silver/90">Top 10 Holdings by Fair Value</div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-silver/20">
                <th className="text-left py-1 text-silver/70">Company</th>
                <th className="text-right py-1 text-silver/70">Fair Value</th>
                <th className="text-right py-1 text-silver/70">% of Portfolio</th>
                <th className="text-left py-1 text-silver/70">Type</th>
              </tr>
            </thead>
            <tbody>
              {topHoldings.map((h, i) => (
                <tr key={i} className="border-b border-silver/10">
                  <td className="py-1 text-silver/90">{h.company_name}</td>
                  <td className="text-right py-1 text-silver/70">${(h.fair_value / 1000).toFixed(0)}k</td>
                  <td className="text-right py-1 text-silver/70">{h.percentage.toFixed(2)}%</td>
                  <td className="py-1 text-silver/60">{h.investment_type}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      
      {/* Red Flags Watchlist */}
      <div className="window p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs font-semibold text-silver/90">Red Flags Watchlist ({filteredRedFlags.length})</div>
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
          <div className="text-xs text-silver/60">No red flags found</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-silver/20">
                  <th className="text-left py-1 text-silver/70">Company</th>
                  <th className="text-left py-1 text-silver/70">Flags</th>
                  <th className="text-right py-1 text-silver/70">Fair Value</th>
                  <th className="text-right py-1 text-silver/70">Principal</th>
                  <th className="text-right py-1 text-silver/70">Cost</th>
                </tr>
              </thead>
              <tbody>
                {filteredRedFlags.map((item, i) => (
                  <tr key={i} className="border-b border-silver/10">
                    <td className="py-1 text-silver/90">{item.holding.company_name}</td>
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
                    <td className="text-right py-1 text-silver/70">${(parseFloat(item.holding.fair_value || '0') / 1000).toFixed(0)}k</td>
                    <td className="text-right py-1 text-silver/70">${(parseFloat(item.holding.principal_amount || '0') / 1000).toFixed(0)}k</td>
                    <td className="text-right py-1 text-silver/70">${(parseFloat(item.holding.cost || item.holding.amortized_cost || '0') / 1000).toFixed(0)}k</td>
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

