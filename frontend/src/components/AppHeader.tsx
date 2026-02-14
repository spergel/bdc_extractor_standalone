import { useState } from 'react';
import { playClickSound } from '../utils/sounds';

export type ViewMode = 'bdc' | 'companies' | 'sectors';

type Props = {
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
};

export function AppHeader({ viewMode, onViewModeChange }: Props) {
  const [collapsed, setCollapsed] = useState(false);

  const handleViewChange = (newMode: ViewMode) => {
    playClickSound();
    onViewModeChange(newMode);
  };

  if (collapsed) {
    return (
      <header className="titlebar py-0.5">
        <button
          onClick={() => setCollapsed(false)}
          className="px-2 py-0.5 text-xs hover:bg-white/10"
          title="Expand header"
        >
          ▼ BDC Extractor
        </button>
      </header>
    );
  }

  return (
    <header className="titlebar">
      <div className="flex flex-wrap items-center gap-3 sm:gap-4">
        <div className="flex items-center gap-2">
          <h1 className="text-sm font-bold text-white">
            BDC Extractor
          </h1>
          <button
            onClick={() => setCollapsed(true)}
            className="px-1 text-xs hover:bg-white/10"
            title="Collapse header"
          >
            ▲
          </button>
        </div>
        <div className="hidden sm:block h-4 w-px bg-white/30" />
        <div className="flex items-center gap-2 flex-wrap">
          {(['bdc', 'companies', 'sectors'] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => handleViewChange(mode)}
              className={`px-2 py-0.5 text-xs font-semibold ${
                viewMode === mode
                  ? 'bg-white text-[#000080]'
                  : 'bg-[#c0c0c0] text-black hover:bg-white/20'
              }`}
              style={{
                border: viewMode === mode ? '1px inset #c0c0c0' : '1px outset #c0c0c0',
                borderTop: viewMode === mode ? '1px solid #808080' : '1px solid #ffffff',
                borderLeft: viewMode === mode ? '1px solid #808080' : '1px solid #ffffff',
                borderRight: viewMode === mode ? '1px solid #ffffff' : '1px solid #808080',
                borderBottom: viewMode === mode ? '1px solid #ffffff' : '1px solid #808080',
              }}
            >
              {mode === 'bdc' ? 'BDC' : mode === 'companies' ? 'Companies' : 'Sectors'}
            </button>
          ))}
        </div>
      </div>
    </header>
  );
}
