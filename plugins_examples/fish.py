"""Fish: the ocean's herbivore, driven entirely by BATCHED primitives (no per-fish
loop) so a big shoal is cheap. It filter-feeds plankton — which only grows on open
water. The fish is registered `aquatic=True`, so the ENGINE confines it to water:
it wanders and swims freely, but any move onto land is undone by the engine (the
water analogue of a bird's `fly`). So the shoal roams the oceans yet can never
strand on land. No hard cap: density-dependent breeding + the plankton supply +
shark predation hold it at a soft carrying capacity."""

PLUGIN_META = {
    "name": "fish_shoal",
    "contract": 1,
    "species": ["fish"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "fish", size=0.45, color="#4aa3c9", speed=0.8, swim_speed=1.2, lifespan=9000,
        strata=(world.SURFACE,), props=("gestation", "heading"), aquatic=True,
    )
    for _ in range(180):   # an established shoal, so it survives the incubation lag
        x, y = world.random_water_point()
        world.spawn("fish", x, y, energy=90.0)


def on_tick(world):
    # the whole shoal in a handful of vectorized passes — no Python per-fish loop
    world.metabolize("fish", 0.03)                                       # low idle burn
    world.graze("fish", rate=0.02, gain=6.0, max_energy=210.0, on="plankton")
    # swim/roam with a slowly-drifting persistent heading so the shoal actually
    # travels the oceans; the engine's aquatic confinement reverts any step that
    # would land a fish on ground, so this never needs explicit shore-avoidance.
    world.wander("fish", speed=0.3, turn=0.2)
    # r-strategist prey: a ~3-day incubation then a SPAWN of 6 fry (fish are
    # fecund) so the shoal out-breeds shark predation. Only fed fish (over water
    # plankton) reach the bar, so breeding stays aquatic; density-dependent.
    world.breed("fish", energy_over=190.0, cost=150.0, offspring_energy=45.0,
                crowd_max=4, crowd_radius=10.0, gestation=1800.0, litter=6)
