"""WorldAPI: the ONLY object a plugin receives. Capability-scoped facade.

Reads see tick-start state; every mutation — entity moves/spawns/energy and
shared-field consumption via `eat_flora` — goes through the command buffer and
applies at tick end, so plugin execution order can never leak between plugins.
Writes are permitted only on species the plugin declared; cross-species
interaction happens through engine-mediated `attack`. Quota violations raise
QuotaViolation with a machine-readable payload the repair round-trip and
notebook consume (FR8).
"""

from __future__ import annotations

from engine.core import World
from engine.entities import GEN_BITS, SKY, SURFACE, UNDERGROUND


class PluginError(Exception):
    """Base for errors raised to plugins; carries a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class QuotaViolation(PluginError):
    pass


class CapabilityViolation(PluginError):
    pass


class PluginStore:
    """Plugin-scoped persistent key-value state (snapshot-included), quota-capped."""

    def __init__(self, data: dict, max_keys: int) -> None:
        self._data = data
        self._max_keys = max_keys

    def get(self, key: str, default: float | int | str | None = None):
        return self._data.get(key, default)

    def set(self, key: str, value: float | int | str) -> None:
        if not isinstance(value, (int, float, str)):
            raise CapabilityViolation("store-type", "world.store values must be int/float/str")
        if key not in self._data and len(self._data) >= self._max_keys:
            raise QuotaViolation("store-quota", f"world.store is limited to {self._max_keys} keys")
        self._data[key] = value


class WorldAPI:
    UNDERGROUND = UNDERGROUND
    SURFACE = SURFACE
    SKY = SKY

    def __init__(self, world: World, plugin_name: str, plugin_id: int,
                 declared_species: list[str]) -> None:
        self._world = world
        self._plugin_name = plugin_name
        self._plugin_id = plugin_id
        self._declared = set(declared_species)
        self._spawns_this_tick = 0
        self._owned_alive = 0
        self.rng = world.plugin_rng(plugin_name)
        self.store = PluginStore(
            world.plugin_stores[plugin_name], world.config.max_store_keys
        )

    # -- internal helpers ------------------------------------------------------

    def _species(self, name: str):
        sp = self._world.registry.by_name.get(name)
        if sp is None:
            raise CapabilityViolation("unknown-species", f"species {name!r} is not registered")
        return sp

    def _owned(self, name: str):
        sp = self._species(name)
        if name not in self._declared:
            raise CapabilityViolation(
                "not-owned", f"plugin {self._plugin_name!r} does not own species {name!r}"
            )
        return sp

    def _row(self, handle: int) -> int:
        if not self._world.store.is_valid(handle):
            raise CapabilityViolation("stale-handle", "entity handle is no longer valid")
        return handle >> GEN_BITS

    def _owned_row(self, handle: int) -> int:
        row = self._row(handle)
        sp = self._world.registry.by_id[int(self._world.store.species_id[row])]
        if sp.name not in self._declared:
            raise CapabilityViolation(
                "not-owned", f"entity belongs to {sp.name!r}, not owned by {self._plugin_name!r}"
            )
        return row

    def on_tick_begin(self) -> None:
        self._spawns_this_tick = 0
        self._owned_alive = sum(
            int(self._world.store.alive_indices(sp.id).size)
            for name in self._declared
            if (sp := self._world.registry.by_name.get(name)) is not None
        )

    # -- species & lifecycle ---------------------------------------------------

    def register_species(self, name: str, size: float = 1.0, color: str = "#cccccc",
                         strata: tuple[int, ...] = (SURFACE,), props: tuple[str, ...] = ()) -> None:
        if name not in self._declared:
            raise CapabilityViolation(
                "undeclared-species",
                f"species {name!r} not in PLUGIN_META['species'] {sorted(self._declared)}",
            )
        self._world.registry.register(
            name, plugin=self._plugin_name, size=size, color=color,
            strata=tuple(strata), props=tuple(props),
        )

    def spawn(self, species: str, x: float, y: float, stratum: int = SURFACE,
              energy: float = 100.0, z: float = 0.0) -> None:
        sp = self._owned(species)
        cfg = self._world.config
        self._spawns_this_tick += 1
        if self._spawns_this_tick > cfg.max_spawns_per_tick:
            raise QuotaViolation("spawn-quota", f"more than {cfg.max_spawns_per_tick} spawns in one tick")
        if self._owned_alive + self._spawns_this_tick > cfg.max_entities_per_plugin:
            raise QuotaViolation("entity-quota", f"plugin entity cap is {cfg.max_entities_per_plugin}")
        self._world.commands.spawn(sp.id, float(x), float(y), float(z), int(stratum),
                                   float(energy), self._plugin_id)

    def remove(self, handle: int) -> None:
        self._owned_row(handle)
        self._world.commands.remove(handle)

    # -- queries (tick-start state) ---------------------------------------------

    def entities(self, species: str) -> list[int]:
        sp = self._species(species)
        rows = self._world.store.alive_indices(sp.id)
        return self._world.store.handles_of(rows)

    def count(self, species: str) -> int:
        sp = self._species(species)
        return int(self._world.store.alive_indices(sp.id).size)

    def pos(self, handle: int) -> tuple[float, float, float]:
        row = self._row(handle)
        s = self._world.store
        return float(s.px[row]), float(s.py[row]), float(s.pz[row])

    def get(self, handle: int, prop: str) -> float:
        row = self._row(handle)
        s = self._world.store
        if prop == "energy":
            return float(s.energy[row])
        if prop == "age":
            return float(s.age[row])
        sp = self._world.registry.by_id[int(s.species_id[row])]
        slot = sp.prop_slots.get(prop)
        if slot is None:
            raise CapabilityViolation("unknown-prop", f"{sp.name!r} has no prop {prop!r}")
        return float(s.props[row, slot])

    def nearest(self, handle: int, species: str | None = None, radius: float = 10.0) -> int | None:
        row = self._row(handle)
        s = self._world.store
        sp_id = self._species(species).id if species is not None else None
        j = self._world.spatial.nearest(
            s, float(s.px[row]), float(s.py[row]), float(radius),
            int(s.stratum[row]), species_id=sp_id, exclude_row=row,
        )
        if j < 0:
            return None
        return (j << GEN_BITS) | int(s.generation[j])

    def within(self, handle: int, radius: float, species: str | None = None) -> list[int]:
        row = self._row(handle)
        s = self._world.store
        sp_id = self._species(species).id if species is not None else None
        rows = self._world.spatial.within(
            s, float(s.px[row]), float(s.py[row]), float(radius),
            int(s.stratum[row]), species_id=sp_id, exclude_row=row,
        )
        return [(j << GEN_BITS) | int(s.generation[j]) for j in rows]

    # -- mutations (command-buffered) ---------------------------------------------

    def move(self, handle: int, dx: float, dy: float, dz: float = 0.0) -> None:
        self._owned_row(handle)
        self._world.commands.move(handle, float(dx), float(dy), float(dz))

    def set(self, handle: int, prop: str, value: float) -> None:
        row = self._owned_row(handle)
        s = self._world.store
        if prop == "energy":
            self._world.commands.set_energy(handle, float(value))
            return
        sp = self._world.registry.by_id[int(s.species_id[row])]
        slot = sp.prop_slots.get(prop)
        if slot is None:
            raise CapabilityViolation("unknown-prop", f"{sp.name!r} has no prop {prop!r}")
        self._world.commands.set_prop(handle, slot, float(value))

    def set_stratum(self, handle: int, stratum: int) -> None:
        row = self._owned_row(handle)
        sp = self._world.registry.by_id[int(self._world.store.species_id[row])]
        if int(stratum) not in sp.strata:
            raise CapabilityViolation(
                "stratum-not-allowed", f"{sp.name!r} may only occupy strata {sp.strata}"
            )
        self._world.commands.set_stratum(handle, int(stratum))

    def attack(self, handle: int, amount: float) -> float:
        """Engine-mediated predation: drain up to `amount` energy from any entity.

        Returns the expected gain, computed against the target's tick-start
        energy. The drain itself is command-buffered and applies after the
        victim's own writes; a prey entity reaching energy <= 0 dies in the
        engine sweep with cause 'predation'.
        """
        row = self._row(handle)
        s = self._world.store
        drained = min(float(s.energy[row]), max(0.0, float(amount)))
        self._world.commands.drain_energy(handle, drained)
        return drained

    # -- environment ---------------------------------------------------------------

    def flora_at(self, x: float, y: float) -> float:
        size = self._world.config.size
        ix = min(max(int(x), 0), size - 1)
        iy = min(max(int(y), 0), size - 1)
        return float(self._world.flora.density[ix, iy])

    def eat_flora(self, x: float, y: float, amount: float) -> float:
        """Consume flora at (x, y); returns the bite estimated against tick-start density.

        The drain is command-buffered and applied at tick end (in submission order,
        clamped), exactly like `attack`: every plugin reads tick-start flora during
        the tick, execution order can't leak between plugins, and the grass can
        never be over-consumed.
        """
        size = self._world.config.size
        ix = min(max(int(x), 0), size - 1)
        iy = min(max(int(y), 0), size - 1)
        avail = float(self._world.flora.density[ix, iy])
        bite = min(avail, max(0.0, float(amount)))
        self._world.commands.eat_flora(ix, iy, bite)
        return bite

    def water_at(self, x: float, y: float) -> bool:
        size = self._world.config.size
        ix = min(max(int(x), 0), size - 1)
        iy = min(max(int(y), 0), size - 1)
        return bool(self._world.terrain.water_mask[ix, iy] > 0.5)

    def height_at(self, x: float, y: float) -> float:
        size = self._world.config.size
        ix = min(max(int(x), 0), size - 1)
        iy = min(max(int(y), 0), size - 1)
        return float(self._world.terrain.height[ix, iy])

    def weather(self) -> dict:
        w = self._world.weather
        return {
            "temperature": float(w.temperature.mean()),
            "precipitation": float(w.precipitation.mean()),
        }

    def temperature_at(self, x: float, y: float) -> float:
        size = self._world.config.size
        ix = min(max(int(x), 0), size - 1)
        iy = min(max(int(y), 0), size - 1)
        return float(self._world.weather.temperature[ix, iy])

    def season(self) -> float:
        return self._world.season_frac

    def day_frac(self) -> float:
        return self._world.day_frac

    def random_surface_point(self) -> tuple[float, float]:
        """A random non-water surface location, drawn from the plugin RNG."""
        size = self._world.config.size
        land = self._world.terrain.land_points
        i = int(self.rng.integers(0, len(land)))
        gx, gy = land[i]
        return (
            min(float(gx) + float(self.rng.uniform(0, 1)), size - 1e-3),
            min(float(gy) + float(self.rng.uniform(0, 1)), size - 1e-3),
        )
