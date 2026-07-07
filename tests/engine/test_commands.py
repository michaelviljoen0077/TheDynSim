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
