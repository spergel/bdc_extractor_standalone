import { ReactNode, useState } from 'react';
import { ProfileCard } from './ProfileCard';
import { FinancialsPanel } from './FinancialsPanel';
import { HoldingsTable } from './HoldingsTable';
import { SimpleAnalyticsPanel } from './SimpleAnalyticsPanel';
import { AnalyticsPanel } from './AnalyticsPanel';
import { DiffViewer } from './DiffViewer';
import { getPreviousQuarter, getYearOverYear, getYearEndComparison, getComparisonLabel } from '../utils/periodComparisons';
import { playClickSound } from '../utils/sounds';
import { MonitorPanel } from './MonitorPanel';

type TabContentProps = {
  ticker?: string;
  selectedPeriod?: string;
  periods?: string[];
  snapshot?: any;
  investments: any[];
  investmentsError?: Error | null;
  isLoadingInvestments: boolean;
  selected?: { name?: string };
  finRange: 'quarters' | 'years';
  recentPeriods: string[];
  activeTab: string;
  diffBeforePeriod?: string;
  diffAfterPeriod?: string;
  diffSnapshots: any[];
  hasUserDiffSelection: boolean;
  onPeriodChange: (period: string) => void;
  onFinRangeChange: (range: 'quarters' | 'years') => void;
  onDiffSelection: (before: string | undefined, after: string | undefined, source: string) => void;
  onUserDiffSelection: () => void;
};

function AnalyticsTabContent({
  investments,
  selectedPeriod,
  periods,
  onPeriodChange,
}: {
  investments: any[];
  selectedPeriod?: string;
  periods?: string[];
  onPeriodChange: (period: string) => void;
}) {
  const [view, setView] = useState<'charts' | 'numbers'>('charts');

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      <div className="mb-2 flex items-center gap-3 flex-wrap flex-shrink-0">
        <div className="flex items-center gap-2 text-xs text-[#808080]">
          <span>Period:</span>
          <select
            className="input text-xs"
            value={selectedPeriod ?? ''}
            onChange={(e) => onPeriodChange(e.target.value)}
            disabled={!periods || !periods.length}
          >
            {periods?.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-1 text-xs">
          <button
            className={`btn text-xs ${view === 'charts' ? 'pressed' : ''}`}
            onClick={() => { playClickSound(); setView('charts'); }}
          >
            Charts
          </button>
          <button
            className={`btn text-xs ${view === 'numbers' ? 'pressed' : ''}`}
            onClick={() => { playClickSound(); setView('numbers'); }}
          >
            Numbers
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {view === 'charts' ? (
          <AnalyticsPanel holdings={investments as any} period={selectedPeriod} />
        ) : (
          <SimpleAnalyticsPanel holdings={investments as any} period={selectedPeriod} />
        )}
      </div>
    </div>
  );
}

export function TabContent({
  ticker,
  selectedPeriod,
  periods,
  snapshot,
  investments,
  investmentsError,
  isLoadingInvestments,
  selected,
  finRange,
  recentPeriods,
  activeTab,
  diffBeforePeriod,
  diffAfterPeriod,
  diffSnapshots,
  hasUserDiffSelection,
  onPeriodChange,
  onFinRangeChange,
  onDiffSelection,
  onUserDiffSelection,
}: TabContentProps) {
  const tabs = [
    {
      id: 'overview',
      label: 'Overview',
      content: ticker ? (
        <div className="flex flex-col h-full min-h-0 overflow-hidden">
          <div className="flex-1 min-h-0 overflow-y-auto space-y-4 p-1">
            <ProfileCard ticker={ticker} name={selected?.name} />
            {/* Data summary */}
            <div className="window p-3">
              <div className="text-xs font-semibold mb-2 text-black">Data Available</div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                <div>
                  <div className="text-[#808080]">Periods</div>
                  <div className="text-black font-semibold">{periods?.length ?? 0} quarters</div>
                </div>
                <div>
                  <div className="text-[#808080]">Holdings</div>
                  <div className="text-black font-semibold">{investments.length} positions</div>
                </div>
                <div>
                  <div className="text-[#808080]">Latest Filing</div>
                  <div className="text-black font-semibold">{selectedPeriod ?? 'N/A'}</div>
                </div>
                <div>
                  <div className="text-[#808080]">SEC Filings</div>
                  <a
                    className="text-[#0000ff] hover:underline font-semibold"
                    href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=${ticker}&type=10-Q&dateb=&owner=include&count=10`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    View on EDGAR
                  </a>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="window p-4">
          <div className="text-sm text-black font-semibold mb-2">Welcome to BDC Analyzer</div>
          <div className="text-xs text-[#808080]">Select a BDC from the sidebar to view portfolio holdings, analytics, and financial data.</div>
        </div>
      ),
    },
    {
      id: 'financials',
      label: 'Financials',
      content: ticker ? (
        <div className="flex flex-col h-full">
          <div className="mb-3 flex items-center gap-3 flex-wrap flex-shrink-0">
            <div className="flex items-center gap-2 text-xs text-[#808080]">
              <span>View:</span>
              <select className="input" value={finRange}
                onChange={(e) => onFinRangeChange(e.target.value as 'quarters' | 'years')}
              >
                <option value="quarters">Last 5 Quarters</option>
                <option value="years">Last 5 Years (20 Quarters)</option>
              </select>
            </div>
          </div>
          <div className="flex-1 min-h-0 overflow-auto">
            {!periods || periods.length === 0 ? (
              <div className="window p-4">
                <div className="text-sm text-[#808080]">No periods available</div>
              </div>
            ) : recentPeriods.length > 0 ? (
              <FinancialsPanel ticker={ticker} periods={recentPeriods} name={selected?.name} mode="historical" />
            ) : (
              <div className="text-xs text-[#808080] p-4">Loading financials...</div>
            )}
          </div>
        </div>
      ) : (
        <div className="text-xs text-[#808080]">Select a BDC to view financials</div>
      ),
    },
    {
      id: 'holdings',
      label: 'Holdings',
      content: ticker ? (
        <div className="flex flex-col h-full min-h-0 overflow-hidden">
          <div className="mb-2 flex items-center gap-2 flex-wrap flex-shrink-0">
            <div className="flex items-center gap-2 text-xs text-[#808080]">
              <span>Period:</span>
              <select
                className="input text-xs"
                value={selectedPeriod ?? ''}
                onChange={(e) => onPeriodChange(e.target.value)}
                disabled={!periods || !periods.length}
              >
                {periods?.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="flex-1 min-h-0 overflow-hidden">
            {investmentsError ? (
              <div className="window p-4">
                <div className="text-sm text-[#ff0000]">
                  ⚠️ Error loading holdings
                </div>
                <div className="text-xs text-[#808080] mt-1">
                  {investmentsError instanceof Error ? investmentsError.message : 'Failed to load holdings data'}
                </div>
              </div>
            ) : isLoadingInvestments ? (
              <div className="text-xs text-[#808080] p-4">Loading holdings...</div>
            ) : snapshot && investments.length > 0 ? (
              <HoldingsTable key={`${ticker ?? 'none'}-${selectedPeriod ?? 'none'}`} data={investments as any} period={selectedPeriod} />
            ) : snapshot && investments.length === 0 ? (
              <div className="window p-4">
                <div className="text-sm text-[#808080]">No holdings data available for this period</div>
              </div>
            ) : (
              <div className="text-xs text-[#808080] p-4">Select a period to view holdings</div>
            )}
          </div>
        </div>
      ) : (
        <div className="text-xs text-[#808080]">Select a BDC to view holdings</div>
      ),
    },
    {
      id: 'analytics',
      label: 'Analytics',
      content: ticker ? (
        snapshot && investments.length > 0 ? (
          <AnalyticsTabContent
            investments={investments}
            selectedPeriod={selectedPeriod}
            periods={periods}
            onPeriodChange={onPeriodChange}
          />
        ) : isLoadingInvestments ? (
          <div className="window p-4">
            <div className="text-xs text-[#808080]">Loading holdings data for analytics...</div>
          </div>
        ) : (
          <div className="window p-4">
            <div className="text-sm text-[#808080]">No holdings data available for analytics</div>
            <div className="text-xs text-[#808080] mt-1">Select a period with holdings data in the Holdings tab.</div>
          </div>
        )
      ) : (
        <div className="window p-4">
          <div className="text-xs text-[#808080]">Select a BDC to view analytics</div>
        </div>
      ),
    },
    {
      id: 'changes',
      label: 'Changes',
      content: ticker && periods && periods.length >= 2 ? (
        <div className="flex flex-col h-full min-h-0 overflow-hidden">
          <div className="space-y-3 sm:space-y-4 overflow-y-auto flex-1 min-h-0 p-1">
            <div className="window p-2 sm:p-3 flex-shrink-0">
              {/* Quick Comparison Buttons */}
              <div className="mb-3 sm:mb-4">
                <div className="text-xs text-[#808080] mb-2">Quick Comparisons:</div>
                <div className="flex items-center gap-1 sm:gap-2 flex-wrap">
                  {(() => {
                    const prevQ = selectedPeriod ? getPreviousQuarter(selectedPeriod, periods) : null;
                    const qoqAvailable = !!prevQ;
                    const isQoqActive = diffBeforePeriod === prevQ && diffAfterPeriod === selectedPeriod;
                    return (
                      <button
                        className={`btn text-xs ${isQoqActive ? 'pressed' : ''}`}
                        onClick={() => {
                          playClickSound();
                          if (selectedPeriod && prevQ) {
                            onUserDiffSelection();
                            onDiffSelection(prevQ, selectedPeriod, 'QoQ button');
                          }
                        }}
                        disabled={!selectedPeriod || !qoqAvailable}
                        title={selectedPeriod && qoqAvailable ? getComparisonLabel('qoq', selectedPeriod) : 'Previous quarter not available'}
                      >
                        QoQ (vs Last Quarter)
                      </button>
                    );
                  })()}
                  {(() => {
                    const yoy = selectedPeriod ? getYearOverYear(selectedPeriod, periods) : null;
                    const yoyAvailable = !!yoy;
                    const isYoyActive = diffBeforePeriod === yoy && diffAfterPeriod === selectedPeriod;
                    return (
                      <button
                        className={`btn text-xs ${isYoyActive ? 'pressed' : ''}`}
                        onClick={() => {
                          playClickSound();
                          if (selectedPeriod && yoy) {
                            onUserDiffSelection();
                            onDiffSelection(yoy, selectedPeriod, 'YoY button');
                          }
                        }}
                        disabled={!selectedPeriod || !yoyAvailable}
                        title={selectedPeriod && yoyAvailable ? getComparisonLabel('yoy', selectedPeriod) : 'Same quarter last year not available'}
                      >
                        YoY (vs Same Q Last Year)
                      </button>
                    );
                  })()}
                  {(() => {
                    const ye = selectedPeriod ? getYearEndComparison(selectedPeriod, periods) : null;
                    const yeAvailable = !!ye;
                    const isYeActive = diffBeforePeriod === ye && diffAfterPeriod === selectedPeriod;
                    return (
                      <button
                        className={`btn text-xs ${isYeActive ? 'pressed' : ''}`}
                        onClick={() => {
                          playClickSound();
                          if (selectedPeriod && ye) {
                            onUserDiffSelection();
                            onDiffSelection(ye, selectedPeriod, 'YE button');
                          }
                        }}
                        disabled={!selectedPeriod || !yeAvailable}
                        title={selectedPeriod && yeAvailable ? getComparisonLabel('ye', selectedPeriod) : 'Year-end comparison not available'}
                      >
                        YE vs Now
                      </button>
                    );
                  })()}
                </div>
              </div>
              
              {/* Manual Period Selection */}
              <div className="flex flex-col sm:flex-row items-start sm:items-center gap-2 sm:gap-4 flex-wrap">
                <div className="flex items-center gap-2 text-xs text-[#808080] w-full sm:w-auto">
                  <span>Compare:</span>
                  <select
                    className="input text-xs flex-1 sm:flex-initial"
                    value={diffBeforePeriod ?? ''}
                    onChange={(e) => {
                      playClickSound();
                      onUserDiffSelection();
                      onDiffSelection(e.target.value, diffAfterPeriod, 'manual before select');
                    }}
                    disabled={!periods || !periods.length}
                  >
                    {periods?.map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                  <span className="text-[#808080]">→</span>
                  <select
                    className="input text-xs flex-1 sm:flex-initial"
                    value={diffAfterPeriod ?? ''}
                    onChange={(e) => {
                      playClickSound();
                      onUserDiffSelection();
                      onDiffSelection(diffBeforePeriod, e.target.value, 'manual after select');
                    }}
                    disabled={!periods || !periods.length}
                  >
                    {periods?.map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
            {diffSnapshots.length === 2 && diffSnapshots[0].data && diffSnapshots[1].data ? (
              <DiffViewer
                beforeHoldings={diffSnapshots[0].data.investments ?? []}
                afterHoldings={diffSnapshots[1].data.investments ?? []}
                beforePeriod={diffBeforePeriod || ''}
                afterPeriod={diffAfterPeriod || ''}
              />
            ) : diffSnapshots.some(s => s.isLoading) ? (
              <div className="text-xs text-[#808080] flex-shrink-0">Loading period data...</div>
            ) : (
              <div className="text-xs text-[#808080] flex-shrink-0">Select periods to compare</div>
            )}
          </div>
        </div>
      ) : (
        <div className="window p-4">
          <div className="text-sm text-[#808080]">
            {!ticker ? 'Select a BDC to compare periods' :
             !periods || periods.length === 0 ? 'No period data available' :
             'Need at least 2 periods to compare. Backfill more historical data to enable comparisons.'}
          </div>
        </div>
      ),
    },
    {
      id: 'monitor',
      label: 'Monitor',
      content: <MonitorPanel />,
    },
  ];

  return tabs;
}

