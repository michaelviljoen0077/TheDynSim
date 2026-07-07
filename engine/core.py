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
        self.commands.apply(self.store, float(self.config.size))
        alive = self.store.alive
        self.store.age[alive] += 1
        self.tick += 1

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.step()


def hash_name(name: str) -> int:
    """Stable string hash (Python's hash() is salted per process — unusable here)."""
    h = 2166136261
    for b in name.encode():
        h = (h ^ b) * 16777619 & 0xFFFFFFFF
    return h
