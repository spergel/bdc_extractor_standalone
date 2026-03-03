import { useEffect, useMemo, useState, useCallback } from 'react';
import './index.css';
import './styles.css';
import { SidebarDock } from './components/SidebarDock';
import { CompaniesSidebar } from './components/CompaniesSidebar';
import { SectorsSidebar } from './components/SectorsSidebar';
import { CompanyPage } from './components/CompanyPage';
import { SectorPage } from './components/SectorPage';
import { useBDCIndex, useBDCInvestments, useBDCInvestmentsMultiple, useBDCPeriods, useCompanyExposures } from './api/hooks';
import { Tabs } from './components/Tabs';
import { AppHeader, type ViewMode } from './components/AppHeader';
import { StatusBar } from './components/StatusBar';
import { MobileSelector } from './components/MobileSelector';
import { TabContent } from './components/TabContent';
import { getYearEndComparison } from './utils/periodComparisons';

function App() {
  const { data: index } = useBDCIndex();
  const { data: exposures } = useCompanyExposures();

  const [viewMode, setViewMode] = useState<ViewMode>('bdc');
  const [ticker, setTicker] = useState<string | undefined>(undefined);
  const [activeTab, setActiveTab] = useState<string>('overview');
  const [selectedCompanyId, setSelectedCompanyId] = useState<string | undefined>(undefined);
  const [selectedSector, setSelectedSector] = useState<string | undefined>(undefined);
  const [showMobileSidebar, setShowMobileSidebar] = useState(false);

  const { data: periods } = useBDCPeriods(ticker);
  const latestPeriodForTicker = useMemo(() => {
    if (!ticker || !index?.bdcs) return undefined;
    const bdc = index.bdcs.find((b) => b.ticker === ticker);
    return bdc?.latest ?? (periods && periods.length ? periods[periods.length - 1] : undefined);
  }, [index, ticker, periods]);
  const defaultPeriod = latestPeriodForTicker;

  const getStoredPeriod = (t: string | undefined, prds: string[] | undefined, latest: string | undefined): string | undefined => {
    if (!t || !prds || prds.length === 0) return undefined;
    const key = `bdc_period_${t}`;
    const stored = localStorage.getItem(key);
    if (stored && prds.includes(stored)) return stored;
    return latest ?? prds[prds.length - 1];
  };

  const [period, setPeriod] = useState<string | undefined>(undefined);
  const selectedPeriod = period ?? defaultPeriod;
  const [finRange, setFinRange] = useState<'quarters' | 'years'>('quarters');

  useEffect(() => {
    if (!ticker && index?.bdcs?.length) {
      setTicker(index.bdcs[0].ticker);
    }
  }, [index, ticker]);

  useEffect(() => {
    if (ticker && periods && periods.length) {
      setPeriod(getStoredPeriod(ticker, periods, latestPeriodForTicker));
    } else {
      setPeriod(undefined);
    }
  }, [ticker, periods, latestPeriodForTicker]);

  useEffect(() => {
    if (periods && periods.length && !period) {
      setPeriod(getStoredPeriod(ticker, periods, latestPeriodForTicker));
    }
  }, [periods, ticker, period, latestPeriodForTicker]);

  useEffect(() => {
    if (ticker && period && periods && periods.includes(period)) {
      localStorage.setItem(`bdc_period_${ticker}`, period);
    }
  }, [ticker, period, periods]);

  const { data: snapshot, isLoading: isLoadingInvestments, error: investmentsError } = useBDCInvestments(ticker, selectedPeriod);

  const [diffBeforePeriod, setDiffBeforePeriod] = useState<string | undefined>(undefined);
  const [diffAfterPeriod, setDiffAfterPeriod] = useState<string | undefined>(undefined);
  const [hasUserDiffSelection, setHasUserDiffSelection] = useState(false);

  const applyDiffSelection = useCallback((before: string | undefined, after: string | undefined) => {
    setDiffBeforePeriod(before);
    setDiffAfterPeriod(after);
  }, []);

  const diffSnapshots = useBDCInvestmentsMultiple(
    ticker,
    diffBeforePeriod && diffAfterPeriod ? [diffBeforePeriod, diffAfterPeriod] : []
  );

  useEffect(() => {
    if (periods && periods.length >= 2 && !diffBeforePeriod && !diffAfterPeriod) {
      applyDiffSelection(periods[periods.length - 2], periods[periods.length - 1]);
    }
  }, [periods, diffBeforePeriod, diffAfterPeriod, applyDiffSelection]);

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
        applyDiffSelection(ye, selectedPeriod);
        setHasUserDiffSelection(true);
      }
    }
  }, [activeTab, selectedPeriod, periods, hasUserDiffSelection, applyDiffSelection]);

  useEffect(() => {
    setHasUserDiffSelection(false);
    applyDiffSelection(undefined, undefined);
  }, [ticker, applyDiffSelection]);

  const investments = snapshot?.investments ?? [];
  const bdcs = index?.bdcs ?? [];
  const selected = bdcs.find((b) => b.ticker === ticker);

  const recentPeriods = useMemo(() => {
    if (!periods || periods.length === 0) return [];
    if (finRange === 'quarters') {
      return [...periods].reverse().slice(0, Math.min(5, periods.length));
    }
    const today = new Date();
    const fiveYearsAgo = new Date(today.getFullYear() - 5, today.getMonth(), today.getDate());
    const inRange = periods.filter((p) => {
      try {
        return new Date(p) >= fiveYearsAgo;
      } catch {
        return true;
      }
    });
    return inRange.length > 0 ? [...inRange].reverse() : [...periods].reverse().slice(0, Math.min(20, periods.length));
  }, [periods, finRange]);

  const handleTickerSelect = useCallback((t: string) => {
    setTicker(t);
  }, []);

  const handleCompanyClick = useCallback((companyId: string) => {
    setViewMode('companies');
    setSelectedCompanyId(companyId);
  }, []);

  /** Resolve company name to company_id using exposures (for holdings that don't have company_id in CSV). */
  const getCompanyIdFromName = useCallback(
    (name: string) => {
      if (!name || !exposures?.length) return undefined;
      const key = name.trim().toLowerCase();
      if (!key) return undefined;
      const e = (exposures as { company_id?: string; company_name?: string }[]).find(
        (x) => (x.company_name ?? '').trim().toLowerCase() === key
      );
      return e?.company_id;
    },
    [exposures]
  );

  const selectedCompanyName = useMemo(() => {
    if (!selectedCompanyId || !exposures?.length) return undefined;
    const e = (exposures as { company_id?: string; company_name?: string }[]).find(
      (x) => x.company_id === selectedCompanyId
    );
    return e?.company_name;
  }, [selectedCompanyId, exposures]);

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
    onDiffSelection: (before, after) => applyDiffSelection(before, after),
    onUserDiffSelection: () => setHasUserDiffSelection(true),
    onCompanyClick: handleCompanyClick,
    getCompanyIdFromName,
  });

  const sidebarContent = viewMode === 'bdc' && (
    <SidebarDock onSelect={handleTickerSelect} selectedTicker={ticker} />
  );
  const sidebarContentCompanies = viewMode === 'companies' && (
    <CompaniesSidebar
      selectedCompanyId={selectedCompanyId}
      onSelectCompany={setSelectedCompanyId}
    />
  );
  const sidebarContentSectors = viewMode === 'sectors' && (
    <SectorsSidebar selectedSector={selectedSector} onSelectSector={setSelectedSector} />
  );

  const mainContent =
    viewMode === 'bdc' ? (
      <div className="window p-1 sm:p-1.5 flex flex-col h-full min-h-0 overflow-hidden">
        <Tabs tabs={tabs} initialId={activeTab} onChange={(id) => setActiveTab(id)} />
      </div>
    ) : viewMode === 'companies' ? (
      <div className="window p-1 sm:p-1.5 flex flex-col h-full min-h-0 overflow-hidden">
        <CompanyPage
          companyId={selectedCompanyId}
          onSelectBDC={handleTickerSelect}
          onSwitchToBDC={() => setViewMode('bdc')}
        />
      </div>
    ) : (
      <div className="window p-1 sm:p-1.5 flex flex-col h-full min-h-0 overflow-hidden">
        <SectorPage
          sector={selectedSector}
          onSelectCompany={(id) => {
            setSelectedCompanyId(id);
            setViewMode('companies');
          }}
          onSwitchToCompanies={() => setViewMode('companies')}
        />
      </div>
    );

  return (
    <div className="h-full flex flex-col">
      <AppHeader viewMode={viewMode} onViewModeChange={setViewMode} />
      <div className="flex-shrink-0 px-2 py-1.5 bg-[#000080] text-white text-xs text-center">
        <strong>Demo:</strong> Showing GSBD, MAIN & MRCC (from 2019). We intend to add the rest — <strong>feedback welcome</strong>.
      </div>
      <div className="flex-1 min-h-0 flex flex-col lg:flex-row gap-1 sm:gap-2 p-1 sm:p-1.5 overflow-hidden">
        <MobileSelector
          viewMode={viewMode}
          ticker={ticker}
          selectedCompanyId={selectedCompanyId}
          selectedSector={selectedSector}
          onTickerSelect={handleTickerSelect}
          onSelectCompany={setSelectedCompanyId}
          onSelectSector={setSelectedSector}
          showSidebar={showMobileSidebar}
          onToggleSidebar={() => setShowMobileSidebar((prev) => !prev)}
        />
        <div className="hidden lg:block w-full lg:w-64 xl:w-72 flex-shrink-0 min-h-0">
          {sidebarContent}
          {sidebarContentCompanies}
          {sidebarContentSectors}
        </div>
        <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
          {mainContent}
        </div>
      </div>
      <StatusBar
        viewMode={viewMode}
        ticker={ticker}
        period={selectedPeriod}
        rowCount={viewMode === 'bdc' ? investments.length : undefined}
        selectedCompanyName={selectedCompanyName}
        selectedSector={selectedSector}
        dataUpdatedAt={index?.generated_at}
      />
    </div>
  );
}

export default App;
