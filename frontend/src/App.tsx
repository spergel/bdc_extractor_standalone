import { useEffect, useMemo, useState, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import './index.css';
import './styles.css';
import { SidebarDock } from './components/SidebarDock';
import { CompaniesSidebar } from './components/CompaniesSidebar';
import { SectorsSidebar } from './components/SectorsSidebar';
import { CompanyPage } from './components/CompanyPage';
import { SectorPage } from './components/SectorPage';
import { IntroPage } from './components/IntroPage';
import { useBDCIndex, useBDCInvestments, useBDCInvestmentsMultiple, useBDCPeriods, useCompanyExposures } from './api/hooks';
import { Tabs } from './components/Tabs';
import { AppHeader, type ViewMode } from './components/AppHeader';
import { StatusBar } from './components/StatusBar';
import { MobileSelector } from './components/MobileSelector';
import { TabContent } from './components/TabContent';
import { PeerMarksPage } from './components/PeerMarksPage';
import { getYearEndComparison } from './utils/periodComparisons';

// ---------------------------------------------------------------------------
// URL schema:
//   /                     → Intro page
//   /bdc                  → BDC list (no ticker selected)
//   /bdc/:ticker          → BDC view, overview tab
//   /bdc/:ticker/:tab     → BDC view, specific tab
//   /companies            → Companies list
//   /companies/:companyId → Company detail
//   /sectors              → Sectors list
//   /sectors/:sector      → Sector detail (sector name is URI-encoded)
// ---------------------------------------------------------------------------

function parseLocation(pathname: string): {
  viewMode: ViewMode;
  ticker: string | undefined;
  activeTab: string;
  selectedCompanyId: string | undefined;
  selectedSector: string | undefined;
} {
  const parts = pathname.split('/').filter(Boolean);
  const section = parts[0];

  if (section === 'peer-marks') {
    return {
      viewMode: 'peer-marks',
      ticker: undefined,
      activeTab: 'overview',
      selectedCompanyId: undefined,
      selectedSector: undefined,
    };
  }
  if (section === 'companies') {
    return {
      viewMode: 'companies',
      ticker: undefined,
      activeTab: 'overview',
      selectedCompanyId: parts[1] ? decodeURIComponent(parts[1]) : undefined,
      selectedSector: undefined,
    };
  }
  if (section === 'sectors') {
    return {
      viewMode: 'sectors',
      ticker: undefined,
      activeTab: 'overview',
      selectedCompanyId: undefined,
      selectedSector: parts[1] ? decodeURIComponent(parts[1]) : undefined,
    };
  }
  // Default: bdc
  return {
    viewMode: 'bdc',
    ticker: parts[1] ?? undefined,
    activeTab: parts[2] ?? 'overview',
    selectedCompanyId: undefined,
    selectedSector: undefined,
  };
}

function App() {
  const location = useLocation();
  const navigate = useNavigate();

  const { viewMode, ticker, activeTab, selectedCompanyId, selectedSector } = parseLocation(location.pathname);

  const { data: index } = useBDCIndex();
  const { data: exposures } = useCompanyExposures();

  const [showMobileSidebar, setShowMobileSidebar] = useState(false);

  // Navigation helpers — all changes go through the URL
  const setViewMode = useCallback((mode: ViewMode) => {
    navigate(`/${mode}`);
  }, [navigate]);

  const handleTickerSelect = useCallback((t: string) => {
    navigate(`/bdc/${t}`);
  }, [navigate]);

  const setActiveTab = useCallback((tab: string) => {
    if (!ticker) return;
    navigate(`/bdc/${ticker}/${tab}`);
  }, [navigate, ticker]);

  const setSelectedCompanyId = useCallback((id: string) => {
    navigate(`/companies/${encodeURIComponent(id)}`);
  }, [navigate]);

  const setSelectedSector = useCallback((s: string) => {
    navigate(`/sectors/${encodeURIComponent(s)}`);
  }, [navigate]);

  const isHome = location.pathname === '/';

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
      // Pull a deeper window so Financials can still show enough columns when
      // some investment periods have no extracted statement rows.
      return [...periods].reverse().slice(0, Math.min(12, periods.length));
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

  const handleCompanyClick = useCallback((companyId: string) => {
    navigate(`/companies/${encodeURIComponent(companyId)}`);
  }, [navigate]);

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
    viewMode === 'peer-marks' ? (
      <div className="window p-1 sm:p-1.5 flex flex-col h-full min-h-0 overflow-hidden">
        <PeerMarksPage />
      </div>
    ) : viewMode === 'bdc' ? (
      <div className="window p-1 sm:p-1.5 flex flex-col h-full min-h-0 overflow-hidden">
        <Tabs tabs={tabs} initialId={activeTab} onChange={(id) => setActiveTab(id)} />
      </div>
    ) : viewMode === 'companies' ? (
      <div className="window p-1 sm:p-1.5 flex flex-col h-full min-h-0 overflow-hidden">
        <CompanyPage
          companyId={selectedCompanyId}
          onSelectBDC={(t) => navigate(`/bdc/${t}`)}
          onSwitchToBDC={() => navigate('/bdc')}
        />
      </div>
    ) : (
      <div className="window p-1 sm:p-1.5 flex flex-col h-full min-h-0 overflow-hidden">
        <SectorPage
          sector={selectedSector}
          onSelectCompany={(id) => navigate(`/companies/${encodeURIComponent(id)}`)}
          onSwitchToCompanies={() => navigate('/companies')}
        />
      </div>
    );

  return (
    <div className="h-full flex flex-col">
      <AppHeader viewMode={viewMode} onViewModeChange={setViewMode} isHome={isHome} />
      {isHome ? (
        <div className="flex-1 min-h-0 overflow-auto">
          <IntroPage />
        </div>
      ) : (
        <>
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
        </>
      )}
    </div>
  );
}

export default App;
