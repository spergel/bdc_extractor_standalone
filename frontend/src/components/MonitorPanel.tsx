import { useMemo } from 'react';
import Papa from 'papaparse';
import { useQuery } from '@tanstack/react-query';

interface MonitorMetric {
  ticker: string;
  investments_mm: number;
  market_cap_mm: number | null;
  current_price: number | null;
  pct_first_lien: number;
  pct_secured: number;
  pct_credit: number;
  pct_floating_rate: number;
  non_accruals_fv_pct: number;
  non_accruals_cost_pct: number;
  yield?: number | null;
  roe?: number | null;
  nav_per_share?: number | null;
  premium_discount_nav?: number | null;
  pik_income_pct?: number | null;
}

function loadMonitorData(): Promise<MonitorMetric[]> {
  return new Promise((resolve, reject) => {
    Papa.parse('/data/monitor_metrics.csv', {
      download: true,
      header: true,
      skipEmptyLines: true,
      complete: (results) => {
        const data = results.data as any[];
        const metrics = data
          .filter(row => row.ticker && row.ticker !== 'AVERAGE')
          .map(row => ({
            ticker: row.ticker,
            investments_mm: parseFloat(row.investments_mm) || 0,
            market_cap_mm: row.market_cap_mm ? parseFloat(row.market_cap_mm) : null,
            current_price: row.current_price ? parseFloat(row.current_price) : null,
            pct_first_lien: parseFloat(row.pct_first_lien) || 0,
            pct_secured: parseFloat(row.pct_secured) || 0,
            pct_credit: parseFloat(row.pct_credit) || 0,
            pct_floating_rate: parseFloat(row.pct_floating_rate) || 0,
            non_accruals_fv_pct: parseFloat(row.non_accruals_fv_pct) || 0,
            non_accruals_cost_pct: parseFloat(row.non_accruals_cost_pct) || 0,
            yield: row.yield ? parseFloat(row.yield) : null,
            roe: row.roe ? parseFloat(row.roe) : null,
            nav_per_share: row.nav_per_share ? parseFloat(row.nav_per_share) : null,
            premium_discount_nav: row.premium_discount_nav ? parseFloat(row.premium_discount_nav) : null,
            pik_income_pct: row.pik_income_pct ? parseFloat(row.pik_income_pct) : null,
          }));
        resolve(metrics);
      },
      error: reject,
    });
  });
}

export function MonitorPanel() {
  const { data: metrics = [], isLoading, error } = useQuery({
    queryKey: ['monitor-metrics'],
    queryFn: loadMonitorData,
    staleTime: 1000 * 60 * 60, // 1 hour
  });

  const averages = useMemo(() => {
    if (metrics.length === 0) return null;
    return {
      yield: metrics.filter(m => m.yield).reduce((sum, m) => sum + (m.yield || 0), 0) / metrics.filter(m => m.yield).length,
      pct_first_lien: metrics.reduce((sum, m) => sum + m.pct_first_lien, 0) / metrics.length,
      pct_secured: metrics.reduce((sum, m) => sum + m.pct_secured, 0) / metrics.length,
      pct_credit: metrics.reduce((sum, m) => sum + m.pct_credit, 0) / metrics.length,
      pct_floating_rate: metrics.reduce((sum, m) => sum + m.pct_floating_rate, 0) / metrics.length,
      non_accruals_fv_pct: metrics.reduce((sum, m) => sum + m.non_accruals_fv_pct, 0) / metrics.length,
      non_accruals_cost_pct: metrics.reduce((sum, m) => sum + m.non_accruals_cost_pct, 0) / metrics.length,
      pik_income_pct: metrics.filter(m => m.pik_income_pct).reduce((sum, m) => sum + (m.pik_income_pct || 0), 0) / metrics.filter(m => m.pik_income_pct).length,
    };
  }, [metrics]);

  const formatMoney = (value: number | null) => {
    if (value === null || value === undefined) return 'N/A';
    return `$${value.toFixed(1)}`;
  };

  const formatPercent = (value: number | null) => {
    if (value === null || value === undefined) return 'N/A';
    return `${value.toFixed(1)}%`;
  };

  if (isLoading) {
    return (
      <div className="window p-4">
        <div className="titlebar mb-2">
          <div className="text-sm tracking-wide">Liquid Private Credit Monitor</div>
        </div>
        <div className="text-xs text-[#808080] p-3">Loading monitor data...</div>
      </div>
    );
  }

  if (error || metrics.length === 0) {
    return (
      <div className="window p-4">
        <div className="titlebar mb-2">
          <div className="text-sm tracking-wide">Liquid Private Credit Monitor</div>
        </div>
        <div className="text-xs text-[#808080] p-3">
          {error ? 'Monitor data is not available yet. Run the monitor data pipeline to populate this view.' : 'No monitor data found.'}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="window">
        <div className="titlebar">
          <div className="text-sm tracking-wide">Liquid Private Credit Monitor</div>
          <div className="text-xs text-[#808080]">{new Date().toLocaleDateString()}</div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-separate border-spacing-0">
            <thead className="sticky top-0 bg-[#c0c0c0] z-10">
              <tr className="border-b border-[#808080]">
                <th className="text-left py-2 px-3 text-[#808080] font-semibold sticky left-0 bg-[#c0c0c0] z-20">BDC</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">Market Cap ($mm)</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">Investments ($mm)</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">Yield</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">Premium/(Discount) to NAV</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">ROE</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">% 1st Lien</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">% Secured</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">% Credit</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">% of Debt at Floating Rate</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">Non-Accruals (Fair Value)</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">Non-Accruals (Cost)</th>
                <th className="text-right py-2 px-3 text-[#808080] font-semibold">% PIK Income</th>
              </tr>
            </thead>
            <tbody>
              {/* Averages row */}
              {averages && (
                <tr className="bg-[#000080]/10 border-b-2 border-[#000080] font-semibold">
                  <td className="py-2 px-3 text-black sticky left-0 bg-[#000080]/10 z-10">AVERAGE</td>
                  <td className="text-right py-2 px-3 text-black">-</td>
                  <td className="text-right py-2 px-3 text-black">-</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(averages.yield)}</td>
                  <td className="text-right py-2 px-3 text-black">-</td>
                  <td className="text-right py-2 px-3 text-black">-</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(averages.pct_first_lien)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(averages.pct_secured)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(averages.pct_credit)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(averages.pct_floating_rate)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(averages.non_accruals_fv_pct)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(averages.non_accruals_cost_pct)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(averages.pik_income_pct)}</td>
                </tr>
              )}
              {metrics.map((metric) => (
                <tr key={metric.ticker} className="border-b border-[#c0c0c0] hover:bg-[#000080]/10">
                  <td className="py-2 px-3 text-black font-mono sticky left-0 bg-[#c0c0c0] z-10">{metric.ticker}</td>
                  <td className="text-right py-2 px-3 text-black">{formatMoney(metric.market_cap_mm)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatMoney(metric.investments_mm)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(metric.yield)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(metric.premium_discount_nav)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(metric.roe)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(metric.pct_first_lien)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(metric.pct_secured)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(metric.pct_credit)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(metric.pct_floating_rate)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(metric.non_accruals_fv_pct)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(metric.non_accruals_cost_pct)}</td>
                  <td className="text-right py-2 px-3 text-black">{formatPercent(metric.pik_income_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}






















