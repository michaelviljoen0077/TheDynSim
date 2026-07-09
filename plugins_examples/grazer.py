"""Grazer: surface herbivore. Grazes, roams to fresh pasture, flees, reproduces."""

import math

PLUGIN_META = {
    "name": "grazer_herd",
    "contract": 1,
    "species": ["grazer"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "grazer", size=0.7, color="#c9a35c", speed=0.9, lifespan=4500,
        strata=(world.SURFACE,), props=("gestation", "heading"),
        # heritable speed: fast grazers flee wolves better but the engine charges
        # a coupled energy cost, so speed evolves toward an equilibrium (natural
        # selection). Offspring inherit a mutated value (see the spawn below).
        genes={"speed": 1.0}, gene_sigma=0.05,
    )
    for _ in range(140):
        x, y = world.random_surface_point()
        world.spawn("grazer", x, y, stratum=world.SURFACE,
                    energy=80.0 + 50.0 * world.rng.random())


def on_tick(world):
    wolves_exist = world.count("wolf") > 0
    for grazer in world.entities("grazer"):
        # Hungrier upkeep so the herd is genuinely FOOD-limited at a healthy flora
        # level, and settles below the safety ceiling instead of overgrazing the
        # planet bare and pinning at the cap (soft equilibrium, not a hard wall).
        energy = world.get(grazer, "energy") - 0.15
        x, y, _z = world.pos(grazer)
        f = world.face(grazer)  # cube face (0 on flat/wrap): read the ground we're on

        # in water the engine drains us (we can't swim): scramble uphill to shore
        if world.water_at(x, y, f):
            world.set(grazer, "energy", energy)
            best_dx, best_dy, best_h = 0.0, 0.0, world.height_at(x, y, f)
            for ddx, ddy in ((3.0, 0.0), (-3.0, 0.0), (0.0, 3.0), (0.0, -3.0)):
                h = world.height_at(x + ddx, y + ddy, f)
                if h > best_h:
                    best_dx, best_dy, best_h = ddx, ddy, h
            world.move(grazer, best_dx / 2.0 + world.rng.uniform(-0.2, 0.2),
                       best_dy / 2.0 + world.rng.uniform(-0.2, 0.2))
            continue

        eaten = world.eat_flora(x, y, 0.03, f)
        energy += eaten * 22.0
        world.set(grazer, "energy", min(energy, 200.0))
        # energy <= 0 is handled by the engine death sweep

        threat = world.nearest(grazer, species="wolf", radius=9.0) if wolves_exist else None
        if threat is not None:
            dx, dy = world.direction_to(grazer, threat)  # seam-aware; flee = away
            world.move(grazer, -dx + world.rng.uniform(-0.2, 0.2),
                       -dy + world.rng.uniform(-0.2, 0.2))
            continue

        # sleep cycle: rest at local night (barely move, graze in place). The
        # predator-flee check above runs first, so danger still wakes them.
        if world.daylight(grazer) < -0.15:
            world.move(grazer, world.rng.uniform(-0.2, 0.2), world.rng.uniform(-0.2, 0.2))
            continue

        neighbour = world.nearest(grazer, species="grazer", radius=6.0)
        if neighbour is not None:
            dx, dy = world.direction_to(grazer, neighbour)  # disperse from crowd
            world.move(grazer, -dx * 0.6 + world.rng.uniform(-0.5, 0.5),
                       -dy * 0.6 + world.rng.uniform(-0.5, 0.5))
            continue

        # ROAM with a persistent, slowly-drifting heading so a grazer actually
        # travels and the herd expands across the land, instead of jittering in
        # place. It lingers (slow) on rich pasture and strikes out (fast) once the
        # grass here is grazed down — natural foraging dispersal.
        hd = world.get(grazer, "heading")
        if hd == 0.0:
            hd = world.rng.uniform(0.1, 6.28)
        hd += world.rng.uniform(-0.25, 0.25)
        # look ahead: if we'd wander into water, turn away (avoid drowning)
        if world.water_at(x + math.cos(hd) * 3.0, y + math.sin(hd) * 3.0, f):
            hd += 2.2
        world.set(grazer, "heading", hd)
        roam = 0.9 if world.flora_at(x, y, f) < 0.12 else 0.35
        world.move(grazer, math.cos(hd) * roam, math.sin(hd) * roam)

        # Reproduction takes TIME and happens ONE pregnancy at a time (the
        # gestation prop is a single countdown — a pregnant grazer can't start
        # another). A pregnancy can still yield a LITTER of 1-2 at term. Long
        # gestation + up-front energy cost keeps growth a slow wave, not a boom.
        # There is NO hard population cap: conception is gated by energy, age,
        # local flora and crowding, so the herd settles at the land's carrying
        # capacity (a soft equilibrium), not at a number.
        gestation = world.get(grazer, "gestation")
        if gestation > 0.0:
            world.set(grazer, "gestation", gestation - 1.0)
            if gestation <= 1.0:
                litter = 1 + int(world.rng.integers(0, 2))  # 1 or 2 young
                for _ in range(litter):
                    world.spawn("grazer", x + world.rng.uniform(-2, 2),
                                y + world.rng.uniform(-2, 2),
                                stratum=world.SURFACE, energy=45.0, face=f,
                                parent=grazer)  # inherit speed gene (mutated)
        elif energy > 135.0 and world.get(grazer, "age") > 200:
            # density-dependent conception: crowded or overgrazed ground means no
            # pregnancy — the herd saturates against its resources
            if world.flora_at(x, y, f) > 0.06 and len(world.within(grazer, 5.0, species="grazer")) < 4:
                world.set(grazer, "gestation", 160.0)   # ~1.3 days of pregnancy
                world.set(grazer, "energy", energy - 50.0)
