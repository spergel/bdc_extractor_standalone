import { useMemo } from 'react';
import type { Holding } from '../data/adapter';

type Props = {
  holdings: Holding[];
  period?: string;
};

function toNum(s: string | undefined | null): number {
  if (!s || s === '') return 0;
  const n = Number(s);
  return Number.isNaN(n) ? 0 : n;
}

function toPercent(s: string | undefined | null): number {
  if (!s || s === '') return 0;
  const cleaned = String(s).replace(/%/g, '').trim();
  const n = Number(cleaned);
  // Filter out nonsensical values (spreads should be 0-20%)
  return Number.isNaN(n) || n > 100 || n < 0 ? 0 : n;
}

export function SimpleAnalyticsPanel({ holdings, period }: Props) {
  const analytics = useMemo(() => {
    let totalFV = 0;
    let totalPrincipal = 0;
    
    // Investment type breakdown
    const byType = new Map<string, number>();
    const byIndustry = new Map<string, number>();
    
    // Spread analysis (only for holdings WITH spreads)
    const spreadsWithValue: number[] = [];
    let spreadFV = 0;
    
    // Rate structure
    let fixedRateFV = 0;
    let floatingRateFV = 0;
    let pikFV = 0;
    
    // Position sizing
    let positions = 0;
    const sizeDistribution = [0, 0, 0, 0]; // <1M, 1-5M, 5-20M, >20M
    
    holdings.forEach(h => {
      const fv = toNum(h.fair_value);
      const principal = toNum(h.principal_amount);
      const spread = toPercent(h.spread);
      
      if (fv > 0) {
        totalFV += fv;
        positions++;
        
        // Type
        const type = h.investment_type || 'Unknown';
        byType.set(type, (byType.get(type) || 0) + fv);
        
        // Industry  
        const industry = h.industry || 'Unknown';
        byIndustry.set(industry, (byIndustry.get(industry) || 0) + fv);
        
        // Spread (only if positive)
        if (spread > 0 && spread <= 20) {
          spreadsWithValue.push(spread);
          spreadFV += fv;
        }
        
        // Rate type
        if (h.reference_rate && h.reference_rate.trim()) {
          floatingRateFV += fv;
        } else {
          fixedRateFV += fv;
        }
        
        // PIK
        const pik = toPercent(h.pik_rate);
        if (pik > 0) {
          pikFV += fv;
        }
        
        // Size buckets (in thousands)
        if (fv < 1000) sizeDistribution[0]++;
        else if (fv < 5000) sizeDistribution[1]++;
        else if (fv < 20000) sizeDistribution[2]++;
        else sizeDistribution[3]++;
      }
      
      if (principal > 0) totalPrincipal += principal;
    });
    
    // Calculate spreads
    spreadsWithValue.sort((a, b) => a - b);
    const spreadAvg = spreadsWithValue.length > 0 
      ? spreadsWithValue.reduce((a, b) => a + b, 0) / spreadsWithValue.length 
      : 0;
    const spreadMedian = spreadsWithValue.length > 0
      ? spreadsWithValue[Math.floor(spreadsWithValue.length / 2)]
      : 0;
    
    // Top types
    const topTypes = Array.from(byType.entries())
      .map(([name, fv]) => ({ name, fv, pct: totalFV > 0 ? (fv / totalFV) * 100 : 0 }))
      .sort((a, b) => b.fv - a.fv)
      .slice(0, 5);
    
    // Top industries
    const topIndustries = Array.from(byIndustry.entries())
      .map(([name, fv]) => ({ name, fv, pct: totalFV > 0 ? (fv / totalFV) * 100 : 0 }))
      .sort((a, b) => b.fv - a.fv)
      .slice(0, 5);
    
    return {
      totalFV,
      totalPrincipal,
      positions,
      spreadAvg,
      spreadMedian,
      spreadCount: spreadsWithValue.length,
      spreadMin: spreadsWithValue[0] || 0,
      spreadMax: spreadsWithValue[spreadsWithValue.length - 1] || 0,
      spreadPctOfPortfolio: totalFV > 0 ? (spreadFV / totalFV) * 100 : 0,
      floatingPct: totalFV > 0 ? (floatingRateFV / totalFV) * 100 : 0,
      fixedPct: totalFV > 0 ? (fixedRateFV / totalFV) * 100 : 0,
      pikPct: totalFV > 0 ? (pikFV / totalFV) * 100 : 0,
      topTypes,
      topIndustries,
      sizeDistribution,
    };
  }, [holdings]);
  
  const fmt = (n: number) => n.toLocaleString('en-US', { maximumFractionDigits: 0 });
  const fmtPct = (n: number) => `${n.toFixed(1)}%`;
  const fmtBps = (n: number) => `${n.toFixed(2)}%`;
  
  return (
    <div className="space-y-4 p-4">
      {/* Portfolio Summary */}
      <div className="window">
        <div className="titlebar">
          <div className="text-sm">Portfolio Summary</div>
          {period && <div className="text-xs text-silver/60">{period}</div>}
        </div>
        <div className="p-3 grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <div className="text-xs text-[#808080]">Total Fair Value</div>
            <div className="text-lg font-bold">${fmt(analytics.totalFV / 1000)}M</div>
          </div>
          <div>
            <div className="text-xs text-[#808080]">Positions</div>
            <div className="text-lg font-bold">{analytics.positions}</div>
          </div>
          <div>
            <div className="text-xs text-[#808080]">Avg Position Size</div>
            <div className="text-lg font-bold">${fmt((analytics.totalFV / analytics.positions) / 1000)}M</div>
          </div>
          <div>
            <div className="text-xs text-[#808080]">Floating Rate</div>
            <div className="text-lg font-bold">{fmtPct(analytics.floatingPct)}</div>
          </div>
        </div>
      </div>
      
      {/* Spread Analysis */}
      {analytics.spreadCount > 0 && (
        <div className="window">
          <div className="titlebar">
            <div className="text-sm">Spread Analysis</div>
            <div className="text-xs text-silver/60">{analytics.spreadCount} positions with spread data</div>
          </div>
          <div className="p-3">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-3">
              <div>
                <div className="text-xs text-[#808080]">Average</div>
                <div className="text-lg font-bold text-[#000080]">{fmtBps(analytics.spreadAvg)}</div>
              </div>
              <div>
                <div className="text-xs text-[#808080]">Median</div>
                <div className="text-lg font-bold">{fmtBps(analytics.spreadMedian)}</div>
              </div>
              <div>
                <div className="text-xs text-[#808080]">Min</div>
                <div className="text-sm">{fmtBps(analytics.spreadMin)}</div>
              </div>
              <div>
                <div className="text-xs text-[#808080]">Max</div>
                <div className="text-sm">{fmtBps(analytics.spreadMax)}</div>
              </div>
              <div>
                <div className="text-xs text-[#808080]">% of Portfolio</div>
                <div className="text-sm">{fmtPct(analytics.spreadPctOfPortfolio)}</div>
              </div>
            </div>
            <div className="text-xs text-[#808080] italic">
              Spread over reference rate (SOFR, Prime, etc.) - Higher = more credit risk premium
            </div>
          </div>
        </div>
      )}
      
      {/* Rate Structure */}
      <div className="window">
        <div className="titlebar">
          <div className="text-sm">Rate Structure</div>
        </div>
        <div className="p-3 grid grid-cols-3 gap-4">
          <div>
            <div className="text-xs text-[#808080]">Floating Rate</div>
            <div className="text-lg font-bold text-green-600">{fmtPct(analytics.floatingPct)}</div>
            <div className="text-xs text-[#808080]">SOFR/Prime + Spread</div>
          </div>
          <div>
            <div className="text-xs text-[#808080]">Fixed Rate</div>
            <div className="text-lg font-bold">{fmtPct(analytics.fixedPct)}</div>
            <div className="text-xs text-[#808080]">No rate variability</div>
          </div>
          <div>
            <div className="text-xs text-[#808080]">Has PIK</div>
            <div className="text-lg font-bold text-orange-600">{fmtPct(analytics.pikPct)}</div>
            <div className="text-xs text-[#808080]">Payment-in-kind</div>
          </div>
        </div>
      </div>
      
      {/* Top Investment Types */}
      <div className="window">
        <div className="titlebar">
          <div className="text-sm">Top Investment Types</div>
        </div>
        <div className="p-3">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-[#808080] border-b border-[#c0c0c0]">
                <th className="pb-1">Type</th>
                <th className="pb-1 text-right">Fair Value</th>
                <th className="pb-1 text-right">% of Portfolio</th>
              </tr>
            </thead>
            <tbody>
              {analytics.topTypes.map((t, i) => (
                <tr key={i} className="border-b border-[#c0c0c0]">
                  <td className="py-1">{t.name}</td>
                  <td className="text-right">${fmt(t.fv / 1000)}M</td>
                  <td className="text-right font-semibold">{fmtPct(t.pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      
      {/* Top Industries */}
      <div className="window">
        <div className="titlebar">
          <div className="text-sm">Top Industries</div>
        </div>
        <div className="p-3">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-[#808080] border-b border-[#c0c0c0]">
                <th className="pb-1">Industry</th>
                <th className="pb-1 text-right">Fair Value</th>
                <th className="pb-1 text-right">% of Portfolio</th>
              </tr>
            </thead>
            <tbody>
              {analytics.topIndustries.map((ind, i) => (
                <tr key={i} className="border-b border-[#c0c0c0]">
                  <td className="py-1">{ind.name}</td>
                  <td className="text-right">${fmt(ind.fv / 1000)}M</td>
                  <td className="text-right font-semibold">{fmtPct(ind.pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      
      {/* Position Sizing */}
      <div className="window">
        <div className="titlebar">
          <div className="text-sm">Position Size Distribution</div>
        </div>
        <div className="p-3 grid grid-cols-4 gap-2">
          <div className="text-center">
            <div className="text-2xl font-bold">{analytics.sizeDistribution[0]}</div>
            <div className="text-xs text-[#808080]">&lt;$1M</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold">{analytics.sizeDistribution[1]}</div>
            <div className="text-xs text-[#808080]">$1-5M</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold">{analytics.sizeDistribution[2]}</div>
            <div className="text-xs text-[#808080]">$5-20M</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold">{analytics.sizeDistribution[3]}</div>
            <div className="text-xs text-[#808080]">&gt;$20M</div>
          </div>
        </div>
      </div>
    </div>
  );
}
