import { useEffect, useMemo, useState, useCallback } from 'react';
import './index.css';
import './styles.css';
import { SidebarDock } from './components/SidebarDock';
import { useBDCIndex, useBDCInvestments, useBDCInvestmentsMultiple, useBDCPeriods } from './api/hooks';
import { Tabs } from './components/Tabs';
import { AppHeader } from './components/AppHeader';
import { StatusBar } from './components/StatusBar';
import { MobileSelector } from './components/MobileSelector';
import { TabContent } from './components/TabContent';
import { getYearEndComparison } from './utils/periodComparisons';

function App() {
  const { data: index } = useBDCIndex();
  const [mode, setMode] = useState<'individual' | 'comparison'>('individual');
  const [ticker, setTicker] = useState<string | undefined>(undefined);
  const [selectedTickers, setSelectedTickers] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState<string>('overview');
  const { data: periods } = useBDCPeriods(ticker);
  const [showMobileSidebar, setShowMobileSidebar] = useState(false);
  const defaultPeriod = useMemo(() => (periods && periods.length ? periods[periods.length - 1] : undefined), [periods]);
  
  // Load period from localStorage or use default
  const getStoredPeriod = (ticker: string | undefined, periods: string[] | undefined): string | undefined => {
    if (!ticker || !periods || periods.length === 0) return undefined;
    const key = `bdc_period_${ticker}`;
    const stored = localStorage.getItem(key);
    if (stored && periods.includes(stored)) {
      return stored;
    }
    return periods[periods.length - 1];
  };

  const [period, setPeriod] = useState<string | undefined>(undefined);
  const selectedPeriod = period ?? defaultPeriod;
  const [finRange, setFinRange] = useState<'quarters' | 'years'>('quarters');

  useEffect(() => {
    if (!ticker && index?.bdcs?.length) {
      setTicker(index.bdcs[0].ticker);
    }
  }, [index, ticker]);

  // Load stored period when ticker changes
  useEffect(() => {
    if (ticker && periods && periods.length) {
      const stored = getStoredPeriod(ticker, periods);
      setPeriod(stored);
    } else {
      setPeriod(undefined);
    }
  }, [ticker, periods]);

  // Ensure period gets set to latest as soon as periods load/change (if no stored period)
  useEffect(() => {
    if (periods && periods.length && !period) {
      const stored = getStoredPeriod(ticker, periods);
      setPeriod(stored);
    }
  }, [periods, ticker, period]);

  // Save period to localStorage when it changes
  useEffect(() => {
    if (ticker && period && periods && periods.includes(period)) {
      localStorage.setItem(`bdc_period_${ticker}`, period);
    }
  }, [ticker, period, periods]);

  const { data: snapshot, isLoading: isLoadingInvestments, error: investmentsError } = useBDCInvestments(ticker, selectedPeriod);
  
  // For diff viewer: compare two periods
  const [diffBeforePeriod, setDiffBeforePeriod] = useState<string | undefined>(undefined);
  const [diffAfterPeriod, setDiffAfterPeriod] = useState<string | undefined>(undefined);
  const [hasUserDiffSelection, setHasUserDiffSelection] = useState(false);

  const applyDiffSelection = useCallback((before: string | undefined, after: string | undefined, source: string) => {
    console.log('[DiffSelection]', source, { before, after });
    setDiffBeforePeriod(before);
    setDiffAfterPeriod(after);
  }, []);
  
  // Fetch both periods for diff
  const diffSnapshots = useBDCInvestmentsMultiple(ticker, 
    diffBeforePeriod && diffAfterPeriod ? [diffBeforePeriod, diffAfterPeriod] : []
  );
  
  // Set default diff periods (previous vs current)
  useEffect(() => {
    if (periods && periods.length >= 2 && !diffBeforePeriod && !diffAfterPeriod) {
      applyDiffSelection(periods[periods.length - 2], periods[periods.length - 1], 'initial default diff');
    }
  }, [periods, diffBeforePeriod, diffAfterPeriod, applyDiffSelection]);

  // Auto-select YE comparison when Changes tab is opened
  useEffect(() => {
    if (
      activeTab === 'changes' &&
      selectedPeriod &&
      periods &&
      periods.length >= 2 &&
      !hasUserDiffSelection
    ) {
      const ye = selectedPeriod && periods ? getYearEndComparison(selectedPeriod, periods) : null;
      if (ye) {
        applyDiffSelection(ye, selectedPeriod, 'auto YE default');
        setHasUserDiffSelection(true);
      }
    }
  }, [activeTab, selectedPeriod, periods, hasUserDiffSelection, applyDiffSelection]);

  // Reset diff selection when ticker changes
  useEffect(() => {
    setHasUserDiffSelection(false);
    applyDiffSelection(undefined, undefined, 'ticker change reset');
  }, [ticker, applyDiffSelection]);


  const investments = snapshot?.investments ?? [];

  const bdcs = index?.bdcs ?? [];
  const selected = bdcs.find((b) => b.ticker === ticker);

  // Get recent periods for financials (last 5 quarters vs last 5 years)
  const recentPeriods = useMemo(() => {
    if (!periods || periods.length === 0) return [];
    if (finRange === 'quarters') {
      // Show last 5 quarters (most recent first)
      const count = Math.min(5, periods.length);
      return [...periods].reverse().slice(0, count);
    } else {
      // Last 5 years - need at least 20 periods (4 quarters per year)
      // Calculate 5 years back from today to ensure we get full 5 years
      const today = new Date();
      const fiveYearsAgo = new Date(today.getFullYear() - 5, today.getMonth(), today.getDate());
      
      // Filter periods to those within the last 5 years
      const periodsInRange = periods.filter(period => {
        try {
          const periodDate = new Date(period);
          return periodDate >= fiveYearsAgo;
        } catch {
          return true; // Include if we can't parse
        }
      });
      
      // If we have periods in range, use them; otherwise take up to 20 most recent
      if (periodsInRange.length > 0) {
        return [...periodsInRange].reverse();
      } else {
        // Fallback: take up to 20 most recent periods
        const count = Math.min(20, periods.length);
        return [...periods].reverse().slice(0, count);
      }
    }
  }, [periods, finRange]);

  const handleTickerToggle = (t: string) => {
    setSelectedTickers(prev => 
      prev.includes(t) 
        ? prev.filter(ticker => ticker !== t)
        : [...prev, t]
    );
  };

  const handleModeChange = (newMode: 'individual' | 'comparison') => {
    setMode(newMode);
    if (newMode === 'individual') {
      setSelectedTickers([]);
    } else {
      // When switching to comparison, add current ticker if available
      if (ticker && !selectedTickers.includes(ticker)) {
        setSelectedTickers([ticker]);
      }
    }
  };

  const handleTickerSelect = useCallback((t: string) => {
    setTicker(t);
    if (mode === 'comparison' && !selectedTickers.includes(t)) {
      setSelectedTickers(prev => [...prev, t]);
    }
  }, [mode, selectedTickers]);

  const tabs = TabContent({
    ticker,
    selectedPeriod,
    periods: periods ?? undefined,
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
    onPeriodChange: setPeriod,
    onFinRangeChange: setFinRange,
    onDiffSelection: applyDiffSelection,
    onUserDiffSelection: () => setHasUserDiffSelection(true),
  });

  return (
    <div className="h-full flex flex-col">
      <AppHeader mode={mode} onModeChange={handleModeChange} />
      <div className="flex-1 min-h-0 flex flex-col lg:flex-row gap-1 sm:gap-2 p-1 sm:p-1.5 overflow-hidden">
        <MobileSelector
          ticker={ticker}
          mode={mode}
          selectedTickers={selectedTickers}
          onTickerSelect={handleTickerSelect}
          onTickerToggle={handleTickerToggle}
          showSidebar={showMobileSidebar}
          onToggleSidebar={() => setShowMobileSidebar(prev => !prev)}
        />
        <div className="hidden lg:block w-full lg:w-64 xl:w-72 flex-shrink-0 min-h-0">
          <SidebarDock 
            onSelect={handleTickerSelect}
            selectedTicker={ticker}
            mode={mode}
            selectedTickers={selectedTickers}
            onTickerToggle={handleTickerToggle}
          />
        </div>
        <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
          <div className="window p-1 sm:p-1.5 flex flex-col h-full min-h-0 overflow-hidden">
            <Tabs
              tabs={tabs}
              initialId={activeTab}
              onChange={(id) => setActiveTab(id)}
            />
          </div>
        </div>
      </div>
      <StatusBar 
        ticker={mode === 'individual' ? ticker : undefined}
        period={mode === 'individual' ? selectedPeriod : undefined}
        rowCount={mode === 'individual' ? investments.length : undefined}
        mode={mode}
      />
    </div>
  );
}

export default App
