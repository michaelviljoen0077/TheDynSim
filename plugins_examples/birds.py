"""Birds: seed-eating sky flock — the BATCHED-PRIMITIVE reference. Its whole tick
is four vectorized herd calls (metabolize/graze/wander/breed) with NO per-entity
Python loop, so a big flock costs almost nothing. This is the pattern to prefer
for any species whose members all behave the same way."""

PLUGIN_META = {
    "name": "sky_flock",
    "contract": 1,
    "species": ["bird"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "bird", size=0.5, color="#7fd4ff", speed=0.9,
        strata=(world.SKY,), props=("heading",),
    )
    for _ in range(90):
        x, y = world.random_surface_point()
        world.spawn("bird", x, y, stratum=world.SKY, energy=110.0,
                    z=3.0 + 3.0 * world.rng.random())


def on_tick(world):
    # The entire flock, processed in one NumPy pass each — no Python per-bird loop.
    # Birds peck seeds from vegetated ground below: they GAIN energy only over
    # flora and starve where the land is barren, so a flock can't persist over a
    # dead planet. Breeding costs energy and is capped, so growth is a slow wave.
    world.metabolize("bird", 0.04)                                    # steady upkeep
    # birds eat LITTLE — a light seed peck, so the flock's footprint on the shared
    # flora is small (it barely competes with the grazing herd)
    world.graze("bird", rate=0.006, gain=45.0, max_energy=150.0)
    world.wander("bird", speed=0.9, turn=0.15)                        # gentle drift
    # No hard cap: density-dependent breeding (crowd_max) plus PREDATION by the
    # raptor hold the flock at a soft equilibrium. Birds eat little (light peck
    # above), so an uncapped flock no longer out-forages the grazing herd.
    world.breed("bird", energy_over=125.0, cost=62.0, offspring_energy=55.0,
                crowd_max=6, crowd_radius=8.0)
