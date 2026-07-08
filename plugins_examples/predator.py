"""Wolf: surface predator. Hunts grazers, patrols to find prey, starves, breeds."""

import math

PLUGIN_META = {
    "name": "wolf_pack",
    "contract": 1,
    "species": ["wolf"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "wolf", size=0.9, color="#8a4a4a", speed=1.05, swim_speed=0.6, lifespan=6000,
        strata=(world.SURFACE,), props=("gestation", "heading"),
    )
    # spawn near the herds: on a large sparse world a randomly-placed wolf can
    # starve before ever finding prey
    grazers = world.entities("grazer")
    for _ in range(10):
        if grazers:
            k = int(world.rng.integers(0, len(grazers)))
            gx, gy, _gz = world.pos(grazers[k])
            x = gx + world.rng.uniform(-12, 12)
            y = gy + world.rng.uniform(-12, 12)
        else:
            x, y = world.random_surface_point()
        # generous founder energy: on a big sparse map a wolf may hunt a long
        # while before its first kill — don't let the pack starve at birth
        world.spawn("wolf", x, y, stratum=world.SURFACE, energy=260.0)


def on_tick(world):
    # predator population rides on prey abundance: ~1 wolf per 25 grazers.
    # counted once per tick (buffered spawns don't show up in count() mid-tick)
    pack_size = world.count("wolf")
    pack_cap = max(2, world.count("grazer") // 25)
    for wolf in world.entities("wolf"):
        energy = world.get(wolf, "energy") - 0.15
        x, y, _z = world.pos(wolf)
        f = world.face(wolf)

        # wolves can paddle (swim_speed) so they never drown, but water is slow
        # and preyless — head for shore instead of hunting while wet
        if world.water_at(x, y, f):
            world.set(wolf, "energy", energy)
            best_dx, best_dy, best_h = 0.0, 0.0, world.height_at(x, y, f)
            for ddx, ddy in ((4.0, 0.0), (-4.0, 0.0), (0.0, 4.0), (0.0, -4.0)):
                h = world.height_at(x + ddx, y + ddy, f)
                if h > best_h:
                    best_dx, best_dy, best_h = ddx, ddy, h
            world.move(wolf, best_dx / 2.0 + world.rng.uniform(-0.3, 0.3),
                       best_dy / 2.0 + world.rng.uniform(-0.3, 0.3))
            continue

        # 3D global queries find prey across face seams, so a modest scent radius
        # suffices; direction_to steers correctly even when the prey is on another
        # face, and distance() is the true 3D range.
        prey = world.nearest(wolf, species="grazer", radius=22.0)
        if prey is not None:
            if world.distance(wolf, prey) < 1.6:
                energy += world.attack(prey, 60.0) * 0.8
            else:
                dx, dy = world.direction_to(wolf, prey)
                world.move(wolf, dx, dy)
                world.set(wolf, "heading", math.atan2(dy, dx))  # remember the chase dir
        else:
            # no prey in range: PATROL along a persistent, drifting heading so the
            # pack ranges out to new hunting grounds instead of milling in place
            # and starving. (Keeps the direction of the last chase, then wanders.)
            hd = world.get(wolf, "heading")
            if hd == 0.0:
                hd = world.rng.uniform(0.1, 6.28)
            hd += world.rng.uniform(-0.12, 0.12)
            if world.water_at(x + math.cos(hd) * 3.0, y + math.sin(hd) * 3.0, f):
                hd += 2.2  # steer around water while patrolling
            world.set(wolf, "heading", hd)
            world.move(wolf, math.cos(hd), math.sin(hd))

        world.set(wolf, "energy", min(energy, 300.0))

        gestation = world.get(wolf, "gestation")
        if gestation > 0.0:
            world.set(wolf, "gestation", gestation - 1.0)
            if gestation <= 1.0 and pack_size < pack_cap:
                pack_size += 1
                world.spawn("wolf", x + world.rng.uniform(-2, 2), y + world.rng.uniform(-2, 2),
                            stratum=world.SURFACE, energy=90.0, face=world.face(wolf))
        elif energy > 175.0 and pack_size < pack_cap:
            world.set(wolf, "gestation", 60.0)
            world.set(wolf, "energy", energy - 55.0)
