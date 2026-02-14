import { useBDCIndex } from '../api/hooks';
import { SidebarDock } from './SidebarDock';
import { CompaniesSidebar } from './CompaniesSidebar';
import { SectorsSidebar } from './SectorsSidebar';
import type { ViewMode } from './AppHeader';

type Props = {
  viewMode: ViewMode;
  ticker?: string;
  selectedCompanyId?: string;
  selectedSector?: string;
  onTickerSelect: (ticker: string) => void;
  onSelectCompany: (companyId: string) => void;
  onSelectSector: (sector: string) => void;
  showSidebar: boolean;
  onToggleSidebar: () => void;
};

export function MobileSelector({
  viewMode,
  ticker,
  selectedCompanyId,
  selectedSector,
  onTickerSelect,
  onSelectCompany,
  onSelectSector,
  showSidebar,
  onToggleSidebar,
}: Props) {
  const { data: index } = useBDCIndex();

  const handleSelect = (value: string) => {
    if (viewMode === 'bdc') {
      onTickerSelect(value);
    }
    onToggleSidebar();
  };

  return (
    <div className="lg:hidden flex-shrink-0">
      <div className="window p-2 mb-2">
        <div className="flex items-center gap-2">
          {viewMode === 'bdc' ? (
            <select
              className="input flex-1 text-xs"
              value={ticker ?? ''}
              onChange={(e) => {
                const value = e.target.value;
                if (value) onTickerSelect(value);
              }}
            >
              {(index?.bdcs ?? []).map((b) => (
                <option key={b.ticker} value={b.ticker}>{`${b.ticker} • ${b.name}`}</option>
              ))}
            </select>
          ) : (
            <span className="flex-1 text-xs text-[#808080] capitalize">{viewMode}</span>
          )}
          <button className="btn text-xs" onClick={onToggleSidebar}>
            {showSidebar ? 'Hide' : 'Browse'}
          </button>
        </div>
      </div>
      {showSidebar && (
        <div className="window mb-2 overflow-auto" style={{ maxHeight: '50vh' }}>
          {viewMode === 'bdc' && (
            <SidebarDock
              onSelect={(t) => handleSelect(t)}
              selectedTicker={ticker}
            />
          )}
          {viewMode === 'companies' && (
            <CompaniesSidebar
              selectedCompanyId={selectedCompanyId}
              onSelectCompany={(id) => {
                onSelectCompany(id);
                onToggleSidebar();
              }}
            />
          )}
          {viewMode === 'sectors' && (
            <SectorsSidebar
              selectedSector={selectedSector}
              onSelectSector={(s) => {
                onSelectSector(s);
                onToggleSidebar();
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}






























