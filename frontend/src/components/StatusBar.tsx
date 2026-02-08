type Props = {
  ticker?: string;
  period?: string;
  rowCount?: number;
  selectedCell?: string;
  mode?: 'individual' | 'comparison';
};

export function StatusBar({ ticker, period, rowCount, selectedCell, mode }: Props) {
  const timestamp = new Date().toLocaleTimeString('en-US', { 
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  });

  return (
    <div className="status-bar flex items-center justify-between text-black">
      <div className="flex items-center gap-4">
        {ticker && (
          <span className="flex items-center gap-2">
            <span className="text-[#808080]">Ticker:</span>
            <span className="text-black font-semibold">{ticker}</span>
          </span>
        )}
        {period && (
          <span className="flex items-center gap-2">
            <span className="text-[#808080]">Period:</span>
            <span className="text-black">{period}</span>
          </span>
        )}
        {rowCount !== undefined && (
          <span className="flex items-center gap-2">
            <span className="text-[#808080]">Rows:</span>
            <span className="text-black">{rowCount.toLocaleString()}</span>
          </span>
        )}
        {selectedCell && (
          <span className="flex items-center gap-2">
            <span className="text-[#808080]">Cell:</span>
            <span className="text-black font-mono text-[11px]">{selectedCell}</span>
          </span>
        )}
        <span className="flex items-center gap-2">
          <span className="text-[#808080]">Mode:</span>
          <span className="text-black">{mode || 'Individual'}</span>
        </span>
      </div>
      <div className="flex items-center gap-4">
        <span className="text-[#808080]">Time:</span>
        <span className="text-black">{timestamp}</span>
      </div>
    </div>
  );
}


