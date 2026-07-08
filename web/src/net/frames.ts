// Hot binary frame data lives in this module singleton, deliberately outside
// React state: the r3f render loop reads it directly at 60 FPS while the
// network layer overwrites it at ~10 Hz. Never put these arrays in Zustand.

export const N_FACES = 6;

export interface TerrainData {
  size: number;
  height: Float32Array; // row-major [x][y], values 0..1
  water: Uint8Array; // 1 = open water
}

export interface EntityFrame {
  tick: number;
  epoch: number;
  n: number;
  id: Uint32Array;
  x: Float32Array;
  y: Float32Array;
  z: Float32Array;
  energy: Float32Array;
  species: Uint16Array;
  stratum: Uint8Array;
  /** Cube face 0..5 per entity (always 0 on flat/wrap). */
  face: Uint8Array;
  /** prevIndex[i] = index of the same entity id in the previous frame, or -1. */
  prevIndex: Int32Array;
  /** performance.now() at receipt — interpolation clock. */
  recvTime: number;
}

interface LiveData {
  /** Face-0 terrain — the flat/wrap renderer reads this directly. */
  terrain: TerrainData | null;
  /** All six faces, indexed by face word. terrains[0] === terrain. */
  terrains: (TerrainData | null)[];
  /** Face-0 flora density (flat/wrap path). */
  flora: Uint8Array | null;
  /** All six faces' flora density (cube path). floras[0] === flora. */
  floras: (Uint8Array | null)[];
  floraVersion: number;
  prev: EntityFrame | null;
  curr: EntityFrame | null;
  /** Estimated ms between entity frames (interpolation window). */
  frameInterval: number;
  /** Animated cube->sphere morph amount 0..1, driven by the cube renderer. */
  spherify: number;
}

export const live: LiveData = {
  terrain: null,
  terrains: new Array<TerrainData | null>(N_FACES).fill(null),
  flora: null,
  floras: new Array<Uint8Array | null>(N_FACES).fill(null),
  floraVersion: 0,
  prev: null,
  curr: null,
  frameInterval: 100,
  spherify: 0,
};

/** Bilinear height sample (0..1) at fractional grid coordinates of a face. */
function sampleHeight(t: TerrainData | null, x: number, y: number): number {
  if (!t) return 0;
  const S = t.size;
  const cx = Math.min(Math.max(x, 0), S - 1.0001);
  const cy = Math.min(Math.max(y, 0), S - 1.0001);
  const x0 = Math.floor(cx);
  const y0 = Math.floor(cy);
  const fx = cx - x0;
  const fy = cy - y0;
  const h = t.height;
  const h00 = h[x0 * S + y0];
  const h10 = h[(x0 + 1) * S + y0];
  const h01 = h[x0 * S + y0 + 1];
  const h11 = h[(x0 + 1) * S + y0 + 1];
  return (
    h00 * (1 - fx) * (1 - fy) +
    h10 * fx * (1 - fy) +
    h01 * (1 - fx) * fy +
    h11 * fx * fy
  );
}

/** Bilinear height sample (0..1) on face 0 — flat/wrap renderer. */
export function heightAt(x: number, y: number): number {
  return sampleHeight(live.terrain, x, y);
}

/** Bilinear height sample (0..1) on a specific cube face. */
export function heightAtFace(face: number, x: number, y: number): number {
  return sampleHeight(live.terrains[face] ?? null, x, y);
}
