import { useMemo, useState } from 'react';
import { useBDCIndex } from '../api/hooks';

type Props = {
  onSelect: (ticker: string) => void;
  selectedTicker?: string;
};

export function SidebarDock({ onSelect, selectedTicker }: Props) {
  const { data: index, isLoading, error } = useBDCIndex();
  const [searchTerm, setSearchTerm] = useState('');
  const [sortBy, setSortBy] = useState<'ticker' | 'name' | 'periods'>('ticker');
  const bdcs = index?.bdcs ?? [];

  const filteredAndSorted = useMemo(() => {
    let filtered = bdcs;

    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      filtered = filtered.filter(bdc =>
        bdc.ticker.toLowerCase().includes(term) ||
        bdc.name.toLowerCase().includes(term)
      );
    }

    filtered = [...filtered].sort((a, b) => {
      if (sortBy === 'ticker') return a.ticker.localeCompare(b.ticker);
      if (sortBy === 'name') return a.name.localeCompare(b.name);
      const aPeriods = a.periods?.length ?? 0;
      const bPeriods = b.periods?.length ?? 0;
      return bPeriods - aPeriods;
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

        <div className="p-2 space-y-2 border-b border-[#808080]">
          <div className="relative">
            <input
              type="text"
              className="input w-full text-xs pr-6"
              placeholder="Search ticker or name..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
            {searchTerm && (
              <button
                className="absolute right-1 top-1/2 -translate-y-1/2 px-1 text-xs text-[#808080] hover:text-black"
                onClick={() => setSearchTerm('')}
                title="Clear search"
                type="button"
              >
                X
              </button>
            )}
          </div>
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

        {error && (
          <div className="px-3 py-2 text-xs text-[#ff0000] border-b border-[#808080]">
            Error loading BDCs: {error.message}
          </div>
        )}

        {isLoading && !error && (
          <div className="px-3 py-2 text-xs text-[#808080]">Loading…</div>
        )}

        <div className="flex-1 min-h-0 overflow-y-auto">
          {!isLoading && !error && filteredAndSorted.length === 0 && (
            <div className="px-3 py-4 text-xs text-[#808080] text-center">
              {searchTerm ? 'No BDCs found' : 'No BDCs available'}
            </div>
          )}
          {filteredAndSorted.map((b) => {
            const isSelected = b.ticker === selectedTicker;
            return (
              <button
                key={b.ticker}
                className={`w-full text-left px-3 py-2 border-t border-[#808080] hover:bg-[#c0c0c0] ${
                  isSelected ? 'bg-[#000080] text-white' : 'bg-white text-black'
                }`}
                onClick={() => onSelect(b.ticker)}
              >
                <div className={`font-medium flex items-baseline gap-1 min-w-0 ${isSelected ? 'text-white' : 'text-black'}`}>
                  <span className="flex-shrink-0">{b.ticker} •</span>
                  <span className="truncate min-w-0">{b.name}</span>
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






