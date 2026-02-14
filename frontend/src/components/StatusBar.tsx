import { formatPeriodLabel } from '../utils/periodComparisons';
import type { ViewMode } from './AppHeader';

type Props = {
  viewMode: ViewMode;
  ticker?: string;
  period?: string;
  rowCount?: number;
  selectedCell?: string;
  selectedCompanyName?: string;
  selectedSector?: string;
};

export function StatusBar({
  viewMode,
  ticker,
  period,
  rowCount,
  selectedCell,
  selectedCompanyName,
  selectedSector,
}: Props) {
  const timestamp = new Date().toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

  return (
    <div className="status-bar flex items-center justify-between text-black">
      <div className="flex items-center gap-4">
        <span className="flex items-center gap-2">
          <span className="text-[#808080]">View:</span>
          <span className="text-black font-semibold capitalize">{viewMode}</span>
        </span>
        {viewMode === 'bdc' && ticker && (
          <>
            <span className="flex items-center gap-2">
              <span className="text-[#808080]">Ticker:</span>
              <span className="text-black font-semibold">{ticker}</span>
            </span>
            {period && (
              <span className="flex items-center gap-2">
                <span className="text-[#808080]">Period:</span>
                <span className="text-black">{formatPeriodLabel(period)}</span>
              </span>
            )}
            {rowCount !== undefined && (
              <span className="flex items-center gap-2">
                <span className="text-[#808080]">Rows:</span>
                <span className="text-black">{rowCount.toLocaleString()}</span>
              </span>
            )}
          </>
        )}
        {viewMode === 'companies' && selectedCompanyName && (
          <span className="flex items-center gap-2">
            <span className="text-[#808080]">Company:</span>
            <span className="text-black truncate max-w-[200px]">{selectedCompanyName}</span>
          </span>
        )}
        {viewMode === 'sectors' && selectedSector && (
          <span className="flex items-center gap-2">
            <span className="text-[#808080]">Sector:</span>
            <span className="text-black">{selectedSector}</span>
          </span>
        )}
        {selectedCell && (
          <span className="flex items-center gap-2">
            <span className="text-[#808080]">Cell:</span>
            <span className="text-black font-mono text-[11px]">{selectedCell}</span>
          </span>
        )}
      </div>
      <div className="flex items-center gap-4">
        <span className="text-[#808080]">Time:</span>
        <span className="text-black">{timestamp}</span>
      </div>
    </div>
  );
}


