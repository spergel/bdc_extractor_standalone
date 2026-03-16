import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { playClickSound } from '../utils/sounds';

export type ViewMode = 'bdc' | 'companies' | 'sectors' | 'peer-marks';

type Props = {
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
  isHome?: boolean;
};

const NAV_LABELS: Record<ViewMode, string> = {
  bdc: 'BDC',
  companies: 'Companies',
  sectors: 'Sectors',
  'peer-marks': 'Peer Marks',
};

export function AppHeader({ viewMode, onViewModeChange, isHome = false }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();

  const handleViewChange = (newMode: ViewMode) => {
    playClickSound();
    onViewModeChange(newMode);
  };

  const handleBack = () => {
    playClickSound();
    navigate(-1);
  };

  const handleHome = () => {
    playClickSound();
    navigate('/');
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
          <button
            onClick={handleHome}
            className="text-sm font-bold text-white hover:text-white/80"
            title="Home"
          >
            BDC Extractor
          </button>
          <button
            onClick={() => setCollapsed(true)}
            className="px-1 text-xs hover:bg-white/10"
            title="Collapse header"
          >
            ▲
          </button>
        </div>

        {/* Back button — hidden on the home page */}
        {!isHome && (
          <button
            onClick={handleBack}
            className="px-2 py-0.5 text-xs bg-[#c0c0c0] text-black hover:bg-white/20"
            title="Go back"
            style={{
              border: '1px outset #c0c0c0',
              borderTop: '1px solid #ffffff',
              borderLeft: '1px solid #ffffff',
              borderRight: '1px solid #808080',
              borderBottom: '1px solid #808080',
            }}
          >
            ← Back
          </button>
        )}

        <div className="hidden sm:block h-4 w-px bg-white/30" />

        {/* View mode tabs — dimmed on home page */}
        <div className="flex items-center gap-2 flex-wrap">
          {(['bdc', 'companies', 'sectors', 'peer-marks'] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => handleViewChange(mode)}
              className={`px-2 py-0.5 text-xs font-semibold ${
                !isHome && viewMode === mode
                  ? 'bg-white text-[#000080]'
                  : 'bg-[#c0c0c0] text-black hover:bg-white/20'
              }`}
              style={{
                border: !isHome && viewMode === mode ? '1px inset #c0c0c0' : '1px outset #c0c0c0',
                borderTop: !isHome && viewMode === mode ? '1px solid #808080' : '1px solid #ffffff',
                borderLeft: !isHome && viewMode === mode ? '1px solid #808080' : '1px solid #ffffff',
                borderRight: !isHome && viewMode === mode ? '1px solid #ffffff' : '1px solid #808080',
                borderBottom: !isHome && viewMode === mode ? '1px solid #ffffff' : '1px solid #808080',
              }}
            >
              {NAV_LABELS[mode]}
            </button>
          ))}
        </div>
      </div>
    </header>
  );
}
