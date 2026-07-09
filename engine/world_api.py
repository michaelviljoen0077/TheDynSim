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
        if not isinstance(key, str):
            # non-str keys survive in memory but JSON-coerce to str on snapshot,
            # so they'd silently desync after a reload/shadow/rollback — reject them
            raise CapabilityViolation("store-key-type", "world.store keys must be str")
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
                         props: tuple[str, ...] = (),
                         genes: dict[str, float] | None = None,
                         gene_sigma: float = 0.08) -> None:
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
            genes=genes, gene_sigma=gene_sigma,
        )

    def spawn(self, species: str, x: float, y: float, stratum: int = SURFACE,
              energy: float = 100.0, z: float = 0.0, face: int = 0,
              genome: object = None, parent: int | None = None) -> None:
        """Spawn an owned entity. Hitting a population/spawn-rate cap is an
        ENVIRONMENTAL limit, not a programming error: the spawn is silently
        dropped and counted in `spawn_drops` — it must never abort the tick or
        push a healthy plugin toward quarantine (a booming herd is not a bug).

        Pass `parent=<a live handle of this species>` for manual reproduction so
        the offspring INHERITS the parent's genome with mutation (natural
        selection). `genome` is the internal path breed() uses; a founder spawn
        leaves both None and gets the species' founder genome."""
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
        if genome is None and parent is not None and sp.gene_slots:
            genome = self._mutate_one(sp, self._world.store.genome[self._row(parent)])
        g = genome if genome is not None else (
            self._world.registry.default_genome(sp) if sp.gene_slots else None)
        self._world.commands.spawn(sp.id, float(x), float(y), float(z), int(stratum),
                                   float(energy), self._plugin_id, int(face), g)

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

    def gene(self, handle: int, name: str) -> float:
        """This entity's heritable value for a named gene (its own mutated copy).
        Founders start at the declared value; offspring inherit a parent's value
        with mutation, so reading a gene lets behaviour depend on evolved traits."""
        row = self._row(handle)
        s = self._world.store
        sp = self._world.registry.by_id[int(s.species_id[row])]
        slot = sp.gene_slots.get(name)
        if slot is None:
            raise CapabilityViolation("unknown-gene", f"{sp.name!r} has no gene {name!r}")
        return float(s.genome[row, slot])

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

    def attack(self, attacker: int, prey: int, amount: float,
               efficiency: float = 0.8) -> float:
        """Engine-mediated predation, ENERGY-CONSERVING. `attacker` (one of your
        own entities) drains up to `amount` energy from `prey` (any entity),
        keeping `efficiency` of what it actually gets. The engine credits your
        attacker AT TICK END from the prey's ACTUAL energy — shared out if several
        predators hit the same prey this tick — so you must NOT add the return
        value to your own energy yourself (that would double-count / mint energy).
        The return is only an estimate for logging. Prey reaching energy <= 0 dies
        in the engine sweep with cause 'predation'.
        """
        a = self._owned_row(attacker)
        p = self._row(prey)
        amt = max(0.0, float(amount))
        self._world.commands.claim_prey(a, p, amt, float(efficiency))
        return min(float(self._world.store.energy[p]), amt) * float(efficiency)

    # -- batch primitives (HERD-AT-ONCE, vectorized) -------------------------------
    # These process an entire OWNED species in one NumPy pass inside the engine, so
    # a plugin expresses a whole herd's tick without a Python per-entity loop — the
    # big performance lever (per-entity plugin loops dominate tick cost). They read
    # tick-start state and their effects are command-buffered exactly like the
    # single-entity calls, so ordering guarantees are unchanged. Energy effects
    # COMPOSE additively, so metabolize + graze + breed on the same herd sum
    # correctly. Prefer these for uniform behaviour; keep per-entity calls only for
    # genuinely conditional logic that can't be expressed in bulk.

    def _owned_rows(self, species: str) -> tuple:
        sp = self._owned(species)
        return sp, self._world.store.alive_indices(sp.id)

    def metabolize(self, species: str, amount: float) -> None:
        """Drain `amount` energy from every member of the species this tick."""
        _sp, rows = self._owned_rows(species)
        if rows.size:
            self._world.commands.energy_delta_batch(
                rows, np.full(rows.size, -float(amount), dtype=np.float32))

    def graze(self, species: str, rate: float, gain: float, max_energy: float = 200.0,
              on: str = "flora") -> None:
        """Every member grazes at its own cell: bite up to `rate`, convert to `gain`
        energy per unit (capped so nobody exceeds `max_energy`). `on="flora"` (land)
        or `on="plankton"` (water — fish/filter-feeders). The engine resolves the
        claims against the cell's ACTUAL density at tick end (energy-conserving), so
        a crowded cell feeds its grazers proportionally instead of each in full."""
        _sp, rows = self._owned_rows(species)
        if not rows.size:
            return
        s = self._world.store
        size = self._world.config.size
        ix = np.clip(s.px[rows].astype(np.int64), 0, size - 1)
        iy = np.clip(s.py[rows].astype(np.int64), 0, size - 1)
        face = s.face[rows].astype(np.int64) if self._world.geom is not None \
            else np.zeros(rows.size, dtype=np.int64)
        g = float(gain)
        headroom = np.maximum(0.0, float(max_energy) - s.energy[rows])
        req = np.minimum(float(rate), headroom / g) if g > 0.0 else np.zeros(rows.size)
        if on == "plankton":
            self._world.commands.claim_plankton_batch(rows, face, ix, iy, req, g)
        else:
            self._world.commands.claim_flora_batch(rows, face, ix, iy, req, g)

    def wander(self, species: str, speed: float, turn: float = 0.25) -> None:
        """Advance every member along a persistent per-entity 'heading' prop, with a
        small random turn each tick. The species must declare a 'heading' prop; the
        engine keeps that heading continuous across cube seams."""
        sp, rows = self._owned_rows(species)
        slot = sp.prop_slots.get("heading")
        if slot is None:
            raise CapabilityViolation(
                "no-heading-prop", f"{species!r} must declare a 'heading' prop to wander()")
        if not rows.size:
            return
        s = self._world.store
        hd = s.props[rows, slot].astype(np.float64)
        unset = hd == 0.0
        if unset.any():
            hd[unset] = self.rng.uniform(0.1, 6.28, size=int(unset.sum()))
        hd = hd + self.rng.uniform(-float(turn), float(turn), size=rows.size)
        # heading is this species' own internal state; write it back now (the engine
        # re-projects it for any entity that folds across a seam during apply)
        s.props[rows, slot] = hd.astype(np.float32)
        dx = (np.cos(hd) * float(speed)).astype(np.float32)
        dy = (np.sin(hd) * float(speed)).astype(np.float32)
        self._world.commands.move_batch(rows, dx, dy)

    def breed(self, species: str, energy_over: float, cost: float,
              offspring_energy: float | None = None, cap: int | None = None,
              crowd_max: int | None = None, crowd_radius: float = 8.0) -> int:
        """Every member whose energy exceeds `energy_over` spawns one offspring
        (jittered to its position), paying `cost` energy. Returns how many bred.

        For a SOFT equilibrium (preferred over a hard `cap`), pass `crowd_max`: an
        entity only breeds where it has at most `crowd_max` same-species neighbours
        within `crowd_radius`, so the birth rate falls as density rises and the
        population settles at the land's carrying capacity — density-dependent
        reproduction, the textbook logistic control. `cap` is a hard backstop."""
        sp, rows = self._owned_rows(species)
        if not rows.size:
            return 0
        s = self._world.store
        mask = s.energy[rows] > float(energy_over)
        if crowd_max is not None:
            mask &= self._local_density(rows, float(crowd_radius)) <= int(crowd_max)
        eligible = rows[mask]
        if cap is not None:
            room = int(cap) - int(rows.size)
            if room <= 0:
                return 0
            if eligible.size > room:
                eligible = eligible[:room]
        if not eligible.size:
            return 0
        off_e = float(offspring_energy) if offspring_energy is not None else float(cost)
        self._world.commands.energy_delta_batch(
            eligible, np.full(eligible.size, -float(cost), dtype=np.float32))
        # offspring inherit each parent's genome with gaussian mutation (natural
        # selection acts on the result); geneless species pass genome=None
        child_g = self._mutated_offspring_genomes(sp, eligible)
        px = s.px[eligible].tolist()
        py = s.py[eligible].tolist()
        pz = s.pz[eligible].tolist()
        strat = s.stratum[eligible].tolist()
        face = s.face[eligible].tolist()
        for k, (x, y, z, st, fc) in enumerate(zip(px, py, pz, strat, face, strict=True)):
            self.spawn(species, x + self.rng.uniform(-1.5, 1.5),
                       y + self.rng.uniform(-1.5, 1.5),
                       stratum=int(st), energy=off_e, z=float(z), face=int(fc),
                       genome=None if child_g is None else child_g[k])
        return int(eligible.size)

    def _mutated_offspring_genomes(self, sp, parent_rows: np.ndarray):
        """Vectorized inheritance: each offspring gets its parent's genome with
        per-gene gaussian mutation, clamped to [0.25x, 4x] the founder value."""
        if not sp.gene_slots:
            return None
        child = self._world.store.genome[parent_rows].copy()
        sigma = sp.gene_sigma
        if sigma > 0.0:
            for name, slot in sp.gene_slots.items():
                default = sp.gene_defaults[name]
                scale = sigma * (abs(default) if default else 1.0)
                noise = self.rng.normal(0.0, scale, size=parent_rows.size)
                lo, hi = sorted((0.25 * default, 4.0 * default))
                child[:, slot] = np.clip(child[:, slot] + noise, lo, hi)
        return child

    def _local_density(self, rows: np.ndarray, radius: float) -> np.ndarray:
        """Same-species neighbours (excluding self) sharing each entity's cell — a
        cheap O(n) density proxy (same trick the engine's crowding stress uses)."""
        s = self._world.store
        cell = max(float(radius), 1.0)
        size = self._world.config.size
        ncell = int(size / cell) + 2
        gx = (s.px[rows] / cell).astype(np.int64)
        gy = (s.py[rows] / cell).astype(np.int64)
        key = gx * ncell + gy
        if self._world.geom is not None:   # don't let separate faces cross-count
            key = key * 6 + s.face[rows].astype(np.int64)
        _u, inv, counts = np.unique(key, return_inverse=True, return_counts=True)
        return counts[inv] - 1

    def _mutate_one(self, sp, base: np.ndarray) -> np.ndarray:
        """Single-offspring inheritance: parent genome + per-gene gaussian mutation."""
        g = base.copy()
        sigma = sp.gene_sigma
        if sigma > 0.0:
            for name, slot in sp.gene_slots.items():
                default = sp.gene_defaults[name]
                scale = sigma * (abs(default) if default else 1.0)
                lo, hi = sorted((0.25 * default, 4.0 * default))
                g[slot] = float(np.clip(g[slot] + self.rng.normal(0.0, scale), lo, hi))
        return g

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

    def eat_flora(self, eater: int, x: float, y: float, amount: float,
                  gain: float, face: int = 0) -> float:
        """Graze flora at (x, y), ENERGY-CONSERVING. `eater` (one of your own
        entities) claims up to `amount` flora worth `gain` energy per unit. The
        engine credits the eater AT TICK END from the cell's ACTUAL density —
        shared out if several grazers hit the same cell — so do NOT add the return
        to your own energy (it's only an estimate). The grass can never be
        over-consumed or turned into free energy.
        """
        row = self._owned_row(eater)
        ix, iy = self._cell(x, y)
        self._world.commands.claim_flora(row, int(face), ix, iy,
                                         max(0.0, float(amount)), float(gain))
        d = self._world.flora.density
        avail = float(d[face, ix, iy] if self._world.geom is not None else d[ix, iy])
        return min(avail, max(0.0, float(amount))) * float(gain)

    def plankton_at(self, x: float, y: float, face: int = 0) -> float:
        """Aquatic food density (0..1) at a cell — nonzero only over open water."""
        ix, iy = self._cell(x, y)
        d = self._world.plankton.density
        return float(d[face, ix, iy] if self._world.geom is not None else d[ix, iy])

    def eat_plankton(self, eater: int, x: float, y: float, amount: float,
                     gain: float, face: int = 0) -> float:
        """Filter-feed plankton at (x, y) — the aquatic mirror of eat_flora, same
        ENERGY-CONSERVING contract (engine credits the eater at tick end; the
        return is only an estimate). Nonzero food only over open water."""
        row = self._owned_row(eater)
        ix, iy = self._cell(x, y)
        self._world.commands.claim_plankton(row, int(face), ix, iy,
                                             max(0.0, float(amount)), float(gain))
        d = self._world.plankton.density
        avail = float(d[face, ix, iy] if self._world.geom is not None else d[ix, iy])
        return min(avail, max(0.0, float(amount))) * float(gain)

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

    def random_water_point(self) -> tuple[float, float]:
        """A random OPEN-WATER location (for aquatic spawns) — the water mirror of
        random_surface_point. On a cube it returns a point on face 0. Spawn a
        swimmer (`swim_speed>0`) here so it can filter-feed plankton."""
        size = self._world.config.size
        terrain = self._world.terrain
        water = terrain.water_points(0) if self._world.geom is not None else terrain.water_points
        i = int(self.rng.integers(0, len(water)))
        gx, gy = water[i]
        return (
            min(float(gx) + float(self.rng.uniform(0, 1)), size - 1e-3),
            min(float(gy) + float(self.rng.uniform(0, 1)), size - 1e-3),
        )
