"""Story 2.2: hostile/broken plugin battery — every fixture rejected with the right code."""

from pathlib import Path

import pytest

from engine.validator import validate_plugin

GOOD = '''
"""A well-behaved plugin."""
import math

PLUGIN_META = {"name": "good_one", "contract": 1, "species": ["blob"], "lineage_parent": None}

def setup(world):
    world.register_species("blob")
    world.spawn("blob", 1.0, 1.0)

def on_tick(world):
    for b in world.entities("blob"):
        world.move(b, math.sin(world.rng.random()), 0.0)
'''

HOSTILE = [
    ("import os\n" + GOOD, "banned-import"),
    ("from subprocess import run\n" + GOOD, "banned-import"),
    (GOOD.replace("math.sin(world.rng.random())", "eval('1')"), "banned-call"),
    (GOOD.replace("math.sin(world.rng.random())", "open('x').read()"), "banned-call"),
    (GOOD.replace("math.sin(world.rng.random())", "getattr(world, '_world')"), "banned-call"),
    (GOOD.replace("math.sin(world.rng.random())", "world.rng.__class__"), "dunder-access"),
    (GOOD.replace("math.sin(world.rng.random())", "random.random()"), "banned-name"),
    (GOOD.replace("math.sin(world.rng.random())", "len(set([1, 2]))"), "non-deterministic"),
    (GOOD.replace("math.sin(world.rng.random())", "float(hash(b))"), "non-deterministic"),
    (GOOD.replace("math.sin(world.rng.random())", "float(id(b))"), "non-deterministic"),
    (GOOD.replace("math.sin(world.rng.random())", "len(frozenset([1]))"), "non-deterministic"),
    (GOOD.replace("math.sin(world.rng.random())", "len({1, 2, 3})"), "non-deterministic"),
    (GOOD.replace("math.sin(world.rng.random())", "len({x for x in range(3)})"), "non-deterministic"),
    (GOOD + "\ncounter = 0\n", "module-state"),
    (GOOD + "\ncache = {}\n", "module-state"),
    (GOOD.replace('"contract": 1', '"contract": 99'), "contract-version"),
    (GOOD.replace('"species": ["blob"]', '"species": []'), "meta-species"),
    (GOOD.replace('"species": ["blob"]', '"species": ["Blob!"]'), "species-name"),
    (GOOD.replace("def setup(world):", "def setup(world, extra):"), "contract-signature"),
    (GOOD.replace("def on_tick(world):\n    for b in world.entities(\"blob\"):\n        world.move(b, math.sin(world.rng.random()), 0.0)\n", "\n"), "contract-missing"),
    (GOOD.replace("PLUGIN_META = {", "PLUGIN_META = dict(x=1) or {"), "meta-not-literal"),
    (GOOD + "\ndef helper():\n    global counter\n    counter = 1\n", "global-state"),
    (GOOD + "\nclass Sneaky:\n    pass\n", "module-state"),
    ("def setup(world:\n", "syntax"),
]


def test_good_plugin_passes():
    result = validate_plugin(GOOD)
    assert result.ok, result.as_dict()
    assert result.meta["name"] == "good_one"


@pytest.mark.parametrize("source,expected_code", HOSTILE, ids=[c for _, c in HOSTILE])
def test_hostile_fixtures_rejected(source, expected_code):
    result = validate_plugin(source)
    assert not result.ok
    codes = {v.code for v in result.errors}
    assert expected_code in codes, f"expected {expected_code} in {codes}: {result.as_dict()}"


def test_unbounded_loop_is_warning_not_error():
    src = GOOD + "\ndef helper(world):\n    while True:\n        world.rng.random()\n"
    result = validate_plugin(src)
    assert result.ok
    assert any(w.code == "unbounded-loop" for w in result.warnings)


def test_example_plugins_pass_validation():
    root = Path(__file__).resolve().parents[2] / "plugins_examples"
    for path in sorted(root.glob("*.py")):
        result = validate_plugin(path.read_text())
        assert result.ok, f"{path.name}: {result.as_dict()}"


def test_reasons_are_machine_readable():
    result = validate_plugin("import os\nPLUGIN_META = {}\n")
    d = result.as_dict()
    assert d["ok"] is False
    assert all({"code", "message", "line"} <= v.keys() for v in d["errors"])
