"""Extinction ledger, plugin reaping/cleanup, and cross-stratum sensing."""

from engine import World, WorldConfig
from engine.entities import SURFACE, UNDERGROUND
from engine.plugin_host import (
    EXTINCTION_GRACE,
    PRUNE_AGE,
    REAP_EVERY,
    PluginHost,
    PluginInstallError,
)

MORTAL = '''
PLUGIN_META = {"name": "mayflies", "contract": 1, "species": ["mayfly"], "lineage_parent": None}
def setup(world):
    world.register_species("mayfly", lifespan=%d)
    for _ in range(20):
        x, y = world.random_surface_point()
        world.spawn("mayfly", x, y, energy=100.0)
def on_tick(world):
    pass  # no reproduction: the cohort ages out and the species goes extinct
'''


def test_species_extinction_is_recorded_and_plugin_retired():
    life = 200
    w = World(WorldConfig(seed=2, size=64, initial_capacity=512))
    host = PluginHost(w)
    rec = host.install(MORTAL % life)
    # run past lifespan + grace + a reap sweep
    w.run(life + EXTINCTION_GRACE + REAP_EVERY + 10)
    assert w.store.alive_indices(w.registry.by_name["mayfly"].id).size == 0
    assert any(e["species"] == "mayfly" for e in w.extinct)
    assert rec.status == "extinct"


def test_extinct_plugin_is_pruned_after_prune_age():
    w = World(WorldConfig(seed=2, size=64, initial_capacity=512))
    host = PluginHost(w)
    host.install(MORTAL % 100)
    w.run(PRUNE_AGE + REAP_EVERY + 200)
    # long-dead plugin removed from the live manifest entirely
    assert "mayflies" not in host.plugins
    assert all(m["name"] != "mayflies" for m in w.plugin_manifest)
    assert any(e["species"] == "mayfly" for e in w.extinct)  # extinction still remembered


def test_cross_stratum_sensing():
    """A burrower can sense surface prey by querying the SURFACE stratum, even
    though its own stratum is underground (fixes 'underground hunters can't hunt')."""
    w = World(WorldConfig(seed=1, size=64, initial_capacity=256))
    hunter = w.registry.register("mole", strata=(UNDERGROUND, SURFACE))
    prey = w.registry.register("worm")
    from engine.plugin_host import PluginHost as _PH  # noqa: F401
    h = w.store.spawn(hunter.id, 30.0, 30.0, 0.0, UNDERGROUND, 100.0)
    w.store.spawn(prey.id, 31.0, 30.0, 0.0, SURFACE, 100.0)
    w.spatial.rebuild(w.store)
    from engine.world_api import WorldAPI
    api = WorldAPI(w, "moles", 0, ["mole"])
    # same stratum (underground): nothing there
    assert api.nearest(h, species="worm", radius=10.0) is None
    # sensing the surface stratum: finds the worm
    assert api.nearest(h, species="worm", radius=10.0, stratum=w.SURFACE) is not None


def test_extinction_survives_snapshot(tmp_path):
    from engine import load_snapshot, save_snapshot
    w = World(WorldConfig(seed=2, size=64, initial_capacity=512))
    host = PluginHost(w)
    host.install(MORTAL % 100)
    w.run(100 + EXTINCTION_GRACE + REAP_EVERY + 10)
    assert w.extinct
    p = save_snapshot(w, tmp_path / "x.npz")
    restored = load_snapshot(p)
    assert restored.extinct == w.extinct


def test_extinct_species_and_plugin_name_can_be_reclaimed():
    """After a species goes extinct, a new plugin may re-use its (species and
    plugin) name to revive the niche — the governor does this constantly."""
    w = World(WorldConfig(seed=2, size=64, initial_capacity=512))
    host = PluginHost(w)
    host.install(MORTAL % 100)  # 'mayflies' plugin, 'mayfly' species; will die out
    w.run(100 + EXTINCTION_GRACE + REAP_EVERY + 10)
    assert any(e["species"] == "mayfly" for e in w.extinct)
    # same plugin name + same species name -> should reclaim, not raise duplicate
    rec = host.install(MORTAL % 100)
    assert rec.status == "live"
    assert w.store.alive_indices(w.registry.by_name["mayfly"].id).size > 0


def test_live_species_name_still_protected():
    """A LIVE species/plugin name cannot be duplicated (only extinct ones reclaim)."""
    import pytest
    w = World(WorldConfig(seed=2, size=64, initial_capacity=512))
    host = PluginHost(w)  # noqa: F841
    host.install(MORTAL % 100000)  # long-lived: stays alive
    with pytest.raises(PluginInstallError):
        host.install(MORTAL % 100000)  # same name, still alive -> duplicate
