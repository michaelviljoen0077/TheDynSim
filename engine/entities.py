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


@dataclass
class Species:
    id: int
    name: str
    plugin: str          # owning plugin name ("" = engine-owned)
    size: float = 1.0
    color: str = "#cccccc"
    strata: tuple[int, ...] = (SURFACE,)
    prop_slots: dict[str, int] = field(default_factory=dict)  # prop name -> slot


class SpeciesRegistry:
    def __init__(self, max_prop_slots: int) -> None:
        self.max_prop_slots = max_prop_slots
        self.by_name: dict[str, Species] = {}
        self.by_id: list[Species] = []

    def register(
        self,
        name: str,
        plugin: str = "",
        size: float = 1.0,
        color: str = "#cccccc",
        strata: tuple[int, ...] = (SURFACE,),
        props: tuple[str, ...] = (),
    ) -> Species:
        if name in self.by_name:
            raise ValueError(f"species {name!r} already registered")
        if len(props) > self.max_prop_slots:
            raise ValueError(f"species {name!r} declares {len(props)} props; max is {self.max_prop_slots}")
        sp = Species(
            id=len(self.by_id),
            name=name,
            plugin=plugin,
            size=size,
            color=color,
            strata=tuple(strata),
            prop_slots={p: i for i, p in enumerate(props)},
        )
        self.by_name[name] = sp
        self.by_id.append(sp)
        return sp

    def to_state(self) -> list[dict]:
        return [
            {
                "id": s.id, "name": s.name, "plugin": s.plugin, "size": s.size,
                "color": s.color, "strata": list(s.strata), "prop_slots": s.prop_slots,
            }
            for s in self.by_id
        ]

    @classmethod
    def from_state(cls, state: list[dict], max_prop_slots: int) -> SpeciesRegistry:
        reg = cls(max_prop_slots)
        for d in state:
            sp = Species(
                id=d["id"], name=d["name"], plugin=d["plugin"], size=d["size"],
                color=d["color"], strata=tuple(d["strata"]), prop_slots=dict(d["prop_slots"]),
            )
            reg.by_name[sp.name] = sp
            reg.by_id.append(sp)
        return reg


class EntityStore:
    ARRAY_FIELDS = (
        "alive", "generation", "species_id", "plugin_id",
        "px", "py", "pz", "stratum", "energy", "age", "props",
    )

    def __init__(self, capacity: int, max_prop_slots: int) -> None:
        self.capacity = capacity
        self.max_prop_slots = max_prop_slots
        self.alive = np.zeros(capacity, dtype=bool)
        self.generation = np.zeros(capacity, dtype=np.uint16)
        self.species_id = np.zeros(capacity, dtype=np.uint16)
        self.plugin_id = np.full(capacity, -1, dtype=np.int16)
        self.px = np.zeros(capacity, dtype=np.float32)
        self.py = np.zeros(capacity, dtype=np.float32)
        self.pz = np.zeros(capacity, dtype=np.float32)
        self.stratum = np.full(capacity, SURFACE, dtype=np.uint8)
        self.energy = np.zeros(capacity, dtype=np.float32)
        self.age = np.zeros(capacity, dtype=np.uint32)
        self.props = np.zeros((capacity, max_prop_slots), dtype=np.float32)
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
    ) -> int:
        if not self.freelist:
            self._grow()
        i = self.freelist.pop()
        self.alive[i] = True
        self.species_id[i] = species_id
        self.plugin_id[i] = plugin_id
        self.px[i], self.py[i], self.pz[i] = x, y, z
        self.stratum[i] = stratum
        self.energy[i] = energy
        self.age[i] = 0
        self.props[i, :] = 0.0
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
            setattr(store, name, arrays[name].copy())
        store.freelist = arrays["freelist"].tolist()
        store.count = int(store.alive.sum())
        return store
