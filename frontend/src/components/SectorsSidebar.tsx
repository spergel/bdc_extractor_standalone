import { useMemo } from 'react';
import { useCompanyExposures } from '../api/hooks';
import type { CompanyExposure } from '../data/adapter';
import { formatMillionsAsCurrency } from '../utils/formatCurrency';

type Props = {
  selectedSector: string | undefined;
  onSelectSector: (sector: string) => void;
};

export function SectorsSidebar({ selectedSector, onSelectSector }: Props) {
  const { data: exposures, isLoading, error } = useCompanyExposures();

  const sectors = useMemo(() => {
    const arr = exposures ?? [];
    const bySector = new Map<string, { count: number; exposure: number }>();
    for (const e of arr as CompanyExposure[]) {
      const sector = (e.primary_industry ?? 'Other').trim() || 'Other';
      const cur = bySector.get(sector) ?? { count: 0, exposure: 0 };
      cur.count += 1;
      cur.exposure += Number(e.total_exposure_millions) || 0;
      bySector.set(sector, cur);
    }
    return Array.from(bySector.entries())
      .map(([name, data]) => ({ name, ...data }))
      .sort((a, b) => b.exposure - a.exposure);
  }, [exposures]);

  return (
    <aside className="h-full min-h-0 w-full lg:w-64 xl:w-72 p-2 sm:p-3 flex flex-col">
      <div className="window flex-1 flex flex-col min-h-0 overflow-hidden">
        <div className="titlebar">
          <span className="text-sm font-semibold text-white">Sectors</span>
          {!isLoading && !error && (
            <span className="text-xs text-white/80">{sectors.length}</span>
          )}
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
          {!isLoading && !error && sectors.length === 0 && (
            <div className="px-3 py-4 text-xs text-[#808080] text-center">
              No sectors available
            </div>
          )}
          {sectors.map((s) => {
            const isSelected = s.name === selectedSector;
            const expStr = formatMillionsAsCurrency(s.exposure);
            return (
              <button
                key={s.name}
                type="button"
                className={`w-full text-left px-3 py-2 border-t border-[#808080] hover:bg-[#c0c0c0] ${isSelected ? 'bg-[#000080] text-white' : 'bg-white text-black'}`}
                onClick={() => onSelectSector(s.name)}
              >
                <div className={`font-medium truncate ${isSelected ? 'text-white' : 'text-black'}`}>
                  {s.name}
                </div>
                <div className={`text-xs mt-0.5 ${isSelected ? 'text-white/90' : 'text-[#808080]'}`}>
                  {s.count} companies • {expStr}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </aside>
  );
}
