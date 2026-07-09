"""Plankton: the aquatic food field (grows on water only) + energy-conserving
filter-feeding via eat_plankton."""

import numpy as np

from engine import World, WorldConfig
from engine.world_api import WorldAPI


def test_plankton_lives_on_water_only():
    w = World(WorldConfig(seed=2, size=64))
    for _ in range(50):
        w.step()
    water = w.terrain.water_mask
    d = w.plankton.density
    assert np.all(d[water < 0.5] == 0.0)         # never on land
    assert float(d[water > 0.5].max()) > 0.0     # blooms on open water


def test_eat_plankton_conserves_energy():
    w = World(WorldConfig(seed=2, size=64))
    api = WorldAPI(w, "school", 0, ["fish"])
    api.register_species("fish", swim_speed=1.0)
    wc = np.argwhere(w.terrain.water_mask > 0.5)[0]
    ix, iy = int(wc[0]), int(wc[1])
    w.plankton.density[ix, iy] = 0.5
    api.on_tick_begin()
    for _ in range(10):
        api.spawn("fish", ix + 0.5, iy + 0.5, energy=50.0)
    w.commands.apply(w.store, float(w.config.size), plankton=w.plankton.density)

    rows = w.store.alive_indices(w.registry.by_name["fish"].id)
    e0 = float(w.store.energy[rows].sum())
    avail0 = float(w.plankton.density[ix, iy])
    api.on_tick_begin()
    for h in w.store.handles_of(rows):                       # 10 fish, demand 2.0 >> 0.5 avail
        api.eat_plankton(h, ix + 0.5, iy + 0.5, 0.2, gain=30.0)
    w.commands.apply(w.store, float(w.config.size), plankton=w.plankton.density)

    consumed = avail0 - float(w.plankton.density[ix, iy])
    gained = float(w.store.energy[rows].sum()) - e0
    assert consumed <= avail0 + 1e-6                          # can't eat more than exists
    assert abs(gained - consumed * 30.0) < 1e-2              # energy gained == plankton eaten x gain
