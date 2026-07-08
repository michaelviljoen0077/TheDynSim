"""Story 1.2 AC5: mutation during iteration is safe; stale ops are skipped, not fatal."""

from engine import World, WorldConfig
from engine.entities import SURFACE, handle_index


def make_world():
    w = World(WorldConfig(seed=5, size=64, initial_capacity=256))
    sp = w.registry.register("bug", speed=8.0)  # generous: these tests probe the buffer, not speed
    return w, sp


def test_reads_see_tick_start_state():
    w, sp = make_world()
    h = w.store.spawn(sp.id, 10.0, 10.0, 0.0, SURFACE, 100.0)
    w.commands.move(h, 5.0, 0.0)
    i = handle_index(h)
    assert w.store.px[i] == 10.0          # not yet applied
    w.step()
    assert w.store.px[i] == 15.0          # applied at tick end


def test_mutate_while_iterating_is_safe():
    w, sp = make_world()
    for k in range(20):
        w.store.spawn(sp.id, float(k), 0.0, 0.0, SURFACE, 50.0)
    rows = w.store.alive_indices(sp.id)
    handles = w.store.handles_of(rows)
    # "plugin" removes some and spawns some while iterating the tick-start view
    for n, h in enumerate(handles):
        if n % 2 == 0:
            w.commands.remove(h)
        else:
            w.commands.spawn(sp.id, 30.0, 30.0, 0.0, SURFACE, 25.0, -1)
    w.step()
    assert w.store.count == 20 - 10 + 10
    assert w.commands.stale_ops == 0


def test_stale_handle_ops_are_counted_and_skipped():
    w, sp = make_world()
    h = w.store.spawn(sp.id, 1.0, 1.0, 0.0, SURFACE, 10.0)
    w.commands.remove(h)
    w.commands.move(h, 3.0, 3.0)          # queued against a handle removed earlier in the buffer
    w.commands.set_energy(h, 99.0)
    w.step()
    assert w.store.count == 0
    assert w.commands.stale_ops == 2


def test_positions_clamped_to_world():
    w, sp = make_world()
    h = w.store.spawn(sp.id, 1.0, 1.0, 0.0, SURFACE, 10.0)
    w.commands.move(h, -8.0, 0.0)         # within speed, past the world edge
    w.step()
    i = handle_index(h)
    assert w.store.px[i] == 0.0
    assert w.store.py[i] < w.config.size


def test_flora_consumption_is_deferred_and_clamped():
    w, _sp = make_world()
    ix, iy = 5, 5
    w.flora.density[ix, iy] = 0.1
    tick_start = float(w.flora.density[ix, iy])
    # two grazers eat the same cell in one tick, each estimating against tick-start
    w.commands.eat_flora(ix, iy, 0.08)
    w.commands.eat_flora(ix, iy, 0.08)
    assert float(w.flora.density[ix, iy]) == tick_start   # reads still see tick-start density
    w.commands.apply(w.store, float(w.config.size), flora=w.flora.density)
    assert float(w.flora.density[ix, iy]) == 0.0   # drained in order, clamped, never negative



def test_speed_limit_clamps_net_displacement():
    w = World(WorldConfig(seed=8, size=64, initial_capacity=256))
    slow = w.registry.register("slug", speed=1.0)
    h = w.store.spawn(slow.id, 30.0, 30.0, 0.0, SURFACE, 10.0)
    w.commands.move(h, 10.0, 0.0)   # way over the species speed
    w.commands.move(h, 0.0, 10.0)   # accumulates before the clamp
    w.step()
    i = handle_index(h)
    dx = float(w.store.px[i]) - 30.0
    dy = float(w.store.py[i]) - 30.0
    assert (dx * dx + dy * dy) ** 0.5 <= 1.0 + 1e-5


def test_positions_stay_on_rendered_terrain():
    """Clamp domain is [0, size-1]: the terrain's last vertex, not size-epsilon."""
    w = World(WorldConfig(seed=8, size=64, initial_capacity=256))
    sp = w.registry.register("fast", speed=8.0)
    h = w.store.spawn(sp.id, 62.5, 62.5, 0.0, SURFACE, 10.0)
    for _ in range(5):
        w.commands.move(h, 8.0, 8.0)
        w.step()
    i = handle_index(h)
    assert float(w.store.px[i]) <= 63.0
    assert float(w.store.py[i]) <= 63.0


def test_swimmers_swim_and_nonswimmers_drown():
    w = World(WorldConfig(seed=12, size=64, initial_capacity=256))
    fish = w.registry.register("fish", speed=3.0, swim_speed=1.2)
    goat = w.registry.register("goat", speed=3.0)  # swim_speed 0: water is lethal
    # find a water cell
    import numpy as np
    wy, wx = 0, 0
    water_cells = np.argwhere(w.terrain.water_mask > 0.5)
    assert len(water_cells) > 0
    wx, wy = float(water_cells[0][0]), float(water_cells[0][1])

    hf = w.store.spawn(fish.id, wx, wy, 0.0, SURFACE, 100.0)
    hg = w.store.spawn(goat.id, wx, wy, 0.0, SURFACE, 3.0)

    # fish on water: moves, but clamped to swim speed
    w.commands.move(hf, 3.0, 0.0)
    w.step()
    fi = handle_index(hf)
    assert abs(float(w.store.px[fi]) - wx) <= 1.2 + 1e-5
    assert w.store.energy[fi] == 100.0  # swimmers never drown

    # goat on water: drains 0.8/tick and dies with cause 'drowning'
    for _ in range(5):
        w.step()
    assert not w.store.is_valid(hg)
    assert w.deaths.get("goat", {}).get("drowning", 0) == 1


def test_moves_survive_store_growth_mid_apply():
    """Regression: a spawn that grows the store mid-apply reallocates every array;
    later ops (and the move write-back) must target the NEW arrays, not orphans."""
    w = World(WorldConfig(seed=3, size=64, initial_capacity=4))
    sp = w.registry.register("bug", speed=8.0, swim_speed=8.0)  # amphibious: no drown drain
    h1 = w.store.spawn(sp.id, 10.0, 10.0, 0.0, SURFACE, 50.0)
    h2 = w.store.spawn(sp.id, 20.0, 20.0, 0.0, SURFACE, 50.0)
    # queue: move h1, then enough spawns to exhaust capacity 4 and force _grow,
    # then a move for h2 and an energy write for h1 (post-grow ops)
    w.commands.move(h1, 5.0, 0.0)
    for _ in range(6):
        w.commands.spawn(sp.id, 1.0, 1.0, 0.0, SURFACE, 10.0, -1)
    w.commands.move(h2, 0.0, 5.0)
    w.commands.set_energy(h1, 77.0)
    w.step()
    assert w.store.capacity > 4
    i1, i2 = handle_index(h1), handle_index(h2)
    assert float(w.store.px[i1]) == 15.0   # pre-grow move applied to live arrays
    assert float(w.store.py[i2]) == 25.0   # post-grow move applied to live arrays
    assert float(w.store.energy[i1]) == 77.0
    assert w.store.count == 8
