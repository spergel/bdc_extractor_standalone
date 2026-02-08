import { useMemo, useState } from 'react';
import type { Holding } from '../data/adapter';
import { calculateDiff, getDiffSummary, type HoldingChange } from '../utils/holdingsDiff';

type Props = {
  beforeHoldings: Holding[];
  afterHoldings: Holding[];
  beforePeriod: string;
  afterPeriod: string;
};

function fmtNumber(v: unknown): string {
  if (v === null || v === undefined || v === '') return '';
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function fmtCurrency(v: unknown): string {
  const n = Number(v);
  if (Number.isNaN(n)) return '';
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  if (abs >= 1000000) return `${sign}$${(abs / 1000000).toFixed(2)}M`;
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(0)}k`;
  return `${sign}$${abs.toFixed(0)}`;
}

function DiffCell({ before, after, isNumeric = false, isPercent = false }: { before: string | number; after: string | number; isNumeric?: boolean; isPercent?: boolean }) {
  const beforeNum = isNumeric ? Number(before) : 0;
  const afterNum = isNumeric ? Number(after) : 0;
  const delta = isNumeric ? afterNum - beforeNum : 0;
  const deltaPercent = isNumeric && beforeNum !== 0 ? (delta / Math.abs(beforeNum)) * 100 : 0;
  
  const isIncrease = delta > 0.01;
  const isDecrease = delta < -0.01;
  const isSame = !isIncrease && !isDecrease;
  
  const beforeStr = isPercent ? `${beforeNum.toFixed(2)}%` : isNumeric ? fmtCurrency(beforeNum) : String(before);
  const afterStr = isPercent ? `${afterNum.toFixed(2)}%` : isNumeric ? fmtCurrency(afterNum) : String(after);
  
  return (
    <td className={`px-2 py-1 text-xs ${isIncrease ? 'bg-[#00ff00]/20 text-[#00ff00]' : isDecrease ? 'bg-[#ff0000]/20 text-[#ff0000]' : 'text-[#808080]'}`}>
      <div className="flex items-center gap-2">
        <span className={isIncrease ? 'text-[#00ff00]' : isDecrease ? 'text-[#ff0000]' : 'text-[#808080]'}>
          {beforeStr}
        </span>
        <span className="text-[#808080]">→</span>
        <span className={isIncrease ? 'text-[#00ff00]' : isDecrease ? 'text-[#ff0000]' : 'text-black'}>
          {afterStr}
        </span>
        {isNumeric && !isSame && (
            <span className={`ml-2 text-[10px] ${isIncrease ? 'text-[#00ff00]' : 'text-[#ff0000]'}`}>
            {delta > 0 ? '+' : ''}{fmtCurrency(delta)}
            {Math.abs(deltaPercent) > 0.1 && ` (${delta > 0 ? '+' : ''}${deltaPercent.toFixed(1)}%)`}
          </span>
        )}
      </div>
    </td>
  );
}

function HoldingRow({ change }: { change: HoldingChange }) {
  const [expanded, setExpanded] = useState(false);
  
  const rowClass = 
    change.type === 'added' ? 'bg-[#00ff00]/10 border-[#00ff00]/30' :
    change.type === 'removed' ? 'bg-[#ff0000]/10 border-[#ff0000]/30' :
    change.type === 'modified' ? 'bg-[#ffff00]/10 border-[#ffff00]/30' :
    'bg-transparent border-[#c0c0c0]';
  
  const indicatorColor =
    change.type === 'added' ? 'text-[#00ff00]' :
    change.type === 'removed' ? 'text-[#ff0000]' :
    change.type === 'modified' ? 'text-[#ffff00]' :
    'text-[#808080]';
  
  const holding = change.after || change.before;
  if (!holding) return null;
  
  return (
    <>
      <tr className={`border-b ${rowClass} cursor-pointer hover:bg-[#c0c0c0]`} onClick={() => setExpanded(!expanded)}>
        <td className="px-2 py-2 text-xs">
          <div className="flex items-center gap-2">
            <span className={indicatorColor}>
              {change.type === 'added' ? '+' : change.type === 'removed' ? '-' : change.type === 'modified' ? '~' : '='}
            </span>
            <span className="text-black">{holding.company_name}</span>
          </div>
        </td>
        <td className="px-2 py-2 text-xs text-[#808080]">{holding.investment_type}</td>
        <td className="px-2 py-2 text-xs text-[#808080]">{holding.industry}</td>
        {change.type === 'added' && change.after ? (
          <>
            <td className="px-2 py-2 text-xs text-[#00ff00]">—</td>
            <td className="px-2 py-2 text-xs text-[#00ff00]">{fmtCurrency(change.after.fair_value)}</td>
            <td className="px-2 py-2 text-xs text-[#00ff00]">{fmtCurrency(change.after.cost || change.after.amortized_cost)}</td>
            <td className="px-2 py-2 text-xs text-[#00ff00]">{fmtCurrency(change.after.principal_amount)}</td>
          </>
        ) : change.type === 'removed' && change.before ? (
          <>
            <td className="px-2 py-2 text-xs text-[#ff0000]">{fmtCurrency(change.before.fair_value)}</td>
            <td className="px-2 py-2 text-xs text-[#ff0000]">—</td>
            <td className="px-2 py-2 text-xs text-[#ff0000]">{fmtCurrency(change.before.cost || change.before.amortized_cost)}</td>
            <td className="px-2 py-2 text-xs text-[#ff0000]">{fmtCurrency(change.before.principal_amount)}</td>
          </>
        ) : change.type === 'modified' && change.before && change.after ? (
          <>
            <DiffCell before={change.before.fair_value || 0} after={change.after.fair_value || 0} isNumeric />
            <DiffCell before={change.before.cost || change.before.amortized_cost || 0} after={change.after.cost || change.after.amortized_cost || 0} isNumeric />
            <DiffCell before={change.before.principal_amount || 0} after={change.after.principal_amount || 0} isNumeric />
          </>
        ) : (
          <>
            <td className="px-2 py-2 text-xs text-black">{fmtCurrency(holding.fair_value)}</td>
            <td className="px-2 py-2 text-xs text-black">{fmtCurrency(holding.cost || holding.amortized_cost)}</td>
            <td className="px-2 py-2 text-xs text-black">{fmtCurrency(holding.principal_amount)}</td>
          </>
        )}
        <td className="px-2 py-2 text-xs text-[#808080]">
          {change.changes.length > 0 && (
            <span className="text-[#ffff00]">{change.changes.length} change{change.changes.length !== 1 ? 's' : ''}</span>
          )}
        </td>
      </tr>
      {expanded && change.changes.length > 0 && (
        <tr className={rowClass}>
          <td colSpan={8} className="px-4 py-2">
            <div className="space-y-1 text-xs">
              {change.changes.map((c, i) => (
                <div key={i} className="flex items-center gap-2 text-[#808080]">
                  <span className="font-semibold">{c.field}:</span>
                  <span className={Number(c.before) < Number(c.after) ? 'text-[#00ff00]' : Number(c.before) > Number(c.after) ? 'text-[#ff0000]' : 'text-black'}>
                    {typeof c.before === 'number' ? fmtCurrency(c.before) : String(c.before)}
                  </span>
                  <span className="text-[#808080]">→</span>
                  <span className={Number(c.before) < Number(c.after) ? 'text-[#00ff00]' : Number(c.before) > Number(c.after) ? 'text-[#ff0000]' : 'text-black'}>
                    {typeof c.after === 'number' ? fmtCurrency(c.after) : String(c.after)}
                  </span>
                  {c.delta !== undefined && (
                    <span className={`text-[10px] ${c.delta > 0 ? 'text-[#00ff00]' : 'text-[#ff0000]'}`}>
                      ({c.delta > 0 ? '+' : ''}{fmtCurrency(c.delta)})
                    </span>
                  )}
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function DiffViewer({ beforeHoldings, afterHoldings, beforePeriod, afterPeriod }: Props) {
  const [filter, setFilter] = useState<'all' | 'added' | 'removed' | 'modified'>('all');
  const [minChangeThreshold, setMinChangeThreshold] = useState<number>(100000); // $100k default
  
  const changes = useMemo(() => calculateDiff(beforeHoldings, afterHoldings), [beforeHoldings, afterHoldings]);
  const summary = useMemo(() => getDiffSummary(changes), [changes]);
  
  const filteredChanges = useMemo(() => {
    let filtered = changes;
    
    // Filter by type
    if (filter !== 'all') {
      filtered = filtered.filter(c => c.type === filter);
    }
    
    // Filter by minimum change threshold (for modified items)
    if (minChangeThreshold > 0) {
      filtered = filtered.filter(c => {
        if (c.type === 'added' && c.after) {
          const fv = Number(c.after.fair_value || 0);
          return fv >= minChangeThreshold;
        }
        if (c.type === 'removed' && c.before) {
          const fv = Number(c.before.fair_value || 0);
          return fv >= minChangeThreshold;
        }
        if (c.type === 'modified' && c.before && c.after) {
          const beforeFV = Number(c.before.fair_value || 0);
          const afterFV = Number(c.after.fair_value || 0);
          return Math.abs(afterFV - beforeFV) >= minChangeThreshold;
        }
        return true;
      });
    }
    
    return filtered;
  }, [changes, filter, minChangeThreshold]);
  
  return (
    <div className="flex flex-col space-y-3 sm:space-y-4">
      {/* Summary Banner */}
      <div className="window p-2 sm:p-3 flex-shrink-0">
        <div className="text-xs font-semibold mb-2 sm:mb-3 text-black">Change Summary</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 sm:gap-4">
          <div className="window p-2" style={{ border: '1px solid #00ff00' }}>
            <div className="text-xs text-[#00ff00] font-semibold">Added</div>
            <div className="text-sm text-black">{summary.added.count}</div>
            <div className="text-xs text-[#808080]">{fmtCurrency(summary.added.fairValue)}</div>
          </div>
          <div className="window p-2" style={{ border: '1px solid #ff0000' }}>
            <div className="text-xs text-[#ff0000] font-semibold">Removed</div>
            <div className="text-sm text-black">{summary.removed.count}</div>
            <div className="text-xs text-[#808080]">{fmtCurrency(summary.removed.fairValue)}</div>
          </div>
          <div className="window p-2" style={{ border: '1px solid #ffff00' }}>
            <div className="text-xs text-[#808080] font-semibold">Modified</div>
            <div className="text-sm text-black">{summary.modified.count}</div>
            <div className="text-xs text-[#808080]">{fmtCurrency(summary.modified.fairValueDelta)}</div>
          </div>
          <div className="window p-2">
            <div className="text-xs text-[#808080] font-semibold">Unchanged</div>
            <div className="text-sm text-black">{summary.unchanged.count}</div>
          </div>
        </div>
        <div className="mt-3 pt-3 border-t border-[#808080] grid grid-cols-2 md:grid-cols-3 gap-4 text-xs">
          <div>
            <div className="text-[#808080]">Total Before</div>
            <div className="text-black">{summary.totalBefore.count} holdings, {fmtCurrency(summary.totalBefore.fairValue)}</div>
          </div>
          <div>
            <div className="text-[#808080]">Total After</div>
            <div className="text-black">{summary.totalAfter.count} holdings, {fmtCurrency(summary.totalAfter.fairValue)}</div>
          </div>
          <div>
            <div className="text-[#808080]">Net Change</div>
            <div className={`${summary.totalAfter.fairValue - summary.totalBefore.fairValue >= 0 ? 'text-[#00ff00]' : 'text-[#ff0000]'}`}>
              {fmtCurrency(summary.totalAfter.fairValue - summary.totalBefore.fairValue)}
            </div>
          </div>
        </div>
      </div>
      
      {/* Filters */}
      <div className="window p-2 sm:p-3 flex-shrink-0">
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-2 sm:gap-4 flex-wrap">
          <div className="flex items-center gap-2 text-xs text-[#808080] w-full sm:w-auto">
            <span>Filter:</span>
            <select
              className="input text-xs flex-1 sm:flex-initial"
              value={filter}
              onChange={(e) => setFilter(e.target.value as typeof filter)}
            >
              <option value="all">All Changes</option>
              <option value="added">Added Only</option>
              <option value="removed">Removed Only</option>
              <option value="modified">Modified Only</option>
            </select>
          </div>
          <div className="flex items-center gap-2 text-xs text-[#808080] w-full sm:w-auto">
            <span>Min Change:</span>
            <select
              className="input text-xs flex-1 sm:flex-initial"
              value={minChangeThreshold}
              onChange={(e) => setMinChangeThreshold(Number(e.target.value))}
            >
              <option value="0">Any</option>
              <option value="10000">$10k+</option>
              <option value="100000">$100k+</option>
              <option value="500000">$500k+</option>
              <option value="1000000">$1M+</option>
            </select>
          </div>
          <div className="text-xs text-[#808080] w-full sm:w-auto sm:ml-auto">
            Showing {filteredChanges.length} of {changes.length} changes
          </div>
        </div>
      </div>
      
      {/* Changes Table */}
      <div className="window flex flex-col flex-shrink-0">
        <div className="titlebar flex-shrink-0">
          <div className="text-xs sm:text-sm tracking-wide">Changes: {beforePeriod} → {afterPeriod}</div>
        </div>
        <div className="overflow-x-auto relative" style={{ maxHeight: 'none' }}>
          <table className="w-full text-xs border-separate border-spacing-0">
            <thead className="sticky top-0 z-10 bg-[#c0c0c0]">
              <tr className="border-b border-[#808080]">
                <th className="px-2 py-2 text-left text-black font-semibold">Company</th>
                <th className="px-2 py-2 text-left text-black font-semibold">Type</th>
                <th className="px-2 py-2 text-left text-black font-semibold">Industry</th>
                <th className="px-2 py-2 text-right text-black font-semibold">Fair Value</th>
                <th className="px-2 py-2 text-right text-black font-semibold">Cost</th>
                <th className="px-2 py-2 text-right text-black font-semibold">Principal</th>
                <th className="px-2 py-2 text-left text-black font-semibold">Details</th>
              </tr>
            </thead>
            <tbody>
              {filteredChanges.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-4 text-center text-[#808080]">
                    No changes found (or filtered out)
                  </td>
                </tr>
              ) : (
                filteredChanges.map((change, i) => (
                  <HoldingRow key={change.key} change={change} />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

