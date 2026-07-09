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


def test_metabolize_drains_the_whole_herd():
    w, api, sid = _world()
    rows = w.store.alive_indices(sid)
    e0 = w.store.energy[rows].copy()
    api.on_tick_begin()
    api.metabolize("sparrow", 5.0)
    _apply(w)
    assert np.allclose(w.store.energy[rows] - e0, -5.0, atol=1e-3)


def test_graze_conserves_energy_on_a_crowded_cell():
    """20 grazers stacked on one cell can't each gain full energy — the engine
    distributes the cell's ACTUAL flora, so total energy gained equals flora
    consumed x gain (conservation), never 20x it."""
    w, api, sid = _world()
    rows = w.store.alive_indices(sid)
    w.flora.density[30, 30] = 1.0
    avail0 = float(w.flora.density[30, 30])
    e0 = float(w.store.energy[rows].sum())
    api.on_tick_begin()
    api.graze("sparrow", rate=0.5, gain=40.0, max_energy=500.0)  # demand 20*0.5=10 >> 1.0 avail
    _apply(w)
    consumed = avail0 - float(w.flora.density[30, 30])
    gained = float(w.store.energy[rows].sum()) - e0
    assert consumed <= avail0 + 1e-6                 # can't eat more flora than exists
    assert abs(gained - consumed * 40.0) < 1e-2      # energy gained == flora eaten x gain


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


def test_breed_crowd_max_suppresses_dense_flocks():
    """Density-dependent breeding: with crowd_max set, packed entities don't breed
    (the soft carrying-capacity control that replaces hard population caps)."""
    w, api, sid = _world()                 # 20 sparrows stacked on one cell
    rows = w.store.alive_indices(sid)
    w.store.energy[rows] = 150.0
    api.on_tick_begin()
    n = api.breed("sparrow", energy_over=100.0, cost=10.0, crowd_max=5, crowd_radius=8.0)
    assert n == 0                          # all 20 share a cell -> too crowded to breed
    api.on_tick_begin()
    n2 = api.breed("sparrow", energy_over=100.0, cost=10.0)  # no crowd limit
    assert n2 == 20


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
