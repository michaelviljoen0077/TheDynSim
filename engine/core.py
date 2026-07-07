"""World: the deterministic kernel. Owns clock, RNG, fields, entities, command buffer.

Step order is fixed and load-bearing for determinism:
  clock -> weather -> flora -> (plugin on_tick, Epic 2) -> command buffer -> aging.
Every random draw goes through `self.rng` or a per-plugin stream in `self.plugin_rngs`
— both are snapshot-included (coding standards #4/#8).
"""

from __future__ import annotations

import numpy as np

from engine.commands import CommandBuffer
from engine.config import WorldConfig
from engine.entities import SKY, SURFACE, UNDERGROUND, EntityStore, SpeciesRegistry
from engine.fields import Flora, Terrain, Weather
from engine.spatial import SpatialHash

STRATA = {"underground": UNDERGROUND, "surface": SURFACE, "sky": SKY}


class World:
    UNDERGROUND = UNDERGROUND
    SURFACE = SURFACE
    SKY = SKY

    def __init__(self, config: WorldConfig, _generate: bool = True) -> None:
        self.config = config
        self.tick = 0
        self.epoch = 0
        self.rng = np.random.default_rng(config.seed)
        self.registry = SpeciesRegistry(config.max_prop_slots)
        self.store = EntityStore(config.initial_capacity, config.max_prop_slots)
        self.commands = CommandBuffer()
        self.spatial = SpatialHash(float(config.size))
        # plugin machinery placeholders (Epic 2) — snapshot-complete from day one
        self.plugin_rngs: dict[str, np.random.Generator] = {}
        self.plugin_stores: dict[str, dict[str, float | int | str]] = {}
        # tick hooks: where PluginHost.on_tick attaches (code wiring, not state — not snapshotted)
        self.tick_hooks: list = []
        # death ledger: species name -> cause -> count (snapshot-included, feeds reports)
        self.deaths: dict[str, dict[str, int]] = {}
        # live plugin set (name/source/meta/status) maintained by PluginHost; snapshot-included
        # so rollback restores world + plugin set through one mechanism
        self.plugin_manifest: list[dict] = []
        self._predation_marks: set[int] = set()   # transient within a tick
        self._drowning_marks: set[int] = set()    # transient within a tick
        if _generate:
            self.terrain = Terrain.generate(
                self.rng, config.size, config.terrain_octaves, config.sea_level_quantile
            )
            self.weather = Weather(config.size)
            self.flora = Flora.generate(self.rng, config.size, self.terrain)

    # -- clock ---------------------------------------------------------------

    @property
    def day_frac(self) -> float:
        return (self.tick % self.config.ticks_per_day) / self.config.ticks_per_day

    @property
    def season_index(self) -> int:
        return (self.tick // self.config.ticks_per_season) % self.config.seasons_per_year

    @property
    def season_frac(self) -> float:
        return (self.tick % self.config.ticks_per_year) / self.config.ticks_per_year

    # -- plugin RNG streams (deterministic per (run_seed, plugin_name)) ------

    def plugin_rng(self, plugin_name: str) -> np.random.Generator:
        if plugin_name not in self.plugin_rngs:
            seed = np.random.SeedSequence((self.config.seed, hash_name(plugin_name)))
            self.plugin_rngs[plugin_name] = np.random.default_rng(seed)
            self.plugin_stores.setdefault(plugin_name, {})
        return self.plugin_rngs[plugin_name]

    # -- tick -----------------------------------------------------------------

    def step(self) -> None:
        self.weather.step(self.rng, self.terrain, self.day_frac, self.season_frac)
        self.flora.step(self.terrain, self.weather, self.season_frac)
        self.spatial.rebuild(self.store)
        for hook in self.tick_hooks:  # PluginHost.on_tick attaches here (Epic 2)
            hook(self)
        self.commands.apply(self.store, float(self.config.size), self._predation_marks,
                            flora=self.flora.density, speeds=self.registry.speeds_array(),
                            water=self.terrain.water_mask,
                            swim_speeds=self.registry.swim_speeds_array())
        self._water_effects()
        self._death_sweep()
        alive = self.store.alive
        self.store.age[alive] += 1
        self.tick += 1

    def mark_predation(self, row: int) -> None:
        self._predation_marks.add(row)

    def record_death(self, species_name: str, cause: str) -> None:
        by_cause = self.deaths.setdefault(species_name, {})
        by_cause[cause] = by_cause.get(cause, 0) + 1

    def _water_effects(self) -> None:
        """Surface entities on open water: swimmers are fine, non-swimmers drown."""
        store = self.store
        rows = np.flatnonzero(store.alive & (store.stratum == SURFACE))
        if rows.size == 0:
            return
        ix = store.px[rows].astype(np.int32)
        iy = store.py[rows].astype(np.int32)
        on_water = self.terrain.water_mask[ix, iy] > 0.5
        if not np.any(on_water):
            return
        wet = rows[on_water]
        swim = self.registry.swim_speeds_array()[store.species_id[wet]]
        drowning = wet[swim <= 0.0]
        store.energy[drowning] -= 0.8
        self._drowning_marks.update(drowning.tolist())

    def _death_sweep(self) -> None:
        """Engine-mediated death: any entity at energy <= 0 dies, with cause attribution."""
        store = self.store
        dead = np.flatnonzero(store.alive & (store.energy <= 0.0))
        for row in dead.tolist():
            species = self.registry.by_id[int(store.species_id[row])].name
            if row in self._predation_marks:
                cause = "predation"
            elif row in self._drowning_marks:
                cause = "drowning"
            else:
                cause = "starvation"
            handle = (row << 16) | int(store.generation[row])
            store.remove(handle)
            self.record_death(species, cause)
        self._predation_marks.clear()
        self._drowning_marks.clear()

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.step()


def hash_name(name: str) -> int:
    """Stable string hash (Python's hash() is salted per process — unusable here)."""
    h = 2166136261
    for b in name.encode():
        h = (h ^ b) * 16777619 & 0xFFFFFFFF
    return h
