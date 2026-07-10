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
    # the whole shoal in a handful of vectorized passes — no Python per-fish loop.
    # A HUNGRIER fish (higher idle burn) needs more plankton to break even, so the
    # ocean sustains far fewer of them — the shoal's food-set carrying capacity
    # settles low, which keeps the sim fast. This is a food-economy limit, not a cap.
    world.metabolize("fish", 0.06)
    world.graze("fish", rate=0.02, gain=6.0, max_energy=210.0, on="plankton")
    # swim/roam with a slowly-drifting persistent heading so the shoal actually
    # travels the oceans; the engine's aquatic confinement reverts any step that
    # would land a fish on ground, so this never needs explicit shore-avoidance.
    world.wander("fish", speed=0.3, turn=0.2)
    # SLOW breeding: a ~5-day incubation then a spawn of 3 fry, gated by local
    # crowding — the shoal grows as a gentle wave to its plankton-set ceiling rather
    # than booming there in a few days. Only fed fish (over water plankton) reach the
    # bar, so breeding stays aquatic; density-dependent. The single engine-wide
    # 500/species cap is the only hard limit; the governor tunes it from here.
    world.breed("fish", energy_over=200.0, cost=150.0, offspring_energy=45.0,
                crowd_max=3, crowd_radius=12.0, gestation=3000.0, litter=3)
