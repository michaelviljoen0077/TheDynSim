import { useEffect, useRef, useState } from 'react';
import { useStore } from '../state/store';
import './hud.css';

const SEASONS = ['Spring', 'Summer', 'Autumn', 'Winter'];
const STRATA: { index: 0 | 1 | 2; label: string }[] = [
  { index: 1, label: 'Surface' },
  { index: 2, label: 'Sky' },
  { index: 0, label: 'Underground (x-ray)' },
];
const OVERLAYS: { value: 'none' | 'flora' | 'water'; label: string }[] = [
  { value: 'none', label: 'Terrain' },
  { value: 'flora', label: 'Flora' },
  { value: 'water', label: 'Water' },
];

function control(cmd: 'start' | 'pause' | 'step' | 'reset'): void {
  void fetch(`/api/control/${cmd}`, { method: 'POST' }).catch(() => undefined);
}

function postSpeed(tps: number): void {
  void fetch('/api/control/speed', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tps }),
  }).catch(() => undefined);
}

function clockText(dayFrac: number): string {
  const mins = Math.floor(((dayFrac % 1) + 1) % 1 * 24 * 60);
  const hh = String(Math.floor(mins / 60)).padStart(2, '0');
  const mm = String(mins % 60).padStart(2, '0');
  return `${hh}:${mm}`;
}

export function Hud() {
  const status = useStore((s) => s.status);
  const frame = useStore((s) => s.frame);
  const species = useStore((s) => s.sync?.species) ?? [];
  const counts = useStore((s) => s.speciesCounts);
  const hidden = useStore((s) => s.hiddenSpecies);
  const strata = useStore((s) => s.strata);
  const toggleSpecies = useStore((s) => s.toggleSpecies);
  const toggleStratum = useStore((s) => s.toggleStratum);
  const overlay = useStore((s) => s.overlay);
  const setOverlay = useStore((s) => s.setOverlay);
  const topology = useStore((s) => s.sync?.topology);
  const spherify = useStore((s) => s.spherify);
  const setSpherify = useStore((s) => s.setSpherify);
  const isCube = topology === 'cube';

  const [tps, setTps] = useState(20);
  const speedTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    void fetch('/api/state')
      .then((r) => r.json())
      .then((s: { targetTps: number }) => {
        if (typeof s.targetTps === 'number') setTps(Math.round(s.targetTps));
      })
      .catch(() => undefined);
  }, []);

  const onSpeed = (value: number) => {
    setTps(value);
    window.clearTimeout(speedTimer.current);
    speedTimer.current = window.setTimeout(() => postSpeed(value), 150);
  };

  const dayFrac = frame?.clock.dayFrac ?? 0;
  const isDay = dayFrac > 0.25 && dayFrac < 0.75;
  const w = frame?.weather;

  return (
    <div className="hud">
      <div className="panel panel-status">
        <div className="panel-title">
          <span className={`conn conn-${status}`} />
          GENESIS · THE OBSERVATORY
          <span className="conn-label">{status}</span>
        </div>
        <div className="stat-grid">
          <span className="k">tick</span>
          <span className="v">{frame?.tick ?? '—'}</span>
          <span className="k">tps</span>
          <span className="v">{frame ? frame.tps.toFixed(1) : '—'}</span>
          <span className="k">entities</span>
          <span className="v">{frame?.entities ?? '—'}</span>
          <span className="k">epoch</span>
          <span className="v">{frame?.epoch ?? '—'}</span>
        </div>
      </div>

      <div className="panel panel-clock">
        <div className="clock-line">
          {isCube ? (
            // on the planet, day/night is per-longitude (fixed sun, spinning
            // world) — a single clock is meaningless, so show the rotation phase
            <>
              <span className="glyph">🌐</span>
              <span className="clock-time">{`${Math.floor(dayFrac * 360)}°`}</span>
              <span className="season">spin</span>
            </>
          ) : (
            <>
              <span className="glyph">{isDay ? '☀' : '☾'}</span>
              <span className="clock-time">{clockText(dayFrac)}</span>
              <span className="season">
                {SEASONS[(frame?.clock.seasonIndex ?? 0) % SEASONS.length]}
              </span>
            </>
          )}
        </div>
        <div className="stat-grid">
          <span className="k">temp</span>
          <span className="v">{w ? `${w.temp.toFixed(1)}°C` : '—'}</span>
          <span className="k">precip</span>
          <span className="v">{w ? `${(w.precip * 100).toFixed(0)}%` : '—'}</span>
          <span className="k">wind</span>
          <span className="v">
            {w ? `${Math.hypot(w.windX, w.windY).toFixed(1)}` : '—'}
          </span>
        </div>
      </div>

      <div className="panel panel-species">
        <div className="panel-title">SPECIES</div>
        {species.length === 0 && <div className="empty">no species registered</div>}
        {species.map((sp) => (
          <button
            key={sp.id}
            className={`species-row ${hidden[sp.id] ? 'species-hidden' : ''}`}
            onClick={() => toggleSpecies(sp.id)}
            title={`${sp.plugin} — click to ${hidden[sp.id] ? 'show' : 'hide'}`}
          >
            <span className="swatch" style={{ background: sp.color }} />
            <span className="species-name">{sp.name}</span>
            <span className="species-count">{counts[sp.id] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="panel panel-controls">
        <div className="panel-title">SIMULATION</div>
        <div className="button-row">
          <button onClick={() => control('start')}>Start</button>
          <button onClick={() => control('pause')}>Pause</button>
          <button onClick={() => control('step')}>Step</button>
          <button className="danger" onClick={() => control('reset')}>
            Reset
          </button>
        </div>
        <label className="speed-row">
          <span className="k">speed</span>
          <input
            type="range"
            min={1}
            max={240}
            value={tps}
            onChange={(e) => onSpeed(Number(e.target.value))}
          />
          <span className="v">{tps} tps</span>
        </label>
        <div className="strata-row">
          {STRATA.map(({ index, label }) => (
            <label key={index} className="stratum-toggle">
              <input
                type="checkbox"
                checked={strata[index]}
                onChange={() => toggleStratum(index)}
              />
              {label}
            </label>
          ))}
        </div>
        <div className="overlay-row">
          {OVERLAYS.map(({ value, label }) => (
            <button
              key={value}
              className={`overlay-btn ${overlay === value ? 'active' : ''}`}
              onClick={() => setOverlay(value)}
              title={`${label} heat-map overlay`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="strata-row">
          <label className="stratum-toggle" title="World topology">
            <span className="k">topology</span>
            <span className="v topology-tag">{isCube ? 'Cube' : 'Flat'}</span>
          </label>
          {isCube && (
            <label className="stratum-toggle" title="Morph the folded cube into a globe">
              <input
                type="checkbox"
                checked={spherify}
                onChange={(e) => setSpherify(e.target.checked)}
              />
              Globe (spherify)
            </label>
          )}
        </div>
      </div>
    </div>
  );
}
