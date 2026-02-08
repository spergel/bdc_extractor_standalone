import { useMemo, useState } from 'react';
import { useBDCIndex } from '../api/hooks';

type Props = {
  onSelect: (ticker: string) => void;
  selectedTicker?: string;
  mode?: 'individual' | 'comparison';
  selectedTickers?: string[];
  onTickerToggle?: (ticker: string) => void;
};

export function SidebarDock({ onSelect, selectedTicker, mode = 'individual', selectedTickers = [], onTickerToggle }: Props) {
  const { data: index, isLoading, error } = useBDCIndex();
  const [searchTerm, setSearchTerm] = useState('');
  const [sortBy, setSortBy] = useState<'ticker' | 'name' | 'periods'>('ticker');
  const bdcs = index?.bdcs ?? [];

  const filteredAndSorted = useMemo(() => {
    let filtered = bdcs;
    
    // Filter by search term
    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      filtered = filtered.filter(bdc => 
        bdc.ticker.toLowerCase().includes(term) ||
        bdc.name.toLowerCase().includes(term)
      );
    }
    
    // Sort
    filtered = [...filtered].sort((a, b) => {
      if (sortBy === 'ticker') {
        return a.ticker.localeCompare(b.ticker);
      } else if (sortBy === 'name') {
        return a.name.localeCompare(b.name);
      } else {
        // Sort by number of periods (descending)
        const aPeriods = a.periods?.length ?? 0;
        const bPeriods = b.periods?.length ?? 0;
        return bPeriods - aPeriods;
      }
    });
    
    return filtered;
  }, [bdcs, searchTerm, sortBy]);

  return (
    <aside className="h-full min-h-0 w-full lg:w-64 xl:w-72 p-2 sm:p-3 flex flex-col">
      <div className="window flex-1 flex flex-col min-h-0 overflow-hidden">
        <div className="titlebar">
          <span className="text-sm font-semibold text-white">BDCs</span>
          {!isLoading && !error && (
            <span className="text-xs text-white/80">
              {filteredAndSorted.length} of {bdcs.length}
            </span>
          )}
        </div>
        
        {/* Search and Filter */}
        <div className="p-2 space-y-2 border-b border-[#808080]">
          <input
            type="text"
            className="input w-full text-xs"
            placeholder="Search ticker or name..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
          <select
            className="input w-full text-xs"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
          >
            <option value="ticker">Sort: Ticker</option>
            <option value="name">Sort: Name</option>
            <option value="periods">Sort: Periods</option>
          </select>
        </div>

        {/* Error State */}
        {error && (
          <div className="px-3 py-2 text-xs text-[#ff0000] border-b border-[#808080]">
            Error loading BDCs: {error.message}
          </div>
        )}

        {/* Loading State */}
        {isLoading && !error && (
          <div className="px-3 py-2 text-xs text-[#808080]">Loading…</div>
        )}

        {/* Companies List */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          {!isLoading && !error && filteredAndSorted.length === 0 && (
            <div className="px-3 py-4 text-xs text-[#808080] text-center">
              {searchTerm ? 'No companies found' : 'No companies available'}
            </div>
          )}
          {filteredAndSorted.map((b) => {
            const isSelected = mode === 'comparison' 
              ? selectedTickers.includes(b.ticker)
              : b.ticker === selectedTicker;
            const active = mode === 'individual' && b.ticker === selectedTicker;
            
            return (
              <button
                key={b.ticker}
                className={`w-full text-left px-3 py-2 border-t border-[#808080] hover:bg-[#c0c0c0] ${
                  isSelected ? 'bg-[#000080] text-white' : 'bg-white text-black'
                }`}
                onClick={() => {
                  if (mode === 'comparison' && onTickerToggle) {
                    onTickerToggle(b.ticker);
                  } else {
                    onSelect(b.ticker);
                  }
                }}
              >
                <div className="flex items-center gap-2 min-w-0">
                  {mode === 'comparison' && (
                    <input
                      type="checkbox"
                      checked={selectedTickers.includes(b.ticker)}
                      onChange={() => onTickerToggle?.(b.ticker)}
                      onClick={(e) => e.stopPropagation()}
                      className="w-3 h-3 border border-[#808080] bg-white text-[#000080] focus:outline-1 focus:outline-dotted focus:outline-black flex-shrink-0"
                      style={{ borderRadius: 0 }}
                    />
                  )}
                  <div className={`font-medium flex items-baseline gap-1 min-w-0 flex-1 ${isSelected ? 'text-white' : 'text-black'}`}>
                    <span className="flex-shrink-0">{b.ticker} •</span>
                    <span className="truncate min-w-0">{b.name}</span>
                  </div>
                </div>
                <div className={`text-xs mt-0.5 ${isSelected ? 'text-white/90' : 'text-[#808080]'}`}>
                  {b.periods?.length ?? 0} periods • {b.latest || b.periods?.[b.periods.length - 1] || 'N/A'}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </aside>
  );
}






