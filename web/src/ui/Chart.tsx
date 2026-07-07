// Dependency-free SVG line chart for the metrics panel (Story 4.1).
// Auto-scales Y across all series; X is sample index. Markers are vertical
// rules positioned by fractional x (0..1), used for promotion/rollback events.

export interface ChartSeries {
  label: string;
  color: string;
  points: number[];
}

export interface ChartMarker {
  pos: number; // 0..1 along the x-axis
  color: string;
  label: string;
}

interface ChartProps {
  series: ChartSeries[];
  markers?: ChartMarker[];
  height?: number;
  yFloor?: number; // force a lower bound (e.g. 0 for populations)
}

const VW = 100;
const VH = 100;

export function Chart({ series, markers = [], height = 78, yFloor }: ChartProps) {
  const lens = series.map((s) => s.points.length);
  const n = Math.max(0, ...lens);
  if (n < 2) {
    return <div className="chart-empty">collecting…</div>;
  }

  let lo = yFloor ?? Infinity;
  let hi = -Infinity;
  for (const s of series) {
    for (const v of s.points) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  if (!isFinite(lo)) lo = 0;
  if (!isFinite(hi)) hi = 1;
  if (hi - lo < 1e-9) hi = lo + 1;

  const xAt = (i: number, len: number) => (len <= 1 ? 0 : (i / (len - 1)) * VW);
  const yAt = (v: number) => VH - ((v - lo) / (hi - lo)) * VH;

  return (
    <div className="chart" style={{ height }}>
      <svg viewBox={`0 0 ${VW} ${VH}`} preserveAspectRatio="none" className="chart-svg">
        <line x1={0} y1={VH} x2={VW} y2={VH} className="chart-axis" />
        <line x1={0} y1={0} x2={0} y2={VH} className="chart-axis" />
        {markers.map((m, i) => (
          <line
            key={i}
            x1={m.pos * VW}
            y1={0}
            x2={m.pos * VW}
            y2={VH}
            stroke={m.color}
            strokeWidth={0.6}
            strokeDasharray="2 2"
            vectorEffect="non-scaling-stroke"
          >
            <title>{m.label}</title>
          </line>
        ))}
        {series.map((s) => (
          <polyline
            key={s.label}
            points={s.points.map((v, i) => `${xAt(i, s.points.length)},${yAt(v)}`).join(' ')}
            fill="none"
            stroke={s.color}
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
      <div className="chart-range">
        <span>{hi.toFixed(hi >= 10 ? 0 : 2)}</span>
        <span>{lo.toFixed(lo >= 10 ? 0 : 2)}</span>
      </div>
    </div>
  );
}
