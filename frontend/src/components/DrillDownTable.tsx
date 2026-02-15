import { useMemo, useState } from 'react';
import { createColumnHelper, flexRender, getCoreRowModel, getSortedRowModel, useReactTable } from '@tanstack/react-table';
import type { SortingState } from '@tanstack/react-table';
import type { Holding } from '../data/adapter';
import { toPercent, getFV, getCost, getPrincipal, getMaturityDateStr } from '../utils/holdingsAnalytics';
import { formatThousandsAsCurrency } from '../utils/formatCurrency';

type Props = {
  holdings: Holding[];
  title: string;
  onClose: () => void;
};

/** Format amount stored in thousands as currency ($M / $B) for consistency with rest of app */
function fmtCurrency(thousands: number): string {
  if (thousands === 0) return '—';
  return formatThousandsAsCurrency(thousands);
}

function fmtRatio(a: number, b: number): string {
  if (b === 0) return '';
  return (a / b).toFixed(3);
}

type DrillDownRow = {
  company_name: string;
  investment_type: string;
  industry: string;
  spread: number;
  fair_value: number;
  principal: number;
  cost: number;
  fvPrin: number;
  fvCost: number;
  refRate: string;
  maturity: string;
};

const columnHelper = createColumnHelper<DrillDownRow>();

const columns = [
  columnHelper.accessor('company_name', {
    header: 'Company',
    cell: c => <span className="truncate max-w-[200px] block" title={c.getValue()}>{c.getValue()}</span>,
  }),
  columnHelper.accessor('investment_type', {
    header: 'Type',
    cell: c => c.getValue(),
  }),
  columnHelper.accessor('industry', {
    header: 'Industry',
    cell: c => <span className="truncate max-w-[120px] block" title={c.getValue()}>{c.getValue()}</span>,
  }),
  columnHelper.accessor('spread', {
    header: 'Spread %',
    cell: c => <span className="block text-right">{c.getValue() > 0 ? c.getValue().toFixed(2) : ''}</span>,
  }),
  columnHelper.accessor('fair_value', {
    header: 'Fair value',
    cell: c => <span className="block text-right">{fmtCurrency(c.getValue())}</span>,
  }),
  columnHelper.accessor('principal', {
    header: 'Principal',
    cell: c => <span className="block text-right">{fmtCurrency(c.getValue())}</span>,
  }),
  columnHelper.accessor('cost', {
    header: 'Cost',
    cell: c => <span className="block text-right">{fmtCurrency(c.getValue())}</span>,
  }),
  columnHelper.accessor('fvPrin', {
    header: 'FV/Prin',
    cell: c => <span className="block text-right">{c.getValue() > 0 ? c.getValue().toFixed(3) : ''}</span>,
  }),
  columnHelper.accessor('fvCost', {
    header: 'FV/Cost',
    cell: c => <span className="block text-right">{c.getValue() > 0 ? c.getValue().toFixed(3) : ''}</span>,
  }),
  columnHelper.accessor('refRate', {
    header: 'Ref Rate',
    cell: c => <span className="text-xs">{c.getValue()}</span>,
  }),
  columnHelper.accessor('maturity', {
    header: 'Maturity',
    cell: c => c.getValue(),
  }),
];

export function DrillDownTable({ holdings, title, onClose }: Props) {
  const [sorting, setSorting] = useState<SortingState>([]);

  const rows = useMemo<DrillDownRow[]>(() => {
    return holdings.map(h => {
      const fv = getFV(h);
      const principal = getPrincipal(h);
      const cost = getCost(h);
      return {
        company_name: h.company_name || '',
        investment_type: h.investment_type || '',
        industry: h.industry || '',
        spread: toPercent(h.spread),
        fair_value: fv,
        principal,
        cost,
        fvPrin: principal > 0 ? fv / principal : 0,
        fvCost: cost > 0 ? fv / cost : 0,
        refRate: h.reference_rate || '',
        maturity: getMaturityDateStr(h) || '',
      };
    });
  }, [holdings]);

  // Summary totals
  const summary = useMemo(() => {
    const totalFV = rows.reduce((s, r) => s + r.fair_value, 0);
    const totalPrin = rows.reduce((s, r) => s + r.principal, 0);
    const totalCost = rows.reduce((s, r) => s + r.cost, 0);
    const spreadsWithValue = rows.filter(r => r.spread > 0);
    const avgSpread = spreadsWithValue.length > 0
      ? spreadsWithValue.reduce((s, r) => s + r.spread, 0) / spreadsWithValue.length
      : 0;
    return { totalFV, totalPrin, totalCost, avgSpread };
  }, [rows]);

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="window">
      <div className="titlebar">
        <div className="text-xs tracking-wide">{title} ({holdings.length} holdings)</div>
        <button
          className="px-1.5 text-white hover:bg-[#000080] text-xs font-bold"
          onClick={onClose}
          title="Close"
        >
          [X]
        </button>
      </div>
      <div className="max-h-96 overflow-y-auto overflow-x-auto">
        <table className="w-full text-xs border-separate border-spacing-0">
          <thead className="sticky top-0 z-10 bg-[#c0c0c0]">
            {table.getHeaderGroups().map(hg => (
              <tr key={hg.id}>
                {hg.headers.map(h => (
                  <th
                    key={h.id}
                    className="whitespace-nowrap cursor-pointer select-none text-black px-2 py-1 text-left border-b border-[#808080]"
                    onClick={h.column.getToggleSortingHandler()}
                  >
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
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="text-black">
            {table.getRowModel().rows.map(row => (
              <tr key={row.id} className="hover:bg-[#c0c0c0] border-b border-[#c0c0c0]">
                {row.getVisibleCells().map(cell => (
                  <td key={cell.id} className="px-2 py-1 text-xs">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          <tfoot className="bg-[#c0c0c0] border-t-2 border-[#808080]">
            <tr className="text-xs font-semibold text-black">
              <td className="px-2 py-1">Totals</td>
              <td className="px-2 py-1" />
              <td className="px-2 py-1" />
              <td className="px-2 py-1 text-right">{summary.avgSpread > 0 ? `avg ${summary.avgSpread.toFixed(2)}` : ''}</td>
              <td className="px-2 py-1 text-right">{fmtCurrency(summary.totalFV)}</td>
              <td className="px-2 py-1 text-right">{fmtCurrency(summary.totalPrin)}</td>
              <td className="px-2 py-1 text-right">{fmtCurrency(summary.totalCost)}</td>
              <td className="px-2 py-1 text-right">{summary.totalPrin > 0 ? fmtRatio(summary.totalFV, summary.totalPrin) : ''}</td>
              <td className="px-2 py-1 text-right">{summary.totalCost > 0 ? fmtRatio(summary.totalFV, summary.totalCost) : ''}</td>
              <td className="px-2 py-1" />
              <td className="px-2 py-1" />
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}
