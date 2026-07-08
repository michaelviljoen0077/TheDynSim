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
        "bird", size=0.5, color="#7fd4ff", speed=0.9,
        strata=(world.SKY,), props=("phase", "heading"),
    )
    for _ in range(120):
        x, y = world.random_surface_point()
        world.spawn("bird", x, y, stratum=world.SKY, energy=100.0,
                    z=2.0 + 4.0 * world.rng.random())


def on_tick(world):
    # PER-BIRD wandering heading. A shared flock heading made birds ping-pong and
    # pile at face edges on the cube (a local heading points back across the seam
    # after a fold); an independent drifting heading per bird just diffuses them
    # smoothly over the whole planet with no edge-trapping.
    for bird in world.entities("bird"):
        heading = world.get(bird, "heading")
        if heading == 0.0:  # unset (new bird): pick one
            heading = world.rng.uniform(0.1, 6.28)
        heading += world.rng.uniform(-0.15, 0.15)  # gentle wander
        world.set(bird, "heading", heading)
        phase = world.get(bird, "phase") + 0.12
        world.set(bird, "phase", phase)
        _x, _y, z = world.pos(bird)
        target_z = 4.0 + 2.0 * math.sin(phase)
        world.move(bird, math.cos(heading) * 0.9, math.sin(heading) * 0.9,
                   (target_z - z) * 0.1)
        world.set(bird, "energy", 100.0)  # decorative: never starves
