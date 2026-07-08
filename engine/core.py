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
from engine.cube import CubeGeometry
from engine.entities import SKY, SURFACE, UNDERGROUND, EntityStore, SpeciesRegistry, make_handle
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
        self.geom = CubeGeometry(config.size) if config.cube else None
        # cube queries use a global 3D index (seamless across faces); flat/wrap
        # use the 2D grid hash (with toroidal wrap for "wrap")
        if config.cube:
            from engine.spatial3d import Spatial3D
            self.spatial3d: Spatial3D | None = Spatial3D(config.size)
            self.spatial = None
        else:
            self.spatial = SpatialHash(float(config.size), wrap=config.wrap)
            self.spatial3d = None
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
        # extinction ledger: species that were alive and died out (snapshot-included)
        self.extinct: list[dict] = []
        self._predation_marks: set[int] = set()   # transient within a tick
        self._drowning_marks: set[int] = set()    # transient within a tick
        self._crowding_marks: set[int] = set()    # transient within a tick
        if _generate:
            if config.cube:
                from engine.cube_fields import CubeFlora, CubeTerrain, CubeWeather
                self.terrain = CubeTerrain.generate(
                    self.rng, config.size, config.terrain_octaves, config.sea_level_quantile
                )
                self.weather = CubeWeather(config.size)
                self.flora = CubeFlora.generate(self.rng, config.size, self.terrain)
            else:
                self.terrain = Terrain.generate(
                    self.rng, config.size, config.terrain_octaves, config.sea_level_quantile
                )
                self.weather = Weather(config.size)
                self.flora = Flora.generate(self.rng, config.size, self.terrain)

    def daylight_at(self, face: int, x: float, y: float) -> float:
        """Local solar illumination at a location, in [-1, 1] (1 = sun overhead,
        <0 = night). On a cube this is per-longitude (the sun is fixed, the planet
        spins); on flat/wrap it's the single global day/night. Lets plugins be
        diurnal/nocturnal or wake in danger."""
        if self.geom is None:
            return float(np.sin(2 * np.pi * self.day_frac - np.pi / 2))
        return self.weather.local_sun(int(face), int(x), int(y), self.day_frac)

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

    @property
    def calendar(self) -> dict:
        """1-based (year, month, day) elapsed since the run began."""
        cfg = self.config
        total_days = self.tick // cfg.ticks_per_day
        day_of_year = total_days % cfg.days_per_year
        return {
            "year": total_days // cfg.days_per_year + 1,
            "month": day_of_year // cfg.days_per_month + 1,
            "day": day_of_year % cfg.days_per_month + 1,
        }

    # -- plugin RNG streams (deterministic per (run_seed, plugin_name)) ------

    def plugin_rng(self, plugin_name: str) -> np.random.Generator:
        if plugin_name not in self.plugin_rngs:
            seed = np.random.SeedSequence((self.config.seed, hash_name(plugin_name)))
            self.plugin_rngs[plugin_name] = np.random.default_rng(seed)
            self.plugin_stores.setdefault(plugin_name, {})
        return self.plugin_rngs[plugin_name]

    # -- tick -----------------------------------------------------------------

    def step(self) -> None:
        # weather/flora change slowly; on big cubes they're the dominant cost, so
        # step them every field_step_every ticks (deterministic; =1 for flat/wrap)
        if self.tick % self.config.field_step_every == 0:
            self.weather.step(self.rng, self.terrain, self.day_frac, self.season_frac)
            self.flora.step(self.terrain, self.weather, self.season_frac)
        if self.geom is not None:
            self.spatial3d.rebuild(self.store)
        else:
            self.spatial.rebuild(self.store)
        for hook in self.tick_hooks:  # PluginHost.on_tick attaches here (Epic 2)
            hook(self)
        self.commands.apply(self.store, float(self.config.size), self._predation_marks,
                            flora=self.flora.density, speeds=self.registry.speeds_array(),
                            water=self.terrain.water_mask,
                            swim_speeds=self.registry.swim_speeds_array(),
                            wrap=self.config.wrap, geom=self.geom)
        self._water_effects()
        self._crowding_stress()
        self._death_sweep()
        self._old_age_sweep()
        alive = self.store.alive
        self.store.age[alive] += 1
        self.tick += 1

    def mark_predation(self, row: int) -> None:
        self._predation_marks.add(row)

    def record_death(self, species_name: str, cause: str) -> None:
        by_cause = self.deaths.setdefault(species_name, {})
        by_cause[cause] = by_cause.get(cause, 0) + 1

    def record_extinction(self, species_name: str, plugin_name: str) -> None:
        """Move a species onto the extinction ledger (once); PluginHost.reap calls this."""
        if any(e["species"] == species_name for e in self.extinct):
            return
        self.extinct.append({
            "species": species_name, "plugin": plugin_name, "tick": self.tick,
            "epoch": self.epoch,
        })

    def _water_effects(self) -> None:
        """Surface entities on open water: swimmers are fine, non-swimmers drown."""
        store = self.store
        rows = np.flatnonzero(store.alive & (store.stratum == SURFACE))
        if rows.size == 0:
            return
        ix = store.px[rows].astype(np.int32)
        iy = store.py[rows].astype(np.int32)
        if self.geom is not None:
            on_water = self.terrain.water_mask[store.face[rows], ix, iy] > 0.5
        else:
            on_water = self.terrain.water_mask[ix, iy] > 0.5
        if not np.any(on_water):
            return
        wet = rows[on_water]
        swim = self.registry.swim_speeds_array()[store.species_id[wet]]
        drowning = wet[swim <= 0.0]
        store.energy[drowning] -= 0.8
        self._drowning_marks.update(drowning.tolist())

    def _crowding_stress(self) -> None:
        """Density-dependent energy drain — the non-predator overpopulation control.

        Each entity crowded by more than `crowding_softcap` same-species neighbours
        within `crowding_radius` loses energy proportional to the excess (models
        competition/disease/stress). Proportional and self-limiting: as density
        falls the drain vanishes, so it throttles growth toward a soft carrying
        capacity without the boom-bust of a hard cap or a lethal plague.
        """
        cfg = self.config
        if cfg.crowding_penalty <= 0.0:
            return
        store = self.store
        alive = np.flatnonzero(store.alive)
        if alive.size == 0:
            return
        # per-cell same-species density (vectorized): key = (species, stratum, cell).
        # The spatial cell (~8) is close to crowding_radius, so same-cell count is a
        # cheap, O(n) proxy for local density — no per-entity neighbour scan.
        cell = max(cfg.crowding_radius, self.spatial.cell if self.spatial else 8.0)
        gx = (store.px[alive] / cell).astype(np.int64)
        gy = (store.py[alive] / cell).astype(np.int64)
        ncell = int(self.config.size / cell) + 2
        key = ((store.species_id[alive].astype(np.int64) * 4 + store.stratum[alive])
               * ncell * ncell + gx * ncell + gy)
        if self.geom is not None:  # keep faces from cross-counting as crowding
            key = key * 6 + store.face[alive].astype(np.int64)
        _uniq, inverse, counts = np.unique(key, return_inverse=True, return_counts=True)
        density = counts[inverse]                       # neighbours+self sharing the cell
        excess = density - 1 - cfg.crowding_softcap     # exclude self
        stressed = excess > 0
        if not np.any(stressed):
            return
        rows = alive[stressed]
        store.energy[rows] -= cfg.crowding_penalty * excess[stressed].astype(np.float32)
        self._crowding_marks.update(rows.tolist())

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
            elif row in self._crowding_marks:
                cause = "crowding"
            else:
                cause = "starvation"
            handle = make_handle(row, store.generation[row])
            store.remove(handle)
            self.record_death(species, cause)
        self._predation_marks.clear()
        self._drowning_marks.clear()
        self._crowding_marks.clear()

    def _old_age_sweep(self) -> None:
        """Engine-mediated old-age death: entities older than their species lifespan die."""
        lifespans = self.registry.lifespans_array()
        if not np.any(lifespans > 0):
            return
        store = self.store
        alive = np.flatnonzero(store.alive)
        if alive.size == 0:
            return
        limits = lifespans[store.species_id[alive]]
        old = alive[(limits > 0) & (store.age[alive] >= limits)]
        for row in old.tolist():
            species = self.registry.by_id[int(store.species_id[row])].name
            store.remove(make_handle(row, store.generation[row]))
            self.record_death(species, "old_age")

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.step()


def hash_name(name: str) -> int:
    """Stable string hash (Python's hash() is salted per process — unusable here)."""
    h = 2166136261
    for b in name.encode():
        h = (h ^ b) * 16777619 & 0xFFFFFFFF
    return h
