"""Command buffer: all plugin-visible mutations queue here and apply at tick end.

Reads see tick-start state; iteration during mutation is therefore safe by
construction. Ops referencing stale generational handles are counted and
skipped — recorded, never corrupting (coding standard #6, docs/architecture.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from engine.entities import GEN_BITS, GEN_MASK, EntityStore

OP_SPAWN = 0
OP_REMOVE = 1
OP_MOVE = 2
OP_SET_ENERGY = 3
OP_SET_PROP = 4
OP_SET_STRATUM = 5
OP_DRAIN_ENERGY = 6


@dataclass
class CommandBuffer:
    ops: list[tuple] = field(default_factory=list)
    stale_ops: int = 0
    spawned_handles: list[int] = field(default_factory=list)
    flora_bites: list[tuple[int, int, float]] = field(default_factory=list)

    def spawn(self, species_id: int, x: float, y: float, z: float,
              stratum: int, energy: float, plugin_id: int = -1) -> None:
        self.ops.append((OP_SPAWN, species_id, x, y, z, stratum, energy, plugin_id))

    def remove(self, handle: int) -> None:
        self.ops.append((OP_REMOVE, handle))

    def move(self, handle: int, dx: float, dy: float, dz: float = 0.0) -> None:
        self.ops.append((OP_MOVE, handle, dx, dy, dz))

    def set_energy(self, handle: int, value: float) -> None:
        self.ops.append((OP_SET_ENERGY, handle, value))

    def set_prop(self, handle: int, slot: int, value: float) -> None:
        self.ops.append((OP_SET_PROP, handle, slot, value))

    def set_stratum(self, handle: int, stratum: int) -> None:
        self.ops.append((OP_SET_STRATUM, handle, stratum))

    def drain_energy(self, handle: int, amount: float) -> None:
        """Predation drain: applied as a delta AFTER earlier ops (incl. the prey's
        own set_energy), so an attack in the same tick cannot be silently
        overwritten by the victim plugin's buffered write."""
        self.ops.append((OP_DRAIN_ENERGY, handle, amount))

    def eat_flora(self, ix: int, iy: int, amount: float) -> None:
        """Buffered flora consumption: the caller's returned bite is an estimate
        against tick-start density (like `attack`); the field is drained here at
        tick end, in submission order and clamped, so grazers see tick-start state
        and the grass can never be over-consumed."""
        self.flora_bites.append((ix, iy, amount))

    def apply(self, store: EntityStore, world_max: float,
              predation_marks: set[int] | None = None,
              flora: np.ndarray | None = None,
              speeds: np.ndarray | None = None,
              water: np.ndarray | None = None,
              swim_speeds: np.ndarray | None = None,
              wrap: bool = False) -> None:
        """Apply ops in submission order (deterministic).

        Topology: when `wrap`, positions are taken modulo world_max (a toroidal
        world — edges join, no walls); otherwise clamped to [0, world_max - 1]
        (the terrain's last vertex, so entities stay on the rendered map).

        `speeds` (per-species max distance/tick) caps each entity's net
        displacement this tick — the engine-enforced speed limit, so no plugin
        can teleport its creatures regardless of what it passes to move().

        Position math batches through Python-float staging dicts and writes back
        vectorized — per-element NumPy scalar read-modify-write is the hot-path
        killer (Spike A / benchmark finding).
        """
        hi = world_max - 1.0
        self.spawned_handles.clear()
        alive = store.alive
        generation = store.generation
        moved: dict[int, tuple[float, float, float]] = {}
        xs = store.px
        ys = store.py
        zs = store.pz
        for op in self.ops:
            kind = op[0]
            if kind == OP_SPAWN:
                _, species_id, x, y, z, stratum, energy, plugin_id = op
                if wrap:
                    x = min(x % world_max, world_max - 0.01)
                    y = min(y % world_max, world_max - 0.01)
                else:
                    x = min(max(x, 0.0), hi)
                    y = min(max(y, 0.0), hi)
                self.spawned_handles.append(
                    store.spawn(species_id, x, y, z, stratum, energy, plugin_id)
                )
                if store.px is not xs:
                    # the spawn grew the store: every array was reallocated, so the
                    # locals aliased above now point at orphaned memory — refresh,
                    # or every subsequent write this tick lands in the void
                    alive = store.alive
                    generation = store.generation
                    xs = store.px
                    ys = store.py
                    zs = store.pz
                continue
            handle = op[1]
            i = handle >> GEN_BITS
            if not (0 <= i < store.capacity and alive[i]
                    and int(generation[i]) == (handle & GEN_MASK)):
                self.stale_ops += 1
                continue
            if kind == OP_REMOVE:
                store.remove(handle)
                moved.pop(i, None)
            elif kind == OP_MOVE:
                _, _, dx, dy, dz = op
                # stage raw displacement; clamp/wrap happens once at the end so
                # accumulated sub-moves and the speed cap compose correctly
                cx, cy, cz = moved.get(i) or (float(xs[i]), float(ys[i]), float(zs[i]))
                moved[i] = (cx + dx, cy + dy, cz + dz)
            elif kind == OP_SET_ENERGY:
                store.energy[i] = op[2]
            elif kind == OP_SET_PROP:
                store.props[i, op[2]] = op[3]
            elif kind == OP_SET_STRATUM:
                store.stratum[i] = op[2]
            elif kind == OP_DRAIN_ENERGY:
                e = float(store.energy[i])
                store.energy[i] = e - min(e, op[2])
                if store.energy[i] <= 0.0 and predation_marks is not None:
                    predation_marks.add(i)
        if moved:
            rows = np.fromiter(moved.keys(), dtype=np.int64, count=len(moved))
            vals = np.array(list(moved.values()), dtype=np.float32)
            if speeds is not None:
                # clamp net horizontal displacement to the species' speed;
                # a swimmer currently on water moves at its swim speed instead
                dx = vals[:, 0] - xs[rows]
                dy = vals[:, 1] - ys[rows]
                dist = np.sqrt(dx * dx + dy * dy)
                limit = speeds[store.species_id[rows]]
                if water is not None and swim_speeds is not None:
                    ix = xs[rows].astype(np.int32)
                    iy = ys[rows].astype(np.int32)
                    on_water = water[ix, iy] > 0.5
                    sw = swim_speeds[store.species_id[rows]]
                    limit = np.where(on_water & (sw > 0.0), sw, limit)
                over = dist > limit
                if np.any(over):
                    scale = np.where(over, limit / np.maximum(dist, 1e-9), 1.0)
                    vals[:, 0] = xs[rows] + dx * scale
                    vals[:, 1] = ys[rows] + dy * scale
            # finalize topology once: wrap (toroidal) or clamp (walled).
            # guard the upper edge: float32 can round mod(x, size) up to exactly
            # `size` (e.g. 639.9999 -> 640.0), which then indexes out of bounds
            if wrap:
                top = np.float32(world_max) - np.float32(0.01)
                vals[:, 0] = np.minimum(np.mod(vals[:, 0], world_max), top)
                vals[:, 1] = np.minimum(np.mod(vals[:, 1], world_max), top)
            else:
                np.clip(vals[:, 0], 0.0, hi, out=vals[:, 0])
                np.clip(vals[:, 1], 0.0, hi, out=vals[:, 1])
            xs[rows] = vals[:, 0]
            ys[rows] = vals[:, 1]
            zs[rows] = vals[:, 2]
        if flora is not None and self.flora_bites:
            for ix, iy, amount in self.flora_bites:
                avail = float(flora[ix, iy])
                flora[ix, iy] = avail - min(avail, amount)
        self.flora_bites.clear()
        self.ops.clear()
