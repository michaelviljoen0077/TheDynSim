"""The fish_shoal example plugin: installs, survives on plankton, stays aquatic."""

from pathlib import Path

import numpy as np

from engine import World, WorldConfig
from engine.plugin_host import PluginHost

FISH = (Path(__file__).resolve().parents[2] / "plugins_examples" / "fish.py").read_text()


def test_fish_installs_survives_and_stays_in_water():
    w = World(WorldConfig(seed=7, size=64, topology="wrap", initial_capacity=8192))
    PluginHost(w).install(FISH)
    fid = w.registry.by_name["fish"].id
    assert w.store.alive_indices(fid).size > 0        # founder shoal spawned
    for _ in range(300):
        w.step()
    rows = w.store.alive_indices(fid)
    assert rows.size > 0                               # fed themselves on plankton
    wm = w.terrain.water_mask
    ix = np.clip(w.store.px[rows].astype(int), 0, 63)
    iy = np.clip(w.store.py[rows].astype(int), 0, 63)
    assert float((wm[ix, iy] > 0.5).mean()) > 0.6      # the shoal keeps to the water
