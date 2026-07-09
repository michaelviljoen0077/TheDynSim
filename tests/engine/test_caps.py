"""Population-cap toggle: the operator can suspend the hard ceilings to let a
population grow past them (experiment mode / the road to soft-caps-only)."""

from engine import World, WorldConfig
from engine.world_api import WorldAPI


def _spawn_burst(caps_enabled: bool, n: int = 30) -> int:
    w = World(WorldConfig(seed=1, size=64, initial_capacity=8192,
                          max_entities_per_species=10))
    w.caps_enabled = caps_enabled
    api = WorldAPI(w, "p", 0, ["rabbit"])
    api.register_species("rabbit")
    api.on_tick_begin()
    for _ in range(n):
        api.spawn("rabbit", 20.0, 20.0)
    w.commands.apply(w.store, float(w.config.size), speeds=w.registry.speeds_array())
    return int(w.store.alive_indices(w.registry.by_name["rabbit"].id).size)


def test_hard_species_cap_binds_when_caps_enabled():
    assert _spawn_burst(caps_enabled=True) == 10   # ceiling of 10 holds


def test_species_cap_suspended_when_caps_disabled():
    assert _spawn_burst(caps_enabled=False) == 30  # all spawns land
