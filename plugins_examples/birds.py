"""Birds: decorative sky flock. Shared heading in world.store, per-entity wing phase."""

import math

PLUGIN_META = {
    "name": "sky_flock",
    "contract": 1,
    "species": ["bird"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "bird", size=1.1, color="#7fd4ff", speed=1.6,
        strata=(world.SKY,), props=("phase", "flock"),
    )
    for k in range(1, 5):  # 4 independent flocks
        world.store.set("heading_" + str(k), world.rng.uniform(0.0, 6.28))
    for _ in range(120):
        x, y = world.random_surface_point()
        world.spawn("bird", x, y, stratum=world.SKY, energy=100.0,
                    z=2.0 + 4.0 * world.rng.random())


def on_tick(world):
    # independent flocks: each drifts its own heading, so the sky holds several
    # groups instead of one converged mass
    headings = {}
    for k in range(1, 5):
        key = "heading_" + str(k)
        h = world.store.get(key, 0.0) + world.rng.uniform(-0.06, 0.06)
        world.store.set(key, h)
        headings[k] = h
    center = world.size * 0.5
    margin = world.size * 0.15
    for bird in world.entities("bird"):
        flock = int(world.get(bird, "flock"))
        if flock == 0:  # newly spawned: join a random flock
            flock = 1 + int(world.rng.integers(0, 4))
            world.set(bird, "flock", float(flock))
        phase = world.get(bird, "phase") + 0.12
        world.set(bird, "phase", phase)
        x, y, z = world.pos(bird)
        angle = headings[flock] + world.rng.uniform(-0.4, 0.4)
        dx = math.cos(angle) * 0.9
        dy = math.sin(angle) * 0.9
        # near an edge, blend in a pull toward the world center so the flock's
        # shared heading can never pile everyone into a corner
        edge = min(x, y, world.size - 1 - x, world.size - 1 - y)
        if edge < margin:
            pull = (margin - edge) / margin  # 0 at margin edge -> 1 at the wall
            cx, cy = center - x, center - y
            d = (cx * cx + cy * cy) ** 0.5 or 1.0
            dx = dx * (1.0 - pull) + (cx / d) * 1.2 * pull
            dy = dy * (1.0 - pull) + (cy / d) * 1.2 * pull
        target_z = 4.0 + 2.0 * math.sin(phase)
        world.move(bird, dx, dy, (target_z - z) * 0.1)
        world.set(bird, "energy", 100.0)  # decorative: never starves
