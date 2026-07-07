import { useEffect, useState } from 'react';
import { useStore } from '../state/store';
import { fetchJson, type Intervention, type MetricsSnapshot } from '../net/api';
import { Chart, type ChartMarker, type ChartSeries } from './Chart';
import './metrics.css';

const POLL_MS = 1000;
const WINDOW = 120; // samples kept (~2 min at 1 Hz)

interface Sample {
  tick: number;
  populations: Record<string, number>;
  diversity: number;
  flora: number;
  deaths: Record<string, number>;
}

const DEATH_COLORS = ['#ff8a7a', '#ffb454', '#c58cff', '#7ad3ff', '#8fd98f'];

export function MetricsPanel() {
  const [open, setOpen] = useState(false);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [interventions, setInterventions] = useState<Intervention[]>([]);
  const species = useStore((s) => s.sync?.species) ?? [];

  useEffect(() => {
    if (!open) return;
    const poll = () => {
      void fetchJson<MetricsSnapshot>('/api/metrics')
        .then((m) => {
          setSamples((prev) => {
            if (prev.length && prev[prev.length - 1].tick === m.tick) return prev;
            const next = [
              ...prev,
              {
                tick: m.tick,
                populations: m.populations,
                diversity: m.shannonDiversity,
                flora: m.floraDensity,
                deaths: m.deathsByCause,
              },
            ];
            return next.length > WINDOW ? next.slice(next.length - WINDOW) : next;
          });
        })
        .catch(() => undefined);
      void fetchJson<Intervention[]>('/api/interventions')
        .then(setInterventions)
        .catch(() => undefined);
    };
    poll();
    const timer = window.setInterval(poll, POLL_MS);
    return () => window.clearInterval(timer);
  }, [open]);

  if (!open) {
    return (
      <button className="metrics-toggle" onClick={() => setOpen(true)} title="Open metrics">
        ▴ METRICS
      </button>
    );
  }

  const firstTick = samples.length ? samples[0].tick : 0;
  const lastTick = samples.length ? samples[samples.length - 1].tick : 0;
  const span = Math.max(1, lastTick - firstTick);

  const markers: ChartMarker[] = interventions
    .filter((iv) => iv.tick >= firstTick && iv.tick <= lastTick)
    .map((iv) => ({
      pos: (iv.tick - firstTick) / span,
      color: iv.kind === 'rollback' ? '#ff8a7a' : '#3ddc84',
      label: `${iv.kind} @ t${iv.tick}${iv.plugin_name ? ` (${iv.plugin_name})` : ''}`,
    }));

  const popSeries: ChartSeries[] = species.map((sp) => ({
    label: sp.name,
    color: sp.color,
    points: samples.map((s) => s.populations[sp.name] ?? 0),
  }));

  const diversitySeries: ChartSeries[] = [
    { label: 'shannon', color: '#4be1ff', points: samples.map((s) => s.diversity) },
  ];
  const floraSeries: ChartSeries[] = [
    { label: 'flora', color: '#3ddc84', points: samples.map((s) => s.flora) },
  ];

  const causes = Array.from(
    new Set(samples.flatMap((s) => Object.keys(s.deaths))),
  ).sort();
  const deathSeries: ChartSeries[] = causes.map((cause, i) => ({
    label: cause,
    color: DEATH_COLORS[i % DEATH_COLORS.length],
    points: samples.map((s) => s.deaths[cause] ?? 0),
  }));

  return (
    <div className="metrics-panel">
      <div className="metrics-header">
        METRICS
        <span className="metrics-tick">t{lastTick}</span>
        <button className="metrics-collapse" onClick={() => setOpen(false)} title="Collapse">
          ▾
        </button>
      </div>
      <div className="metrics-grid">
        <div className="metrics-cell">
          <div className="metrics-cell-title">
            POPULATION
            <span className="metrics-legend">
              {popSeries.map((s) => (
                <span key={s.label} style={{ color: s.color }} title={s.label}>
                  ●
                </span>
              ))}
            </span>
          </div>
          <Chart series={popSeries} markers={markers} yFloor={0} />
        </div>
        <div className="metrics-cell">
          <div className="metrics-cell-title">SHANNON DIVERSITY</div>
          <Chart series={diversitySeries} markers={markers} yFloor={0} />
        </div>
        <div className="metrics-cell">
          <div className="metrics-cell-title">FLORA DENSITY</div>
          <Chart series={floraSeries} yFloor={0} />
        </div>
        <div className="metrics-cell">
          <div className="metrics-cell-title">
            DEATHS BY CAUSE
            <span className="metrics-legend">
              {deathSeries.map((s) => (
                <span key={s.label} style={{ color: s.color }} title={s.label}>
                  ●
                </span>
              ))}
            </span>
          </div>
          <Chart series={deathSeries} yFloor={0} />
        </div>
      </div>
    </div>
  );
}
