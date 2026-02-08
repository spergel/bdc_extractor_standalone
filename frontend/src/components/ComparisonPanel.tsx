import { useState, useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
import { useBDCIndex } from '../api/hooks';
import { fetchPeriods, fetchPeriodSnapshot } from '../api/client-csv';
import { HoldingsTable } from './HoldingsTable';
import { SimpleAnalyticsPanel } from './SimpleAnalyticsPanel';
import { FinancialsPanel } from './FinancialsPanel';
import { ProfileCard } from './ProfileCard';
import { Tabs } from './Tabs';

type Props = {
  selectedTickers: string[];
  onTickerToggle: (ticker: string) => void;
};

export function ComparisonPanel({ selectedTickers, onTickerToggle }: Props) {
  const { data: index } = useBDCIndex();
  const [activeTab, setActiveTab] = useState<string>('overview');
  const [period, setPeriod] = useState<Record<string, string>>({});

  const bdcs = index?.bdcs ?? [];
  const selectedBdcs = bdcs.filter(b => selectedTickers.includes(b.ticker));

  // Get periods for all selected tickers using useQueries
  const periodsQueries = useQueries({
    queries: selectedTickers.map(ticker => ({
      queryKey: ['bdc-periods', ticker],
      queryFn: () => fetchPeriods(ticker),
      staleTime: 24 * 60 * 60 * 1000,
    })),
  });

  const periodsData = useMemo(() => {
    return selectedTickers.map((ticker, idx) => ({
      ticker,
      data: periodsQueries[idx]?.data ?? null,
      isLoading: periodsQueries[idx]?.isLoading ?? false,
    }));
  }, [selectedTickers, periodsQueries]);

  // Get default periods
  const defaultPeriods = useMemo(() => {
    const defaults: Record<string, string> = {};
    periodsData.forEach(({ ticker, data: periods }) => {
      if (periods && periods.length > 0) {
        defaults[ticker] = periods[periods.length - 1];
      }
    });
    return defaults;
  }, [periodsData]);

  // Merge user-selected periods with defaults
  const effectivePeriods = useMemo(() => {
    return selectedTickers.reduce((acc, ticker) => {
      acc[ticker] = period[ticker] || defaultPeriods[ticker] || '';
      return acc;
    }, {} as Record<string, string>);
  }, [selectedTickers, period, defaultPeriods]);

  // Fetch investments for all selected tickers using useQueries
  const investmentsQueries = useQueries({
    queries: selectedTickers.map(ticker => ({
      queryKey: ['bdc-investments', ticker, effectivePeriods[ticker]],
      queryFn: () => fetchPeriodSnapshot(ticker, effectivePeriods[ticker]),
      enabled: !!ticker && !!effectivePeriods[ticker],
      staleTime: 24 * 60 * 60 * 1000,
    })),
  });

  const investmentsData = useMemo(() => {
    return selectedTickers.map((ticker, idx) => ({
      ticker,
      period: effectivePeriods[ticker],
      data: investmentsQueries[idx]?.data ?? null,
      isLoading: investmentsQueries[idx]?.isLoading ?? false,
      error: investmentsQueries[idx]?.error ?? null,
    }));
  }, [selectedTickers, effectivePeriods, investmentsQueries]);

  if (selectedTickers.length === 0) {
    return (
      <div className="window p-8 text-center">
        <div className="text-lg text-[#808080] mb-2">No BDCs Selected</div>
        <div className="text-sm text-[#808080]">
          Select BDCs from the sidebar to compare them side-by-side
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Selected BDCs Header */}
      <div className="window p-3 mb-4">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-[#808080]">Comparing:</span>
          {selectedBdcs.map((bdc) => (
            <button
              key={bdc.ticker}
              onClick={() => onTickerToggle(bdc.ticker)}
              className="btn text-xs flex items-center gap-1"
            >
              {bdc.ticker}
              <span className="text-black/60">×</span>
            </button>
          ))}
          {selectedTickers.length < 2 && (
            <span className="text-xs text-[#ffff00] ml-2">
              Select at least 2 BDCs to compare
            </span>
          )}
        </div>
      </div>

      {/* Tabbed Content */}
      <div className="window p-3 flex flex-col min-h-0 flex-1">
        <Tabs
          tabs={[
            {
              id: 'overview',
              label: 'Overview',
              content: (
                <div className="grid grid-cols-1 gap-4">
                  {selectedBdcs.map((bdc) => (
                    <div key={bdc.ticker} className="window p-3">
                      <div className="text-sm font-semibold text-black mb-2">
                        {bdc.ticker} • {bdc.name}
                      </div>
                      <ProfileCard ticker={bdc.ticker} name={bdc.name} />
                    </div>
                  ))}
                </div>
              ),
            },
            {
              id: 'holdings',
              label: 'Holdings',
              content: (
                <div className="space-y-4">
                  {selectedBdcs.map((bdc) => {
                    const ticker = bdc.ticker;
                    const selectedPeriod = effectivePeriods[ticker];
                    const query = investmentsData.find(q => q.ticker === ticker);
                    const investments = query?.data?.investments ?? [];
                    const isLoading = query?.isLoading ?? false;
                    const error = query?.error;

                    return (
                      <div key={ticker} className="window overflow-hidden">
                        <div className="titlebar p-2 flex items-center justify-between">
                          <div className="text-sm font-semibold text-white">
                            {ticker} • {bdc.name}
                          </div>
                          <select
                            className="input text-xs"
                            value={selectedPeriod || ''}
                            onChange={(e) => setPeriod(prev => ({ ...prev, [ticker]: e.target.value }))}
                            disabled={!periodsData.find(q => q.ticker === ticker)?.data?.length}
                          >
                            {periodsData
                              .find(q => q.ticker === ticker)
                              ?.data?.map((p) => (
                                <option key={p} value={p}>{p}</option>
                              ))}
                          </select>
                        </div>
                        <div className="p-2">
                          {error ? (
                            <div className="text-xs text-[#ff0000] p-2">
                              Error loading holdings
                            </div>
                          ) : isLoading ? (
                            <div className="text-xs text-[#808080] p-2">Loading...</div>
                          ) : investments.length > 0 ? (
                            <HoldingsTable data={investments as any} period={selectedPeriod} />
                          ) : (
                            <div className="text-xs text-[#808080] p-2">No holdings data</div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ),
            },
            {
              id: 'financials',
              label: 'Financials',
              content: (
                <div className="space-y-4">
                  {selectedBdcs.map((bdc) => {
                    const ticker = bdc.ticker;
                    const periods = periodsData.find(q => q.ticker === ticker)?.data ?? [];
                    const recentPeriods = periods.slice(-5).reverse();

                    return (
                      <div key={ticker} className="border border-silver/10 rounded overflow-hidden">
                        <div className="titlebar p-2">
                          <div className="text-sm font-semibold text-white">
                            {ticker} • {bdc.name}
                          </div>
                        </div>
                        <div className="p-2">
                          {recentPeriods.length > 0 ? (
                            <FinancialsPanel ticker={ticker} periods={recentPeriods} name={bdc.name} mode="historical" />
                          ) : (
                            <div className="text-xs text-[#808080] p-2">No financials data</div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ),
            },
            {
              id: 'analytics',
              label: 'Analytics',
              content: (
                <div className="space-y-4">
                  {selectedBdcs.map((bdc) => {
                    const ticker = bdc.ticker;
                    const selectedPeriod = effectivePeriods[ticker];
                    const query = investmentsData.find(q => q.ticker === ticker);
                    const investments = query?.data?.investments ?? [];

                    return (
                      <div key={ticker} className="border border-silver/10 rounded overflow-hidden">
                        <div className="titlebar p-2">
                          <div className="text-sm font-semibold text-white">
                            {ticker} • {bdc.name}
                          </div>
                        </div>
                        <div className="p-2">
                          {investments.length > 0 ? (
                            <SimpleAnalyticsPanel holdings={investments as any} period={selectedPeriod} />
                          ) : (
                            <div className="text-xs text-[#808080] p-2">No analytics data</div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ),
            },
          ]}
          activeTab={activeTab}
          onTabChange={setActiveTab}
        />
      </div>
    </div>
  );
}

