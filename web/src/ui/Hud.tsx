import { useEffect, useRef, useState } from 'react';
import { useStore } from '../state/store';
import './hud.css';

const SEASONS = ['Spring', 'Summer', 'Autumn', 'Winter'];
const STRATA: { index: 0 | 1 | 2; label: string }[] = [
  { index: 1, label: 'Surface' },
  { index: 2, label: 'Sky' },
];
const OVERLAYS: { value: 'none' | 'flora' | 'water' | 'plankton'; label: string }[] = [
  { value: 'none', label: 'Terrain' },
  { value: 'flora', label: 'Flora' },
  { value: 'water', label: 'Water' },
  { value: 'plankton', label: 'Plankton' },
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

function god(path: 'spawn' | 'cull' | 'flora', body: object): void {
  void fetch(`/api/god/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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
  const [godSpecies, setGodSpecies] = useState('');
  const [spawnCount, setSpawnCount] = useState(20);
  const [capsEnabled, setCapsEnabled] = useState(true);
  const activeGodSpecies = godSpecies || species[0]?.name || '';

  const toggleCaps = (enabled: boolean) => {
    setCapsEnabled(enabled); // optimistic
    void fetch('/api/god/caps', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
      .then((r) => r.json())
      .then((s: { capsEnabled?: boolean }) => {
        if (typeof s.capsEnabled === 'boolean') setCapsEnabled(s.capsEnabled);
      })
      .catch(() => undefined);
  };

  useEffect(() => {
    void fetch('/api/state')
      .then((r) => r.json())
      .then((s: { targetTps: number; capsEnabled?: boolean }) => {
        if (typeof s.targetTps === 'number') setTps(Math.round(s.targetTps));
        if (typeof s.capsEnabled === 'boolean') setCapsEnabled(s.capsEnabled);
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
        {frame?.clock.calendar && (
          <div className="clock-line calendar-line">
            <span className="glyph">📅</span>
            <span className="clock-time">
              {`Y${frame.clock.calendar.year} · M${frame.clock.calendar.month} · D${frame.clock.calendar.day}`}
            </span>
            <span className="season">
              {SEASONS[(frame?.clock.seasonIndex ?? 0) % SEASONS.length]}
            </span>
          </div>
        )}
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

      <div className="panel-col-bl">
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

      <div className="panel panel-god">
        <div className="panel-title">⚡ GOD MODE</div>
        <div className="god-row">
          <select
            className="god-select"
            value={activeGodSpecies}
            onChange={(e) => setGodSpecies(e.target.value)}
            disabled={species.length === 0}
            title="Species to spawn or cull"
          >
            {species.length === 0 && <option>no species</option>}
            {species.map((sp) => (
              <option key={sp.id} value={sp.name}>
                {sp.name}
              </option>
            ))}
          </select>
          <input
            className="god-count"
            type="number"
            min={1}
            max={2000}
            value={spawnCount}
            onChange={(e) => setSpawnCount(Math.max(1, Math.min(2000, Number(e.target.value))))}
            title="How many to spawn"
          />
        </div>
        <div className="button-row">
          <button
            disabled={activeGodSpecies === ''}
            onClick={() => god('spawn', { species: activeGodSpecies, count: spawnCount })}
            title={`Spawn ${spawnCount} ${activeGodSpecies} across the world`}
          >
            Spawn
          </button>
          <button
            className="danger"
            disabled={activeGodSpecies === ''}
            onClick={() => god('cull', { species: activeGodSpecies })}
            title={`Cull half of all ${activeGodSpecies} (a population shock, not extinction)`}
          >
            Cull ½
          </button>
        </div>
        <div className="god-row god-flora-row">
          <span className="k">flora</span>
          <button
            className="overlay-btn"
            onClick={() => god('flora', { mode: 'bloom', amount: 0.4 })}
            title="Green the whole world (transient — dynamics settle it back)"
          >
            🌱 Bloom
          </button>
          <button
            className="overlay-btn danger"
            onClick={() => god('flora', { mode: 'scorch', amount: 0.9 })}
            title="Scorch all vegetation (grows back over time)"
          >
            🔥 Scorch
          </button>
        </div>
        <label
          className="stratum-toggle god-caps-row"
          title="Hard population ceilings (per-species/per-plugin). Turn off to let populations grow until the SOFT controls — crowding stress, food, breeding cost — limit them. Those soft controls stay on either way."
        >
          <input
            type="checkbox"
            checked={capsEnabled}
            onChange={(e) => toggleCaps(e.target.checked)}
          />
          population caps
        </label>
      </div>
      </div>
    </div>
  );
}
