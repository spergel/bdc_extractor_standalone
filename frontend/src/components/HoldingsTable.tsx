import { useEffect, useMemo, useState, useRef, memo, useTransition, useCallback, useDeferredValue } from 'react';
import { createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel, useReactTable } from '@tanstack/react-table';
import type { SortingState } from '@tanstack/react-table';
import type { Holding, CompanyProfile } from '../data/adapter';
import { loadCompanyProfiles } from '../data/adapter';
import { checkRedFlags, getMaturityDateStr } from '../utils/holdingsAnalytics';
import { ExportBar } from './ExportBar';
import { calculateCurrentYield } from '../utils/referenceRates';
import { HoldingsFilterBar, filterHoldings, defaultFilterState, filterHoldingsAdvanced, defaultAdvancedFilterState, hasActiveAdvancedFilters, type FilterState, type AdvancedFilterState } from './HoldingsFilterBar';
import { AdvancedSearchDialog } from './AdvancedSearchDialog';
import { useDebounce } from '../hooks/useDebounce';

type Props = {
  data: Holding[];
  period?: string;
  /** When set, company name is clickable and switches to Companies view with this company. */
  onCompanyClick?: (companyId: string) => void;
  /** Resolve company name to company_id when row has no company_id (e.g. CSV without resolution). */
  getCompanyIdFromName?: (name: string) => string | undefined;
};

const columnHelper = createColumnHelper<Holding>();

function fmtNumber(v: unknown): string {
  if (v === null || v === undefined || v === '') return '';
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

// Sorting helpers
function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const num = Number(value);
  return Number.isNaN(num) ? null : num;
}

function toPercent(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'string') {
    const cleaned = value.replace(/%/g, '').trim();
    const num = Number(cleaned);
    return Number.isNaN(num) ? null : num;
  }
  const num = Number(value);
  return Number.isNaN(num) ? null : num;
}

function toDateEpoch(value: unknown): number | null {
  if (!value || typeof value !== 'string') return null;
  const t = Date.parse(value);
  return Number.isNaN(t) ? null : t;
}

function compareNullable(a: number | null, b: number | null): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1; // nulls last
  if (b === null) return -1;
  return a - b;
}

function decodeHtmlEntities(text: unknown): string {
  if (text === null || text === undefined || text === '') return '';
  const str = String(text);
  // Decode common HTML entities
  return str
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'");
}

// Memoize the component to prevent unnecessary re-renders
function HoldingsTableComponent({ data, period, onCompanyClick, getCompanyIdFromName }: Props) {
  const dataRef = useRef<number>(0);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const [isPending, startTransition] = useTransition();
  const [currentPage, setCurrentPage] = useState<number>(0);
  const [pageSize, setPageSize] = useState<number>(100);
  const [compactMode, setCompactMode] = useState<boolean>(false);
  const [hoveredRowId, setHoveredRowId] = useState<string | null>(null);
  const [holdingsFilters, setHoldingsFilters] = useState<FilterState>(defaultFilterState);
  const [advancedFilters, setAdvancedFilters] = useState<AdvancedFilterState>(defaultAdvancedFilterState);
  const [showAdvancedSearch, setShowAdvancedSearch] = useState(false);
  const [companyProfiles, setCompanyProfiles] = useState<Record<string, CompanyProfile>>({});
  useEffect(() => {
    loadCompanyProfiles().then(setCompanyProfiles);
  }, []);

  // Debounced search
  const { displayValue: searchDisplay, debouncedValue: searchQuery, setDisplayValue: setSearchDisplay, flushNow: flushSearch, reset: resetSearch } = useDebounce('', 300);

  // Deferred values for non-blocking filtering
  const deferredSearchQuery = useDeferredValue(searchQuery);
  const deferredFilters = useDeferredValue(holdingsFilters);
  const deferredAdvFilters = useDeferredValue(advancedFilters);
  const isFilterStale = deferredSearchQuery !== searchQuery || deferredFilters !== holdingsFilters || deferredAdvFilters !== advancedFilters;

  // Default sort by company name ascending
  const [sorting, setSorting] = useState<SortingState>([{ id: 'company_name', desc: false }]);

  // Wrapper to make sorting updates non-blocking
  const handleSortingChange = useCallback((updater: any) => {
    startTransition(() => {
      if (typeof updater === 'function') {
        setSorting(updater);
      } else {
        setSorting(updater);
      }
    });
  }, [startTransition]);

  // Reset to default sort and page when data actually changes (new ticker/period)
  useEffect(() => {
    if (dataRef.current !== data.length) {
      dataRef.current = data.length;
      setSorting([{ id: 'company_name', desc: false }]);
      setCurrentPage(0);
      setHoldingsFilters(defaultFilterState);
      setAdvancedFilters(defaultAdvancedFilterState);
      resetSearch('');
    }
  }, [data.length, resetSearch]);

  // Reset pagination when search/filters change
  useEffect(() => {
    setCurrentPage(0);
  }, [deferredSearchQuery, deferredFilters, deferredAdvFilters]);

  // Ctrl+F / Cmd+F keyboard shortcut to focus search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
        e.preventDefault();
        searchInputRef.current?.focus();
        searchInputRef.current?.select();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  // Check if any search or filter is active
  const hasAnyFilter = searchQuery !== '' ||
    holdingsFilters.industry !== 'all' || holdingsFilters.type !== 'all' ||
    hasActiveAdvancedFilters(advancedFilters);

  const clearAll = useCallback(() => {
    resetSearch('');
    setHoldingsFilters(defaultFilterState);
    setAdvancedFilters(defaultAdvancedFilterState);
  }, [resetSearch]);

  // Precompute sort keys once per dataset - decode HTML entities once, compute sort keys
  const processedData = useMemo(() => {
    const processed = data.map((d: any) => {
      // Use cleaned fields if available, fallback to legacy fields
      const companyName = decodeHtmlEntities(d?.company_name_clean ?? d?.company_name ?? '');
      const investmentType = decodeHtmlEntities(d?.investment_type_standardized ?? d?.investment_type ?? '');
      const industry = decodeHtmlEntities(d?.industry_clean ?? d?.industry ?? '');

      return {
        ...d,
        // Use cleaned fields for display
        company_name: companyName,
        investment_type: investmentType,
        industry: industry,
        // Pre-computed sort keys (reuse decoded values)
        _s_company: companyName.toLowerCase(),
        _s_type: investmentType.toLowerCase(),
        _s_industry: industry.toLowerCase(),
        _n_principal: toNumber(d?.principal_amount_thousands ?? d?.principal_amount),
        _n_cost: toNumber(d?.cost_thousands ?? d?.amortized_cost_thousands ?? d?.cost ?? d?.amortized_cost),
        _n_fair: toNumber(d?.fair_value_thousands ?? d?.fair_value),
        _p_rate: d?.total_interest_rate ? d.total_interest_rate * 100 : toPercent(d?.interest_rate),
        _p_spread: d?.spread_clean ? d.spread_clean * 100 : toPercent(d?.spread),
        _t_acq: toDateEpoch(d?.acquisition_date),
        _t_mat: toDateEpoch(getMaturityDateStr(d)),
      };
    });
    return processed;
  }, [data]);

  // Filter data based on deferred search query and deferred filter bar
  const filteredData = useMemo(() => {
    let result = processedData;

    // Text search
    if (deferredSearchQuery.trim()) {
      const query = deferredSearchQuery.toLowerCase().trim();
      result = result.filter((d: any) => {
        return (
          d._s_company.includes(query) ||
          d._s_type.includes(query) ||
          d._s_industry.includes(query) ||
          (d?.reference_rate && String(d.reference_rate).toLowerCase().includes(query)) ||
          (getMaturityDateStr(d) && String(getMaturityDateStr(d)).toLowerCase().includes(query))
        );
      });
    }

    // Apply filter bar filters
    result = filterHoldings(result, deferredFilters);

    // Apply advanced filters
    result = filterHoldingsAdvanced(result, deferredAdvFilters);

    return result;
  }, [processedData, deferredSearchQuery, deferredFilters, deferredAdvFilters]);

  const columns = useMemo(
    () => [
      columnHelper.accessor('company_name', {
        header: 'Company',
        cell: (c) => {
          const name = c.getValue() ?? '';
          const row = c.row.original as any;
          const companyId = (row?.company_id as string | undefined) ?? (typeof name === 'string' ? getCompanyIdFromName?.(name) : undefined);
          const profile = companyId ? companyProfiles[companyId] : undefined;
          const parts = [
            profile?.description?.trim(),
            profile?.leadership?.trim() && `Leadership: ${profile.leadership}`,
            profile?.funding?.trim() && `Funding: ${profile.funding}`,
            profile?.recent_news?.trim() && `Recent: ${profile.recent_news}`,
          ].filter(Boolean) as string[];
          const tooltip = parts.length > 0 ? parts.join('\n') : undefined;
          if (onCompanyClick && companyId) {
            return (
              <button
                type="button"
                title={tooltip ? `${tooltip}\n\nClick to view company` : 'View company'}
                className="text-left text-[#0000ff] hover:underline cursor-pointer w-full truncate"
                onClick={() => onCompanyClick(companyId)}
              >
                {name}
              </button>
            );
          }
          return (
            <span title={tooltip || undefined} className={tooltip ? 'cursor-help' : ''}>
              {name}
            </span>
          );
        },
        enableSorting: true,
        sortingFn: (rowA, rowB) => {
          const a = (rowA.original as any)._s_company;
          const b = (rowB.original as any)._s_company;
          return a < b ? -1 : a > b ? 1 : 0;
        },
      }),
      columnHelper.accessor('investment_type', {
        header: 'Type',
        cell: (c) => c.getValue() ?? '',
        enableSorting: true,
        sortingFn: (rowA, rowB) => {
          const a = (rowA.original as any)._s_type;
          const b = (rowB.original as any)._s_type;
          return a < b ? -1 : a > b ? 1 : 0;
        },
      }),
      columnHelper.accessor('industry', {
        header: 'Industry',
        cell: (c) => c.getValue() ?? '',
        enableSorting: true,
        sortingFn: (rowA, rowB) => {
          const a = (rowA.original as any)._s_industry;
          const b = (rowB.original as any)._s_industry;
          return a < b ? -1 : a > b ? 1 : 0;
        },
      }),
      columnHelper.display({
        id: 'principal',
        header: 'Principal ($K)',
        cell: (c) => {
          const row = c.row.original as any;
          const val = row.principal_amount_thousands ?? row.principal_amount;
          return <span className="block text-right">{fmtNumber(val)}</span>;
        },
        enableSorting: true,
        sortingFn: (rowA, rowB) => compareNullable((rowA.original as any)._n_principal, (rowB.original as any)._n_principal),
      }),
      columnHelper.display({
        id: 'cost',
        header: 'Cost ($K)',
        cell: (c) => {
          const row = c.row.original as any;
          const val = row.cost_thousands ?? row.amortized_cost_thousands ?? row.cost ?? row.amortized_cost;
          return <span className="block text-right">{fmtNumber(val)}</span>;
        },
        enableSorting: true,
        sortingFn: (rowA, rowB) => compareNullable((rowA.original as any)._n_cost, (rowB.original as any)._n_cost),
      }),
      columnHelper.display({
        id: 'fair_value',
        header: 'Fair Value ($K)',
        cell: (c) => {
          const row = c.row.original as any;
          const val = row.fair_value_thousands ?? row.fair_value;
          return <span className="block text-right">{fmtNumber(val)}</span>;
        },
        enableSorting: true,
        sortingFn: (rowA, rowB) => compareNullable((rowA.original as any)._n_fair, (rowB.original as any)._n_fair),
      }),
      columnHelper.display({
        id: 'yield',
        header: 'Yield %',
        cell: (c) => {
          const row = c.row.original as any;

          // Try to calculate current yield from spread + ref rate
          const currentYield = calculateCurrentYield(row.spread ?? row.spread_clean, row.reference_rate);

          // Use calculated yield if available, otherwise use stated rate
          if (currentYield !== null) {
            return (
              <span className="block text-right text-[#ff0000] font-semibold" title={`Calculated: ${row.reference_rate} + ${row.spread ?? row.spread_clean}`}>
                {currentYield.toFixed(2)}
              </span>
            );
          }

          // Fallback to stated rate
          const rate = row.total_interest_rate ? (row.total_interest_rate * 100).toFixed(2) :
                       row.interest_rate ?? '';
          return <span className="block text-right">{rate}</span>;
        },
        enableSorting: true,
        sortingFn: (rowA, rowB) => compareNullable((rowA.original as any)._p_rate, (rowB.original as any)._p_rate),
      }),
      columnHelper.display({
        id: 'spread',
        header: 'Spread %',
        cell: (c) => {
          const row = c.row.original as any;
          const spread = row.spread_clean ? (row.spread_clean * 100).toFixed(2) :
                        row.spread ?? '';
          return <span className="block text-right">{spread}</span>;
        },
        enableSorting: true,
        sortingFn: (rowA, rowB) => compareNullable((rowA.original as any)._p_spread, (rowB.original as any)._p_spread),
      }),
      columnHelper.accessor('reference_rate', {
        header: 'Ref Rate',
        cell: (c) => {
          const val = c.getValue();
          return <span className="text-xs">{val ?? ''}</span>;
        },
        enableSorting: true,
      }),
      columnHelper.display({
        id: 'asset_class',
        header: 'Asset Class',
        cell: (c) => {
          const row = c.row.original as any;
          const assetClass = row.asset_class ?? '';
          return <span className="text-xs">{assetClass}</span>;
        },
        enableSorting: true,
      }),
      columnHelper.accessor('acquisition_date', {
        header: 'Acq',
        cell: (c) => c.getValue() ?? '',
        enableSorting: true,
        sortingFn: (rowA, rowB) => compareNullable((rowA.original as any)._t_acq, (rowB.original as any)._t_acq),
      }),
      columnHelper.accessor((row) => getMaturityDateStr(row) ?? '', {
        id: 'maturity_date',
        header: 'Mat',
        cell: (c) => c.getValue() || '',
        enableSorting: true,
        sortingFn: (rowA, rowB) => compareNullable((rowA.original as any)._t_mat, (rowB.original as any)._t_mat),
      }),
      columnHelper.display({
        id: 'red_flags',
        header: 'Flags',
        cell: (c) => {
          const holding = c.row.original as Holding;
          const flags = checkRedFlags(holding, period || '');
          if (flags.length === 0) return null;
          return (
            <div className="flex flex-wrap gap-1">
              {flags.slice(0, 2).map((flag, i) => (
                <span
                  key={i}
                  className={`badge text-[10px] ${
                    flag.severity === 'high' ? 'badge-danger' :
                    flag.severity === 'medium' ? 'badge-warn' : 'badge-ok'
                  }`}
                  title={flag.message}
                >
                  {flag.type === 'fv_equals_principal' ? 'FV=Prin' :
                   flag.type === 'fv_below_principal' ? 'FV<Prin' :
                   flag.type === 'fv_below_cost' ? 'FV<Cost' :
                   flag.type === 'has_pik' ? 'PIK' :
                   flag.type === 'near_maturity' ? 'Mat' : flag.type}
                </span>
              ))}
              {flags.length > 2 && (
                <span className="badge badge-ok text-[10px]" title={flags.slice(2).map(f => f.message).join('; ')}>
                  +{flags.length - 2}
                </span>
              )}
            </div>
          );
        },
        enableSorting: false,
      }),
    ],
    [period, companyProfiles, onCompanyClick, getCompanyIdFromName]
  );

  const table = useReactTable({
    data: filteredData as any,
    columns,
    state: { sorting },
    onSortingChange: handleSortingChange,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    manualSorting: false,
    enableSortingRemoval: true,
  });

  const allRows = table.getRowModel().rows;

  // Paginate sorted rows
  const paginatedRows = useMemo(() => {
    const start = currentPage * pageSize;
    const end = start + pageSize;
    return allRows.slice(start, end);
  }, [allRows, currentPage, pageSize]);

  const totalPages = Math.ceil(filteredData.length / pageSize);

  // Get filtered/sorted holdings for export
  const exportData = useMemo(() => {
    return allRows.map(row => row.original);
  }, [allRows]);

  // Generate filename with period if available
  const exportFilename = useMemo(() => {
    const base = 'holdings';
    if (period) {
      const periodStr = period.replace(/[^0-9]/g, '_');
      return `${base}_${periodStr}`;
    }
    return `${base}_${new Date().toISOString().split('T')[0]}`;
  }, [period]);

  // Search input keyboard handlers
  const handleSearchKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      flushSearch();
    } else if (e.key === 'Escape') {
      resetSearch('');
      searchInputRef.current?.blur();
    }
  }, [flushSearch, resetSearch]);

  return (
    <div className="window flex flex-col h-full min-h-0 overflow-hidden">
      <div className="titlebar flex-shrink-0">
        <div className="text-xs sm:text-sm tracking-wide">Holdings</div>
        <div className="flex items-center gap-2 sm:gap-3 text-xs text-white flex-wrap" aria-live="polite">
          <span className="whitespace-nowrap">Rows: {filteredData.length}{hasAnyFilter ? ` (${data.length})` : ''}</span>
          {(isPending || isFilterStale) ? (
            <span className="inline-flex items-center gap-1 text-[#808080]">
              <span className="h-3 w-3 border-2 border-[#000080] border-t-transparent animate-spin" style={{ borderRadius: 0 }} />
              {isPending ? 'Sorting' : 'Filtering'}
            </span>
          ) : null}
        </div>
      </div>
      <div className="p-1 border-b border-[#c0c0c0] flex-shrink-0 flex gap-1 items-center flex-wrap">
        <div className="relative flex-1 min-w-[200px]">
          <input
            ref={searchInputRef}
            type="text"
            className="input w-full text-xs py-0.5 pr-6"
            placeholder="Search by company name, type, industry... (Ctrl+F)"
            value={searchDisplay}
            onChange={(e) => setSearchDisplay(e.target.value)}
            onKeyDown={handleSearchKeyDown}
          />
          {searchDisplay && (
            <button
              className="absolute right-1 top-1/2 -translate-y-1/2 px-1 text-xs text-[#808080] hover:text-black"
              onClick={() => resetSearch('')}
              title="Clear search (Escape)"
              type="button"
            >
              X
            </button>
          )}
        </div>
        <button
          onClick={flushSearch}
          className="px-1.5 py-0.5 text-xs window"
          title="Search now (Enter)"
        >
          Find
        </button>
        {hasAnyFilter && (
          <button
            onClick={clearAll}
            className="px-1.5 py-0.5 text-xs window text-[#ff0000]"
            title="Clear search and all filters"
          >
            Reset
          </button>
        )}
        <button
          onClick={() => setCompactMode(!compactMode)}
          className="px-1.5 py-0.5 text-xs window flex items-center gap-1"
          title={compactMode ? "Normal view" : "Compact view (smaller text)"}
        >
          {compactMode ? '🔍+' : '🔍−'}
        </button>
        <button
          onClick={() => setShowAdvancedSearch(true)}
          className={`px-1.5 py-0.5 text-xs window ${hasActiveAdvancedFilters(advancedFilters) ? 'font-bold text-[#000080]' : ''}`}
          title="Advanced search filters"
        >
          {hasActiveAdvancedFilters(advancedFilters) ? 'Advanced*' : 'Advanced...'}
        </button>
      </div>
      <div className="p-1 border-b border-[#c0c0c0] flex-shrink-0">
        <HoldingsFilterBar holdings={data} filters={holdingsFilters} onChange={setHoldingsFilters} />
      </div>
      {hasActiveAdvancedFilters(advancedFilters) && (
        <div className="px-2 py-0.5 border-b border-[#c0c0c0] flex-shrink-0 flex items-center gap-2 bg-[#ffffcc] text-xs">
          <span className="text-[#000080] font-bold">Advanced filters active</span>
          <button
            className="text-[#ff0000] underline"
            onClick={() => setAdvancedFilters(defaultAdvancedFilterState)}
            type="button"
          >
            clear
          </button>
        </div>
      )}
      <div className="overflow-auto flex-1 min-h-0 relative">
        {filteredData.length === 0 && hasAnyFilter ? (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
            <div className="text-sm text-[#808080] mb-2">No holdings match your criteria</div>
            {searchQuery && (
              <div className="text-xs text-[#808080] mb-1">
                Search: "<span className="text-black font-medium">{searchQuery}</span>"
              </div>
            )}
            {(holdingsFilters.industry !== 'all' || holdingsFilters.type !== 'all' ||
              hasActiveAdvancedFilters(advancedFilters)) && (
              <div className="text-xs text-[#808080] mb-3">
                Active filters are narrowing results
              </div>
            )}
            <button
              onClick={clearAll}
              className="px-3 py-1 text-xs window"
            >
              Clear All Filters
            </button>
          </div>
        ) : (
          <>
            <table className={`w-full table-excel ${compactMode ? 'text-[9px] sm:text-[10px]' : 'text-xs sm:text-sm'}`}>
              <thead className="sticky top-0 z-10">
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id}>
                    <th className={`text-[#808080] text-right border border-[#c0c0c0] bg-[#c0c0c0] sticky left-0 z-10 ${compactMode ? 'px-0.5 py-0.5 text-[8px]' : 'px-1 sm:px-2 py-1 sm:py-2 text-[9px] sm:text-[10px]'}`}>
                      #
                    </th>
                        {hg.headers.map((h, colIdx) => {
                          const colLetter = String.fromCharCode(65 + colIdx + 1); // B, C, D, etc. (A is row number)
                          return (
                          <th
                            key={h.id}
                        className={`whitespace-nowrap cursor-pointer select-none text-black relative border border-[#c0c0c0] ${h.column.id === 'principal_amount' || h.column.id === 'amortized_cost' || h.column.id === 'fair_value' || h.column.id === 'interest_rate' || h.column.id === 'spread' ? 'text-right' : 'text-left'} ${compactMode ? 'px-1 py-0.5 text-[8px]' : 'px-2 sm:px-3 py-1 sm:py-2 text-[10px] sm:text-xs'}`}
                            onClick={(e) => {
                              const handler = h.column.getToggleSortingHandler();
                              if (handler) {
                                handler(e);
                              }
                            }}
                            aria-sort={h.column.getIsSorted() ? (h.column.getIsSorted() === 'desc' ? 'descending' : 'ascending') : 'none'}
                          >
                            {h.isPlaceholder ? null : (
                              <div className="flex items-center gap-1">
                                {flexRender(h.column.columnDef.header, h.getContext())}
                                {h.column.getIsSorted() === 'asc' ? (
                                  <svg viewBox="0 0 20 20" className="h-3 w-3 text-black" aria-hidden="true">
                                    <path d="M10 6l-4 6h8l-4-6z" fill="currentColor" />
                                  </svg>
                                ) : h.column.getIsSorted() === 'desc' ? (
                                  <svg viewBox="0 0 20 20" className="h-3 w-3 text-black" aria-hidden="true">
                                    <path d="M10 14l4-6H6l4 6z" fill="currentColor" />
                                  </svg>
                                ) : null}
                              </div>
                            )}
                            <span className="cell-ref">{colLetter}1</span>
                          </th>
                        );
                        })}
                  </tr>
                ))}
              </thead>
              <tbody className="text-black">
                {paginatedRows.map((row, rowIdx) => {
                  const rowNum = currentPage * pageSize + rowIdx + 2; // global row index (header is row 1)
                  const isHovered = hoveredRowId === row.id;
                  return (
                  <tr
                    key={row.id}
                    className={isHovered ? 'bg-[#ffffcc]' : ''}
                    onMouseEnter={() => setHoveredRowId(row.id)}
                    onMouseLeave={() => setHoveredRowId(prev => (prev === row.id ? null : prev))}
                  >
                    <td className={`text-[#808080] text-right border border-[#c0c0c0] sticky left-0 z-10 ${compactMode ? 'px-0.5 py-0.5 text-[8px]' : 'px-1 sm:px-2 py-1 text-[9px] sm:text-[10px]'}`}>
                      {rowNum}
                    </td>
                    {row.getVisibleCells().map((cell) => {
                      return (
                      <td key={cell.id} className={`align-top relative border border-[#c0c0c0] ${compactMode ? 'px-1 py-0.5 text-[8px]' : 'px-2 sm:px-3 py-1 sm:py-2 text-[10px] sm:text-xs'}`}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                      );
                    })}
                  </tr>
                  );
                })}
              </tbody>
            </table>
            {isPending ? (
              <div className="absolute inset-0 bg-[#c0c0c0]/80 flex items-center justify-center z-20">
                <div className="flex items-center gap-2 text-black text-xs">
                  <span className="h-5 w-5 border-2 border-[#000080] border-t-transparent animate-spin" style={{ borderRadius: 0 }} />
                  <span>Sorting…</span>
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>
      {exportData.length > 0 && (
        <div className="flex-shrink-0">
          <ExportBar data={exportData as Holding[]} filename={exportFilename} />
        </div>
      )}

      {/* Pagination controls */}
      {filteredData.length > pageSize && (
        <div className="mt-1 flex items-center justify-between gap-2 border-t border-[#808080] pt-1">
          <div className="text-xs text-[#000080]">
            Showing {currentPage * pageSize + 1}-{Math.min((currentPage + 1) * pageSize, filteredData.length)} of {filteredData.length} rows
          </div>
          <div className="flex items-center gap-1">
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setCurrentPage(0);
              }}
              className="px-1.5 py-0.5 text-xs border border-[#808080] bg-[#c0c0c0]"
            >
              <option value={50}>50 rows</option>
              <option value={100}>100 rows</option>
              <option value={250}>250 rows</option>
              <option value={500}>500 rows</option>
            </select>
            <button
              onClick={() => setCurrentPage(0)}
              disabled={currentPage === 0}
              className="px-1.5 py-0.5 text-xs window disabled:opacity-40"
            >
              First
            </button>
            <button
              onClick={() => setCurrentPage(p => Math.max(0, p - 1))}
              disabled={currentPage === 0}
              className="px-1.5 py-0.5 text-xs window disabled:opacity-40"
            >
              Prev
            </button>
            <span className="text-xs text-[#000080]">
              Page {currentPage + 1} of {totalPages}
            </span>
            <button
              onClick={() => setCurrentPage(p => Math.min(totalPages - 1, p + 1))}
              disabled={currentPage >= totalPages - 1}
              className="px-1.5 py-0.5 text-xs window disabled:opacity-40"
            >
              Next
            </button>
            <button
              onClick={() => setCurrentPage(totalPages - 1)}
              disabled={currentPage >= totalPages - 1}
              className="px-1.5 py-0.5 text-xs window disabled:opacity-40"
            >
              Last
            </button>
          </div>
        </div>
      )}
      {showAdvancedSearch && (
        <AdvancedSearchDialog
          filters={advancedFilters}
          onApply={setAdvancedFilters}
          onClose={() => setShowAdvancedSearch(false)}
          matchCount={filteredData.length}
          holdings={data}
        />
      )}
    </div>
  );
}

export const HoldingsTable = memo(HoldingsTableComponent);
