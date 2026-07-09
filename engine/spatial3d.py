"""Global 3D spatial index for the cube planet.

Entities live on a 2D surface but interact in 3D: this hashes every entity by its
3D position on the cube and answers neighbour queries by true 3D radius. A wolf
near a face edge finds prey on the adjacent face automatically, and the 8 corners
where three faces meet just work — there is NO face special-casing in querying.
Faces remain only as storage for the terrain/weather/flora grids.

Movement still folds per-face (CubeGeometry.step); only queries are global here.
"""

from __future__ import annotations

import numpy as np

from engine.cube import positions_3d
from engine.entities import GEN_BITS, EntityStore

CELL3D = 12.0  # 3D bucket size (world units); ~face cell scale, cheap query reach


class Spatial3D:
    def __init__(self, size: int, cell: float = CELL3D) -> None:
        self.size = size
        self.cell = cell
        # (stratum, species_id) -> {(cx,cy,cz) -> [row,...]}
        self.buckets: dict[tuple[int, int], dict[tuple[int, int, int], list[int]]] = {}
        self._layers_by_stratum: dict[int, list[dict]] = {}
        self._posarr: np.ndarray = np.zeros((0, 3))  # 3D pos indexed by store row

    def rebuild(self, store: EntityStore) -> None:
        idx = np.flatnonzero(store.alive)
        buckets: dict[tuple[int, int], dict[tuple[int, int, int], list[int]]] = {}
        # positions kept as a row-indexed NumPy array so candidate distances can
        # be computed in one vectorized shot per query (the per-candidate Python
        # distance was ~a third of the whole tick)
        posarr = np.full((store.capacity, 3), np.inf, dtype=np.float64)
        if idx.size:
            pos = positions_3d(store.face[idx], store.px[idx], store.py[idx], self.size)
            posarr[idx] = pos     # every alive entity is a valid query ORIGIN...
            cells = np.floor(pos / self.cell).astype(np.int64)
            strata = store.stratum[idx].tolist()
            species = store.species_id[idx].tolist()
            hidden = store.hidden[idx].tolist()
            rows = idx.tolist()
            cl = cells.tolist()
            for i, st, sp, hid, c in zip(rows, strata, species, hidden, cl, strict=True):
                # ...but a hidden/burrowed entity is NOT put in the buckets, so no
                # other creature's nearest/within can find it (it can still query).
                if hid:
                    continue
                layer = buckets.setdefault((st, sp), {})
                layer.setdefault((c[0], c[1], c[2]), []).append(i)
        self.buckets = buckets
        self._posarr = posarr
        layers: dict[int, list[dict]] = {}
        for st, _sp in sorted(buckets.keys()):
            layers.setdefault(st, []).append(buckets[(st, _sp)])
        self._layers_by_stratum = layers

    def pos_of(self, row: int) -> tuple[float, float, float] | None:
        if row >= self._posarr.shape[0] or not np.isfinite(self._posarr[row, 0]):
            return None
        p = self._posarr[row]
        return (float(p[0]), float(p[1]), float(p[2]))

    def _target_layers(self, stratum: int, species_id: int | None) -> list[dict]:
        if species_id is not None:
            layer = self.buckets.get((stratum, species_id))
            return [layer] if layer else []
        return self._layers_by_stratum.get(stratum, [])

    def _candidate_rows(self, px: float, py: float, pz: float, radius: float,
                        layers: list[dict]) -> list[int]:
        """Row ids in the 3D cell neighbourhood of the query point (deterministic
        order: layer, then cell-major, then insertion order)."""
        reach = max(1, int(radius / self.cell) + 1)
        c0x, c0y, c0z = int(np.floor(px / self.cell)), int(np.floor(py / self.cell)), \
            int(np.floor(pz / self.cell))
        out: list[int] = []
        for layer in layers:
            for dx in range(-reach, reach + 1):
                for dy in range(-reach, reach + 1):
                    for dz in range(-reach, reach + 1):
                        rows = layer.get((c0x + dx, c0y + dy, c0z + dz))
                        if rows:
                            out.extend(rows)
        return out

    def within(self, store: EntityStore, row: int, radius: float, stratum: int,
               species_id: int | None = None) -> list[int]:
        p = self.pos_of(row)
        if p is None:
            return []
        layers = self._target_layers(stratum, species_id)
        if not layers:
            return []
        cand = self._candidate_rows(p[0], p[1], p[2], radius, layers)
        if not cand:
            return []
        rows = np.fromiter((j for j in cand if j != row), dtype=np.int64)
        if rows.size == 0:
            return []
        d = self._posarr[rows] - np.array(p)     # vectorized distance
        d2 = np.einsum("ij,ij->i", d, d)
        hit = rows[d2 <= radius * radius]
        hit.sort()                                # deterministic
        return hit.tolist()

    def nearest(self, store: EntityStore, row: int, radius: float, stratum: int,
                species_id: int | None = None) -> int:
        p = self.pos_of(row)
        if p is None:
            return -1
        layers = self._target_layers(stratum, species_id)
        if not layers:
            return -1
        cand = self._candidate_rows(p[0], p[1], p[2], radius, layers)
        if not cand:
            return -1
        rows = np.fromiter((j for j in cand if j != row), dtype=np.int64)
        if rows.size == 0:
            return -1
        d = self._posarr[rows] - np.array(p)
        d2 = np.einsum("ij,ij->i", d, d)
        within = d2 <= radius * radius
        if not within.any():
            return -1
        rows, d2 = rows[within], d2[within]
        dmin = d2.min()
        # tie-break to the lowest row id (deterministic)
        return int(rows[d2 == dmin].min())

    def distance(self, row_a: int, row_b: int) -> float:
        a = self.pos_of(row_a)
        b = self.pos_of(row_b)
        if a is None or b is None:
            return float("inf")
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        return float((dx * dx + dy * dy + dz * dz) ** 0.5)


def rows_to_handles(store: EntityStore, rows: list[int]) -> list[int]:
    return [(j << GEN_BITS) | int(store.generation[j]) for j in rows]
