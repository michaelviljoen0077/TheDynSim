"""Story 3.6 integration: full evolution cycles against ReplayProvider, offline.

Cycle 1: three candidates — a viable scavenger (should be promoted), a hostile
one (validation-rejected after a failed repair), and an ecosystem-killer that
must lose to the control run. Cycle 2: verifies outcome feedback (FR17) and
notebook recall.
"""


from pathlib import Path

import pytest

from engine import WorldConfig
from governor.fitness import FitnessWeights
from governor.llm import ReplayProvider
from governor.notebook import Notebook
from governor.orchestrator import GovernorConfig, Orchestrator
from governor.shadow import Budgets
from server.runner import EngineRunner

EXAMPLES = Path(__file__).resolve().parents[2] / "plugins_examples"

BEETLE = '''
PLUGIN_META = {"name": "beetle_colony", "contract": 1, "species": ["beetle"], "lineage_parent": None}

def setup(world):
    world.register_species("beetle", size=0.8, color="#446644")
    for _ in range(60):
        x, y = world.random_surface_point()
        world.spawn("beetle", x, y, energy=80.0)

def on_tick(world):
    for b in world.entities("beetle"):
        e = world.get(b, "energy") - 0.02
        x, y, _z = world.pos(b)
        e += world.eat_flora(x, y, 0.004) * 30.0
        world.set(b, "energy", min(e, 120.0))
        world.move(b, world.rng.uniform(-0.5, 0.5), world.rng.uniform(-0.5, 0.5))
        if e > 100.0 and world.count("beetle") < 220:
            world.set(b, "energy", e * 0.5)
            world.spawn("beetle", x, y, energy=e * 0.5)
'''

HOSTILE = 'import os\nPLUGIN_META = {"name": "evil", "contract": 1, "species": ["x"]}\n' \
          'def setup(world):\n    pass\ndef on_tick(world):\n    pass\n'

KILLER = '''
PLUGIN_META = {"name": "plague", "contract": 1, "species": ["locust"], "lineage_parent": None}

def setup(world):
    world.register_species("locust", size=0.5, color="#222222")
    for _ in range(150):
        x, y = world.random_surface_point()
        world.spawn("locust", x, y, energy=200.0)

def on_tick(world):
    for lo in world.entities("locust"):
        x, y, _z = world.pos(lo)
        world.eat_flora(x, y, 1.0)
        world.set(lo, "energy", 200.0)
        world.move(lo, world.rng.uniform(-2, 2), world.rng.uniform(-2, 2))
        if world.count("locust") < 900:
            world.spawn("locust", x, y, energy=200.0)
'''


def proposal(source, name, expected="richer ecosystem"):
    return {
        "analysis": f"{name}: test analysis",
        "hypothesis": f"{name} improves the ecosystem",
        "expected_outcome": expected,
        "confidence": 0.8,
        "plugin_source": source,
        "lineage_parent": None,
    }


@pytest.fixture()
def rig(tmp_path):
    sources = [(EXAMPLES / "grazer.py").read_text(), (EXAMPLES / "predator.py").read_text()]
    runner = EngineRunner(WorldConfig(seed=55, size=96, initial_capacity=8192),
                          plugin_sources=sources)
    runner.world.run(80)  # settle the ecosystem a little
    notebook = Notebook(tmp_path / "run.db")
    notebook.start_run(55, "{}")
    return runner, notebook, tmp_path


def make_orch(runner, notebook, provider, tmp_path):
    cfg = GovernorConfig(
        n_candidates=3, shadow_ticks=250, promotion_threshold=0.3,
        budgets=Budgets(wall_s=60.0, rss_mb=800.0, tick_ms=250.0),
        weights=FitnessWeights(),
    )
    return Orchestrator(runner, notebook, provider, cfg, snapshot_dir=tmp_path / "snaps")


def test_full_cycle_promotes_good_rejects_bad(rig):
    runner, notebook, tmp_path = rig
    provider = ReplayProvider([
        # generation phase consumes n_candidates fixtures in order...
        proposal(BEETLE, "beetle", expected="beetle population establishes, diversity rises"),
        proposal(HOSTILE, "evil"),
        proposal(KILLER, "plague"),
        # ...then the repair round-trip for the failed one consumes the next
        proposal(HOSTILE, "evil-repair-attempt"),
    ])
    orch = make_orch(runner, notebook, provider, tmp_path)

    decision = orch.run_cycle()
    assert decision == "promoted"

    cycles = notebook.cycles()
    assert len(cycles) == 1 and cycles[0]["decision"] == "promoted"
    cands = {c["label"]: c for c in notebook.candidates_for(cycles[0]["id"])}
    assert cands["cand-0"]["fate"] == "promoted"
    assert cands["cand-1"]["fate"] == "rejected_validation"
    assert any(v["code"] == "banned-import" for v in cands["cand-1"]["validation"]["errors"])
    # the plague either loses on fitness or trips a shadow budget — never promoted
    assert cands["cand-2"]["fate"] in ("scored", "rejected_shadow")
    if cands["cand-2"]["fate"] == "scored":
        assert cands["cand-2"]["fitness"] < cands["cand-0"]["fitness"]

    # the beetle is now live in the real world
    plugins = {p["name"] for p in runner.host.state()}
    assert "beetle_colony" in plugins
    assert runner.world.registry.by_name.get("beetle") is not None


def test_outcome_feedback_and_recall(rig):
    runner, notebook, tmp_path = rig
    provider = ReplayProvider([
        # cycle 1 generation, then repair for the hostile one
        proposal(BEETLE, "beetle", expected="beetles establish"),
        proposal(HOSTILE, "evil"),
        proposal(KILLER, "plague"),
        proposal(HOSTILE, "evil-repair"),
        # cycle 2: nothing viable (3 generations + 3 failed repairs)
        proposal(HOSTILE, "evil2"), proposal(HOSTILE, "evil3"), proposal(HOSTILE, "evil4"),
        proposal(HOSTILE, "evil2-repair"), proposal(HOSTILE, "evil3-repair"),
        proposal(HOSTILE, "evil4-repair"),
    ])
    orch = make_orch(runner, notebook, provider, tmp_path)

    assert orch.run_cycle() == "promoted"
    runner.world.run(120)  # let the promotion play out in the live world
    assert orch.run_cycle() == "no_change"

    # FR17: cycle 2 measured cycle 1's real outcome
    row = notebook.db.execute("SELECT * FROM outcomes").fetchone()
    assert row is not None
    assert row["plugin_name"] == "beetle_colony"
    assert row["verdict"] in ("better", "as_expected", "worse", "catastrophic")

    # recall surfaces the promoted experiment for related species
    recall = notebook.recall(["beetle", "grazer"])
    assert any(r["fate"] == "promoted" for r in recall)


def test_busy_guard(rig):
    runner, notebook, tmp_path = rig
    provider = ReplayProvider([proposal(BEETLE, "beetle")])
    orch = make_orch(runner, notebook, provider, tmp_path)
    orch._busy.acquire()
    try:
        assert orch.run_cycle_async() is False
    finally:
        orch._busy.release()


def test_clear_run_wipes_cycles(rig):
    runner, notebook, tmp_path = rig
    provider = ReplayProvider([proposal(BEETLE, "beetle")] + [proposal(HOSTILE, "x")] * 8)
    orch = make_orch(runner, notebook, provider, tmp_path)
    orch.run_cycle()
    assert notebook.cycles(), "precondition: a cycle exists"
    assert notebook.db.execute("SELECT COUNT(*) c FROM candidates").fetchone()["c"] > 0
    # reset wipes the run's history
    notebook.clear_run()
    assert notebook.cycles() == []
    assert notebook.db.execute("SELECT COUNT(*) c FROM candidates").fetchone()["c"] == 0
    assert notebook.db.execute("SELECT COUNT(*) c FROM interventions").fetchone()["c"] == 0
    orch.reset_state()
    assert orch._last_promotion is None
