"""Batched herd primitives: metabolize/graze/wander/breed operate on a whole
species in one vectorized pass, with the same buffered semantics as per-entity
calls. These verify the EFFECTS match, so plugins can drop their Python loops."""

import numpy as np

from engine import World, WorldConfig
from engine.world_api import WorldAPI


def _world():
    w = World(WorldConfig(seed=3, size=64, initial_capacity=4096))
    api = WorldAPI(w, "flock", 0, ["sparrow"])
    api.register_species("sparrow", speed=4.0, props=("heading",))
    api.on_tick_begin()
    for _ in range(20):
        api.spawn("sparrow", 30.0, 30.0, energy=100.0)
    w.commands.apply(w.store, float(w.config.size), speeds=w.registry.speeds_array())
    return w, api, w.registry.by_name["sparrow"].id


def _apply(w):
    w.commands.apply(w.store, float(w.config.size), speeds=w.registry.speeds_array(),
                     flora=w.flora.density, heading_slots=w.registry.heading_slots_array())


def test_metabolize_and_graze_compose_additively_on_energy():
    w, api, sid = _world()
    rows = w.store.alive_indices(sid)
    e0 = w.store.energy[rows].copy()
    w.flora.density[30, 30] = 1.0                 # rich cell under the whole flock
    api.on_tick_begin()
    api.metabolize("sparrow", 5.0)                # -5
    api.graze("sparrow", rate=0.5, gain=40.0)     # +min(1.0,0.5)*40 = +20
    _apply(w)
    # the two batch energy effects SUM (compose), not overwrite: net +15
    assert np.allclose(w.store.energy[rows] - e0, 15.0, atol=1e-2)
    assert float(w.flora.density[30, 30]) < 1.0   # flora was actually drained


def test_wander_moves_the_herd_and_sets_headings():
    w, api, sid = _world()
    rows = w.store.alive_indices(sid)
    slot = w.registry.by_name["sparrow"].prop_slots["heading"]
    assert np.all(w.store.props[rows, slot] == 0.0)  # unset at spawn
    px0 = w.store.px[rows].copy()
    api.on_tick_begin()
    api.wander("sparrow", speed=2.0)
    _apply(w)
    assert np.all(w.store.props[rows, slot] != 0.0)  # each got a heading
    assert np.any(w.store.px[rows] != px0)           # the herd moved


def test_breed_grows_population_and_costs_the_parents():
    w, api, sid = _world()
    rows = w.store.alive_indices(sid)
    w.store.energy[rows] = 150.0                      # all above the breeding bar
    api.on_tick_begin()
    n = api.breed("sparrow", energy_over=120.0, cost=60.0, offspring_energy=50.0)
    _apply(w)
    assert n == 20
    now = w.store.alive_indices(sid)
    assert now.size == 40                            # 20 parents + 20 young
    # parents paid the cost (their energy dropped ~60 from 150)
    parents = now[np.isin(now, rows)]
    assert np.allclose(w.store.energy[parents], 90.0, atol=1e-2)
