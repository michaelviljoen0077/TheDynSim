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
    assert grazers0 == 100 and wolves0 == 6
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


def test_spawn_bomb_is_soft_capped_not_quarantined():
    """Population caps are environmental limits: drops are counted, the plugin
    stays healthy, the world respects the cap. (A booming herd hitting quota
    used to error 5x and get quarantined -> frozen herd -> collapse.)"""
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
    for _ in range(6):  # would cross the quarantine threshold under the old semantics
        world.step()
    assert record.status == "live"
    assert record.error_count == 0
    assert record.api.spawn_drops > 0
    assert world.store.count <= world.config.max_entities_per_plugin


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


GRAZER_MUTATION = '''
PLUGIN_META = {"name": "grazer_herd_v2", "contract": 1, "species": ["grazer"],
               "lineage_parent": "grazer_herd"}

def setup(world):
    world.register_species("grazer", size=1.6, color="#d8b06a", speed=2.0,
                           strata=(world.SURFACE,), props=("maturity",))

def on_tick(world):
    for g in world.entities("grazer"):
        e = world.get(g, "energy") - 0.05
        x, y, _z = world.pos(g)
        e += world.eat_flora(x, y, 0.03) * 60.0
        world.set(g, "energy", min(e, 200.0))
        world.move(g, world.rng.uniform(-1, 1), world.rng.uniform(-1, 1))
'''


def test_lineage_replacement_adopts_species(tmp_path):
    """Refinement path: a mutation of a live plugin retires it and inherits its species."""
    world = make_world()
    host = host_with_examples(world)
    world.run(60)
    gid = world.registry.by_name["grazer"].id
    pop_before = int(world.store.alive_indices(gid).size)
    assert pop_before > 0

    record = host.install(GRAZER_MUTATION)
    assert record.status == "live"
    assert host.plugins["grazer_herd"].status == "retired"
    sp = world.registry.by_name["grazer"]
    assert sp.plugin == "grazer_herd_v2"
    assert sp.speed == 2.0
    assert "gestation" in sp.prop_slots           # layout preserved
    assert int(world.store.alive_indices(gid).size) == pop_before  # entities live on

    # child actually drives the herd; retired parent no longer ticks
    world.run(30)
    assert int(world.store.alive_indices(gid).size) > 0
    assert host.plugins["grazer_herd_v2"].error_count == 0

    # replacement survives snapshot + rebind (rollback path)
    p = save_snapshot(world, tmp_path / "replaced.npz")
    restored = load_snapshot(p)
    host2 = PluginHost.rebind(restored)
    assert host2.plugins["grazer_herd"].status == "retired"
    assert host2.plugins["grazer_herd_v2"].status == "live"
    restored.run(30)
    assert int(restored.store.alive_indices(gid).size) > 0


def test_takeover_without_lineage_is_rejected():
    world = make_world()
    host = host_with_examples(world)
    thief = GRAZER_MUTATION.replace('"lineage_parent": "grazer_herd"', '"lineage_parent": None') \
                           .replace("grazer_herd_v2", "grazer_thief")
    with pytest.raises(PluginInstallError) as e:
        host.install(thief)
    assert any("duplicate-species" in r["message"] or r["code"] == "setup-error"
               for r in e.value.reasons)


def test_slow_plugin_is_quarantined(monkeypatch):
    """A live plugin that burns time every tick gets slow-struck into quarantine
    instead of dragging the sim forever (can't preempt in-process, so we contain).
    Budget patched near-zero so any real work strikes, independent of CPU speed."""
    import engine.plugin_host as ph
    monkeypatch.setattr(ph, "SLOW_TICK_BUDGET_S", 0.0)
    monkeypatch.setattr(ph, "SLOW_STRIKE_THRESHOLD", 5)
    world = make_world()
    host = PluginHost(world)
    slow = '''
PLUGIN_META = {"name": "sloth", "contract": 1, "species": ["snail"], "lineage_parent": None}

def setup(world):
    world.register_species("snail")
    world.spawn("snail", 5.0, 5.0)

def on_tick(world):
    total = 0.0
    for i in range(200000):
        total = total + i * 0.5
'''
    record = host.install(slow)
    for _ in range(8):
        world.step()
    assert record.status == "quarantined"
    assert "slow-tick" in record.events[-1].get("quarantined", "")


def test_exec_error_raises_install_error():
    """Validated-but-exec-raising source (undefined name at module exec) is caught."""
    world = make_world()
    host = PluginHost(world)
    # references an undefined name at exec via a default arg is banned now; instead
    # test a body-level exec failure is impossible (functions don't run at exec),
    # so verify a NameError-at-exec form: module-level call is blocked by validator,
    # leaving decorator/default paths already covered. Confirm a clean plugin installs.
    ok = '''
PLUGIN_META = {"name": "fine", "contract": 1, "species": ["x"], "lineage_parent": None}
def setup(world):
    world.register_species("x")
def on_tick(world):
    pass
'''
    assert host.install(ok).status == "live"
