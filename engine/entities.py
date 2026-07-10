"""SoA entity store with generational handles, plus the species registry.

A handle is an int: (row_index << 16) | generation. The freelist recycles rows
LIFO (deterministic); each reuse bumps the row's generation so stale handles
fail the generation check loudly instead of addressing a recycled stranger.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

UNDERGROUND, SURFACE, SKY = 0, 1, 2

GEN_BITS = 16
GEN_MASK = (1 << GEN_BITS) - 1


def handle_index(handle: int) -> int:
    return handle >> GEN_BITS


def handle_generation(handle: int) -> int:
    return handle & GEN_MASK


def make_handle(row: int, generation: int) -> int:
    return (row << GEN_BITS) | int(generation)


@dataclass
class Species:
    id: int
    name: str
    plugin: str          # owning plugin name ("" = engine-owned)
    size: float = 1.0
    color: str = "#cccccc"
    speed: float = 2.5      # engine-enforced max distance per tick (see CommandBuffer.apply)
    swim_speed: float = 0.0  # max speed while on water; 0 = cannot swim (drowns on SURFACE water)
    aquatic: bool = False    # confined to water: the engine reverts any move onto land (fish etc.)
    lifespan: int = 0        # max age in ticks; 0 = no old-age death (engine sweeps age>lifespan)
    strata: tuple[int, ...] = (SURFACE,)
    prop_slots: dict[str, int] = field(default_factory=dict)  # prop name -> slot
    # heritable genes: name -> slot in the per-entity genome. Founder values +
    # mutation sigma live in gene_defaults / gene_sigma. Offspring inherit the
    # parent's genome with gaussian mutation (see WorldAPI.breed / world.mutate).
    gene_slots: dict[str, int] = field(default_factory=dict)
    gene_defaults: dict[str, float] = field(default_factory=dict)
    gene_sigma: float = 0.08      # per-generation mutation std-dev (fraction of value)


class SpeciesRegistry:
    def __init__(self, max_prop_slots: int, max_gene_slots: int = 6) -> None:
        self.max_prop_slots = max_prop_slots
        self.max_gene_slots = max_gene_slots
        self.by_name: dict[str, Species] = {}
        self.by_id: list[Species] = []

    def register(
        self,
        name: str,
        plugin: str = "",
        size: float = 1.0,
        color: str = "#cccccc",
        speed: float = 2.5,
        swim_speed: float = 0.0,
        aquatic: bool = False,
        lifespan: int = 0,
        strata: tuple[int, ...] = (SURFACE,),
        props: tuple[str, ...] = (),
        genes: dict[str, float] | None = None,
        gene_sigma: float = 0.08,
    ) -> Species:
        if name in self.by_name:
            raise ValueError(f"species {name!r} already registered")
        if len(props) > self.max_prop_slots:
            raise ValueError(f"species {name!r} declares {len(props)} props; max is {self.max_prop_slots}")
        genes = genes or {}
        if len(genes) > self.max_gene_slots:
            raise ValueError(
                f"species {name!r} declares {len(genes)} genes; max is {self.max_gene_slots}")
        sp = Species(
            id=len(self.by_id),
            name=name,
            plugin=plugin,
            size=size,
            color=color,
            speed=max(0.1, min(float(speed), 8.0)),
            swim_speed=max(0.0, min(float(swim_speed), 8.0)),
            aquatic=bool(aquatic),
            lifespan=max(0, int(lifespan)),
            strata=tuple(strata),
            prop_slots={p: i for i, p in enumerate(props)},
            gene_slots={g: i for i, g in enumerate(genes)},
            gene_defaults={g: float(v) for g, v in genes.items()},
            gene_sigma=max(0.0, float(gene_sigma)),
        )
        self.by_name[name] = sp
        self.by_id.append(sp)
        return sp

    def adopt(self, name: str, new_plugin: str, size: float | None = None,
              color: str | None = None, speed: float | None = None,
              swim_speed: float | None = None, lifespan: int | None = None) -> Species:
        """Transfer species ownership to a replacement plugin (lineage mutation).

        Prop slots are preserved — live entities carry data in them; a mutation
        may restyle and re-tune the species but not re-shape its state layout.
        """
        sp = self.by_name[name]
        sp.plugin = new_plugin
        if size is not None:
            sp.size = size
        if color is not None:
            sp.color = color
        if speed is not None:
            sp.speed = max(0.1, min(float(speed), 8.0))
        if swim_speed is not None:
            sp.swim_speed = max(0.0, min(float(swim_speed), 8.0))
        if lifespan is not None:
            sp.lifespan = max(0, int(lifespan))
        return sp

    def speeds_array(self) -> np.ndarray:
        """Per-species max speed, indexed by species id (for CommandBuffer.apply)."""
        return np.array([s.speed for s in self.by_id] or [2.5], dtype=np.float32)

    def swim_speeds_array(self) -> np.ndarray:
        """Per-species max swim speed (0 = drowns), indexed by species id."""
        return np.array([s.swim_speed for s in self.by_id] or [0.0], dtype=np.float32)

    def aquatic_array(self) -> np.ndarray:
        """Per-species 'aquatic' flag (confined to water), indexed by species id."""
        return np.array([s.aquatic for s in self.by_id] or [False], dtype=bool)

    def lifespans_array(self) -> np.ndarray:
        """Per-species lifespan in ticks (0 = immortal), indexed by species id."""
        return np.array([s.lifespan for s in self.by_id] or [0], dtype=np.int64)

    def heading_slots_array(self) -> np.ndarray:
        """Per-species prop slot of a prop literally named "heading" (else -1),
        indexed by species id. The engine uses this to keep a roaming creature's
        heading continuous when it folds across a cube seam (so it doesn't
        ping-pong along face edges)."""
        return np.array(
            [s.prop_slots.get("heading", -1) for s in self.by_id] or [-1], dtype=np.int64
        )

    def default_genome(self, sp: Species) -> np.ndarray:
        """A founder's genome: each declared gene at its founder value, zeros
        elsewhere. Offspring inherit a mutated copy of a parent's genome instead."""
        g = np.zeros(self.max_gene_slots, dtype=np.float32)
        for name, slot in sp.gene_slots.items():
            g[slot] = sp.gene_defaults[name]
        return g

    def gene_slot_array(self, gene: str) -> np.ndarray:
        """Per-species genome slot of a named gene (else -1), indexed by species id.
        The engine reads the "speed" gene through this to scale each entity's speed
        cap by its own heritable value (natural selection on mobility)."""
        return np.array(
            [s.gene_slots.get(gene, -1) for s in self.by_id] or [-1], dtype=np.int64
        )

    def to_state(self) -> list[dict]:
        return [
            {
                "id": s.id, "name": s.name, "plugin": s.plugin, "size": s.size,
                "color": s.color, "speed": s.speed, "swim_speed": s.swim_speed,
                "aquatic": s.aquatic,
                "lifespan": s.lifespan, "strata": list(s.strata), "prop_slots": s.prop_slots,
                "gene_slots": s.gene_slots, "gene_defaults": s.gene_defaults,
                "gene_sigma": s.gene_sigma,
            }
            for s in self.by_id
        ]

    @classmethod
    def from_state(cls, state: list[dict], max_prop_slots: int,
                   max_gene_slots: int = 6) -> SpeciesRegistry:
        reg = cls(max_prop_slots, max_gene_slots)
        for d in state:
            sp = Species(
                id=d["id"], name=d["name"], plugin=d["plugin"], size=d["size"],
                color=d["color"], speed=d.get("speed", 2.5),
                swim_speed=d.get("swim_speed", 0.0), aquatic=d.get("aquatic", False),
                lifespan=d.get("lifespan", 0),
                strata=tuple(d["strata"]), prop_slots=dict(d["prop_slots"]),
                gene_slots=dict(d.get("gene_slots", {})),
                gene_defaults=dict(d.get("gene_defaults", {})),
                gene_sigma=d.get("gene_sigma", 0.08),
            )
            reg.by_name[sp.name] = sp
            reg.by_id.append(sp)
        return reg


class EntityStore:
    ARRAY_FIELDS = (
        "alive", "generation", "species_id", "plugin_id",
        "px", "py", "pz", "stratum", "face", "hidden", "energy", "age", "props", "genome",
    )

    def __init__(self, capacity: int, max_prop_slots: int, max_gene_slots: int = 6) -> None:
        self.capacity = capacity
        self.max_prop_slots = max_prop_slots
        self.max_gene_slots = max_gene_slots
        self.alive = np.zeros(capacity, dtype=bool)
        self.generation = np.zeros(capacity, dtype=np.uint16)
        self.species_id = np.zeros(capacity, dtype=np.uint16)
        self.plugin_id = np.full(capacity, -1, dtype=np.int16)
        self.px = np.zeros(capacity, dtype=np.float32)
        self.py = np.zeros(capacity, dtype=np.float32)
        self.pz = np.zeros(capacity, dtype=np.float32)
        self.stratum = np.full(capacity, SURFACE, dtype=np.uint8)
        self.face = np.zeros(capacity, dtype=np.uint8)   # cube face (0 for flat/wrap)
        self.hidden = np.zeros(capacity, dtype=bool)      # burrowed/hidden: unseen by queries
        self.energy = np.zeros(capacity, dtype=np.float32)
        self.age = np.zeros(capacity, dtype=np.uint32)
        self.props = np.zeros((capacity, max_prop_slots), dtype=np.float32)
        self.genome = np.zeros((capacity, max_gene_slots), dtype=np.float32)  # heritable genes
        # LIFO freelist, highest row first so early spawns use low rows
        self.freelist: list[int] = list(range(capacity - 1, -1, -1))
        self.count = 0

    # -- lifecycle ---------------------------------------------------------

    def _grow(self) -> None:
        old = self.capacity
        new = old * 2
        for name in self.ARRAY_FIELDS:
            arr = getattr(self, name)
            shape = (new,) + arr.shape[1:]
            grown = np.zeros(shape, dtype=arr.dtype)
            grown[:old] = arr
            setattr(self, name, grown)
        self.plugin_id[old:] = -1
        self.stratum[old:] = SURFACE
        self.freelist.extend(range(new - 1, old - 1, -1))
        self.capacity = new

    def spawn(
        self,
        species_id: int,
        x: float, y: float, z: float,
        stratum: int,
        energy: float,
        plugin_id: int = -1,
        face: int = 0,
        genome: np.ndarray | None = None,
    ) -> int:
        if not self.freelist:
            self._grow()
        i = self.freelist.pop()
        self.alive[i] = True
        self.species_id[i] = species_id
        self.plugin_id[i] = plugin_id
        self.px[i], self.py[i], self.pz[i] = x, y, z
        self.stratum[i] = stratum
        self.face[i] = face
        self.hidden[i] = False
        self.energy[i] = energy
        self.age[i] = 0
        self.props[i, :] = 0.0
        if genome is not None:
            self.genome[i, :] = genome
        else:
            self.genome[i, :] = 0.0
        self.count += 1
        return (i << GEN_BITS) | int(self.generation[i])

    def remove(self, handle: int) -> bool:
        i = handle >> GEN_BITS
        if not self.is_valid(handle):
            return False
        self.alive[i] = False
        self.generation[i] = np.uint16((int(self.generation[i]) + 1) & GEN_MASK)
        self.freelist.append(i)
        self.count -= 1
        return True

    def is_valid(self, handle: int) -> bool:
        i = handle >> GEN_BITS
        return (
            0 <= i < self.capacity
            and bool(self.alive[i])
            and int(self.generation[i]) == (handle & GEN_MASK)
        )

    # -- queries -----------------------------------------------------------

    def alive_indices(self, species_id: int | None = None) -> np.ndarray:
        if species_id is None:
            return np.flatnonzero(self.alive)
        return np.flatnonzero(self.alive & (self.species_id == species_id))

    def handles_of(self, indices: np.ndarray) -> list[int]:
        gens = self.generation[indices]
        return [
            (int(i) << GEN_BITS) | int(g)
            for i, g in zip(indices.tolist(), gens.tolist(), strict=True)
        ]

    # -- snapshot ----------------------------------------------------------

    def to_arrays(self) -> dict[str, np.ndarray]:
        d = {name: getattr(self, name) for name in self.ARRAY_FIELDS}
        d["freelist"] = np.asarray(self.freelist, dtype=np.int64)
        return d

    @classmethod
    def from_arrays(cls, arrays: dict[str, np.ndarray], max_prop_slots: int) -> EntityStore:
        capacity = int(arrays["alive"].shape[0])
        store = cls.__new__(cls)
        store.capacity = capacity
        store.max_prop_slots = max_prop_slots
        for name in cls.ARRAY_FIELDS:
            if name in arrays:
                setattr(store, name, arrays[name].copy())
        # genome may be absent in snapshots written before genes existed
        if "genome" not in arrays:
            store.genome = np.zeros((capacity, 6), dtype=np.float32)
        store.max_gene_slots = int(store.genome.shape[1])
        store.freelist = arrays["freelist"].tolist()
        store.count = int(store.alive.sum())
        return store
