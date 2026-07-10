"""The fish_shoal example plugin: installs, survives on plankton, stays aquatic."""

from pathlib import Path

import numpy as np

from engine import World, WorldConfig
from engine.plugin_host import PluginHost

EX = Path(__file__).resolve().parents[2] / "plugins_examples"
FISH = (EX / "fish.py").read_text()
SHARK = (EX / "shark.py").read_text()
BIRDS = (EX / "birds.py").read_text()
RAPTOR = (EX / "raptor.py").read_text()


def test_fish_installs_survives_moves_and_stays_in_water():
    w = World(WorldConfig(seed=7, size=64, topology="wrap", initial_capacity=8192))
    PluginHost(w).install(FISH)
    fid = w.registry.by_name["fish"].id
    rows0 = w.store.alive_indices(fid)
    assert rows0.size > 0                              # founder shoal spawned
    start = np.column_stack((w.store.px[rows0].copy(), w.store.py[rows0].copy()))
    for _ in range(300):
        w.step()
    rows = w.store.alive_indices(fid)
    assert rows.size > 0                               # fed themselves on plankton
    wm = w.terrain.water_mask
    ix = np.clip(w.store.px[rows].astype(int), 0, 63)
    iy = np.clip(w.store.py[rows].astype(int), 0, 63)
    assert float((wm[ix, iy] > 0.5).mean()) > 0.9      # the shoal keeps to the water
    # the founders actually SWAM: they no longer sit at their spawn coords (the
    # aquatic ability lets them roam the water instead of being stranded/stationary)
    surviving = rows0[np.isin(rows0, rows)]
    moved = np.abs(w.store.px[surviving] - start[np.isin(rows0, rows), 0]) \
          + np.abs(w.store.py[surviving] - start[np.isin(rows0, rows), 1])
    assert float((moved > 0.5).mean()) > 0.5           # most surviving fish travelled


def test_fish_and_shark_form_an_aquatic_food_chain():
    w = World(WorldConfig(seed=7, size=64, topology="wrap", initial_capacity=8192))
    host = PluginHost(w)
    host.install(FISH)
    host.install(SHARK)
    fid = w.registry.by_name["fish"].id
    sid = w.registry.by_name["shark"].id
    for _ in range(400):
        w.step()
    assert w.store.alive_indices(fid).size > 0          # prey persists
    assert w.store.alive_indices(sid).size > 0          # predator feeds itself on fish


def test_birds_and_raptor_form_a_sky_food_chain():
    w = World(WorldConfig(seed=5, size=64, topology="wrap", initial_capacity=8192))
    host = PluginHost(w)
    host.install(BIRDS)
    host.install(RAPTOR)
    bid = w.registry.by_name["bird"].id
    rid = w.registry.by_name["raptor"].id
    for _ in range(400):
        w.step()
    assert w.store.alive_indices(bid).size > 0          # flock persists under predation
    assert w.store.alive_indices(rid).size > 0          # raptors feed on birds
