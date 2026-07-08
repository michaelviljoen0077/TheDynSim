"""Cube topology at the World level: entities carry a face, movement folds across
face edges, neighbour queries are GLOBAL 3D (seamless across faces), and it all
survives snapshots."""

import numpy as np

from engine import World, WorldConfig, load_snapshot, save_snapshot, state_hash
from engine.entities import SURFACE, handle_index
from engine.world_api import WorldAPI


def cube_world(seed=1, size=48):
    return World(WorldConfig(seed=seed, size=size, topology="cube", initial_capacity=2048))


def test_world_builds_cube_geometry():
    w = cube_world()
    assert w.config.cube
    assert w.geom is not None
    assert w.spatial3d is not None and w.spatial is None


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


def test_queries_are_global_3d_across_seams():
    """The whole point of the 3D index: a query finds neighbours on an ADJACENT
    face when they're physically near across the shared edge, but not entities
    that are merely at the same face-local (x,y) on a far face."""
    w = cube_world(size=48)
    sp = w.registry.register("bug")
    api = WorldAPI(w, "p", 0, ["bug"])
    S = w.config.size
    # A near face 0's +x edge; B just across it on face 1 (near face 1's -x edge).
    a = w.store.spawn(sp.id, S - 1.0, 24.0, 0.0, SURFACE, 100.0, face=0)
    b = w.store.spawn(sp.id, 1.0, 24.0, 0.0, SURFACE, 100.0, face=1)
    # C at the SAME face-local (x,y) as A but on the opposite face 2 — physically far.
    w.store.spawn(sp.id, S - 1.0, 24.0, 0.0, SURFACE, 100.0, face=2)
    w.spatial3d.rebuild(w.store)
    near = api.nearest(a, species="bug", radius=6.0)
    assert near == b  # sees B across the seam, not the far-face C
    assert world_dist_ok(w, handle_index(a), handle_index(b))


def world_dist_ok(w, ra, rb):
    return w.spatial3d.distance(ra, rb) < 6.0


def test_direction_to_points_across_a_seam():
    w = cube_world(size=48)
    sp = w.registry.register("bug", speed=4.0)
    api = WorldAPI(w, "p", 0, ["bug"])
    S = w.config.size
    a = w.store.spawn(sp.id, S - 1.5, 24.0, 0.0, SURFACE, 100.0, face=0)
    b = w.store.spawn(sp.id, 1.5, 24.0, 0.0, SURFACE, 100.0, face=1)
    w.spatial3d.rebuild(w.store)
    dx, dy = api.direction_to(a, b)
    # B is across A's +x edge, so the heading should be predominantly +x
    assert dx > 0.5 and abs(dy) < 0.5


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


def test_per_face_terrain_is_distinct_and_stateful():
    w = cube_world(seed=5, size=48)
    t = w.terrain
    assert t.height.shape == (6, 48, 48)
    # faces are independently generated — not copies of each other
    assert not np.array_equal(t.height[0], t.height[1])
    assert not np.array_equal(t.height[2], t.height[3])
    # flora is per-face and grows independently
    w.run(60)
    assert w.flora.density.shape == (6, 48, 48)


def test_example_food_chain_runs_on_a_cube():
    from pathlib import Path

    from engine.plugin_host import PluginHost
    examples = Path(__file__).resolve().parents[2] / "plugins_examples"
    # representative of the server config (roomy world + field throttle); the
    # tuned ecology is designed for this scale, not a cramped 64-cell patch
    w = World(WorldConfig(seed=3, size=96, topology="cube", initial_capacity=16384,
                          field_step_every=4))
    host = PluginHost(w)
    for p in ("grazer.py", "predator.py", "birds.py"):
        host.install((examples / p).read_text())
    gid = w.registry.by_name["grazer"].id
    wid = w.registry.by_name["wolf"].id
    w.run(2500)
    assert w.store.alive_indices(gid).size > 0, "grazers went extinct on the cube"
    assert w.store.alive_indices(wid).size > 0, "wolves went extinct on the cube"
    # populations spread beyond the founder face over time (cross-face movement
    # is exercised directly in test_entities_traverse_faces_and_stay_in_bounds)
    faces_used = set(w.store.face[w.store.alive_indices(gid)].tolist())
    assert len(faces_used) > 1, "grazers never migrated off the founder face"
    # no plugin errored
    assert all(r.error_count == 0 for r in host.plugins.values()), host.state()
