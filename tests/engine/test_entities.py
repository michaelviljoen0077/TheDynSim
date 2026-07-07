"""Story 1.2: SoA store, generational handles, growth, species registry."""

import pytest

from engine.entities import SURFACE, EntityStore, SpeciesRegistry, handle_generation, handle_index


def make_store(cap=8):
    return EntityStore(capacity=cap, max_prop_slots=4)


def test_spawn_and_valid():
    s = make_store()
    h = s.spawn(0, 1.0, 2.0, 0.0, SURFACE, energy=50.0)
    assert s.is_valid(h)
    assert s.count == 1
    i = handle_index(h)
    assert (s.px[i], s.py[i]) == (1.0, 2.0)


def test_remove_invalidates_and_recycles_with_new_generation():
    s = make_store()
    h1 = s.spawn(0, 0, 0, 0, SURFACE, 1.0)
    row = handle_index(h1)
    assert s.remove(h1)
    assert not s.is_valid(h1)
    assert not s.remove(h1)  # double remove is a recorded no-op
    # freelist is LIFO: the same row comes back with a bumped generation
    h2 = s.spawn(1, 5, 5, 0, SURFACE, 2.0)
    assert handle_index(h2) == row
    assert handle_generation(h2) == handle_generation(h1) + 1
    assert not s.is_valid(h1)  # stale handle stays dead even though row is alive
    assert s.is_valid(h2)


def test_grow_preserves_entities():
    s = make_store(cap=4)
    handles = [s.spawn(0, i, i, 0, SURFACE, float(i)) for i in range(4)]
    h5 = s.spawn(0, 99, 99, 0, SURFACE, 99.0)  # triggers growth
    assert s.capacity == 8
    assert all(s.is_valid(h) for h in handles) and s.is_valid(h5)
    assert s.energy[handle_index(handles[3])] == 3.0


def test_registry_rejects_duplicates_and_excess_props():
    r = SpeciesRegistry(max_prop_slots=2)
    r.register("vole", props=("hunger",))
    with pytest.raises(ValueError):
        r.register("vole")
    with pytest.raises(ValueError):
        r.register("owl", props=("a", "b", "c"))


def test_alive_indices_by_species():
    s = make_store()
    a = s.spawn(0, 0, 0, 0, SURFACE, 1)
    s.spawn(1, 1, 1, 0, SURFACE, 1)
    s.spawn(0, 2, 2, 0, SURFACE, 1)
    assert list(s.alive_indices(0)) == [handle_index(a), 2]
    assert len(s.alive_indices()) == 3
