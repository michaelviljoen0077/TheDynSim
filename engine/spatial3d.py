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
        self.pos: np.ndarray = np.zeros((0, 3))   # 3D pos per alive row (aligned to `rows`)
        self._row_pos: dict[int, tuple[float, float, float]] = {}

    def rebuild(self, store: EntityStore) -> None:
        idx = np.flatnonzero(store.alive)
        buckets: dict[tuple[int, int], dict[tuple[int, int, int], list[int]]] = {}
        row_pos: dict[int, tuple[float, float, float]] = {}
        if idx.size:
            pos = positions_3d(store.face[idx], store.px[idx], store.py[idx], self.size)
            cells = np.floor(pos / self.cell).astype(np.int64)
            strata = store.stratum[idx].tolist()
            species = store.species_id[idx].tolist()
            rows = idx.tolist()
            pl = pos.tolist()
            cl = cells.tolist()
            for i, st, sp, p, c in zip(rows, strata, species, pl, cl, strict=True):
                row_pos[i] = (p[0], p[1], p[2])
                layer = buckets.setdefault((st, sp), {})
                layer.setdefault((c[0], c[1], c[2]), []).append(i)
        self.buckets = buckets
        self._row_pos = row_pos
        layers: dict[int, list[dict]] = {}
        for st, _sp in sorted(buckets.keys()):
            layers.setdefault(st, []).append(buckets[(st, _sp)])
        self._layers_by_stratum = layers

    def pos_of(self, row: int) -> tuple[float, float, float] | None:
        return self._row_pos.get(row)

    def _target_layers(self, stratum: int, species_id: int | None) -> list[dict]:
        if species_id is not None:
            layer = self.buckets.get((stratum, species_id))
            return [layer] if layer else []
        return self._layers_by_stratum.get(stratum, [])

    def _candidates(self, px: float, py: float, pz: float, radius: float,
                    layers: list[dict]):
        reach = max(1, int(radius / self.cell) + 1)
        c0x, c0y, c0z = int(np.floor(px / self.cell)), int(np.floor(py / self.cell)), \
            int(np.floor(pz / self.cell))
        for layer in layers:
            for dx in range(-reach, reach + 1):
                for dy in range(-reach, reach + 1):
                    for dz in range(-reach, reach + 1):
                        rows = layer.get((c0x + dx, c0y + dy, c0z + dz))
                        if rows:
                            yield from rows

    def within(self, store: EntityStore, row: int, radius: float, stratum: int,
               species_id: int | None = None) -> list[int]:
        p = self._row_pos.get(row)
        if p is None:
            return []
        layers = self._target_layers(stratum, species_id)
        if not layers:
            return []
        r2 = radius * radius
        rp = self._row_pos
        out: list[int] = []
        for j in self._candidates(p[0], p[1], p[2], radius, layers):
            if j == row:
                continue
            q = rp[j]
            dx, dy, dz = q[0] - p[0], q[1] - p[1], q[2] - p[2]
            if dx * dx + dy * dy + dz * dz <= r2:
                out.append(j)
        return out

    def nearest(self, store: EntityStore, row: int, radius: float, stratum: int,
                species_id: int | None = None) -> int:
        p = self._row_pos.get(row)
        if p is None:
            return -1
        layers = self._target_layers(stratum, species_id)
        if not layers:
            return -1
        best, best_d = -1, radius * radius + 1e-9
        rp = self._row_pos
        for j in self._candidates(p[0], p[1], p[2], radius, layers):
            if j == row:
                continue
            q = rp[j]
            dx, dy, dz = q[0] - p[0], q[1] - p[1], q[2] - p[2]
            d = dx * dx + dy * dy + dz * dz
            if d < best_d or (d == best_d and best != -1 and j < best):
                best_d, best = d, j
        return best

    def distance(self, row_a: int, row_b: int) -> float:
        a, b = self._row_pos.get(row_a), self._row_pos.get(row_b)
        if a is None or b is None:
            return float("inf")
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        return float((dx * dx + dy * dy + dz * dz) ** 0.5)


def rows_to_handles(store: EntityStore, rows: list[int]) -> list[int]:
    return [(j << GEN_BITS) | int(store.generation[j]) for j in rows]
