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
        "bird", size=0.5, color="#7fd4ff", speed=0.9, lifespan=11000,
        strata=(world.SKY,), props=("heading", "gestation"),
    )
    for _ in range(150):   # an established flock, so it survives the incubation lag
        x, y = world.random_surface_point()
        world.spawn("bird", x, y, stratum=world.SKY, energy=110.0,
                    z=3.0 + 3.0 * world.rng.random())


def on_tick(world):
    # The entire flock, processed in one NumPy pass each — no Python per-bird loop.
    # Birds peck seeds from vegetated ground below: they GAIN energy only over
    # flora and starve where the land is barren, so a flock can't persist over a
    # dead planet. Breeding costs energy and is capped, so growth is a slow wave.
    world.metabolize("bird", 0.02)                                    # low idle burn
    # birds eat LITTLE — a light seed peck, so the flock's footprint on the shared
    # flora is small (it barely competes with the grazing herd), and the modest
    # gain means breeding surplus takes days to build
    world.graze("bird", rate=0.008, gain=28.0, max_energy=210.0)
    world.wander("bird", speed=0.9, turn=0.15)                        # gentle drift
    # SLOW breeding (high energy bar + steep cost) + density dependence + raptor
    # predation hold the flock at a low soft equilibrium; the long lifespan carries
    # birds through. No hard cap.
    # r-strategist prey: a ~4-day egg incubation (gestation) then a CLUTCH of 3,
    # so the flock out-breeds raptor predation. Density-dependent (crowd_max).
    world.breed("bird", energy_over=160.0, cost=110.0, offspring_energy=55.0,
                crowd_max=5, crowd_radius=10.0, gestation=2400.0, litter=3, cap=900)
