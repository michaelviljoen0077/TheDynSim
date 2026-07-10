"""Raptor: a sky predator. Wheels over the world, stoops on the bird flock via
engine-mediated attack, and its numbers ride on bird abundance — the aerial mirror
of the wolf. With a raptor in the sky, the bird flock is capped by PREDATION rather
than a hard number, so birds can run capless. Fliers never drown."""

import math

PLUGIN_META = {
    "name": "raptor_wing",
    "contract": 1,
    "species": ["raptor"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "raptor", size=0.6, color="#b58a5a", speed=1.1, lifespan=15000,
        strata=(world.SKY,), props=("gestation", "heading"),
    )
    birds = world.entities("bird")
    for _ in range(5):
        if birds:
            k = int(world.rng.integers(0, len(birds)))
            bx, by, _bz = world.pos(birds[k])
            x, y = bx + world.rng.uniform(-10, 10), by + world.rng.uniform(-10, 10)
        else:
            x, y = world.random_surface_point()
        world.spawn("raptor", x, y, stratum=world.SKY, energy=230.0,
                    z=5.0 + 3.0 * world.rng.random())


def on_tick(world):
    raptors = world.entities("raptor")
    preys = world.nearest_many("raptor", "bird", 24.0)   # one batched query for the wing
    for i, raptor in enumerate(raptors):
        energy = world.get(raptor, "energy") - 0.05   # burn: meals last a while, but starves back fast
        x, y, z = world.pos(raptor)
        f = world.face(raptor)
        prey = preys[i]
        prey_close = prey is not None and world.distance(raptor, prey) < 6.0

        # roost at local night unless a bird is right there (predators don't hunt
        # 24/7). Roosting does NOT manufacture energy: a raptor lives on caught birds
        # alone, so a wing with no flock to hunt starves back (predator-prey feedback).
        if world.daylight(raptor) < -0.15 and not prey_close:
            world.set(raptor, "energy", energy)
            world.move(raptor, world.rng.uniform(-0.15, 0.15), world.rng.uniform(-0.15, 0.15))
            continue

        if prey is not None:
            if world.distance(raptor, prey) < 2.0:
                world.attack(raptor, prey, 55.0)          # stoop: engine credits at tick end
            else:
                dx, dy = world.direction_to(raptor, prey)
                world.move(raptor, dx, dy)
                world.set(raptor, "heading", math.atan2(dy, dx))
        else:
            hd = world.get(raptor, "heading")
            if hd == 0.0:
                hd = world.rng.uniform(0.1, 6.28)
            hd += world.rng.uniform(-0.12, 0.12)
            world.set(raptor, "heading", hd)
            world.move(raptor, math.cos(hd), math.sin(hd))

        # hold a soaring altitude in the sky band
        world.move(raptor, 0.0, 0.0, (5.0 - z) * 0.1)
        world.set(raptor, "energy", min(energy, 350.0))

        # NO per-plugin cap: the wing is limited by FOOD, not a number. A raptor
        # breeds only when a full belly (caught birds) carries it over the bar, then
        # pays a steep energy cost — so the wing grows only when the flock is fat
        # enough to feed it and thins by starvation when birds are scarce. That
        # predation is what holds the (capless) flock in check; the single 500/species
        # ceiling is the only hard limit.
        gestation = world.get(raptor, "gestation")
        if gestation > 0.0:
            world.set(raptor, "gestation", gestation - 1.0)
            if gestation <= 1.0:
                for _ in range(2):   # a small clutch (K-strategist: few, invested)
                    world.spawn("raptor", x + world.rng.uniform(-2, 2),
                                y + world.rng.uniform(-2, 2),
                                stratum=world.SKY, energy=120.0, z=z, face=f)
        elif energy > 230.0 and world.count("bird") > 30 * world.count("raptor"):
            # RESOURCE-GATED breeding (food security), the same principle as the herd
            # only breeding on lush grass — a raptor raises young only when there are
            # plenty of birds PER raptor. This softly pins the wing near a healthy
            # predator:prey ratio (~1 raptor per 10 birds) so it crops the flock DOWN
            # rather than to zero. It is NOT a population cap — it's "don't breed in a
            # famine", and it scales with the flock instead of a fixed number (which
            # is what a big, thinly-populated world needs; local density barely bites).
            world.set(raptor, "gestation", 3600.0)  # ~6-day pregnancy
            world.set(raptor, "energy", energy - 170.0)
