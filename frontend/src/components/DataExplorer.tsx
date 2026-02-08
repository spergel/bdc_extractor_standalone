import { useState } from 'react';
import { useBDCIndex, useBDCPeriods, useBDCInvestments, useBDCFinancials } from '../api/hooks';
import { playClickSound } from '../utils/sounds';

type Props = {
  ticker?: string;
};

export function DataExplorer({ ticker }: Props) {
  const { data: index } = useBDCIndex();
  const [selectedTicker, setSelectedTicker] = useState<string>(ticker || '');
  const [selectedPeriod, setSelectedPeriod] = useState<string>('');
  const [showInvestments, setShowInvestments] = useState(false);
  const [showFinancials, setShowFinancials] = useState(false);

  const { data: periods } = useBDCPeriods(selectedTicker || undefined);
  const { data: investments, isLoading: loadingInvestments } = useBDCInvestments(
    selectedTicker || undefined,
    selectedPeriod || undefined
  );
  const { data: financials, isLoading: loadingFinancials } = useBDCFinancials(
    selectedTicker || undefined,
    selectedPeriod || undefined
  );

  const handleTickerChange = (t: string) => {
    playClickSound();
    setSelectedTicker(t);
    setSelectedPeriod('');
    setShowInvestments(false);
    setShowFinancials(false);
  };

  const handlePeriodChange = (p: string) => {
    playClickSound();
    setSelectedPeriod(p);
    setShowInvestments(false);
    setShowFinancials(false);
  };

  return (
    <div className="window p-3 space-y-4">
      <div className="text-sm font-semibold mb-4 text-black">Data Explorer - Test All Scraper Data</div>
      
      {/* Ticker Selection */}
      <div className="space-y-2">
        <div className="text-xs font-semibold text-black">Select Ticker:</div>
        <select
          className="input w-full"
          value={selectedTicker}
          onChange={(e) => handleTickerChange(e.target.value)}
        >
          <option value="">-- Select Ticker --</option>
          {index?.bdcs.map((b) => (
            <option key={b.ticker} value={b.ticker}>
              {b.ticker} • {b.name} ({b.periods?.length || 0} periods)
            </option>
          ))}
        </select>
      </div>

      {/* Period Selection */}
      {selectedTicker && periods && periods.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-semibold text-black">Select Period:</div>
          <select
            className="input w-full"
            value={selectedPeriod}
            onChange={(e) => handlePeriodChange(e.target.value)}
          >
            <option value="">-- Select Period --</option>
            {periods.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Data Availability Summary */}
      {selectedTicker && (
        <div className="window p-2 border-2 border-[#808080]">
          <div className="text-xs font-semibold mb-2 text-black">Data Availability:</div>
          <div className="space-y-1 text-xs">
            <div className="flex items-center gap-2">
              <span className="text-[#808080]">Periods:</span>
              <span className="text-black">{periods?.length || 0} available</span>
            </div>
            {selectedPeriod && (
              <>
                <div className="flex items-center gap-2">
                  <span className="text-[#808080]">Investments:</span>
                  <span className={investments ? 'text-[#00ff00]' : 'text-[#ff0000]'}>
                    {investments ? '✓ Available' : loadingInvestments ? 'Loading...' : '✗ Not Available'}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[#808080]">Financials:</span>
                  <span className={financials ? 'text-[#00ff00]' : 'text-[#ff0000]'}>
                    {financials ? '✓ Available' : loadingFinancials ? 'Loading...' : '✗ Not Available'}
                  </span>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Action Buttons */}
      {selectedTicker && selectedPeriod && (
        <div className="flex gap-2 flex-wrap">
          <button
            className="btn text-xs"
            onClick={() => {
              playClickSound();
              setShowInvestments(!showInvestments);
            }}
          >
            {showInvestments ? 'Hide' : 'Show'} Investments Data
          </button>
          <button
            className="btn text-xs"
            onClick={() => {
              playClickSound();
              setShowFinancials(!showFinancials);
            }}
          >
            {showFinancials ? 'Hide' : 'Show'} Financials Data
          </button>
        </div>
      )}

      {/* Investments Data Display */}
      {showInvestments && investments && (
        <div className="window p-3 border-2 border-[#808080]">
          <div className="text-xs font-semibold mb-2 text-black">Investments Data ({investments.investments?.length || 0} holdings):</div>
          <div className="text-xs text-[#808080] mb-2">
            Period: {investments.period} | Ticker: {investments.ticker}
          </div>
          <div className="overflow-auto max-h-96">
            <pre className="text-[9px] text-black bg-white p-2 border border-[#c0c0c0]">
              {JSON.stringify(investments, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {/* Financials Data Display */}
      {showFinancials && financials && (
        <div className="window p-3 border-2 border-[#808080]">
          <div className="text-xs font-semibold mb-2 text-black">Financials Data:</div>
          <div className="text-xs text-[#808080] mb-2">
            Period: {financials.period} | Ticker: {financials.ticker}
          </div>
          <div className="space-y-2 mb-2">
            {financials.income_statement && (
              <div className="text-xs">
                <span className="font-semibold text-black">Income Statement:</span>
                <span className="text-[#808080] ml-2">
                  {Object.keys(financials.income_statement).length} items
                </span>
              </div>
            )}
            {financials.balance_sheet && (
              <div className="text-xs">
                <span className="font-semibold text-black">Balance Sheet:</span>
                <span className="text-[#808080] ml-2">
                  {Object.keys(financials.balance_sheet).length} items
                </span>
              </div>
            )}
            {financials.cash_flow_statement && (
              <div className="text-xs">
                <span className="font-semibold text-black">Cash Flow:</span>
                <span className="text-[#808080] ml-2">
                  {Object.keys(financials.cash_flow_statement).length} items
                </span>
              </div>
            )}
          </div>
          <div className="overflow-auto max-h-96">
            <pre className="text-[9px] text-black bg-white p-2 border border-[#c0c0c0]">
              {JSON.stringify(financials, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {/* All Tickers Summary */}
      <div className="window p-3 border-2 border-[#808080]">
        <div className="text-xs font-semibold mb-2 text-black">All Available Tickers ({index?.bdcs.length || 0}):</div>
        <div className="text-xs text-[#808080] mb-2">
          Click any ticker to explore its data. Green = has data, Red = missing data.
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2 max-h-64 overflow-y-auto">
          {index?.bdcs.map((b) => (
            <button
              key={b.ticker}
              className="btn text-xs text-left px-2 py-1"
              onClick={() => handleTickerChange(b.ticker)}
              title={`${b.name} - ${b.periods?.length || 0} periods available`}
            >
              {b.ticker} ({b.periods?.length || 0})
            </button>
          ))}
        </div>
      </div>

      {/* Instructions */}
      <div className="window p-3 border-2 border-[#808080] bg-[#c0c0c0]">
        <div className="text-xs font-semibold mb-2 text-black">How to Use:</div>
        <div className="text-xs text-black space-y-1">
          <div>1. Select a ticker from the dropdown or grid above</div>
          <div>2. Select a period to view data for that quarter</div>
          <div>3. Click "Show Investments Data" to view raw investment holdings JSON</div>
          <div>4. Click "Show Financials Data" to view raw financial statements JSON</div>
          <div>5. Use this to verify all scraper data is accessible and test financials extraction</div>
        </div>
      </div>
    </div>
  );
}

