"""Uniform-grid spatial hash, bucketed per (stratum, species).

Two query paths by design (Spike A finding, docs/spikes.md): scalar per-entity
queries run in pure Python against cached tick-start position lists (NumPy
scalar indexing is ~10x slower per element); bulk queries use vectorized NumPy
over the entity arrays. The cached positions equal tick-start state, which is
exactly the command-buffer read semantics plugins are promised.

Per-species buckets keep query cost proportional to the *queried* species'
density, not total world density (a 5x candidate reduction in the benchmark mix).
"""

from __future__ import annotations

import math

import numpy as np

from engine.entities import GEN_BITS, EntityStore


class SpatialHash:
    def __init__(self, world_size: float, cell: float = 8.0, wrap: bool = False) -> None:
        self.world_size = world_size
        self.cell = cell
        self.wrap = wrap                       # toroidal topology: cells/distances wrap
        self.ncell = max(1, int(world_size / cell))
        # (stratum, species_id) -> {(cx, cy) -> [row, ...]}
        self.buckets: dict[tuple[int, int], dict[tuple[int, int], list[int]]] = {}
        self._layers_by_stratum: dict[int, list[dict]] = {}
        self.xs: list[float] = []
        self.ys: list[float] = []

    def _cell_span(self, c0: int, reach: int) -> list[int]:
        """Cell indices c0-reach..c0+reach; when wrapping, each cell at most once."""
        if not self.wrap:
            return list(range(c0 - reach, c0 + reach + 1))
        # a reach wider than the world would otherwise visit (and report) cells twice
        return list(dict.fromkeys((c0 + d) % self.ncell for d in range(-reach, reach + 1)))

    def _delta(self, a: float, b: float) -> float:
        """b - a, min-image (shortest path across the seam) when wrapping."""
        d = b - a
        if self.wrap:
            if d > self.world_size * 0.5:
                d -= self.world_size
            elif d < -self.world_size * 0.5:
                d += self.world_size
        return d

    def rebuild(self, store: EntityStore) -> None:
        # tick-start position cache: plain Python floats, indexed by row
        self.xs = store.px.tolist()
        self.ys = store.py.tolist()
        buckets: dict[tuple[int, int], dict[tuple[int, int], list[int]]] = {}
        idx = np.flatnonzero(store.alive)
        if idx.size:
            inv = 1.0 / self.cell
            cx = (store.px[idx] * inv).astype(np.int32)
            cy = (store.py[idx] * inv).astype(np.int32)
            if self.wrap:
                # size need not be a multiple of cell: the partial last cell folds
                # into cell 0, matching the wrapped indices queries look up
                cx %= self.ncell
                cy %= self.ncell
            cx, cy = cx.tolist(), cy.tolist()
            strata = store.stratum[idx].tolist()
            species = store.species_id[idx].tolist()
            for i, sx, sy, st, sp in zip(idx.tolist(), cx, cy, strata, species, strict=True):
                layer = buckets.setdefault((st, sp), {})
                cell_rows = layer.setdefault((sx, sy), [])
                cell_rows.append(i)
        self.buckets = buckets
        layers: dict[int, list[dict]] = {}
        # Sort by (stratum, species_id) so the species-none `within`/`nearest`
        # layer order is a stable function of species id — never of which species
        # happens to hold the lowest alive row (which unrelated plugins can shift).
        for st, _sp in sorted(buckets.keys()):
            layers.setdefault(st, []).append(buckets[(st, _sp)])
        self._layers_by_stratum = layers

    def _target_layers(self, stratum: int, species_id: int | None) -> list[dict]:
        if species_id is not None:
            layer = self.buckets.get((stratum, species_id))
            return [layer] if layer else []
        return self._layers_by_stratum.get(stratum, [])

    # -- scalar path (pure Python; the plugin hot path) ----------------------

    def within(
        self,
        store: EntityStore,
        x: float, y: float,
        radius: float,
        stratum: int,
        species_id: int | None = None,
        exclude_row: int = -1,
    ) -> list[int]:
        """Row indices within radius, deterministic order (species/cell-major, insertion order)."""
        layers = self._target_layers(stratum, species_id)
        if not layers:
            return []
        r2 = radius * radius
        c0x, c0y = int(x / self.cell), int(y / self.cell)
        reach = max(1, math.ceil(radius / self.cell))
        delta = self._delta
        span_x = self._cell_span(c0x, reach)
        span_y = self._cell_span(c0y, reach)
        xs, ys = self.xs, self.ys
        out: list[int] = []
        for layer in layers:
            for cx in span_x:
                for cy in span_y:
                    rows = layer.get((cx, cy))
                    if not rows:
                        continue
                    for j in rows:
                        if j == exclude_row:
                            continue
                        ddx = delta(x, xs[j])
                        ddy = delta(y, ys[j])
                        if ddx * ddx + ddy * ddy <= r2:
                            out.append(j)
        return out

    def nearest(
        self,
        store: EntityStore,
        x: float, y: float,
        radius: float,
        stratum: int,
        species_id: int | None = None,
        exclude_row: int = -1,
    ) -> int:
        """Nearest row index within radius, or -1. Ties break to lowest row (deterministic)."""
        layers = self._target_layers(stratum, species_id)
        if not layers:
            return -1
        best, best_d = -1, radius * radius + 1e-9
        c0x, c0y = int(x / self.cell), int(y / self.cell)
        reach = max(1, math.ceil(radius / self.cell))
        delta = self._delta
        span_x = self._cell_span(c0x, reach)
        span_y = self._cell_span(c0y, reach)
        xs, ys = self.xs, self.ys
        for layer in layers:
            for cx in span_x:
                for cy in span_y:
                    rows = layer.get((cx, cy))
                    if not rows:
                        continue
                    for j in rows:
                        if j == exclude_row:
                            continue
                        ddx = delta(x, xs[j])
                        ddy = delta(y, ys[j])
                        d = ddx * ddx + ddy * ddy
                        if d < best_d or (d == best_d and best != -1 and j < best):
                            best_d, best = d, j
        return best

    # -- bulk path (vectorized; the WorldAPI helper idiom) --------------------

    def neighbor_counts(self, store: EntityStore, rows: np.ndarray, radius: float) -> np.ndarray:
        """Vectorized same-cell-block density estimate for `rows` (approximate, cheap)."""
        if rows.size == 0:
            return np.zeros(0, dtype=np.int32)
        cx = (store.px[rows] / self.cell).astype(np.int64)
        cy = (store.py[rows] / self.cell).astype(np.int64)
        key = cx * self.ncell + cy
        uniq, inverse, counts = np.unique(key, return_inverse=True, return_counts=True)
        return counts[inverse].astype(np.int32)


def rows_to_handles(store: EntityStore, rows: list[int]) -> list[int]:
    return [(j << GEN_BITS) | int(store.generation[j]) for j in rows]
