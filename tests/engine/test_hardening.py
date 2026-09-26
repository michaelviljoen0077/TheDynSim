"""Regression tests: wrap-topology spatial queries, setup rollback, API input checks."""

from pathlib import Path

import pytest

from engine import World, WorldConfig, state_hash
from engine.entities import EntityStore
from engine.plugin_host import PluginHost, PluginInstallError
from engine.spatial import SpatialHash
from engine.validator import validate_plugin

EXAMPLES = Path(__file__).resolve().parents[2] / "plugins_examples"

PLUGIN = '''PLUGIN_META = {{"name": "probe", "contract": 1, "species": ["probe"]}}

def setup(world):
    world.register_species("probe")
    for _ in range(5):
        world.spawn("probe", 10.0, 10.0)
    {setup_extra}

def on_tick(world):
    {tick_body}
'''


def plugin(setup_extra="pass", tick_body="pass"):
    return PLUGIN.format(setup_extra=setup_extra, tick_body=tick_body)


def test_wrap_hash_finds_entities_in_partial_last_cell():
    # size 100 is not a multiple of the 8-unit cell: x=98 lives in the folded cell
    store = EntityStore(16, 2)
    store.spawn(0, 98.0, 50.0, 0, 1, 1.0)
    store.spawn(0, 97.0, 50.0, 0, 1, 1.0)
    sh = SpatialHash(100.0, wrap=True)
    sh.rebuild(store)
    assert sh.within(store, 97.0, 50.0, 3.0, 1, exclude_row=1) == [0]
    assert sorted(sh.within(store, 1.0, 50.0, 5.0, 1)) == [0, 1]


def test_wrap_hash_large_radius_reports_each_row_once():
    store = EntityStore(16, 2)
    store.spawn(0, 10.0, 10.0, 0, 1, 1.0)
    sh = SpatialHash(64.0, wrap=True)
    sh.rebuild(store)
    assert sh.within(store, 20.0, 20.0, 60.0, 1) == [0]


def test_failed_setup_leaves_no_trace():
    world = World(WorldConfig(seed=3, size=64))
    before = state_hash(world)
    host = PluginHost(world)
    with pytest.raises(PluginInstallError):
        host.install(plugin(setup_extra='raise ValueError("boom")'))
    assert "probe" not in world.registry.by_name
    assert state_hash(world) == before
    # the fixed plugin installs cleanly afterwards (no duplicate-species)
    host.install(plugin())
    world.step()
    assert world.store.count == 5


def test_non_finite_move_is_a_plugin_error_not_an_engine_crash():
    world = World(WorldConfig(seed=3, size=64, topology="wrap"))
    host = PluginHost(world)
    record = host.install(plugin(tick_body='''for h in world.entities("probe"):
        world.move(h, float("inf"), 0.0)'''))
    world.run(3)  # must not raise
    assert record.error_count > 0


def test_store_rejects_non_str_keys():
    world = World(WorldConfig(seed=3, size=64))
    with pytest.raises(PluginInstallError):
        PluginHost(world).install(plugin(setup_extra="world.store.set(1, 2.0)"))
    state_hash(world)  # snapshot/hash still work


def test_count_of_unregistered_species_is_zero():
    world = World(WorldConfig(seed=3, size=64, topology="wrap"))
    host = PluginHost(world)
    record = host.install((EXAMPLES / "grazer.py").read_text())  # no wolf plugin
    world.run(6)
    assert record.status == "live" and record.error_count == 0


@pytest.mark.parametrize("body", [
    "world._world.store",
    "{}.keys() | {}.keys()",
])
def test_validator_blocks_internal_access_and_view_sets(body):
    assert not validate_plugin(plugin(tick_body=body)).ok
