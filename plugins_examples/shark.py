"""Shark: the ocean's predator. Swims (faster than fish), hunts the fish shoal via
engine-mediated attack, stays wet, and its numbers ride on fish abundance — the
aquatic mirror of the wolf. Pair it with fish.py to close the water food chain."""

import math

PLUGIN_META = {
    "name": "shark_school",
    "contract": 1,
    "species": ["shark"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "shark", size=0.8, color="#c56b6b", speed=0.9, swim_speed=1.5, lifespan=6000,
        strata=(world.SURFACE,), props=("gestation", "heading"),
    )
    # seed near the shoals so a founder shark isn't stranded in empty ocean
    fish = world.entities("fish")
    for _ in range(12):
        if fish:
            k = int(world.rng.integers(0, len(fish)))
            fx, fy, _fz = world.pos(fish[k])
            x, y = fx + world.rng.uniform(-8, 8), fy + world.rng.uniform(-8, 8)
        else:
            x, y = world.random_water_point()
        world.spawn("shark", x, y, energy=240.0)


def on_tick(world):
    pack = world.count("shark")
    pack_cap = max(2, world.count("fish") // 30)   # predators ride on prey abundance
    for shark in world.entities("shark"):
        energy = world.get(shark, "energy") - 0.12
        x, y, _z = world.pos(shark)
        f = world.face(shark)

        # stay wet: beached sharks turn back to open water
        if not world.water_at(x, y, f):
            hd = world.get(shark, "heading") + math.pi
            world.set(shark, "heading", hd)
            world.set(shark, "energy", energy)
            world.move(shark, math.cos(hd) * 1.5, math.sin(hd) * 1.5)
            continue

        prey = world.nearest(shark, species="fish", radius=20.0)
        prey_close = prey is not None and world.distance(shark, prey) < 6.0

        # night rest (unless a fish is right there) — predators don't hunt 24/7
        if world.daylight(shark) < -0.15 and not prey_close:
            world.set(shark, "energy", min(energy + 0.1, 300.0))
            world.move(shark, world.rng.uniform(-0.15, 0.15), world.rng.uniform(-0.15, 0.15))
            continue

        if prey is not None:
            if world.distance(shark, prey) < 1.8:
                world.attack(shark, prey, 55.0)        # engine credits at tick end
            else:
                dx, dy = world.direction_to(shark, prey)
                world.move(shark, dx, dy)
                world.set(shark, "heading", math.atan2(dy, dx))
        else:
            # patrol open water, steering off the shore
            hd = world.get(shark, "heading")
            if hd == 0.0:
                hd = world.rng.uniform(0.1, 6.28)
            hd += world.rng.uniform(-0.12, 0.12)
            if not world.water_at(x + math.cos(hd) * 4.0, y + math.sin(hd) * 4.0, f):
                hd += 2.2
            world.set(shark, "heading", hd)
            world.move(shark, math.cos(hd), math.sin(hd))

        world.set(shark, "energy", min(energy, 300.0))

        gestation = world.get(shark, "gestation")
        if gestation > 0.0:
            world.set(shark, "gestation", gestation - 1.0)
            if gestation <= 1.0 and pack < pack_cap:
                pack += 1
                world.spawn("shark", x + world.rng.uniform(-2, 2), y + world.rng.uniform(-2, 2),
                            energy=90.0, face=f)
        elif energy > 190.0 and pack < pack_cap:
            world.set(shark, "gestation", 70.0)
            world.set(shark, "energy", energy - 60.0)
