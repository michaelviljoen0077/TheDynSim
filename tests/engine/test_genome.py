"""Heritable genes: mutation on reproduction + engine-applied natural selection
(the 'speed' gene scales the speed cap and carries a coupled energy cost)."""

import numpy as np

from engine import World, WorldConfig
from engine.world_api import WorldAPI


def _rig(**cfg):
    w = World(WorldConfig(seed=1, size=cfg.pop("size", 64), initial_capacity=8192, **cfg))
    api = WorldAPI(w, "p", 0, ["mover"])
    return w, api


def _apply(w, **kw):
    w.commands.apply(w.store, float(w.config.size), speeds=w.registry.speeds_array(), **kw)


def test_founder_gene_is_the_declared_value():
    w, api = _rig()
    api.register_species("mover", genes={"speed": 1.0})
    api.on_tick_begin()
    api.spawn("mover", 20.0, 20.0, energy=100.0)
    _apply(w)
    h = w.store.handles_of(w.store.alive_indices(w.registry.by_name["mover"].id))[0]
    assert api.gene(h, "speed") == 1.0


def test_offspring_inherit_and_mutate_within_bounds():
    w, api = _rig()
    api.register_species("mover", genes={"speed": 1.0}, gene_sigma=0.2)
    sid = w.registry.by_name["mover"].id
    api.on_tick_begin()
    for _ in range(40):
        api.spawn("mover", 20.0, 20.0, energy=200.0)
    _apply(w)
    parents = w.store.alive_indices(sid)
    api.on_tick_begin()
    n = api.breed("mover", energy_over=100.0, cost=50.0, offspring_energy=100.0)
    _apply(w)
    now = w.store.alive_indices(sid)
    kids = now[~np.isin(now, parents)]
    slot = w.registry.by_name["mover"].gene_slots["speed"]
    kg = w.store.genome[kids, slot]
    assert kids.size == n
    assert np.all(kg >= 0.25) and np.all(kg <= 4.0)     # clamped to founder bounds
    assert float(np.std(kg)) > 0.0                       # they actually diverged


def test_speed_gene_scales_the_movement_cap():
    w, api = _rig(size=256)
    api.register_species("mover", speed=2.0, genes={"speed": 1.0})
    sid = w.registry.by_name["mover"].id
    api.on_tick_begin()
    api.spawn("mover", 100.0, 100.0, energy=100.0)
    api.spawn("mover", 100.0, 100.0, energy=100.0)
    _apply(w)
    rows = w.store.alive_indices(sid)
    slot = w.registry.by_name["mover"].gene_slots["speed"]
    w.store.genome[rows[0], slot] = 2.0                  # fast variant
    w.store.genome[rows[1], slot] = 1.0                  # baseline
    hs = w.store.handles_of(rows)
    x0b, x1b = float(w.store.px[rows[0]]), float(w.store.px[rows[1]])
    api.move(hs[0], 100.0, 0.0)
    api.move(hs[1], 100.0, 0.0)
    _apply(w, speed_gene_slots=w.registry.gene_slot_array("speed"))
    d0 = w.store.px[rows[0]] - x0b
    d1 = w.store.px[rows[1]] - x1b
    assert d0 > d1 * 1.5                                  # ~2x cap -> ~2x distance


def test_speed_gene_carries_an_energy_cost():
    w, api = _rig()
    api.register_species("mover", genes={"speed": 1.0})
    sid = w.registry.by_name["mover"].id
    api.on_tick_begin()
    api.spawn("mover", 20.0, 20.0, energy=100.0)
    api.spawn("mover", 20.0, 20.0, energy=100.0)
    _apply(w)
    rows = w.store.alive_indices(sid)
    slot = w.registry.by_name["mover"].gene_slots["speed"]
    w.store.genome[rows[0], slot] = 3.0                  # fast -> costly
    w.store.genome[rows[1], slot] = 1.0                  # baseline -> free
    e0b, e1b = float(w.store.energy[rows[0]]), float(w.store.energy[rows[1]])
    w.step()                                             # runs _gene_costs
    lost_fast = e0b - float(w.store.energy[rows[0]])
    lost_base = e1b - float(w.store.energy[rows[1]])
    assert lost_fast > lost_base                         # selection pressure exists
