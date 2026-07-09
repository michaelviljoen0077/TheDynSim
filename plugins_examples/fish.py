"""Fish: the ocean's herbivore, driven entirely by BATCHED primitives (no per-fish
loop) so a big shoal is cheap. It filter-feeds plankton — which only grows on open
water — so a fish stranded on land simply can't feed and dies back; the shoal thus
stays where the food is (in the water) without any explicit shore-steering. No hard
cap: density-dependent breeding + the plankton supply + shark predation hold it at
a soft carrying capacity."""

PLUGIN_META = {
    "name": "fish_shoal",
    "contract": 1,
    "species": ["fish"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "fish", size=0.45, color="#4aa3c9", speed=0.8, swim_speed=1.2,
        strata=(world.SURFACE,),
    )
    for _ in range(120):
        x, y = world.random_water_point()
        world.spawn("fish", x, y, energy=90.0)


def on_tick(world):
    # the whole shoal in three vectorized passes — no Python per-fish loop
    world.metabolize("fish", 0.05)                                       # steady upkeep
    world.graze("fish", rate=0.02, gain=32.0, max_energy=160.0, on="plankton")
    # density-dependent breeding (crowd_max) — no hard cap; only fed fish (over
    # plankton, i.e. in water) ever reach the energy bar, so breeding stays aquatic
    world.breed("fish", energy_over=120.0, cost=50.0, offspring_energy=45.0,
                crowd_max=5, crowd_radius=8.0)
