import { useStore, type FrameInfo, type SyncInfo } from '../state/store';
import { live, type EntityFrame } from './frames';

// Wire protocol v2 (docs/protocol.md). All binary little-endian.
// Common header: 4 x uint32 = [kind, tick, epoch, n].
// Terrain/field frames carry a face index; the entity frame carries a per-entity
// face byte. This (flat/wrap) renderer only shows face 0; the cube renderer folds
// all six faces (Layer 5).

const HEADER_BYTES = 16;
const RECONNECT_DELAY_MS = 1000;

let socket: WebSocket | null = null;
let started = false;

export function connect(): void {
  if (started) return; // idempotent (StrictMode / HMR safe)
  started = true;
  open();
}

function open(): void {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';
  socket = ws;

  ws.onopen = () => useStore.getState().setStatus('connected');
  ws.onmessage = (ev: MessageEvent) => {
    if (typeof ev.data === 'string') {
      handleText(ev.data);
    } else {
      handleBinary(ev.data as ArrayBuffer);
    }
  };
  ws.onclose = () => {
    if (socket !== ws) return;
    socket = null;
    useStore.getState().setStatus('reconnecting');
    window.setTimeout(open, RECONNECT_DELAY_MS);
  };
  ws.onerror = () => ws.close();
}

function handleText(data: string): void {
  const msg = JSON.parse(data) as { t: string };
  const store = useStore.getState();
  if (msg.t === 'sync') {
    store.setSync(msg as unknown as SyncInfo);
  } else if (msg.t === 'frame') {
    store.setFrame(msg as unknown as FrameInfo);
  }
}

function handleBinary(buf: ArrayBuffer): void {
  const dv = new DataView(buf);
  const kind = dv.getUint32(0, true);
  const tick = dv.getUint32(4, true);
  const epoch = dv.getUint32(8, true);
  const n = dv.getUint32(12, true);

  if (kind === 1) decodeTerrain(buf, n);
  else if (kind === 2) decodeEntities(buf, tick, epoch, n);
  else if (kind === 3) decodeField(buf, dv, n);
}

// kind 1: uint32 face, float32 height[S*S], uint8 water[S*S].
// Flat/wrap sends face 0 only; a cube sends all six. We keep every face so the
// cube renderer can fold them; the flat path still reads face 0 via live.terrain.
function decodeTerrain(buf: ArrayBuffer, size: number): void {
  const dv = new DataView(buf);
  const face = dv.getUint32(HEADER_BYTES, true);
  if (face < 0 || face >= live.terrains.length) return;
  const cells = size * size;
  let off = HEADER_BYTES + 4;
  const height = new Float32Array(buf.slice(off, off + 4 * cells));
  off += 4 * cells;
  const water = new Uint8Array(buf.slice(off, off + cells));
  const data = { size, height, water };
  live.terrains[face] = data;
  if (face === 0) live.terrain = data;
  useStore.getState().bumpTerrain();
}

// kind 2: uint32 id, f32 x/y/z/energy, u16 species, u8 stratum, u8 face (each [N]).
function decodeEntities(buf: ArrayBuffer, tick: number, epoch: number, n: number): void {
  let off = HEADER_BYTES;
  const id = new Uint32Array(buf.slice(off, off + 4 * n));
  off += 4 * n;
  const x = new Float32Array(buf.slice(off, off + 4 * n));
  off += 4 * n;
  const y = new Float32Array(buf.slice(off, off + 4 * n));
  off += 4 * n;
  const z = new Float32Array(buf.slice(off, off + 4 * n));
  off += 4 * n;
  const energy = new Float32Array(buf.slice(off, off + 4 * n));
  off += 4 * n;
  const species = new Uint16Array(buf.slice(off, off + 2 * n));
  off += 2 * n;
  const stratum = new Uint8Array(buf.slice(off, off + n));
  off += n;
  // Per-entity cube face (0 on flat/wrap). Kept so the cube renderer can place
  // each instance on its face and snap interpolation across a face change.
  const face = new Uint8Array(buf.slice(off, off + n));

  const now = performance.now();
  // Never interpolate across an epoch boundary (reset/rollback teleports).
  const prev = live.curr && live.curr.epoch === epoch ? live.curr : null;

  // Match entities by generational id once per network frame so the 60 FPS
  // render loop can lerp prev->curr with a plain indexed lookup.
  const prevIndex = new Int32Array(n).fill(-1);
  if (prev) {
    const map = new Map<number, number>();
    for (let i = 0; i < prev.n; i++) map.set(prev.id[i], i);
    for (let i = 0; i < n; i++) {
      const j = map.get(id[i]);
      if (j !== undefined) prevIndex[i] = j;
    }
    live.frameInterval = Math.min(500, Math.max(33, now - prev.recvTime));
  }

  const frame: EntityFrame = {
    tick, epoch, n, id, x, y, z, energy, species, stratum, face, prevIndex, recvTime: now,
  };
  live.prev = prev;
  live.curr = frame;

  const counts: Record<number, number> = {};
  for (let i = 0; i < n; i++) {
    counts[species[i]] = (counts[species[i]] ?? 0) + 1;
  }
  useStore.getState().setSpeciesCounts(counts);
}

// kind 3: uint32 fieldId, uint32 face, then uint8 values[S*S].
// We keep flora (fieldId 0) for every face; the cube renderer tints all six,
// the flat path reads live.flora (face 0). Other fields are unused for now.
function decodeField(buf: ArrayBuffer, dv: DataView, size: number): void {
  const fieldId = dv.getUint32(HEADER_BYTES, true);
  const face = dv.getUint32(HEADER_BYTES + 4, true);
  if (fieldId !== 0 || face < 0 || face >= live.floras.length) return;
  const off = HEADER_BYTES + 8;
  const values = new Uint8Array(buf.slice(off, off + size * size));
  live.floras[face] = values;
  if (face === 0) live.flora = values;
  live.floraVersion++;
}
