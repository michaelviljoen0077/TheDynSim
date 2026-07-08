"""Cube topology at the World level (Layer 2): entities carry a face, movement
folds across face edges, queries are face-local, and it all survives snapshots."""

import numpy as np

from engine import World, WorldConfig, load_snapshot, save_snapshot, state_hash
from engine.entities import SURFACE, handle_index


def cube_world(seed=1, size=48):
    return World(WorldConfig(seed=seed, size=size, topology="cube", initial_capacity=2048))


def test_world_builds_cube_geometry():
    w = cube_world()
    assert w.config.cube
    assert w.geom is not None
    assert w.spatial.faced


def test_entities_traverse_faces_and_stay_in_bounds():
    w = cube_world()
    sp = w.registry.register("rover", speed=6.0, swim_speed=8.0)  # amphibious: no drown noise
    # one entity per face, all near an edge, all pushed east every tick
    handles = []
    for f in range(6):
        handles.append(w.store.spawn(sp.id, w.config.size - 2.0, 24.0, 0.0, SURFACE, 100.0, face=f))
    faces_seen = {f: set() for f in range(6)}
    prng = w.plugin_rng("drive")
    for _ in range(400):
        for h in handles:
            if w.store.is_valid(h):
                w.commands.move(h, 3.0, float(prng.uniform(-1, 1)))
        w.step()
        for h in handles:
            i = handle_index(h)
            if w.store.is_valid(h):
                assert 0 <= w.store.px[i] < w.config.size
                assert 0 <= w.store.py[i] < w.config.size
                faces_seen[handle_index(handles[0])].add(int(w.store.face[i]))
    # the entity that started on face 0 should have visited more than one face
    assert len(faces_seen[handle_index(handles[0])]) > 1


def test_queries_are_face_local():
    w = cube_world()
    sp = w.registry.register("bug")
    # same (x,y) on two different faces — must NOT see each other
    a = w.store.spawn(sp.id, 24.0, 24.0, 0.0, SURFACE, 100.0, face=0)
    w.store.spawn(sp.id, 24.0, 24.0, 0.0, SURFACE, 100.0, face=1)
    w.spatial.rebuild(w.store)
    near = w.spatial.nearest(w.store, 24.0, 24.0, 10.0, SURFACE, species_id=sp.id,
                             exclude_row=handle_index(a), face=0)
    assert near == -1  # the face-1 entity is invisible from face 0


def test_cube_determinism_and_snapshot(tmp_path):
    def build(seed):
        w = cube_world(seed=seed)
        sp = w.registry.register("c", speed=4.0, swim_speed=8.0)
        for k in range(60):
            w.store.spawn(sp.id, float(w.rng.uniform(0, 48)), float(w.rng.uniform(0, 48)),
                          0.0, SURFACE, 100.0, face=k % 6)
        return w, sp

    a, spa = build(7)
    prng = a.plugin_rng("p")
    for _ in range(150):
        for h in a.store.handles_of(a.store.alive_indices(spa.id)):
            a.commands.move(h, float(prng.uniform(-4, 4)), float(prng.uniform(-4, 4)))
        a.step()
    # snapshot mid-run, restore, and confirm identical continuation (face included)
    p = save_snapshot(a, tmp_path / "cube.npz")
    b = load_snapshot(p)
    assert b.config.cube and b.geom is not None
    assert state_hash(a) == state_hash(b)
    assert np.array_equal(a.store.face, b.store.face)

    prng_a = a.plugin_rng("p2")
    prng_b = b.plugin_rng("p2")
    for _ in range(80):
        for h in a.store.handles_of(a.store.alive_indices(spa.id)):
            a.commands.move(h, float(prng_a.uniform(-4, 4)), float(prng_a.uniform(-4, 4)))
        a.step()
        for h in b.store.handles_of(b.store.alive_indices(spa.id)):
            b.commands.move(h, float(prng_b.uniform(-4, 4)), float(prng_b.uniform(-4, 4)))
        b.step()
    assert state_hash(a) == state_hash(b)


def test_flat_world_unaffected():
    """face defaults to 0 everywhere; a flat world behaves exactly as before."""
    w = World(WorldConfig(seed=1, size=48, initial_capacity=256))
    assert w.geom is None and not w.spatial.faced
    sp = w.registry.register("bug", speed=8.0)
    h = w.store.spawn(sp.id, 10.0, 10.0, 0.0, SURFACE, 100.0)
    assert int(w.store.face[handle_index(h)]) == 0
    w.commands.move(h, 5.0, 0.0)
    w.step()
    assert w.store.px[handle_index(h)] == 15.0
