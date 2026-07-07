"""Stories 2.1/2.4: contract enforcement, capabilities, quarantine, food chain, rebind."""

from pathlib import Path

import pytest

from engine import World, WorldConfig, load_snapshot, save_snapshot, state_hash
from engine.plugin_host import PluginHost, PluginInstallError

EXAMPLES = Path(__file__).resolve().parents[2] / "plugins_examples"


def make_world(seed=21, size=96):
    return World(WorldConfig(seed=seed, size=size, initial_capacity=4096))


def host_with_examples(world):
    host = PluginHost(world)
    host.install((EXAMPLES / "grazer.py").read_text())
    host.install((EXAMPLES / "predator.py").read_text())
    return host


def test_install_rejects_invalid_source():
    world = make_world()
    host = PluginHost(world)
    with pytest.raises(PluginInstallError) as e:
        host.install("import os\n")
    assert any(r["code"] == "banned-import" for r in e.value.reasons)


def test_food_chain_runs(caplog):
    world = make_world()
    host = host_with_examples(world)
    grazers0 = world.store.alive_indices(world.registry.by_name["grazer"].id).size
    wolves0 = world.store.alive_indices(world.registry.by_name["wolf"].id).size
    assert grazers0 == 120 and wolves0 == 10
    world.run(600)
    grazers = world.store.alive_indices(world.registry.by_name["grazer"].id).size
    wolves = world.store.alive_indices(world.registry.by_name["wolf"].id).size
    assert grazers > 0, "grazers went extinct in 600 ticks"
    assert wolves > 0, "wolves went extinct in 600 ticks"
    # predation must actually have happened and been attributed
    assert world.deaths.get("grazer", {}).get("predation", 0) > 0
    assert all(r.error_count == 0 for r in host.plugins.values()), host.state()


def test_capability_violations_do_not_corrupt_world():
    """A plugin touching another plugin's species errors out and gets quarantined."""
    world = make_world()
    host = host_with_examples(world)
    rogue = '''
PLUGIN_META = {"name": "rogue", "contract": 1, "species": ["rat"], "lineage_parent": None}

def setup(world):
    world.register_species("rat")
    world.spawn("rat", 5.0, 5.0)

def on_tick(world):
    for g in world.entities("grazer"):
        world.remove(g)  # not ours: CapabilityViolation every tick
'''
    record = host.install(rogue)
    world.run(6)
    assert record.status == "quarantined"
    assert "not-owned" in record.last_error
    assert world.store.alive_indices(world.registry.by_name["grazer"].id).size > 0


def test_quota_violation_on_spawn_bomb():
    world = make_world()
    host = PluginHost(world)
    bomb = '''
PLUGIN_META = {"name": "bomber", "contract": 1, "species": ["boom"], "lineage_parent": None}

def setup(world):
    world.register_species("boom")

def on_tick(world):
    for _ in range(100000):
        world.spawn("boom", 1.0, 1.0)
'''
    record = host.install(bomb)
    world.step()
    assert record.error_count == 1
    assert "spawn-quota" in record.last_error
    assert world.store.count <= world.config.max_spawns_per_tick


def test_snapshot_rebind_preserves_behavior(tmp_path):
    """Restore + rebind == uninterrupted run: the property rollback depends on."""
    a = make_world()
    host_with_examples(a)
    a.run(150)
    p = save_snapshot(a, tmp_path / "mid.npz")

    b = load_snapshot(p)
    PluginHost.rebind(b)  # setup NOT re-run
    assert state_hash(b) == state_hash(a)
    a.run(150)
    b.run(150)
    assert state_hash(a) == state_hash(b)


def test_rebind_skips_setup_no_respawn(tmp_path):
    world = make_world()
    host_with_examples(world)
    world.run(20)
    n0 = world.store.count
    p = save_snapshot(world, tmp_path / "s.npz")
    restored = load_snapshot(p)
    PluginHost.rebind(restored)
    assert restored.store.count == n0  # setup would have doubled populations
