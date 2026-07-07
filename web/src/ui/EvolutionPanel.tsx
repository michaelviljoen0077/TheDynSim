import { useCallback, useEffect, useRef, useState } from 'react';
import { CandidateCard } from './CandidateCard';
import './evolution.css';

// ---------------------------------------------------------------------------
// API types
// ---------------------------------------------------------------------------

export type GovernorStage =
  | 'idle'
  | 'reporting'
  | 'generating'
  | 'validating'
  | 'shadow'
  | 'scoring'
  | 'committing';

export interface GovernorStatus {
  configured: boolean;
  provider: string;
  stage: GovernorStage;
  cycle_id: string;
  detail: string;
}

export type CycleDecision = 'in_progress' | 'promoted' | 'no_change';

export interface CycleSummary {
  id: string;
  epoch: number;
  tick: number;
  decision: CycleDecision;
  provider: string;
  tokens_in: number;
  tokens_out: number;
  started_at: string | null;
  finished_at: string | null;
}

export type CandidateFate =
  | 'promoted'
  | 'scored'
  | 'rejected_validation'
  | 'rejected_shadow'
  | 'rejected_generation'
  | 'rejected_no_control'
  | 'promotion_failed';

export interface ValidationError {
  code: string;
  message: string;
  line: number | null;
}

export interface CandidateMeta {
  analysis?: string;
  hypothesis?: string;
  expected_outcome?: string;
  confidence?: number;
  species?: string;
  [key: string]: unknown;
}

export interface FitnessBreakdown {
  total: number;
  breakdown: Record<string, number>;
}

export interface Candidate {
  id: string;
  label: string;
  source?: string | null;
  plugin_source?: string | null;
  meta: CandidateMeta | null;
  validation: { ok: boolean; errors: ValidationError[] } | null;
  shadow_metrics: ({ reason?: string } & Record<string, unknown>) | null;
  fitness_breakdown: FitnessBreakdown | null;
  fitness: number | null;
  fate: CandidateFate;
}

export interface PluginInfo {
  name: string;
  status: 'live' | 'quarantined';
  species: string[];
  errors: number;
  lastError: string | null;
  tickMsEma: number | null;
}

interface RollbackResult {
  restoredTick?: number;
  epoch?: number;
  seconds?: number;
  error?: string;
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return (await r.json()) as T;
}

const POLL_MS = 3000;

function decisionBadgeClass(decision: CycleDecision): string {
  switch (decision) {
    case 'promoted':
      return 'evo-badge good';
    case 'in_progress':
      return 'evo-badge warn pulsing';
    case 'no_change':
      return 'evo-badge neutral';
  }
}

// ---------------------------------------------------------------------------
// panel
// ---------------------------------------------------------------------------

export function EvolutionPanel() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<GovernorStatus | null>(null);
  const [statusFailed, setStatusFailed] = useState(false);
  const [cycles, setCycles] = useState<CycleSummary[]>([]);
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<Record<string, Candidate[]>>({});
  const [evolving, setEvolving] = useState(false);
  const [rollbackMsg, setRollbackMsg] = useState<string | null>(null);

  const expandedRef = useRef<string | null>(null);
  expandedRef.current = expandedId;
  const rollbackTimer = useRef<number | undefined>(undefined);

  const loadCandidates = useCallback((cycleId: string) => {
    void fetchJson<Candidate[]>(`/api/cycles/${encodeURIComponent(cycleId)}/candidates`)
      .then((list) => setCandidates((c) => ({ ...c, [cycleId]: list })))
      .catch(() => undefined);
  }, []);

  // Poll governor status, cycles, plugins (and the expanded cycle's
  // candidates) every 3s — but only while the panel is open.
  useEffect(() => {
    if (!open) return;
    const poll = () => {
      void fetchJson<GovernorStatus>('/api/governor/status')
        .then((s) => {
          setStatus(s);
          setStatusFailed(false);
        })
        .catch(() => setStatusFailed(true));
      void fetchJson<CycleSummary[]>('/api/cycles')
        .then(setCycles)
        .catch(() => undefined);
      void fetchJson<PluginInfo[]>('/api/plugins')
        .then(setPlugins)
        .catch(() => undefined);
      const expanded = expandedRef.current;
      if (expanded !== null) loadCandidates(expanded);
    };
    poll();
    const timer = window.setInterval(poll, POLL_MS);
    return () => window.clearInterval(timer);
  }, [open, loadCandidates]);

  useEffect(() => () => window.clearTimeout(rollbackTimer.current), []);

  const toggleCycle = (id: string) => {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    loadCandidates(id);
  };

  const evolveNow = () => {
    setEvolving(true);
    void fetchJson<{ started: boolean; status: GovernorStatus }>('/api/governor/cycle', {
      method: 'POST',
    })
      .then((r) => setStatus(r.status))
      .catch(() => undefined)
      .finally(() => setEvolving(false));
  };

  const flashRollback = (msg: string) => {
    setRollbackMsg(msg);
    window.clearTimeout(rollbackTimer.current);
    rollbackTimer.current = window.setTimeout(() => setRollbackMsg(null), 5000);
  };

  const rollback = () => {
    if (!window.confirm('Roll back the simulation to the last snapshot?')) return;
    void fetchJson<RollbackResult>('/api/control/rollback', { method: 'POST' })
      .then((r) => {
        if (r.error !== undefined) flashRollback(`rollback failed: ${r.error}`);
        else
          flashRollback(
            `restored tick ${r.restoredTick ?? '?'} (epoch ${r.epoch ?? '?'}, ${r.seconds ?? '?'}s back)`,
          );
      })
      .catch(() => flashRollback('rollback request failed'));
  };

  if (!open) {
    return (
      <button className="evo-toggle" onClick={() => setOpen(true)} title="Open evolution panel">
        ◂ EVOLUTION
      </button>
    );
  }

  const offline = statusFailed || (status !== null && !status.configured);
  const stage: GovernorStage = status?.stage ?? 'idle';
  const stageActive = status !== null && status.configured && stage !== 'idle';
  const evolveDisabled = evolving || offline || status === null || stage !== 'idle';

  return (
    <div className="evo-panel">
      <div className="evo-header">
        EVOLUTION
        <button
          className="evo-collapse"
          onClick={() => setOpen(false)}
          title="Collapse panel"
        >
          ▸
        </button>
      </div>

      <div className="evo-section">
        <div className="evo-section-title">GOVERNOR</div>
        {offline ? (
          <div className="evo-offline">governor offline — Ollama unreachable</div>
        ) : (
          <div className="evo-status-row">
            <span className="evo-provider">{status?.provider ?? '…'}</span>
            <span className={`evo-stage-pill ${stageActive ? 'active' : ''}`}>{stage}</span>
            <button
              className="evo-btn evo-evolve"
              onClick={evolveNow}
              disabled={evolveDisabled}
            >
              EVOLVE NOW
            </button>
            <span className="evo-stage-detail">{status?.detail ?? ''}</span>
          </div>
        )}
      </div>

      <div className="evo-section evo-timeline">
        <div className="evo-section-title">CYCLES</div>
        {cycles.length === 0 && <div className="evo-empty">no evolution cycles yet</div>}
        {cycles.map((c) => (
          <div key={c.id}>
            <button
              className={`evo-cycle-row ${expandedId === c.id ? 'expanded' : ''}`}
              onClick={() => toggleCycle(c.id)}
            >
              <span className="evo-cycle-caret">{expandedId === c.id ? '▾' : '▸'}</span>
              <span className="evo-cycle-tick">t{c.tick}</span>
              <span className={decisionBadgeClass(c.decision)}>
                {c.decision.replace('_', ' ')}
              </span>
              <span
                className="evo-cycle-tokens"
                title={`tokens in → out (${c.provider})`}
              >
                {c.tokens_in}→{c.tokens_out}
              </span>
            </button>
            {expandedId === c.id && (
              <div className="evo-candidates">
                {candidates[c.id] === undefined && (
                  <div className="evo-empty">loading candidates…</div>
                )}
                {candidates[c.id] !== undefined && candidates[c.id].length === 0 && (
                  <div className="evo-empty">no candidates</div>
                )}
                {candidates[c.id]?.map((cand) => (
                  <CandidateCard key={cand.id} candidate={cand} />
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="evo-section evo-plugins">
        <div className="evo-section-title">PLUGINS</div>
        {plugins.length === 0 && <div className="evo-empty">no plugins loaded</div>}
        {plugins.map((p) => (
          <div
            key={p.name}
            className="evo-plugin-row"
            title={
              p.status === 'quarantined' && p.lastError !== null
                ? p.lastError
                : undefined
            }
          >
            <span className={`evo-badge ${p.status === 'live' ? 'good' : 'bad'}`}>
              {p.status}
            </span>
            <span className="evo-plugin-name">{p.name}</span>
            <span className="evo-plugin-species">{p.species.join(', ')}</span>
            <span className="evo-plugin-ms">
              {typeof p.tickMsEma === 'number' ? `${p.tickMsEma.toFixed(2)}ms` : '—'}
            </span>
          </div>
        ))}
        <div className="evo-plugins-foot">
          <button className="evo-btn danger" onClick={rollback}>
            ROLLBACK
          </button>
          {rollbackMsg !== null && <span className="evo-rollback-msg">{rollbackMsg}</span>}
        </div>
      </div>
    </div>
  );
}
