"""Fish: the ocean's herbivore. Swims in open water, filter-feeds plankton, and
breeds where the plankton is rich. Stays wet (turns back at the shore) and never
drowns (swim_speed > 0). No hard cap — density-gated breeding + the plankton
supply hold it at a soft carrying capacity."""

import math

PLUGIN_META = {
    "name": "fish_shoal",
    "contract": 1,
    "species": ["fish"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "fish", size=0.45, color="#4aa3c9", speed=0.8, swim_speed=1.2,
        strata=(world.SURFACE,), props=("heading",),
    )
    for _ in range(120):
        x, y = world.random_water_point()
        world.spawn("fish", x, y, energy=90.0)


def on_tick(world):
    for fish in world.entities("fish"):
        energy = world.get(fish, "energy") - 0.05
        x, y, _z = world.pos(fish)
        f = world.face(fish)

        # stay wet: if it has drifted onto land, turn around and swim back to water
        if not world.water_at(x, y, f):
            hd = world.get(fish, "heading") + math.pi
            world.set(fish, "heading", hd)
            world.set(fish, "energy", energy)
            world.move(fish, math.cos(hd) * 1.2, math.sin(hd) * 1.2)
            continue

        # filter-feed plankton — the engine credits this fish at tick end from the
        # cell's ACTUAL plankton (shared with the shoal), so it's food-limited
        world.eat_plankton(fish, x, y, 0.02, gain=32.0, face=f)
        world.set(fish, "energy", min(energy, 160.0))

        # swim with a drifting heading, steering away from the shore (look-ahead)
        hd = world.get(fish, "heading")
        if hd == 0.0:
            hd = world.rng.uniform(0.1, 6.28)
        hd += world.rng.uniform(-0.2, 0.2)
        if not world.water_at(x + math.cos(hd) * 3.0, y + math.sin(hd) * 3.0, f):
            hd += 2.2  # shore ahead — veer back into open water
        world.set(fish, "heading", hd)
        world.move(fish, math.cos(hd), math.sin(hd))

        # soft-capped breeding: only in rich, uncrowded water (no hard population cap)
        if energy > 120.0 and world.get(fish, "age") > 150:
            if world.plankton_at(x, y, f) > 0.05 and \
                    len(world.within(fish, 5.0, species="fish")) < 5:
                world.spawn("fish", x + world.rng.uniform(-2, 2),
                            y + world.rng.uniform(-2, 2), energy=45.0, face=f)
                world.set(fish, "energy", energy - 50.0)
