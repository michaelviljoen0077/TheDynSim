// Shared typed REST helpers for the Observatory panels (Epic 4).
// The hot 3D path never touches this — these are poll-driven side panels.

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return (await r.json()) as T;
}

// -- /api/metrics -----------------------------------------------------------

export interface MetricsSnapshot {
  tick: number;
  epoch: number;
  populations: Record<string, number>;
  shannonDiversity: number;
  floraDensity: number;
  deathsByCause: Record<string, number>;
}

// -- /api/interventions -----------------------------------------------------

export interface Intervention {
  epoch: number;
  tick: number;
  kind: string; // promotion | rollback | control_failed | ...
  plugin_name: string;
  details: Record<string, unknown>;
  created_at: number;
}

// -- /api/lab/plugins -------------------------------------------------------

export interface LabPlugin {
  key: string;
  name: string;
  source: string;
  fate: string; // live | quarantined | promoted | scored | rejected_* | ...
  species: string[];
  lineageParent: string | null;
  origin: 'live' | 'candidate';
  fitness: number | null;
  candidateId: string | null;
}

// -- /api/entity/{id} -------------------------------------------------------

export interface EntityDetail {
  id: number;
  species: string;
  speciesId: number;
  plugin: string;
  energy: number;
  age: number;
  x: number;
  y: number;
  z: number;
  stratum: number;
  error?: string;
}

export const STRATUM_NAMES = ['underground', 'surface', 'sky'] as const;
