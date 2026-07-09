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

import numpy as np

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
    # Two strata only: the ground and the air. (An underground layer used to exist
    # but was removed — it was hard to see and the governor fixated on it; a
    # creature "goes to ground" now via hide()/burrow as a STATE, not a layer.)
    SURFACE = SURFACE
    SKY = SKY

    def __init__(self, world: World, plugin_name: str, plugin_id: int,
                 declared_species: list[str],
                 adoptable_species: set[str] | None = None) -> None:
        self._world = world
        self._plugin_name = plugin_name
        self._plugin_id = plugin_id
        self._declared = set(declared_species)
        self._adoptable = set(adoptable_species or ())
        self._spawns_this_tick = 0
        self._owned_alive = 0
        self._species_counts: dict[int, int] = {}
        self.spawn_drops = 0  # spawns dropped by quota (an environmental limit, not an error)
        self.size = float(world.config.size)  # world edge length (positions span 0..size-1)
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
        self._species_counts = {
            sp.id: int(self._world.store.alive_indices(sp.id).size)
            for name in self._declared
            if (sp := self._world.registry.by_name.get(name)) is not None
        }
        self._owned_alive = sum(self._species_counts.values())

    # -- species & lifecycle ---------------------------------------------------

    def register_species(self, name: str, size: float = 1.0, color: str = "#cccccc",
                         speed: float = 2.5, swim_speed: float = 0.0, lifespan: int = 0,
                         strata: tuple[int, ...] = (SURFACE,),
                         props: tuple[str, ...] = ()) -> None:
        if name not in self._declared:
            raise CapabilityViolation(
                "undeclared-species",
                f"species {name!r} not in PLUGIN_META['species'] {sorted(self._declared)}",
            )
        if UNDERGROUND in tuple(strata):
            raise CapabilityViolation(
                "no-underground",
                "the underground stratum was removed; use SURFACE and/or SKY. To make a "
                "creature go to ground for safety, give it hide()/burrow behaviour instead.",
            )
        if name in self._world.registry.by_name:
            existing = self._world.registry.by_name[name]
            extinct = int(self._world.store.alive_indices(existing.id).size) == 0
            if name in self._adoptable or extinct:
                # lineage replacement, OR reclaiming an EXTINCT species name (no
                # living members) — the governor revives niches by re-using names,
                # so an empty species is free to take over. Prop layout is kept.
                self._world.registry.adopt(name, self._plugin_name, size=size, color=color,
                                           speed=speed, swim_speed=swim_speed, lifespan=lifespan)
                return
            raise CapabilityViolation(
                "duplicate-species",
                f"species {name!r} already exists and is alive; to take it over, set "
                "PLUGIN_META['lineage_parent'] to the owning plugin's name",
            )
        self._world.registry.register(
            name, plugin=self._plugin_name, size=size, color=color, speed=speed,
            swim_speed=swim_speed, lifespan=lifespan, strata=tuple(strata), props=tuple(props),
        )

    def spawn(self, species: str, x: float, y: float, stratum: int = SURFACE,
              energy: float = 100.0, z: float = 0.0, face: int = 0) -> None:
        """Spawn an owned entity. Hitting a population/spawn-rate cap is an
        ENVIRONMENTAL limit, not a programming error: the spawn is silently
        dropped and counted in `spawn_drops` — it must never abort the tick or
        push a healthy plugin toward quarantine (a booming herd is not a bug)."""
        sp = self._owned(species)
        cfg = self._world.config
        self._spawns_this_tick += 1
        # count this species' pending spawns this tick toward its hard cap
        self._species_counts[sp.id] = self._species_counts.get(sp.id, 0) + 1
        # the per-tick spawn RATE is always enforced (a cheap safety valve against
        # a pathological plugin); the population CEILINGS (per-species, per-plugin)
        # are suspended when the operator turns caps off to experiment.
        over_rate = self._spawns_this_tick > cfg.max_spawns_per_tick
        over_ceiling = (
            self._owned_alive + self._spawns_this_tick > cfg.max_entities_per_plugin
            or self._species_counts[sp.id] > cfg.max_entities_per_species
        )
        if over_rate or (self._world.caps_enabled and over_ceiling):
            self.spawn_drops += 1
            return
        self._world.commands.spawn(sp.id, float(x), float(y), float(z), int(stratum),
                                   float(energy), self._plugin_id, int(face))

    def remove(self, handle: int) -> None:
        self._owned_row(handle)
        self._world.commands.remove(handle)

    def face(self, handle: int) -> int:
        """Cube face this entity is on (0 for flat/wrap worlds). Pass to spawn()
        to place offspring on the same face as the parent."""
        return int(self._world.store.face[self._row(handle)])

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

    def nearest(self, handle: int, species: str | None = None, radius: float = 10.0,
                stratum: int | None = None) -> int | None:
        """Nearest entity within radius. Queries the caller's own stratum by default;
        pass `stratum=` to sense another layer (e.g. a burrower detecting surface
        prey — it must still `set_stratum` to that layer to interact)."""
        row = self._row(handle)
        s = self._world.store
        sp_id = self._species(species).id if species is not None else None
        st = int(s.stratum[row]) if stratum is None else int(stratum)
        if self._world.geom is not None:  # cube: seamless 3D query across faces
            j = self._world.spatial3d.nearest(s, row, float(radius), st, species_id=sp_id)
        else:
            j = self._world.spatial.nearest(
                s, float(s.px[row]), float(s.py[row]), float(radius),
                st, species_id=sp_id, exclude_row=row, face=int(s.face[row]),
            )
        if j < 0:
            return None
        return (j << GEN_BITS) | int(s.generation[j])

    def within(self, handle: int, radius: float, species: str | None = None,
               stratum: int | None = None) -> list[int]:
        row = self._row(handle)
        s = self._world.store
        sp_id = self._species(species).id if species is not None else None
        st = int(s.stratum[row]) if stratum is None else int(stratum)
        if self._world.geom is not None:  # cube: seamless 3D query across faces
            rows = self._world.spatial3d.within(s, row, float(radius), st, species_id=sp_id)
        else:
            rows = self._world.spatial.within(
                s, float(s.px[row]), float(s.py[row]), float(radius),
                st, species_id=sp_id, exclude_row=row, face=int(s.face[row]),
            )
        return [(j << GEN_BITS) | int(s.generation[j]) for j in rows]

    def direction_to(self, handle: int, target: int) -> tuple[float, float]:
        """Unit (dx, dy) in the CALLER's local frame that heads toward `target`,
        correct across face seams on the cube (project the 3D direction onto the
        caller's face tangents). On flat/wrap it's the seam-aware planar direction.
        Use it to pursue/flee prey that `nearest` found on another face."""
        a = self._row(handle)
        b = self._row(target)
        s = self._world.store
        if self._world.geom is not None:
            from engine.cube import face_basis, positions_3d
            sp3 = self._world.spatial3d
            pa = sp3.pos_of(a)
            pb = sp3.pos_of(b)
            if pa is None or pb is None:
                p = positions_3d(s.face[[a, b]], s.px[[a, b]], s.py[[a, b]], self._world.config.size)
                pa, pb = tuple(p[0]), tuple(p[1])
            d = np.array([pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]])
            r, u = face_basis(int(s.face[a]))
            dx, dy = float(d @ r), float(d @ u)
        else:
            dx = self.wrap_delta(float(s.px[a]), float(s.px[b]))
            dy = self.wrap_delta(float(s.py[a]), float(s.py[b]))
        mag = (dx * dx + dy * dy) ** 0.5
        return (dx / mag, dy / mag) if mag > 1e-9 else (0.0, 0.0)

    def distance(self, handle: int, target: int) -> float:
        """Distance between two entities — true 3D (great-circle-ish) on the cube,
        seam-aware planar on flat/wrap."""
        a = self._row(handle)
        b = self._row(target)
        s = self._world.store
        if self._world.geom is not None:
            return self._world.spatial3d.distance(a, b)
        dx = self.wrap_delta(float(s.px[a]), float(s.px[b]))
        dy = self.wrap_delta(float(s.py[a]), float(s.py[b]))
        return (dx * dx + dy * dy) ** 0.5

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

    def hide(self, handle: int, hidden: bool = True) -> None:
        """Burrow / hide (or resurface). A hidden creature is invisible to every
        other creature's nearest()/within() next tick — a way to escape predators
        or ambush prey WITHOUT a separate underground layer. It can still sense the
        world itself. Design trade-off is yours: e.g. don't forage while hidden, or
        drain a little energy, so hiding costs something."""
        self._owned_row(handle)
        self._world.commands.set_hidden(handle, bool(hidden))

    def is_hidden(self, handle: int) -> bool:
        return bool(self._world.store.hidden[self._row(handle)])

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
    # env reads take a `face` (0 for flat/wrap). On a cube, pass world.face(handle)
    # so a creature reads the terrain of the face it's actually standing on.

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        size = self._world.config.size
        return min(max(int(x), 0), size - 1), min(max(int(y), 0), size - 1)

    def flora_at(self, x: float, y: float, face: int = 0) -> float:
        ix, iy = self._cell(x, y)
        d = self._world.flora.density
        return float(d[face, ix, iy] if self._world.geom is not None else d[ix, iy])

    def eat_flora(self, x: float, y: float, amount: float, face: int = 0) -> float:
        """Consume flora at (x, y); returns the bite estimated against tick-start density.

        The drain is command-buffered and applied at tick end (in submission order,
        clamped), exactly like `attack`: every plugin reads tick-start flora during
        the tick, execution order can't leak between plugins, and the grass can
        never be over-consumed.
        """
        ix, iy = self._cell(x, y)
        d = self._world.flora.density
        avail = float(d[face, ix, iy] if self._world.geom is not None else d[ix, iy])
        bite = min(avail, max(0.0, float(amount)))
        self._world.commands.eat_flora(ix, iy, bite, int(face))
        return bite

    def water_at(self, x: float, y: float, face: int = 0) -> bool:
        ix, iy = self._cell(x, y)
        m = self._world.terrain.water_mask
        val = m[face, ix, iy] if self._world.geom is not None else m[ix, iy]
        return bool(val > 0.5)

    def height_at(self, x: float, y: float, face: int = 0) -> float:
        ix, iy = self._cell(x, y)
        h = self._world.terrain.height
        return float(h[face, ix, iy] if self._world.geom is not None else h[ix, iy])

    def weather(self) -> dict:
        w = self._world.weather
        return {
            "temperature": float(w.temperature.mean()),
            "precipitation": float(w.precipitation.mean()),
        }

    def temperature_at(self, x: float, y: float, face: int = 0) -> float:
        ix, iy = self._cell(x, y)
        t = self._world.weather.temperature
        return float(t[face, ix, iy] if self._world.geom is not None else t[ix, iy])

    def season(self) -> float:
        return self._world.season_frac

    def day_frac(self) -> float:
        return self._world.day_frac

    def daylight(self, handle: int) -> float:
        """Local sun at this entity, in [-1,1] (1 = overhead sun, <0 = night). On
        the planet this is per-longitude, so a creature can sleep/rest at its own
        night, be nocturnal, etc. Combine with a danger check to 'wake' when
        threatened."""
        row = self._row(handle)
        s = self._world.store
        return self._world.daylight_at(int(s.face[row]), float(s.px[row]), float(s.py[row]))

    def wrap_delta(self, a: float, b: float) -> float:
        """Shortest signed distance from a to b along one axis. On a wrapped
        (toroidal) world this crosses the seam when that's shorter, so steering
        `world.move(h, world.wrap_delta(x, tx), world.wrap_delta(y, ty))` heads the
        correct way even near an edge. On a flat world it's just b - a."""
        d = b - a
        if self._world.config.wrap:
            size = self._world.config.size
            if d > size * 0.5:
                d -= size
            elif d < -size * 0.5:
                d += size
        return d

    def random_surface_point(self) -> tuple[float, float]:
        """A random non-water surface location, drawn from the plugin RNG.

        On a cube this returns a point on face 0 (the founder face) — spawn there
        and populations migrate across faces over time. Pass the resulting point
        to spawn(); use face=0 (the default)."""
        size = self._world.config.size
        terrain = self._world.terrain
        land = terrain.land_points(0) if self._world.geom is not None else terrain.land_points
        i = int(self.rng.integers(0, len(land)))
        gx, gy = land[i]
        return (
            min(float(gx) + float(self.rng.uniform(0, 1)), size - 1e-3),
            min(float(gy) + float(self.rng.uniform(0, 1)), size - 1e-3),
        )
