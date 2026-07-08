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


def test_old_age_death():
    w = World(WorldConfig(seed=4, size=64, initial_capacity=64))
    sp = w.registry.register("mayfly", lifespan=10)
    h = w.store.spawn(sp.id, 5.0, 5.0, 0.0, SURFACE, 100.0)
    w.run(10)
    assert w.store.is_valid(h)   # age just reached 10; sweep runs before the increment
    w.run(1)
    assert not w.store.is_valid(h)
    assert w.deaths.get("mayfly", {}).get("old_age", 0) == 1


def test_per_species_hard_cap():
    from engine.plugin_host import PluginHost
    w = World(WorldConfig(seed=4, size=128, initial_capacity=8192,
                          max_entities_per_species=50, max_entities_per_plugin=10000))
    breeder = '''
PLUGIN_META = {"name": "rabbits", "contract": 1, "species": ["rabbit"], "lineage_parent": None}
def setup(world):
    world.register_species("rabbit")
    for _ in range(40):
        x, y = world.random_surface_point()
        world.spawn("rabbit", x, y, energy=100.0)
def on_tick(world):
    for r in world.entities("rabbit"):
        x, y, _z = world.pos(r)
        world.spawn("rabbit", x, y, energy=100.0)  # breed with no limit
'''
    h = PluginHost(w)
    rec = h.install(breeder)
    w.run(20)
    assert w.store.alive_indices(w.registry.by_name["rabbit"].id).size <= 50
    assert rec.api.spawn_drops > 0
    assert rec.status == "live"  # hitting the cap is not an error


def test_crowding_stress_drains_dense_clusters():
    # pack many entities into one spot; crowding must drain energy and kill some
    w = World(WorldConfig(seed=4, size=128, initial_capacity=8192,
                          crowding_softcap=4, crowding_penalty=5.0, crowding_radius=6.0))
    sp = w.registry.register("packed", lifespan=0)
    for _ in range(40):
        w.store.spawn(sp.id, 20.0 + w.rng.uniform(-1, 1), 20.0 + w.rng.uniform(-1, 1),
                      0.0, SURFACE, 20.0)
    w.run(10)
    survivors = w.store.alive_indices(sp.id).size
    assert survivors < 40  # dense cluster thinned
    assert w.deaths.get("packed", {}).get("crowding", 0) > 0


def test_wrap_topology_positions_and_queries():
    """Toroidal world: crossing an edge wraps to the far side, and nearest/within
    see across the seam (min-image distance)."""
    w = World(WorldConfig(seed=2, size=64, topology="wrap", initial_capacity=256))
    assert w.config.wrap
    sp = w.registry.register("wrapbug", speed=8.0, swim_speed=8.0)
    # entity near the right edge steps off it -> reappears near the left edge
    h = w.store.spawn(sp.id, 62.0, 10.0, 0.0, SURFACE, 100.0)
    w.commands.move(h, 5.0, 0.0)   # 62 + 5 = 67 -> wraps to 3
    w.step()
    i = handle_index(h)
    assert abs(float(w.store.px[i]) - 3.0) < 1e-4

    # two entities on opposite sides of the seam are neighbours on a torus
    a = w.store.spawn(sp.id, 1.0, 30.0, 0.0, SURFACE, 100.0)
    b = w.store.spawn(sp.id, 63.0, 30.0, 0.0, SURFACE, 100.0)  # 2 apart across the seam
    w.spatial.rebuild(w.store)
    near = w.spatial.nearest(w.store, 1.0, 30.0, 5.0, SURFACE, species_id=sp.id,
                             exclude_row=handle_index(a))
    assert near == handle_index(b)


def test_flat_topology_still_clamps():
    w = World(WorldConfig(seed=2, size=64, topology="flat", initial_capacity=256))
    sp = w.registry.register("edgebug", speed=8.0, swim_speed=8.0)
    h = w.store.spawn(sp.id, 62.0, 10.0, 0.0, SURFACE, 100.0)
    w.commands.move(h, 8.0, 0.0)
    w.step()
    assert float(w.store.px[handle_index(h)]) <= 63.0   # clamped, not wrapped


def test_wrap_determinism():
    from engine import state_hash
    def run(seed):
        w = World(WorldConfig(seed=seed, size=64, topology="wrap", initial_capacity=512))
        sp = w.registry.register("c", speed=3.0)
        for _ in range(50):
            w.store.spawn(sp.id, float(w.rng.uniform(0, 64)), float(w.rng.uniform(0, 64)),
                          0.0, SURFACE, 100.0)
        prng = w.plugin_rng("p")
        for _ in range(120):
            for hnd in w.store.handles_of(w.store.alive_indices(sp.id)):
                w.commands.move(hnd, float(prng.uniform(-3, 3)), float(prng.uniform(-3, 3)))
            w.step()
        return state_hash(w)
    assert run(9) == run(9)
