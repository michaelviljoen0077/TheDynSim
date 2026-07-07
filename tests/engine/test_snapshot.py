"""Story 1.4: byte-exact snapshot/restore — the mechanism shadow forks and rollback trust."""

import time

from engine import World, WorldConfig, load_snapshot, save_snapshot, state_hash
from engine.entities import SURFACE


def make_world(seed=11, size=128, n=300):
    w = World(WorldConfig(seed=seed, size=size, initial_capacity=4096))
    sp = w.registry.register("critter", props=("hunger", "fear"))
    prng = w.plugin_rng("plug_a")
    for _ in range(n):
        w.store.spawn(
            sp.id,
            float(prng.uniform(0, size)), float(prng.uniform(0, size)), 0.0,
            SURFACE, 100.0,
        )
    w.plugin_stores["plug_a"]["memory"] = 42
    w.run(50)
    return w


def test_roundtrip_hash_identical(tmp_path):
    w = make_world()
    h0 = state_hash(w)
    p = save_snapshot(w, tmp_path / "snap.npz")
    restored = load_snapshot(p)
    assert state_hash(restored) == h0


def test_restored_world_evolves_identically(tmp_path):
    """The stronger property: restore then step == never-snapshotted then step."""
    w = make_world()
    p = save_snapshot(w, tmp_path / "snap.npz")
    restored = load_snapshot(p)
    w.run(100)
    restored.run(100)
    assert state_hash(w) == state_hash(restored)


def test_plugin_rng_and_store_survive_restore(tmp_path):
    w = make_world()
    expected_next = float(
        load_snapshot(save_snapshot(w, tmp_path / "a.npz")).plugin_rng("plug_a").random()
    )
    assert float(w.plugin_rng("plug_a").random()) == expected_next
    restored = load_snapshot(save_snapshot(w, tmp_path / "b.npz"))
    assert restored.plugin_stores["plug_a"]["memory"] == 42


def test_snapshot_speed_10k_entities(tmp_path):
    w = World(WorldConfig(seed=1, size=256, initial_capacity=16384))
    sp = w.registry.register("critter")
    for _ in range(10_000):
        w.store.spawn(
            sp.id,
            float(w.rng.uniform(0, 256)), float(w.rng.uniform(0, 256)), 0.0,
            SURFACE, 100.0,
        )
    t0 = time.perf_counter()
    p = save_snapshot(w, tmp_path / "big.npz")
    t_save = time.perf_counter() - t0
    t0 = time.perf_counter()
    load_snapshot(p)
    t_load = time.perf_counter() - t0
    assert t_save < 2.0, f"save took {t_save:.2f}s"
    assert t_load < 2.0, f"load took {t_load:.2f}s"
