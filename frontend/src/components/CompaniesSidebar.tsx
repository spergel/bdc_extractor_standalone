import { useMemo, useState } from 'react';
import { useCompanyExposures } from '../api/hooks';
import type { CompanyExposure } from '../data/adapter';
import { formatMillionsAsCurrency } from '../utils/formatCurrency';

type Props = {
  selectedCompanyId: string | undefined;
  onSelectCompany: (companyId: string) => void;
};

export function CompaniesSidebar({ selectedCompanyId, onSelectCompany }: Props) {
  const { data: exposures, isLoading, error } = useCompanyExposures();
  const [searchTerm, setSearchTerm] = useState('');
  const [sortBy, setSortBy] = useState<'name' | 'exposure' | 'bdcs'>('name');

  const list = useMemo(() => {
    const arr = exposures ?? [];
    let filtered = arr;
    if (searchTerm.trim()) {
      const term = searchTerm.toLowerCase().trim();
      filtered = arr.filter(
        (e: CompanyExposure) =>
          (e.company_name ?? '').toLowerCase().includes(term) ||
          (e.company_id ?? '').toLowerCase().includes(term)
      );
    }
    return [...filtered].sort((a: CompanyExposure, b: CompanyExposure) => {
      if (sortBy === 'name') {
        return (a.company_name ?? '').localeCompare(b.company_name ?? '');
      }
      if (sortBy === 'exposure') {
        const va = Number(a.total_exposure_millions) || 0;
        const vb = Number(b.total_exposure_millions) || 0;
        return vb - va;
      }
      const na = Number(a.num_bdcs_invested) || 0;
      const nb = Number(b.num_bdcs_invested) || 0;
      return nb - na;
    });
  }, [exposures, searchTerm, sortBy]);

  return (
    <aside className="h-full min-h-0 w-full lg:w-64 xl:w-72 p-2 sm:p-3 flex flex-col">
      <div className="window flex-1 flex flex-col min-h-0 overflow-hidden">
        <div className="titlebar">
          <span className="text-sm font-semibold text-white">Companies</span>
          {!isLoading && !error && (
            <span className="text-xs text-white/80">
              {list.length} of {exposures?.length ?? 0}
            </span>
          )}
        </div>
        <div className="p-2 space-y-2 border-b border-[#808080]">
          <div className="relative">
            <input
              type="text"
              className="input w-full text-xs pr-6"
              placeholder="Search company..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
            {searchTerm && (
              <button
                type="button"
                className="absolute right-1 top-1/2 -translate-y-1/2 px-1 text-xs text-[#808080] hover:text-black"
                onClick={() => setSearchTerm('')}
                title="Clear"
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
            <option value="name">Sort: Name</option>
            <option value="exposure">Sort: Exposure</option>
            <option value="bdcs">Sort: # BDCs</option>
          </select>
        </div>
        {error && (
          <div className="px-3 py-2 text-xs text-[#ff0000] border-b border-[#808080]">
            Error: {error.message}
          </div>
        )}
        {isLoading && !error && (
          <div className="px-3 py-2 text-xs text-[#808080]">Loading…</div>
        )}
        <div className="flex-1 min-h-0 overflow-y-auto">
          {!isLoading && !error && list.length === 0 && (
            <div className="px-3 py-4 text-xs text-[#808080] text-center">
              {searchTerm ? 'No companies found' : 'No companies available'}
            </div>
          )}
          {list.map((e: CompanyExposure) => {
            const id = e.company_id ?? '';
            const isSelected = id === selectedCompanyId;
            const exp = e.total_exposure_millions != null ? Number(e.total_exposure_millions) : 0;
            const expStr = formatMillionsAsCurrency(exp);
            return (
              <button
                key={id}
                type="button"
                className={`w-full text-left px-3 py-2 border-t border-[#808080] hover:bg-[#c0c0c0] ${isSelected ? 'bg-[#000080] text-white' : 'bg-white text-black'}`}
                onClick={() => onSelectCompany(id)}
              >
                <div className={`font-medium truncate ${isSelected ? 'text-white' : 'text-black'}`}>
                  {e.company_name ?? id}
                </div>
                <div className={`text-xs mt-0.5 ${isSelected ? 'text-white/90' : 'text-[#808080]'}`}>
                  {e.num_bdcs_invested ?? 0} BDCs • {expStr}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </aside>
  );
}
