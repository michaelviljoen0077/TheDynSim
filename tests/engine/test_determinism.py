"""FR1: identical seed => identical state hash. The suite everything else trusts."""

import numpy as np

from engine import World, WorldConfig, state_hash

TICKS = 400  # full 10k-tick soak runs nightly; this gates every commit


def _world_with_life(seed: int) -> World:
    cfg = WorldConfig(seed=seed, size=128, initial_capacity=2048)
    w = World(cfg)
    sp = w.registry.register("critter", props=("hunger",))
    for _ in range(200):
        x = float(w.rng.uniform(0, cfg.size))
        y = float(w.rng.uniform(0, cfg.size))
        w.store.spawn(sp.id, x, y, 0.0, World.SURFACE, energy=100.0)
    return w


def _drive(w: World, ticks: int) -> None:
    """Exercise entities + command buffer + plugin RNG stream every tick."""
    sp = w.registry.by_name["critter"]
    prng = w.plugin_rng("testplugin")
    for _ in range(ticks):
        rows = w.store.alive_indices(sp.id)
        handles = w.store.handles_of(rows[:50])
        for hnd in handles:
            w.commands.move(hnd, float(prng.uniform(-1, 1)), float(prng.uniform(-1, 1)))
        if handles:
            w.commands.set_energy(handles[0], float(prng.uniform(0, 200)))
        w.plugin_stores["testplugin"]["ticks"] = w.plugin_stores["testplugin"].get("ticks", 0) + 1
        w.step()


def test_same_seed_same_hash():
    a, b = _world_with_life(42), _world_with_life(42)
    _drive(a, TICKS)
    _drive(b, TICKS)
    assert a.tick == b.tick == TICKS
    assert state_hash(a) == state_hash(b)


def test_different_seed_different_hash():
    a, b = _world_with_life(1), _world_with_life(2)
    _drive(a, 50)
    _drive(b, 50)
    assert state_hash(a) != state_hash(b)


def test_plugin_rng_streams_are_independent():
    """One plugin's draw count must not perturb another plugin's stream."""
    a, b = _world_with_life(7), _world_with_life(7)
    ra1 = a.plugin_rng("p1")
    rb1 = b.plugin_rng("p1")
    a.plugin_rng("p2").random(1000)  # extra draws on another stream in world a only
    assert float(ra1.random()) == float(rb1.random())


def test_flora_and_weather_evolve():
    w = _world_with_life(9)
    t0 = w.flora.density.copy()
    h0 = w.weather.humidity.copy()
    w.run(200)
    assert not np.array_equal(t0, w.flora.density)
    assert not np.array_equal(h0, w.weather.humidity)
    assert np.all(w.flora.density >= 0) and np.all(w.flora.density <= 1)
