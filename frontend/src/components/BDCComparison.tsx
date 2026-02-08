import { useState, useEffect } from 'react';
import { loadPortfolioSummaries, loadBDCInvestments, type PortfolioSummary, type Holding } from '../data/adapter';

type BDCComparisonProps = {
  tickers: string[];
};

export default function BDCComparison({ tickers: initialTickers }: BDCComparisonProps) {
  const [summaries, setSummaries] = useState<PortfolioSummary[]>([]);
  const [selectedTickers, setSelectedTickers] = useState<string[]>(initialTickers.slice(0, 4));
  const [loading, setLoading] = useState(true);
  const [availableTickers, setAvailableTickers] = useState<string[]>([]);

  useEffect(() => {
    loadData();
  }, [selectedTickers]);

  async function loadData() {
    setLoading(true);
    try {
      const allSummaries = await loadPortfolioSummaries();
      
      // Get unique tickers
      const tickers = Array.from(new Set(allSummaries.map(s => s.ticker))).sort();
      setAvailableTickers(tickers);
      
      // Get latest summary for each selected ticker
      const latestSummaries = selectedTickers.map(ticker => {
        const tickerSummaries = allSummaries.filter(s => s.ticker === ticker);
        tickerSummaries.sort((a, b) => b.filing_date.localeCompare(a.filing_date));
        return tickerSummaries[0];
      }).filter(Boolean);
      
      setSummaries(latestSummaries);
    } catch (err) {
      console.error('Error loading comparison data:', err);
    } finally {
      setLoading(false);
    }
  }

  function toggleTicker(ticker: string) {
    if (selectedTickers.includes(ticker)) {
      setSelectedTickers(selectedTickers.filter(t => t !== ticker));
    } else if (selectedTickers.length < 6) {
      setSelectedTickers([...selectedTickers, ticker]);
    }
  }

  function formatMoney(millions: number | undefined) {
    if (!millions) return '$0M';
    if (millions >= 1000) return `$${(millions / 1000).toFixed(2)}B`;
    return `$${millions.toFixed(1)}M`;
  }

  function formatPercent(value: number | undefined) {
    if (value === undefined || value === null) return '—';
    return `${value.toFixed(1)}%`;
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4"></div>
          <p className="text-gray-600">Loading comparison data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-6 max-w-7xl">
      <div className="mb-6">
        <h1 className="text-3xl font-bold mb-4">BDC Portfolio Comparison</h1>
        
        {/* Ticker Selector */}
        <div className="bg-white rounded-lg shadow p-4 mb-6">
          <p className="text-sm text-gray-600 mb-3">Select up to 6 BDCs to compare:</p>
          <div className="flex flex-wrap gap-2">
            {availableTickers.map(ticker => (
              <button
                key={ticker}
                onClick={() => toggleTicker(ticker)}
                className={`px-4 py-2 rounded-lg font-medium transition-colors ${
                  selectedTickers.includes(ticker)
                    ? 'bg-blue-500 text-white'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
                disabled={!selectedTickers.includes(ticker) && selectedTickers.length >= 6}
              >
                {ticker}
              </button>
            ))}
          </div>
        </div>

        {/* Comparison Table */}
        <div className="bg-white rounded-lg shadow overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="px-4 py-3 text-left text-sm font-semibold text-gray-700">Metric</th>
                {summaries.map(s => (
                  <th key={s.ticker} className="px-4 py-3 text-right text-sm font-semibold text-gray-700">
                    <div>{s.ticker}</div>
                    <div className="text-xs font-normal text-gray-500">{s.filing_date}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {/* Portfolio Size */}
              <tr className="bg-blue-50">
                <td colSpan={summaries.length + 1} className="px-4 py-2 text-sm font-semibold text-gray-700">
                  Portfolio Size
                </td>
              </tr>
              <tr>
                <td className="px-4 py-3 text-sm text-gray-600">Total Fair Value</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right font-medium">
                    {formatMoney(s.total_fair_value_millions)}
                  </td>
                ))}
              </tr>
              <tr className="bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-600"># of Investments</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right">
                    {s.total_investments_count.toLocaleString()}
                  </td>
                ))}
              </tr>

              {/* Asset Mix */}
              <tr className="bg-blue-50">
                <td colSpan={summaries.length + 1} className="px-4 py-2 text-sm font-semibold text-gray-700">
                  Asset Mix
                </td>
              </tr>
              <tr>
                <td className="px-4 py-3 text-sm text-gray-600">Senior Debt Value</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right">
                    {formatMoney(s.senior_debt_fair_value_millions)}
                  </td>
                ))}
              </tr>
              <tr className="bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-600">Senior Debt Count</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right">
                    {s.senior_debt_count.toLocaleString()}
                  </td>
                ))}
              </tr>
              <tr>
                <td className="px-4 py-3 text-sm text-gray-600">Equity Value</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right">
                    {formatMoney(s.equity_fair_value_millions)}
                  </td>
                ))}
              </tr>
              <tr className="bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-600">Equity Count</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right">
                    {s.equity_count.toLocaleString()}
                  </td>
                ))}
              </tr>

              {/* Yield & Credit Quality */}
              <tr className="bg-blue-50">
                <td colSpan={summaries.length + 1} className="px-4 py-2 text-sm font-semibold text-gray-700">
                  Yield & Credit Quality
                </td>
              </tr>
              <tr>
                <td className="px-4 py-3 text-sm text-gray-600">Weighted Avg Yield</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right font-medium text-green-600">
                    {formatPercent(s.weighted_avg_yield * 100)}
                  </td>
                ))}
              </tr>
              <tr className="bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-600">% Senior Secured</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right">
                    {formatPercent(s.pct_senior_secured)}
                  </td>
                ))}
              </tr>
              <tr>
                <td className="px-4 py-3 text-sm text-gray-600">% Floating Rate</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right">
                    {formatPercent(s.pct_floating_rate)}
                  </td>
                ))}
              </tr>

              {/* Diversification */}
              <tr className="bg-blue-50">
                <td colSpan={summaries.length + 1} className="px-4 py-2 text-sm font-semibold text-gray-700">
                  Diversification
                </td>
              </tr>
              <tr>
                <td className="px-4 py-3 text-sm text-gray-600"># of Companies</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right">
                    {s.num_companies.toLocaleString()}
                  </td>
                ))}
              </tr>
              <tr className="bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-600"># of Industries</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right">
                    {s.num_industries}
                  </td>
                ))}
              </tr>
              <tr>
                <td className="px-4 py-3 text-sm text-gray-600">Top Industry</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right text-gray-600">
                    {s.top_industry}
                  </td>
                ))}
              </tr>
              <tr className="bg-gray-50">
                <td className="px-4 py-3 text-sm text-gray-600">Largest Position</td>
                {summaries.map(s => (
                  <td key={s.ticker} className="px-4 py-3 text-sm text-right">
                    {formatMoney(s.largest_position_millions)}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>

        {/* Export Button */}
        <div className="mt-4 flex justify-end">
          <button
            onClick={() => {
              const csv = generateCSV(summaries);
              downloadCSV(csv, 'bdc_comparison.csv');
            }}
            className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600"
          >
            Export to CSV
          </button>
        </div>
      </div>
    </div>
  );
}

function generateCSV(summaries: PortfolioSummary[]): string {
  const headers = ['Metric', ...summaries.map(s => `${s.ticker} (${s.filing_date})`)];
  const rows = [
    ['Total Fair Value (M)', ...summaries.map(s => s.total_fair_value_millions.toFixed(2))],
    ['# of Investments', ...summaries.map(s => s.total_investments_count)],
    ['Senior Debt Value (M)', ...summaries.map(s => s.senior_debt_fair_value_millions.toFixed(2))],
    ['Senior Debt Count', ...summaries.map(s => s.senior_debt_count)],
    ['Equity Value (M)', ...summaries.map(s => s.equity_fair_value_millions.toFixed(2))],
    ['Equity Count', ...summaries.map(s => s.equity_count)],
    ['Weighted Avg Yield (%)', ...summaries.map(s => (s.weighted_avg_yield * 100).toFixed(2))],
    ['% Senior Secured', ...summaries.map(s => s.pct_senior_secured.toFixed(1))],
    ['% Floating Rate', ...summaries.map(s => s.pct_floating_rate.toFixed(1))],
    ['# of Companies', ...summaries.map(s => s.num_companies)],
    ['# of Industries', ...summaries.map(s => s.num_industries)],
    ['Top Industry', ...summaries.map(s => s.top_industry)],
    ['Largest Position (M)', ...summaries.map(s => s.largest_position_millions.toFixed(2))],
  ];
  
  return [headers, ...rows].map(row => row.join(',')).join('\n');
}

function downloadCSV(csv: string, filename: string) {
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}



























