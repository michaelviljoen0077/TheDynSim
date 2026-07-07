"""Story 2.3 torture battery: the pool contains and survives everything.

Runs one parallel batch: control + benign candidate + crasher + hanger + memory
bomb. Every bad candidate must be disqualified with a machine-readable reason;
the good ones must return metrics; the parent (this test) must survive.
"""

from pathlib import Path

import pytest

from engine import World, WorldConfig, save_snapshot
from engine.plugin_host import PluginHost
from governor.shadow import Budgets, ShadowJob, run_shadow_batch

EXAMPLES = Path(__file__).resolve().parents[2] / "plugins_examples"

BENIGN = '''
PLUGIN_META = {"name": "scavenger", "contract": 1, "species": ["beetle"], "lineage_parent": None}

def setup(world):
    world.register_species("beetle", size=0.8, color="#557755")
    for _ in range(30):
        x, y = world.random_surface_point()
        world.spawn("beetle", x, y, energy=60.0)

def on_tick(world):
    for b in world.entities("beetle"):
        world.set(b, "energy", 60.0)
        world.move(b, world.rng.uniform(-1, 1), world.rng.uniform(-1, 1))
'''

CRASHER = '''
PLUGIN_META = {"name": "crasher", "contract": 1, "species": ["ghost"], "lineage_parent": None}

def setup(world):
    world.register_species("ghost")

def on_tick(world):
    x = 1 / 0
'''

HANGER = '''
PLUGIN_META = {"name": "hanger", "contract": 1, "species": ["snail"], "lineage_parent": None}

def setup(world):
    world.register_species("snail")

def on_tick(world):
    n = 0.0
    while True:
        n = n + 1.0
        if n < 0:
            break
'''

MEMBOMB = '''
PLUGIN_META = {"name": "membomb", "contract": 1, "species": ["blob"], "lineage_parent": None}

def setup(world):
    world.register_species("blob")

def on_tick(world):
    xs = []
    n = 0
    while True:
        xs.append([0.0] * 1000000)
        n = n + 1
        if n < 0:
            break
'''


@pytest.fixture(scope="module")
def live_snapshot(tmp_path_factory):
    world = World(WorldConfig(seed=77, size=96, initial_capacity=4096))
    host = PluginHost(world)
    host.install((EXAMPLES / "grazer.py").read_text())
    host.install((EXAMPLES / "predator.py").read_text())
    world.run(100)
    return str(save_snapshot(world, tmp_path_factory.mktemp("shadow") / "live.npz"))


def test_torture_batch(live_snapshot):
    tight = Budgets(wall_s=25.0, rss_mb=400.0, tick_ms=250.0)
    jobs = [
        ShadowJob(live_snapshot, None, ticks=200, label="control", budgets=tight),
        ShadowJob(live_snapshot, BENIGN, ticks=200, label="benign", budgets=tight),
        ShadowJob(live_snapshot, CRASHER, ticks=200, label="crasher", budgets=tight),
        ShadowJob(live_snapshot, HANGER, ticks=200, label="hanger",
                  budgets=Budgets(wall_s=25.0, rss_mb=400.0, tick_ms=100.0)),
        ShadowJob(live_snapshot, MEMBOMB, ticks=200, label="membomb", budgets=tight),
    ]
    results = {r.label: r for r in run_shadow_batch(jobs, max_parallel=5)}

    control = results["control"]
    assert control.ok, control.reason
    assert control.metrics["final_populations"]["grazer"] > 0
    assert len(control.metrics["samples"]) >= 4

    benign = results["benign"]
    assert benign.ok, benign.reason
    assert benign.metrics["final_populations"]["beetle"] > 0
    assert not benign.metrics["plugin_errors"]

    crasher = results["crasher"]
    assert not crasher.ok
    assert "quarantined-in-shadow" in crasher.reason

    hanger = results["hanger"]
    assert not hanger.ok
    assert "tick-budget" in hanger.reason or "wall-budget" in hanger.reason

    membomb = results["membomb"]
    assert not membomb.ok
    assert any(k in membomb.reason for k in ("rss-budget", "wall-budget", "tick-budget", "worker-died"))


def _socket_probe(q):
    from governor.shadow import _sandbox_bootstrap
    _sandbox_bootstrap()
    import socket
    try:
        socket.create_connection(("example.com", 80), timeout=2)
        q.put("escaped")
    except RuntimeError as e:
        q.put(f"blocked: {e}")


def test_socket_block_in_worker(live_snapshot):
    """NFR1: the worker bootstrap denies outbound sockets (probe via a candidate
    is impossible — the validator bans socket imports — so probe the bootstrap)."""
    import multiprocessing as mp

    probe = _socket_probe
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=probe, args=(q,))
    p.start()
    p.join(timeout=20)
    assert q.get(timeout=5).startswith("blocked")
