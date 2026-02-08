import { useState } from 'react';
import { playClickSound } from '../utils/sounds';

type Props = {
  mode: 'individual' | 'comparison';
  onModeChange: (mode: 'individual' | 'comparison') => void;
};

export function AppHeader({ mode, onModeChange }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  
  const handleModeChange = (newMode: 'individual' | 'comparison') => {
    playClickSound();
    onModeChange(newMode);
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
          <button
            onClick={() => handleModeChange('individual')}
            className={`px-2 py-0.5 text-xs font-semibold ${
              mode === 'individual'
                ? 'bg-white text-[#000080]'
                : 'bg-[#c0c0c0] text-black hover:bg-white/20'
            }`}
            style={{
              border: mode === 'individual' ? '1px inset #c0c0c0' : '1px outset #c0c0c0',
              borderTop: mode === 'individual' ? '1px solid #808080' : '1px solid #ffffff',
              borderLeft: mode === 'individual' ? '1px solid #808080' : '1px solid #ffffff',
              borderRight: mode === 'individual' ? '1px solid #ffffff' : '1px solid #808080',
              borderBottom: mode === 'individual' ? '1px solid #ffffff' : '1px solid #808080',
            }}
          >
            Individual
          </button>
          <button
            onClick={() => handleModeChange('comparison')}
            className={`px-2 py-0.5 text-xs font-semibold ${
              mode === 'comparison'
                ? 'bg-white text-[#000080]'
                : 'bg-[#c0c0c0] text-black hover:bg-white/20'
            }`}
            style={{
              border: mode === 'comparison' ? '1px inset #c0c0c0' : '1px outset #c0c0c0',
              borderTop: mode === 'comparison' ? '1px solid #808080' : '1px solid #ffffff',
              borderLeft: mode === 'comparison' ? '1px solid #808080' : '1px solid #ffffff',
              borderRight: mode === 'comparison' ? '1px solid #ffffff' : '1px solid #808080',
              borderBottom: mode === 'comparison' ? '1px solid #ffffff' : '1px solid #808080',
            }}
          >
            Comparison
          </button>
        </div>
      </div>
    </header>
  );
}

