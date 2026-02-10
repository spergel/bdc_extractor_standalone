import { useEffect, useMemo, useState, useRef, memo, useTransition, useCallback } from 'react';
import { createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel, useReactTable } from '@tanstack/react-table';
import type { SortingState } from '@tanstack/react-table';
import type { Holding } from '../data/adapter';
import { checkRedFlags } from '../utils/holdingsAnalytics';
import { ExportBar } from './ExportBar';
import { calculateCurrentYield } from '../utils/referenceRates';
import { HoldingsFilterBar, filterHoldings, defaultFilterState, type FilterState } from './HoldingsFilterBar';

type Props = {
  data: Holding[];
  period?: string;
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
function HoldingsTableComponent({ data, period }: Props) {
  const dataRef = useRef<number>(0);
  const [isPending, startTransition] = useTransition();
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [currentPage, setCurrentPage] = useState<number>(0);
  const [pageSize, setPageSize] = useState<number>(100);
  const [compactMode, setCompactMode] = useState<boolean>(false);
  const [holdingsFilters, setHoldingsFilters] = useState<FilterState>(defaultFilterState);

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
    }
  }, [data.length]);

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
        _t_mat: toDateEpoch(d?.maturity_date),
      };
    });
    return processed;
  }, [data]);

  // Filter data based on search query and filter bar
  const filteredData = useMemo(() => {
    let result = processedData;

    // Text search
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase().trim();
      result = result.filter((d: any) => {
        return (
          d._s_company.includes(query) ||
          d._s_type.includes(query) ||
          d._s_industry.includes(query) ||
          (d?.reference_rate && String(d.reference_rate).toLowerCase().includes(query)) ||
          (d?.maturity_date && String(d.maturity_date).toLowerCase().includes(query))
        );
      });
    }

    // Apply filter bar filters
    result = filterHoldings(result, holdingsFilters);

    return result;
  }, [processedData, searchQuery, holdingsFilters]);

  const columns = useMemo(
    () => [
      columnHelper.accessor('company_name', {
        header: 'Company',
        cell: (c) => c.getValue() ?? '',
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
      columnHelper.accessor('maturity_date', {
        header: 'Mat',
        cell: (c) => c.getValue() ?? '',
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
    [period]
  );

  // Paginate the filtered data
  const paginatedData = useMemo(() => {
    const start = currentPage * pageSize;
    const end = start + pageSize;
    return filteredData.slice(start, end);
  }, [filteredData, currentPage, pageSize]);

  const totalPages = Math.ceil(filteredData.length / pageSize);

  const table = useReactTable({
    data: paginatedData as any,
    columns,
    state: { sorting },
    onSortingChange: handleSortingChange,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    manualSorting: false,
    enableSortingRemoval: true,
  });

  const sortedRows = table.getRowModel().rows;

  // Get filtered/sorted holdings for export
  const exportData = useMemo(() => {
    return sortedRows.map(row => row.original);
  }, [sortedRows]);

  // Generate filename with period if available
  const exportFilename = useMemo(() => {
    const base = 'holdings';
    if (period) {
      const periodStr = period.replace(/[^0-9]/g, '_');
      return `${base}_${periodStr}`;
    }
    return `${base}_${new Date().toISOString().split('T')[0]}`;
  }, [period]);

  return (
    <div className="window flex flex-col h-full min-h-0 overflow-hidden">
      <div className="titlebar flex-shrink-0">
        <div className="text-xs sm:text-sm tracking-wide">Holdings</div>
        <div className="flex items-center gap-2 sm:gap-3 text-xs text-white flex-wrap" aria-live="polite">
          <span className="whitespace-nowrap">Rows: {filteredData.length}{searchQuery ? ` (${data.length})` : ''}</span>
          {isPending ? (
            <span className="inline-flex items-center gap-1 text-[#808080]">
              <span className="h-3 w-3 border-2 border-[#000080] border-t-transparent animate-spin" style={{ borderRadius: 0 }} />
              Sorting
            </span>
          ) : null}
        </div>
      </div>
      <div className="p-1 border-b border-[#c0c0c0] flex-shrink-0 flex gap-1 items-center flex-wrap">
        <input
          type="text"
          className="input flex-1 min-w-[200px] text-xs py-0.5"
          placeholder="Search by company name, type, industry..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        <button
          onClick={() => setCompactMode(!compactMode)}
          className="px-1.5 py-0.5 text-xs window flex items-center gap-1"
          title={compactMode ? "Normal view" : "Compact view (smaller text)"}
        >
          {compactMode ? '🔍+' : '🔍−'}
        </button>
      </div>
      <div className="p-1 border-b border-[#c0c0c0] flex-shrink-0">
        <HoldingsFilterBar holdings={data} filters={holdingsFilters} onChange={setHoldingsFilters} />
      </div>
      <div className="overflow-auto flex-1 min-h-0 relative">
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
            {sortedRows.map((row, rowIdx) => {
              const rowNum = rowIdx + 2; // Start from row 2 (row 1 is header)
              return (
              <tr key={row.id} className="hover:bg-[#c0c0c0]">
                <td className={`text-[#808080] text-right border border-[#c0c0c0] bg-[#c0c0c0] sticky left-0 z-10 ${compactMode ? 'px-0.5 py-0.5 text-[8px]' : 'px-1 sm:px-2 py-1 text-[9px] sm:text-[10px]'}`}>
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
    </div>
  );
}

export const HoldingsTable = memo(HoldingsTableComponent);


