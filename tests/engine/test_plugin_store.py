"""PluginStore quotas + type enforcement (snapshot-safety)."""

import pytest

from engine import World, WorldConfig
from engine.world_api import CapabilityViolation, WorldAPI


def _store():
    w = World(WorldConfig(seed=1, size=32))
    return WorldAPI(w, "p", 0, ["x"]).store


def test_str_keys_and_scalar_values_are_accepted():
    s = _store()
    s.set("count", 3)
    s.set("ratio", 1.5)
    s.set("label", "hi")
    assert s.get("count") == 3 and s.get("ratio") == 1.5 and s.get("label") == "hi"


def test_non_str_keys_are_rejected():
    # non-str keys survive in memory but JSON-coerce to str on snapshot, silently
    # desyncing after a reload/shadow/rollback — so they must be rejected up front
    s = _store()
    with pytest.raises(CapabilityViolation):
        s.set(5, 1.0)


def test_non_scalar_values_are_rejected():
    s = _store()
    with pytest.raises(CapabilityViolation):
        s.set("bad", [1, 2, 3])
