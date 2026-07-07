"""Spatial hash correctness: cross-checked against brute force."""

import numpy as np

from engine.entities import SURFACE, EntityStore
from engine.spatial import SpatialHash


def build(n=500, seed=3, size=64.0):
    rng = np.random.default_rng(seed)
    store = EntityStore(capacity=1024, max_prop_slots=2)
    for _ in range(n):
        store.spawn(
            int(rng.integers(0, 3)),
            float(rng.uniform(0, size)), float(rng.uniform(0, size)), 0.0,
            SURFACE, 10.0,
        )
    sh = SpatialHash(size, cell=8.0)
    sh.rebuild(store)
    return store, sh, rng, size


def brute_within(store, x, y, r, species=None):
    idx = np.flatnonzero(store.alive)
    dx = store.px[idx] - x
    dy = store.py[idx] - y
    m = dx * dx + dy * dy <= r * r
    if species is not None:
        m &= store.species_id[idx] == species
    return set(idx[m].tolist())


def test_within_matches_brute_force():
    store, sh, rng, size = build()
    for _ in range(50):
        x, y = float(rng.uniform(0, size)), float(rng.uniform(0, size))
        r = float(rng.uniform(1, 20))
        got = set(sh.within(store, x, y, r, SURFACE))
        assert got == brute_within(store, x, y, r)


def test_within_species_filter():
    store, sh, rng, size = build()
    got = set(sh.within(store, 32, 32, 30, SURFACE, species_id=1))
    assert got == brute_within(store, 32, 32, 30, species=1)


def test_nearest_matches_brute_force():
    store, sh, rng, size = build()
    for _ in range(50):
        x, y = float(rng.uniform(0, size)), float(rng.uniform(0, size))
        r = 15.0
        cands = brute_within(store, x, y, r)
        got = sh.nearest(store, x, y, r, SURFACE)
        if not cands:
            assert got == -1
        else:
            dx = store.px[got] - x
            dy = store.py[got] - y
            best = min(
                (store.px[j] - x) ** 2 + (store.py[j] - y) ** 2 for j in cands
            )
            assert abs((dx * dx + dy * dy) - best) < 1e-6


def test_strata_are_isolated():
    store = EntityStore(capacity=16, max_prop_slots=2)
    store.spawn(0, 10, 10, 0, SURFACE, 1.0)
    from engine.entities import SKY
    store.spawn(0, 10, 10, 0, SKY, 1.0)
    sh = SpatialHash(64.0, cell=8.0)
    sh.rebuild(store)
    assert len(sh.within(store, 10, 10, 5, SURFACE)) == 1
    assert len(sh.within(store, 10, 10, 5, SKY)) == 1
