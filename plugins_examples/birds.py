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
        "bird", size=1.1, color="#7fd4ff",
        strata=(world.SKY,), props=("phase",),
    )
    world.store.set("heading", 0.0)
    for _ in range(200):
        x, y = world.random_surface_point()
        world.spawn("bird", x, y, stratum=world.SKY, energy=100.0,
                    z=2.0 + 4.0 * world.rng.random())


def on_tick(world):
    heading = world.store.get("heading", 0.0) + world.rng.uniform(-0.06, 0.06)
    world.store.set("heading", heading)
    for bird in world.entities("bird"):
        phase = world.get(bird, "phase") + 0.12
        world.set(bird, "phase", phase)
        angle = heading + world.rng.uniform(-0.4, 0.4)
        _x, _y, z = world.pos(bird)
        target_z = 4.0 + 2.0 * math.sin(phase)
        world.move(bird, math.cos(angle) * 0.9, math.sin(angle) * 0.9,
                   (target_z - z) * 0.1)
        world.set(bird, "energy", 100.0)  # decorative: never starves
