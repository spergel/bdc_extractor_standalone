import { useState, useEffect, useCallback } from 'react';
import { ExprInput } from './HoldingsFilterBar';
import type { AdvancedFilterState } from './HoldingsFilterBar';
import { defaultAdvancedFilterState } from './HoldingsFilterBar';
import type { Holding } from '../data/adapter';

type Props = {
  filters: AdvancedFilterState;
  onApply: (filters: AdvancedFilterState) => void;
  onClose: () => void;
  matchCount: number;
  holdings: Holding[];
};

export function AdvancedSearchDialog({ filters, onApply, onClose, matchCount, holdings: _holdings }: Props) {
  const [local, setLocal] = useState<AdvancedFilterState>({ ...filters });

  const update = (partial: Partial<AdvancedFilterState>) => {
    setLocal(prev => ({ ...prev, ...partial }));
  };

  const handleApply = useCallback(() => {
    onApply(local);
    onClose();
  }, [local, onApply, onClose]);

  const handleClear = useCallback(() => {
    onApply(defaultAdvancedFilterState);
    onClose();
  }, [onApply, onClose]);

  // Escape key closes
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const exprInput = 'input text-xs py-0.5 w-24';
  const rangeInput = 'input text-xs py-0.5 flex-1 min-w-0';
  const dateInput = 'input text-xs py-0.5 flex-1 min-w-0';

  const RadioGroup = ({ label, value, onChange, options }: {
    label: string;
    value: string;
    onChange: (val: string) => void;
    options: { value: string; label: string }[];
  }) => (
    <div className="flex items-center gap-2">
      <span className="text-xs text-[#808080] w-10 shrink-0">{label}</span>
      <div className="flex gap-1">
        {options.map(opt => (
          <button
            key={opt.value}
            className={`px-2 py-0.5 text-xs window ${value === opt.value ? 'border-inset bg-[#ffffff] font-bold text-[#000080]' : ''}`}
            onClick={() => onChange(opt.value)}
            type="button"
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div
      className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="window" style={{ width: 440 }} onMouseDown={e => e.stopPropagation()}>
        <div className="titlebar flex items-center justify-between">
          <span className="text-xs sm:text-sm tracking-wide">Advanced Search</span>
          <button
            className="px-1.5 text-xs text-white hover:bg-[#000080] leading-none"
            onClick={onClose}
            type="button"
          >
            X
          </button>
        </div>
        <div className="bg-[#c0c0c0] p-3 flex flex-col gap-2.5 max-h-[80vh] overflow-y-auto">

          {/* Valuation */}
          <fieldset className="border border-[#808080] p-2 flex flex-col gap-1.5">
            <legend className="text-xs font-bold text-[#000080] px-1">Valuation</legend>
            <label className="flex items-center gap-2">
              <span className="text-xs text-[#808080] w-16 shrink-0">Spread%</span>
              <ExprInput className={exprInput} value={local.spread} onChange={val => update({ spread: val })} placeholder=">5" title="Spread % (e.g. >5, 3-7, <10)" />
            </label>
            <label className="flex items-center gap-2">
              <span className="text-xs text-[#808080] w-16 shrink-0">FV ($K)</span>
              <ExprInput className={exprInput} value={local.fv} onChange={val => update({ fv: val })} placeholder=">1m" title="Fair Value (e.g. >1m, 100k-5m)" />
            </label>
            <label className="flex items-center gap-2">
              <span className="text-xs text-[#808080] w-16 shrink-0">Prin ($K)</span>
              <ExprInput className={exprInput} value={local.principal} onChange={val => update({ principal: val })} placeholder=">1m" title="Principal (e.g. >1m, <500k)" />
            </label>
            <label className="flex items-center gap-2">
              <span className="text-xs text-[#808080] w-16 shrink-0">FV/Cost</span>
              <ExprInput className={exprInput} value={local.fvCost} onChange={val => update({ fvCost: val })} placeholder="<0.9" title="FV/Cost ratio (e.g. <0.9, 0.95-1.05)" />
            </label>
            <label className="flex items-center gap-2">
              <span className="text-xs text-[#808080] w-16 shrink-0">FV/Prin</span>
              <ExprInput className={exprInput} value={local.fvPrin} onChange={val => update({ fvPrin: val })} placeholder="<0.95" title="FV/Principal ratio (e.g. <0.95, >1)" />
            </label>
          </fieldset>

          {/* Portfolio Weight */}
          <fieldset className="border border-[#808080] p-2">
            <legend className="text-xs font-bold text-[#000080] px-1">Portfolio Weight</legend>
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                className={rangeInput}
                value={local.percentOfBookMin}
                onChange={e => update({ percentOfBookMin: e.target.value })}
                placeholder="min"
                title="Minimum % of book"
              />
              <span className="text-xs text-[#808080] font-bold shrink-0">&lt; % of Book &gt;</span>
              <input
                type="text"
                className={rangeInput}
                value={local.percentOfBookMax}
                onChange={e => update({ percentOfBookMax: e.target.value })}
                placeholder="max"
                title="Maximum % of book"
              />
            </div>
          </fieldset>

          {/* Yield */}
          <fieldset className="border border-[#808080] p-2">
            <legend className="text-xs font-bold text-[#000080] px-1">Yield</legend>
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                className={rangeInput}
                value={local.yieldMin}
                onChange={e => update({ yieldMin: e.target.value })}
                placeholder="min"
                title="Minimum yield %"
              />
              <span className="text-xs text-[#808080] font-bold shrink-0">&lt; Yield % &gt;</span>
              <input
                type="text"
                className={rangeInput}
                value={local.yieldMax}
                onChange={e => update({ yieldMax: e.target.value })}
                placeholder="max"
                title="Maximum yield %"
              />
            </div>
          </fieldset>

          {/* Maturity / Term */}
          <fieldset className="border border-[#808080] p-2 flex flex-col gap-1.5">
            <legend className="text-xs font-bold text-[#000080] px-1">Maturity / Term</legend>
            <label className="flex items-center gap-2">
              <span className="text-xs text-[#808080] w-24 shrink-0">Term (yrs)</span>
              <ExprInput className={exprInput} value={local.remainingTerm} onChange={val => update({ remainingTerm: val })} placeholder="<2" title="Remaining term in years (e.g. <2, 1-5, >3)" />
            </label>
            <div className="flex items-center gap-1.5">
              <input
                type="date"
                className={dateInput}
                value={local.maturityAfter}
                onChange={e => update({ maturityAfter: e.target.value })}
                title="Maturity after this date"
              />
              <span className="text-xs text-[#808080] font-bold shrink-0">&lt; Maturity &gt;</span>
              <input
                type="date"
                className={dateInput}
                value={local.maturityBefore}
                onChange={e => update({ maturityBefore: e.target.value })}
                title="Maturity before this date"
              />
            </div>
          </fieldset>

          {/* Characteristics */}
          <fieldset className="border border-[#808080] p-2 flex flex-col gap-1.5">
            <legend className="text-xs font-bold text-[#000080] px-1">Characteristics</legend>
            <RadioGroup
              label="PIK"
              value={local.pikFilter}
              onChange={val => update({ pikFilter: val as AdvancedFilterState['pikFilter'] })}
              options={[
                { value: 'all', label: 'All' },
                { value: 'pik', label: 'PIK' },
                { value: 'non-pik', label: 'Non-PIK' },
              ]}
            />
            <RadioGroup
              label="Rate"
              value={local.rateType}
              onChange={val => update({ rateType: val as AdvancedFilterState['rateType'] })}
              options={[
                { value: 'all', label: 'All' },
                { value: 'floating', label: 'Floating' },
                { value: 'fixed', label: 'Fixed' },
              ]}
            />
          </fieldset>

          {/* Match count */}
          <div className="text-xs text-[#000080] text-center">
            {matchCount} holding{matchCount !== 1 ? 's' : ''} match
          </div>
        </div>

        {/* Button bar */}
        <div className="border-t border-[#808080] bg-[#c0c0c0] px-3 py-2 flex justify-end gap-2">
          <button className="px-4 py-1 text-xs window font-bold" onClick={handleApply} type="button">
            Apply
          </button>
          <button className="px-4 py-1 text-xs window" onClick={handleClear} type="button">
            Clear
          </button>
          <button className="px-4 py-1 text-xs window" onClick={onClose} type="button">
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
