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
OP_SET_HIDDEN = 7


@dataclass
class CommandBuffer:
    ops: list[tuple] = field(default_factory=list)
    stale_ops: int = 0
    spawned_handles: list[int] = field(default_factory=list)
    flora_bites: list[tuple[int, int, int, float]] = field(default_factory=list)  # (face,ix,iy,amt)
    # BATCH ops: a whole species processed at once by the engine primitives
    # (world.metabolize/graze/wander/breed). Each holds numpy arrays and applies
    # vectorized in apply(), so a plugin never loops per-entity in Python. Rows
    # are entity indices, valid because the primitive queried them from tick-start
    # alive state and batch ops apply BEFORE the per-op loop (no removals yet).
    batch_energy: list[tuple] = field(default_factory=list)   # (rows, energy_deltas)
    batch_moves: list[tuple] = field(default_factory=list)     # (rows, dx, dy)
    batch_flora: list[tuple] = field(default_factory=list)     # (face, ix, iy, amount) arrays
    # CONSUMPTION CLAIMS (energy conservation): eating/predation register a CLAIM
    # on a shared resource rather than crediting the consumer synchronously. At
    # tick end the engine sums each resource's claims and distributes only what is
    # actually there (proportionally if over-subscribed), so N consumers of one
    # cell/prey can never mint energy from tick-start estimates.
    eat_claims: list[tuple] = field(default_factory=list)      # flora: (rows, face, ix, iy, bite, gain)
    plankton_claims: list[tuple] = field(default_factory=list)  # plankton: same shape (aquatic food)
    attack_claims: list[tuple] = field(default_factory=list)   # (attacker_row, prey_row, amount, eff)

    def spawn(self, species_id: int, x: float, y: float, z: float,
              stratum: int, energy: float, plugin_id: int = -1, face: int = 0,
              genome: np.ndarray | None = None) -> None:
        self.ops.append((OP_SPAWN, species_id, x, y, z, stratum, energy, plugin_id, face, genome))

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

    def set_hidden(self, handle: int, value: bool) -> None:
        self.ops.append((OP_SET_HIDDEN, handle, value))

    def drain_energy(self, handle: int, amount: float) -> None:
        """Predation drain: applied as a delta AFTER earlier ops (incl. the prey's
        own set_energy), so an attack in the same tick cannot be silently
        overwritten by the victim plugin's buffered write."""
        self.ops.append((OP_DRAIN_ENERGY, handle, amount))

    def eat_flora(self, ix: int, iy: int, amount: float, face: int = 0) -> None:
        """Buffered flora consumption: the caller's returned bite is an estimate
        against tick-start density (like `attack`); the field is drained here at
        tick end, in submission order and clamped, so grazers see tick-start state
        and the grass can never be over-consumed."""
        self.flora_bites.append((face, ix, iy, amount))

    # -- batch ops (engine primitives) ----------------------------------------

    def energy_delta_batch(self, rows: np.ndarray, deltas: np.ndarray) -> None:
        """Add per-entity energy deltas (compose additively, so metabolize + graze
        + breed on the same herd sum correctly instead of overwriting)."""
        self.batch_energy.append((rows, deltas))

    def move_batch(self, rows: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> None:
        self.batch_moves.append((rows, dx, dy))

    def flora_bite_batch(self, face: np.ndarray, ix: np.ndarray, iy: np.ndarray,
                         amount: np.ndarray) -> None:
        self.batch_flora.append((face, ix, iy, amount))

    # -- consumption claims (energy-conserving) -------------------------------

    def claim_flora(self, row: int, face: int, ix: int, iy: int,
                    bite: float, gain: float) -> None:
        """One entity claims up to `bite` flora at a cell, worth `gain` energy per
        unit. Resolved (distributed + credited) at tick end."""
        self.eat_claims.append((np.array([row], dtype=np.int64), np.array([face], dtype=np.int64),
                                np.array([ix], dtype=np.int64), np.array([iy], dtype=np.int64),
                                np.array([bite], dtype=np.float64), np.array([gain], dtype=np.float64)))

    def claim_flora_batch(self, rows: np.ndarray, face: np.ndarray, ix: np.ndarray,
                          iy: np.ndarray, bite: np.ndarray, gain: float) -> None:
        """A whole herd's flora claims at once (world.graze)."""
        self.eat_claims.append((rows.astype(np.int64), face.astype(np.int64),
                                ix.astype(np.int64), iy.astype(np.int64),
                                bite.astype(np.float64),
                                np.full(rows.size, float(gain), dtype=np.float64)))

    def claim_plankton(self, row: int, face: int, ix: int, iy: int,
                       bite: float, gain: float) -> None:
        """Like claim_flora but against the aquatic plankton field (fish food)."""
        self.plankton_claims.append((np.array([row], dtype=np.int64), np.array([face], dtype=np.int64),
                                     np.array([ix], dtype=np.int64), np.array([iy], dtype=np.int64),
                                     np.array([bite], dtype=np.float64), np.array([gain], dtype=np.float64)))

    def claim_plankton_batch(self, rows: np.ndarray, face: np.ndarray, ix: np.ndarray,
                             iy: np.ndarray, bite: np.ndarray, gain: float) -> None:
        self.plankton_claims.append((rows.astype(np.int64), face.astype(np.int64),
                                     ix.astype(np.int64), iy.astype(np.int64),
                                     bite.astype(np.float64),
                                     np.full(rows.size, float(gain), dtype=np.float64)))

    def claim_prey(self, attacker_row: int, prey_row: int, amount: float,
                   efficiency: float) -> None:
        """Predation claim: `attacker` drains up to `amount` from `prey`, keeping
        `efficiency` of what it actually gets. Resolved at tick end."""
        self.attack_claims.append((int(attacker_row), int(prey_row),
                                   float(amount), float(efficiency)))

    def apply(self, store: EntityStore, world_max: float,
              predation_marks: set[int] | None = None,
              flora: np.ndarray | None = None,
              speeds: np.ndarray | None = None,
              water: np.ndarray | None = None,
              swim_speeds: np.ndarray | None = None,
              wrap: bool = False,
              geom=None,
              heading_slots: np.ndarray | None = None,
              speed_gene_slots: np.ndarray | None = None,
              plankton: np.ndarray | None = None,
              aquatic: np.ndarray | None = None) -> None:
        """Apply ops in submission order (deterministic).

        Topology: `geom` (a CubeGeometry) folds edge-crossers onto neighbour
        faces; else `wrap` takes positions modulo world_max (toroidal); else
        positions clamp to [0, world_max - 1] (walled).

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

        # -- BATCH ops flush (engine primitives) --------------------------------
        # Applied BEFORE the per-op loop: rows were queried from tick-start alive
        # state and no OP_REMOVE has run yet, so every row is valid. Energy deltas
        # compose additively (np.add.at handles repeats); a later predation drain
        # in the op loop stacks on top, preserving "drain after the victim's writes".
        for rows, deltas in self.batch_energy:
            np.add.at(store.energy, rows, deltas)
        for rows, dxa, dya in self.batch_moves:
            txs = (xs[rows] + dxa).tolist()
            tys = (ys[rows] + dya).tolist()
            for r, tx, ty in zip(rows.tolist(), txs, tys, strict=True):
                prev = moved.get(r)
                if prev is None:
                    moved[r] = (tx, ty, float(zs[r]))
                else:  # compose with an earlier move on the same entity
                    moved[r] = (prev[0] + (tx - float(xs[r])),
                                prev[1] + (ty - float(ys[r])), prev[2])
        for fa, ixa, iya, amta in self.batch_flora:
            self.flora_bites.extend(zip(fa.tolist(), ixa.tolist(), iya.tolist(),
                                        amta.tolist(), strict=True))

        for op in self.ops:
            kind = op[0]
            if kind == OP_SPAWN:
                _, species_id, x, y, z, stratum, energy, plugin_id, face, genome = op
                if geom is not None:
                    x = min(max(x, 0.0), world_max - 0.01)
                    y = min(max(y, 0.0), world_max - 0.01)
                elif wrap:
                    x = min(x % world_max, world_max - 0.01)
                    y = min(y % world_max, world_max - 0.01)
                else:
                    x = min(max(x, 0.0), hi)
                    y = min(max(y, 0.0), hi)
                self.spawned_handles.append(
                    store.spawn(species_id, x, y, z, stratum, energy, plugin_id, face, genome)
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
            elif kind == OP_SET_HIDDEN:
                store.hidden[i] = op[2]
            elif kind == OP_DRAIN_ENERGY:
                e = float(store.energy[i])
                store.energy[i] = e - min(e, op[2])
                if store.energy[i] <= 0.0 and predation_marks is not None:
                    predation_marks.add(i)
        if moved:
            rows = np.fromiter(moved.keys(), dtype=np.int64, count=len(moved))
            vals = np.array(list(moved.values()), dtype=np.float32)
            # pre-move position/face, kept for the aquatic revert below (xs[rows]
            # still holds the tick-start coords until the final write-back)
            ox_r = xs[rows].copy()
            oy_r = ys[rows].copy()
            pre_faces = store.face[rows].copy() if geom is not None else None
            if speeds is not None:
                # clamp net horizontal displacement to the species' speed;
                # a swimmer currently on water moves at its swim speed instead
                dx = vals[:, 0] - xs[rows]
                dy = vals[:, 1] - ys[rows]
                dist = np.sqrt(dx * dx + dy * dy)
                limit = speeds[store.species_id[rows]].astype(np.float32)
                if speed_gene_slots is not None:
                    # per-entity 'speed' gene scales the speed cap (natural
                    # selection on mobility; the coupled energy cost is _gene_costs)
                    gslot = speed_gene_slots[store.species_id[rows]]
                    have = np.flatnonzero(gslot >= 0)
                    if have.size:
                        limit = limit.copy()
                        limit[have] *= store.genome[rows[have], gslot[have]]
                if water is not None and swim_speeds is not None:
                    ix = xs[rows].astype(np.int32)
                    iy = ys[rows].astype(np.int32)
                    if geom is not None:      # per-face water mask
                        on_water = water[store.face[rows], ix, iy] > 0.5
                    else:
                        on_water = water[ix, iy] > 0.5
                    sw = swim_speeds[store.species_id[rows]]
                    limit = np.where(on_water & (sw > 0.0), sw, limit)
                over = dist > limit
                if np.any(over):
                    scale = np.where(over, limit / np.maximum(dist, 1e-9), 1.0)
                    vals[:, 0] = xs[rows] + dx * scale
                    vals[:, 1] = ys[rows] + dy * scale
            # finalize topology once. guard the upper edge: float32 can round a
            # coord up to exactly `size` (e.g. 639.9999 -> 640.0), out of bounds.
            top = np.float32(world_max) - np.float32(0.01)
            if geom is not None:
                # cube: in-face moves keep their face; edge-crossers (rare) fold
                # onto a neighbour face via the geometry, using the speed-clamped
                # net displacement from their pre-move position
                fdx = vals[:, 0] - xs[rows]
                fdy = vals[:, 1] - ys[rows]
                oob = np.flatnonzero((vals[:, 0] < 0) | (vals[:, 0] >= world_max)
                                     | (vals[:, 1] < 0) | (vals[:, 1] >= world_max))
                for k in oob.tolist():
                    i = int(rows[k])
                    old_face = int(store.face[i])
                    ox, oy = float(xs[i]), float(ys[i])
                    dxk, dyk = float(fdx[k]), float(fdy[k])
                    nf, nx, ny = geom.step(old_face, ox, oy, dxk, dyk)
                    store.face[i] = nf
                    vals[k, 0] = nx
                    vals[k, 1] = ny
                    # keep a roaming creature's heading continuous across the seam.
                    # A cube edge turns the surface 90 deg, so the world direction
                    # isn't preserved as a tangent — instead fold a point slightly
                    # FURTHER along the same trajectory and read the heading from the
                    # folded step ON THE NEW FACE. Without this a crosser gets a
                    # scrambled local angle and ping-pongs along the edge.
                    if heading_slots is not None:
                        slot = int(heading_slots[int(store.species_id[i])])
                        if slot >= 0:
                            nf2, nx2, ny2 = geom.step(old_face, ox, oy, dxk * 1.05, dyk * 1.05)
                            hdx, hdy = nx2 - nx, ny2 - ny
                            if nf2 == nf and (hdx * hdx + hdy * hdy) > 1e-12:
                                store.props[i, slot] = np.float32(
                                    float(np.arctan2(hdy, hdx)) or 1e-4)
                np.clip(vals[:, 0], 0.0, top, out=vals[:, 0])
                np.clip(vals[:, 1], 0.0, top, out=vals[:, 1])
            elif wrap:
                vals[:, 0] = np.minimum(np.mod(vals[:, 0], world_max), top)
                vals[:, 1] = np.minimum(np.mod(vals[:, 1], world_max), top)
            else:
                np.clip(vals[:, 0], 0.0, hi, out=vals[:, 0])
                np.clip(vals[:, 1], 0.0, hi, out=vals[:, 1])
            # AQUATIC confinement: a species flagged aquatic can swim and wander
            # freely, but the engine forbids it from ever leaving the water. Any
            # move whose destination cell is land is undone — the creature stays
            # put this tick (position AND face restored, so a seam-crossing fold
            # onto a land neighbour face is rolled back cleanly). This is the water
            # analogue of `fly` (SKY stratum) keeping birds off the ground.
            if aquatic is not None and water is not None:
                aq = aquatic[store.species_id[rows]]
                if aq.any():
                    nix = np.clip(vals[:, 0].astype(np.int32), 0, int(world_max) - 1)
                    niy = np.clip(vals[:, 1].astype(np.int32), 0, int(world_max) - 1)
                    if geom is not None:
                        wet = water[store.face[rows], nix, niy] > 0.5
                    else:
                        wet = water[nix, niy] > 0.5
                    rev = aq & (~wet)
                    if rev.any():
                        vals[rev, 0] = ox_r[rev]
                        vals[rev, 1] = oy_r[rev]
                        if pre_faces is not None:
                            store.face[rows[rev]] = pre_faces[rev]
            xs[rows] = vals[:, 0]
            ys[rows] = vals[:, 1]
            zs[rows] = vals[:, 2]
        # legacy per-bite flora drain (kept for any direct commands.eat_flora use;
        # empty when consumption goes through the conserving claim system below)
        if flora is not None and self.flora_bites:
            if geom is not None:
                for fc, ix, iy, amount in self.flora_bites:
                    avail = float(flora[fc, ix, iy])
                    flora[fc, ix, iy] = avail - min(avail, amount)
            else:
                for _fc, ix, iy, amount in self.flora_bites:
                    avail = float(flora[ix, iy])
                    flora[ix, iy] = avail - min(avail, amount)

        # -- resolve consumption claims (energy conservation) -------------------
        # Predation first (credits attackers from prey energy), then grazing
        # (credits eaters from flora). Both distribute only the ACTUAL available
        # resource among contenders, so energy can't be minted from tick-start
        # estimates. Applied after the per-op loop, so a consumer's own set_energy
        # this tick has already landed and the credit stacks on top.
        self._resolve_predation(store, predation_marks)
        self._resolve_field_claims(store, flora, geom, self.eat_claims)
        self._resolve_field_claims(store, plankton, geom, self.plankton_claims)

        self.flora_bites.clear()
        self.ops.clear()
        self.batch_energy.clear()
        self.batch_moves.clear()
        self.batch_flora.clear()
        self.eat_claims.clear()
        self.plankton_claims.clear()
        self.attack_claims.clear()

    def _resolve_predation(self, store: EntityStore, predation_marks: set[int] | None) -> None:
        if not self.attack_claims:
            return
        a_rows = np.fromiter((c[0] for c in self.attack_claims), dtype=np.int64,
                             count=len(self.attack_claims))
        p_rows = np.fromiter((c[1] for c in self.attack_claims), dtype=np.int64,
                             count=len(self.attack_claims))
        amounts = np.fromiter((c[2] for c in self.attack_claims), dtype=np.float64,
                              count=len(self.attack_claims))
        effs = np.fromiter((c[3] for c in self.attack_claims), dtype=np.float64,
                           count=len(self.attack_claims))
        prey, inv = np.unique(p_rows, return_inverse=True)
        inv = inv.reshape(-1)   # numpy 2.0 briefly returned a 2-D inverse
        demand = np.zeros(prey.size)
        np.add.at(demand, inv, amounts)
        avail = store.energy[prey].astype(np.float64)
        scale = np.ones(prey.size)
        nz = demand > 0.0
        scale[nz] = np.minimum(1.0, avail[nz] / demand[nz])      # over-subscribed prey shared out
        actual = amounts * scale[inv]                             # each attacker's real drain
        np.add.at(store.energy, a_rows, (actual * effs).astype(np.float32))   # credit attackers
        drained = np.zeros(prey.size)
        np.add.at(drained, inv, actual)
        store.energy[prey] -= drained.astype(np.float32)          # drain prey once, by total taken
        if predation_marks is not None:
            predation_marks.update(prey[store.energy[prey] <= 0.0].tolist())

    def _resolve_field_claims(self, store: EntityStore, field: np.ndarray | None, geom,
                              claims: list[tuple]) -> None:
        """Distribute a shared resource field (flora or plankton) among its
        claimants at tick end and credit each its real share (energy-conserving)."""
        if field is None or not claims:
            return
        flora = field
        rows = np.concatenate([c[0] for c in claims])
        face = np.concatenate([c[1] for c in claims])
        ix = np.concatenate([c[2] for c in claims])
        iy = np.concatenate([c[3] for c in claims])
        bite = np.concatenate([c[4] for c in claims])
        gain = np.concatenate([c[5] for c in claims])
        s = int(flora.shape[-1])
        key = (face * s + ix) * s + iy
        # np.unique returns (unique, index, inverse) in THAT order regardless of
        # kwarg order; keep the unpack matching it
        cells, first, inv = np.unique(key, return_index=True, return_inverse=True)
        inv = inv.reshape(-1)   # numpy 2.0 briefly returned a 2-D inverse
        demand = np.zeros(cells.size)
        np.add.at(demand, inv, bite)
        gf, gix, giy = face[first], ix[first], iy[first]          # a cell per unique key
        avail = (flora[gf, gix, giy] if geom is not None else flora[gix, giy]).astype(np.float64)
        scale = np.ones(cells.size)
        nz = demand > 0.0
        scale[nz] = np.minimum(1.0, avail[nz] / demand[nz])       # over-grazed cells shared out
        actual = bite * scale[inv]                                # each eater's real bite
        np.add.at(store.energy, rows, (actual * gain).astype(np.float32))     # credit eaters
        taken = np.zeros(cells.size)
        np.add.at(taken, inv, actual)
        if geom is not None:
            flora[gf, gix, giy] -= taken.astype(np.float32)
        else:
            flora[gix, giy] -= taken.astype(np.float32)
