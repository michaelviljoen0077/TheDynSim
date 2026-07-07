import { useState } from 'react';
import type { Candidate, CandidateFate } from './EvolutionPanel';

// Map a candidate fate to a badge colour class defined in evolution.css.
function fateBadgeClass(fate: CandidateFate): string {
  switch (fate) {
    case 'promoted':
      return 'evo-badge good';
    case 'scored':
      return 'evo-badge info';
    case 'promotion_failed':
      return 'evo-badge warn';
    case 'rejected_validation':
    case 'rejected_shadow':
    case 'rejected_generation':
    case 'rejected_no_control':
      return 'evo-badge bad';
  }
}

function fateLabel(fate: CandidateFate): string {
  return fate.replace(/_/g, ' ');
}

interface FitnessBarsProps {
  breakdown: Record<string, number>;
}

function FitnessBars({ breakdown }: FitnessBarsProps) {
  const entries = Object.entries(breakdown);
  if (entries.length === 0) return null;
  const maxAbs = Math.max(1e-9, ...entries.map(([, v]) => Math.abs(v)));
  return (
    <div className="evo-fitness-bars">
      {entries.map(([key, value]) => {
        const pct = Math.min(100, (Math.abs(value) / maxAbs) * 100);
        const sign = value >= 0 ? 'pos' : 'neg';
        return (
          <div key={key} className="evo-fitness-row">
            <span className="evo-fitness-key" title={key}>
              {key}
            </span>
            <span className="evo-fitness-track">
              <span className={`evo-fitness-bar ${sign}`} style={{ width: `${pct}%` }} />
            </span>
            <span className="evo-fitness-val">{value.toFixed(2)}</span>
          </div>
        );
      })}
    </div>
  );
}

interface CandidateCardProps {
  candidate: Candidate;
}

export function CandidateCard({ candidate }: CandidateCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [showSource, setShowSource] = useState(false);

  const meta = candidate.meta ?? {};
  const hypothesis = meta.hypothesis ?? '';
  const validationErrors =
    candidate.validation !== null && !candidate.validation.ok
      ? candidate.validation.errors
      : [];
  const shadowReason =
    candidate.fate === 'rejected_shadow'
      ? candidate.shadow_metrics?.reason ?? null
      : null;
  const source = candidate.plugin_source ?? candidate.source ?? null;

  return (
    <div className="evo-card">
      <button className="evo-card-head" onClick={() => setExpanded((e) => !e)}>
        <span className={fateBadgeClass(candidate.fate)}>{fateLabel(candidate.fate)}</span>
        <span className="evo-card-label" title={candidate.label}>
          {candidate.label}
        </span>
        {candidate.fitness !== null && (
          <span className="evo-card-fitness">{candidate.fitness.toFixed(2)}</span>
        )}
      </button>

      {hypothesis !== '' && <div className="evo-card-hypothesis">{hypothesis}</div>}

      {expanded && (
        <div className="evo-card-details">
          {meta.analysis !== undefined && meta.analysis !== '' && (
            <div>
              <div className="evo-detail-k">analysis</div>
              <div className="evo-detail-text">{meta.analysis}</div>
            </div>
          )}

          {meta.expected_outcome !== undefined && meta.expected_outcome !== '' && (
            <div>
              <div className="evo-detail-k">expected outcome</div>
              <div className="evo-detail-text">{meta.expected_outcome}</div>
            </div>
          )}

          {candidate.fitness_breakdown !== null && (
            <div>
              <div className="evo-detail-k">fitness breakdown</div>
              <FitnessBars breakdown={candidate.fitness_breakdown.breakdown} />
            </div>
          )}

          {validationErrors.length > 0 && (
            <div>
              <div className="evo-detail-k">validation errors</div>
              {validationErrors.map((err, i) => (
                <div key={i} className="evo-verr">
                  <span className="code">{err.code}</span>
                  {err.line !== null ? ` (line ${err.line})` : ''}: {err.message}
                </div>
              ))}
            </div>
          )}

          {shadowReason !== null && (
            <div>
              <div className="evo-detail-k">shadow rejection</div>
              <div className="evo-verr">{shadowReason}</div>
            </div>
          )}

          {source !== null && (
            <>
              <button
                className="evo-btn evo-source-toggle"
                onClick={() => setShowSource((s) => !s)}
              >
                {showSource ? 'hide source' : 'view source'}
              </button>
              {showSource && <pre className="evo-source">{source}</pre>}
            </>
          )}
        </div>
      )}
    </div>
  );
}
