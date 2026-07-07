import { create } from 'zustand';

export interface SpeciesInfo {
  id: number;
  name: string;
  color: string;
  size: number;
  plugin: string;
}

export interface SyncInfo {
  protocol: number;
  tick: number;
  epoch: number;
  size: number;
  seaLevel: number;
  heightScale: number;
  species: SpeciesInfo[];
}

export interface Weather {
  temp: number;
  precip: number;
  windX: number;
  windY: number;
}

export interface SimClock {
  dayFrac: number;
  seasonFrac: number;
  seasonIndex: number;
}

export interface FrameInfo {
  tick: number;
  epoch: number;
  tps: number;
  entities: number;
  weather: Weather;
  clock: SimClock;
}

export type ConnStatus = 'connecting' | 'connected' | 'reconnecting';

interface GenesisStore {
  status: ConnStatus;
  sync: SyncInfo | null;
  frame: FrameInfo | null;
  /** Live per-species alive counts, recomputed from each binary entity frame. */
  speciesCounts: Record<number, number>;
  hiddenSpecies: Record<number, boolean>;
  /** Visibility per stratum: [underground, surface, sky]. Underground on = terrain x-ray. */
  strata: [boolean, boolean, boolean];
  /** Bumped whenever a kind-1 terrain frame arrives (data itself lives outside React). */
  terrainVersion: number;

  setStatus: (status: ConnStatus) => void;
  setSync: (sync: SyncInfo) => void;
  setFrame: (frame: FrameInfo) => void;
  setSpeciesCounts: (counts: Record<number, number>) => void;
  toggleSpecies: (id: number) => void;
  toggleStratum: (index: 0 | 1 | 2) => void;
  bumpTerrain: () => void;
}

export const useStore = create<GenesisStore>()((set) => ({
  status: 'connecting',
  sync: null,
  frame: null,
  speciesCounts: {},
  hiddenSpecies: {},
  strata: [false, true, true],
  terrainVersion: 0,

  setStatus: (status) => set({ status }),
  setSync: (sync) => set({ sync }),
  setFrame: (frame) => set({ frame }),
  setSpeciesCounts: (speciesCounts) => set({ speciesCounts }),
  toggleSpecies: (id) =>
    set((s) => ({
      hiddenSpecies: { ...s.hiddenSpecies, [id]: !s.hiddenSpecies[id] },
    })),
  toggleStratum: (index) =>
    set((s) => {
      const strata: [boolean, boolean, boolean] = [...s.strata];
      strata[index] = !strata[index];
      return { strata };
    }),
  bumpTerrain: () => set((s) => ({ terrainVersion: s.terrainVersion + 1 })),
}));
