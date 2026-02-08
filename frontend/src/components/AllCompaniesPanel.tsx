import { useMemo, useState } from 'react';
import { useBDCIndex } from '../api/hooks';

type Props = {
  onSelectTicker: (ticker: string) => void;
};

export function AllCompaniesPanel({ onSelectTicker }: Props) {
  const { data: index, isLoading } = useBDCIndex();
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

  if (isLoading) {
    return (
      <div className="window p-4">
        <div className="text-xs text-silver/60">Loading companies...</div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Search and Filter Bar */}
      <div className="window p-3">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2 flex-1 min-w-[200px]">
            <label className="text-xs text-silver/70">Search:</label>
            <input
              type="text"
              className="input flex-1"
              placeholder="Search by ticker or name..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-silver/70">Sort by:</label>
            <select
              className="input"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
            >
              <option value="ticker">Ticker</option>
              <option value="name">Name</option>
              <option value="periods">Periods</option>
            </select>
          </div>
        </div>
      </div>

      {/* Companies Grid */}
      <div className="window overflow-auto">
        <div className="titlebar">
          <div className="text-sm tracking-wide">
            All Companies ({filteredAndSorted.length} of {bdcs.length})
          </div>
        </div>
        <div className="overflow-auto">
          <table className="w-full text-sm border-separate border-spacing-0">
            <thead className="sticky top-0 bg-[#1b1f23] z-10">
              <tr className="border-b border-silver/20">
                <th className="text-left py-2 px-3 text-silver/70 font-semibold">Ticker</th>
                <th className="text-left py-2 px-3 text-silver/70 font-semibold">Name</th>
                <th className="text-right py-2 px-3 text-silver/70 font-semibold">Periods</th>
                <th className="text-left py-2 px-3 text-silver/70 font-semibold">Latest Period</th>
                <th className="text-center py-2 px-3 text-silver/70 font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredAndSorted.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-4 text-center text-silver/60">
                    No companies found
                  </td>
                </tr>
              ) : (
                filteredAndSorted.map((bdc) => (
                  <tr
                    key={bdc.ticker}
                    className="border-b border-silver/10 hover:bg-silver/5 cursor-pointer"
                    onClick={() => onSelectTicker(bdc.ticker)}
                  >
                    <td className="py-2 px-3 text-silver/90 font-mono">{bdc.ticker}</td>
                    <td className="py-2 px-3 text-silver/90">{bdc.name}</td>
                    <td className="text-right py-2 px-3 text-silver/70">
                      {bdc.periods?.length ?? 0}
                    </td>
                    <td className="py-2 px-3 text-silver/60 text-xs">
                      {bdc.latest || bdc.periods?.[bdc.periods.length - 1] || 'N/A'}
                    </td>
                    <td className="text-center py-2 px-3">
                      <button
                        className="btn btn-primary text-xs"
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectTicker(bdc.ticker);
                        }}
                      >
                        View
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}





































