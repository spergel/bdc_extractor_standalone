import { useState, useCallback, useRef } from 'react';
import { formatThousandsAsCurrency } from '../utils/formatCurrency';

export type PieChartDatum = {
  category: string;
  count: number;
  fairValue: number;
  percentage: number;
};

export function PieChart({
  data,
  title,
  byValue = true,
  onSliceClick,
  selectedCategory,
}: {
  data: PieChartDatum[];
  title: string;
  byValue?: boolean;
  onSliceClick?: (category: string) => void;
  selectedCategory?: string | null;
}) {
  const [hovered, setHovered] = useState<{ item: PieChartDatum; x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  if (data.length === 0) {
    return (
      <div className="window p-3">
        <div className="text-xs text-[#808080]">{title}: No data</div>
      </div>
    );
  }

  const top = data.slice(0, 10);
  const rest = data.slice(10);
  const otherValue = rest.reduce((sum, item) => sum + (byValue ? item.fairValue : item.count), 0);
  const otherPercentage = rest.reduce((sum, item) => sum + item.percentage, 0);

  const displayData =
    otherValue > 0
      ? [...top, { category: 'Other', count: rest.reduce((s, i) => s + i.count, 0), fairValue: otherValue, percentage: otherPercentage }]
      : top;

  const total = displayData.reduce((sum, item) => sum + (byValue ? item.fairValue : item.count), 0);

  const colors = [
    '#0000ff', '#ff0000', '#00ff00', '#ffff00', '#ff00ff',
    '#00ffff', '#808080', '#c0c0c0', '#800000', '#008000',
  ];

  let currentAngle = -90;
  const paths = displayData.map((item, i) => {
    const value = byValue ? item.fairValue : item.count;
    const percentage = total > 0 ? (value / total) * 100 : 0;
    const angle = (percentage / 100) * 360;
    const startAngle = currentAngle;
    const endAngle = currentAngle + angle;
    currentAngle = endAngle;
    const largeArc = angle > 180 ? 1 : 0;
    const radius = 60;
    const centerX = 80;
    const centerY = 80;
    const x1 = centerX + radius * Math.cos((startAngle * Math.PI) / 180);
    const y1 = centerY + radius * Math.sin((startAngle * Math.PI) / 180);
    const x2 = centerX + radius * Math.cos((endAngle * Math.PI) / 180);
    const y2 = centerY + radius * Math.sin((endAngle * Math.PI) / 180);

    // Handle the 100% slice case explicitly so we draw a full circle
    // instead of a degenerate arc that looks like a line.
    const path =
      angle >= 359.999
        ? `M ${centerX} ${centerY} m -${radius},0 a ${radius} ${radius} 0 1 0 ${radius * 2} 0 a ${radius} ${radius} 0 1 0 -${radius * 2} 0 Z`
        : `M ${centerX} ${centerY} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`;
    const midAngle = (startAngle + endAngle) / 2;
    const midX = centerX + (radius * 0.7) * Math.cos((midAngle * Math.PI) / 180);
    const midY = centerY + (radius * 0.7) * Math.sin((midAngle * Math.PI) / 180);
    return { path, color: colors[i % colors.length], item, percentage, midX, midY };
  });

  const containerRef = useRef<HTMLDivElement>(null);
  const handleMouseMove = useCallback((e: React.MouseEvent<SVGElement>, item: PieChartDatum) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    setHovered({ item, x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, []);
  const handleMouseLeave = useCallback(() => setHovered(null), []);

  return (
    <div className="window p-3 relative">
      <div className="text-xs font-semibold mb-2 text-black">{title}</div>
      <div className="flex items-start gap-4">
        <div ref={containerRef} className="flex-shrink-0 relative">
          <svg ref={svgRef} viewBox="0 0 160 160" className="w-32 h-32" onMouseLeave={handleMouseLeave}>
            {paths.map(({ path, color, item }, i) => (
              <path
                key={i}
                d={path}
                fill={color}
                stroke={selectedCategory === item.category ? '#ffffff' : '#000000'}
                strokeWidth={selectedCategory === item.category ? 3 : 1}
                onMouseMove={(e) => handleMouseMove(e, item)}
                onClick={() => onSliceClick?.(item.category)}
                style={{ cursor: onSliceClick ? 'pointer' : 'default' }}
                opacity={
                  hovered?.item.category === item.category || selectedCategory === item.category ? 1 : hovered || selectedCategory ? 0.5 : 1
                }
              />
            ))}
          </svg>
          {hovered && (
            <div
              className="absolute pointer-events-none z-10 bg-white border-2 border-[#000000] px-2 py-1 text-xs text-black"
              style={{
                left: `${hovered.x + 10}px`,
                top: `${hovered.y - 10}px`,
                transform: hovered.x > 128 ? 'translateX(-100%)' : 'none',
              }}
            >
              <div className="text-black font-medium">{hovered.item.category}</div>
              <div className="text-black">{hovered.item.percentage.toFixed(1)}%</div>
              {byValue ? (
                <div className="text-black">{formatThousandsAsCurrency(hovered.item.fairValue)}</div>
              ) : null}
            </div>
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="space-y-1 text-xs">
            {displayData.slice(0, 8).map((item, i) => (
              <div key={i} className="flex items-center gap-2">
                <div className="w-3 h-3 flex-shrink-0" style={{ backgroundColor: colors[i % colors.length] }} />
                <div className="flex-1 min-w-0 truncate">{item.category}</div>
                <div className="text-[#808080]">{item.percentage.toFixed(1)}%</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export type MaturityLadderDatum = {
  bucket: string;
  count: number;
  fairValue: number;
  percentage: number;
};

export function MaturityLadderChart({ data }: { data: MaturityLadderDatum[] }) {
  const [hovered, setHovered] = useState<{ item: MaturityLadderDatum; x: number; y: number } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const maxFV = Math.max(...data.map((d) => d.fairValue), 1);

  const handleMouseEnter = useCallback((e: React.MouseEvent<HTMLDivElement>, item: MaturityLadderDatum) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    setHovered({ item, x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, []);
  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>, item: MaturityLadderDatum) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    setHovered({ item, x: e.clientX - rect.left, y: e.clientY - rect.top });
  }, []);
  const handleMouseLeave = useCallback(() => setHovered(null), []);

  if (data.length === 0) {
    return (
      <div className="window p-3">
        <div className="text-xs text-[#808080]">Maturity Ladder: No data</div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="window p-3 relative">
      <div className="text-xs font-semibold mb-2 text-black">Maturity Ladder</div>
      <div className="space-y-1">
        {data.map((item, i) => (
          <div
            key={i}
            className="flex items-center gap-2"
            onMouseEnter={(e) => handleMouseEnter(e, item)}
            onMouseMove={(e) => handleMouseMove(e, item)}
            onMouseLeave={handleMouseLeave}
            style={{ cursor: 'pointer' }}
          >
            <div className="w-20 text-xs text-[#808080]">{item.bucket}</div>
            <div className="flex-1 bg-[#c0c0c0] h-4 overflow-hidden relative">
              <div
                className="h-full bg-[#0000ff] transition-opacity"
                style={{
                  width: `${(item.fairValue / maxFV) * 100}%`,
                  opacity: hovered?.item.bucket === item.bucket ? 1 : hovered ? 0.5 : 1,
                }}
              />
            </div>
            <div className="w-24 text-xs text-[#808080] text-right">
              {formatThousandsAsCurrency(item.fairValue)} ({item.percentage.toFixed(1)}%)
            </div>
          </div>
        ))}
      </div>
      {hovered && (
        <div
          className="absolute pointer-events-none z-10 bg-white border-2 border-[#000000] px-2 py-1 text-xs text-black"
          style={{ left: `${hovered.x + 10}px`, top: `${hovered.y - 10}px` }}
        >
          <div className="text-black font-medium">{hovered.item.bucket}</div>
          <div className="text-black">{formatThousandsAsCurrency(hovered.item.fairValue)}</div>
          <div className="text-black">{hovered.item.percentage.toFixed(1)}%</div>
        </div>
      )}
    </div>
  );
}
