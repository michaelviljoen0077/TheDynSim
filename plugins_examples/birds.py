"""Birds: seed-eating sky flock. They forage flora seeds from the ground below,
starve without it, and reproduce modestly — a real (if light) part of the web."""

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
    for _ in range(90):
        x, y = world.random_surface_point()
        world.spawn("bird", x, y, stratum=world.SKY, energy=110.0,
                    z=2.0 + 4.0 * world.rng.random())


def on_tick(world):
    # PER-BIRD wandering heading (a shared flock heading piled them at face edges
    # on the cube). Birds now peck seeds from vegetated ground below: they GAIN
    # energy only over flora, lose it steadily, and starve where the land is
    # barren — so a flock can't persist over a dead planet.
    pop = world.count("bird")
    for bird in world.entities("bird"):
        energy = world.get(bird, "energy") - 0.04
        x, y, z = world.pos(bird)
        f = world.face(bird)

        if world.flora_at(x, y, f) > 0.02:      # seeds below: forage (light)
            energy += world.eat_flora(x, y, 0.01, f) * 45.0
        world.set(bird, "energy", min(energy, 150.0))
        # energy <= 0 -> engine death sweep (starvation)

        heading = world.get(bird, "heading")
        if heading == 0.0:  # unset (new bird): pick one
            heading = world.rng.uniform(0.1, 6.28)
        heading += world.rng.uniform(-0.15, 0.15)  # gentle wander
        world.set(bird, "heading", heading)
        phase = world.get(bird, "phase") + 0.12
        world.set(bird, "phase", phase)
        target_z = 4.0 + 2.0 * math.sin(phase)
        world.move(bird, math.cos(heading) * 0.9, math.sin(heading) * 0.9,
                   (target_z - z) * 0.1)

        if energy > 125.0 and pop < 300:        # modest, capped breeding
            pop += 1
            world.set(bird, "energy", energy * 0.5)
            world.spawn("bird", x, y, stratum=world.SKY, energy=energy * 0.5,
                        z=z, face=f)
