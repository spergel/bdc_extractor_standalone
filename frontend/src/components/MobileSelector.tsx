import { useBDCIndex } from '../api/hooks';
import { SidebarDock } from './SidebarDock';

type Props = {
  ticker?: string;
  mode: 'individual' | 'comparison';
  selectedTickers: string[];
  onTickerSelect: (ticker: string) => void;
  onTickerToggle?: (ticker: string) => void;
  showSidebar: boolean;
  onToggleSidebar: () => void;
};

export function MobileSelector({
  ticker,
  mode,
  selectedTickers,
  onTickerSelect,
  onTickerToggle,
  showSidebar,
  onToggleSidebar,
}: Props) {
  const { data: index } = useBDCIndex();

  return (
    <div className="lg:hidden flex-shrink-0">
      <div className="window p-2 mb-2">
        <div className="flex items-center gap-2">
          <select
            className="input flex-1 text-xs"
            value={ticker ?? ''}
            onChange={(e) => {
              const value = e.target.value;
              if (value) {
                onTickerSelect(value);
              }
            }}
          >
            {(index?.bdcs ?? []).map((b) => (
              <option key={b.ticker} value={b.ticker}>{`${b.ticker} • ${b.name}`}</option>
            ))}
          </select>
          <button
            className="btn text-xs"
            onClick={onToggleSidebar}
          >
            {showSidebar ? 'Hide' : 'Browse'}
          </button>
        </div>
      </div>
      {showSidebar && (
        <div className="window mb-2" style={{ maxHeight: '50vh', overflow: 'hidden' }}>
          <SidebarDock
            onSelect={(t) => {
              onTickerSelect(t);
              onToggleSidebar();
            }}
            selectedTicker={ticker}
            mode={mode}
            selectedTickers={selectedTickers}
            onTickerToggle={onTickerToggle}
          />
        </div>
      )}
    </div>
  );
}






























