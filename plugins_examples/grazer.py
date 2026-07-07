"""Grazer: surface herbivore. Eats flora, wanders, disperses, starves, reproduces."""

PLUGIN_META = {
    "name": "grazer_herd",
    "contract": 1,
    "species": ["grazer"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "grazer", size=1.6, color="#c9a35c", speed=1.8,
        strata=(world.SURFACE,), props=("gestation",),
    )
    for _ in range(100):
        x, y = world.random_surface_point()
        world.spawn("grazer", x, y, stratum=world.SURFACE,
                    energy=80.0 + 50.0 * world.rng.random())


def on_tick(world):
    population = world.count("grazer")
    for grazer in world.entities("grazer"):
        energy = world.get(grazer, "energy") - 0.08
        x, y, _z = world.pos(grazer)

        # in water the engine drains us (we can't swim): scramble uphill to shore
        if world.water_at(x, y):
            world.set(grazer, "energy", energy)
            best_dx, best_dy, best_h = 0.0, 0.0, world.height_at(x, y)
            for ddx, ddy in ((3.0, 0.0), (-3.0, 0.0), (0.0, 3.0), (0.0, -3.0)):
                h = world.height_at(x + ddx, y + ddy)
                if h > best_h:
                    best_dx, best_dy, best_h = ddx, ddy, h
            world.move(grazer, best_dx / 2.0 + world.rng.uniform(-0.2, 0.2),
                       best_dy / 2.0 + world.rng.uniform(-0.2, 0.2))
            continue

        eaten = world.eat_flora(x, y, 0.03)
        energy += eaten * 32.0
        world.set(grazer, "energy", min(energy, 200.0))
        # energy <= 0 is handled by the engine death sweep

        threat = world.nearest(grazer, species="wolf", radius=8.0)
        if threat is not None:
            tx, ty, _tz = world.pos(threat)
            dx, dy = x - tx, y - ty
            d = (dx * dx + dy * dy) ** 0.5 or 1.0
            world.move(grazer, dx / d * 1.7 + world.rng.uniform(-0.3, 0.3),
                       dy / d * 1.7 + world.rng.uniform(-0.3, 0.3))
            continue

        neighbour = world.nearest(grazer, species="grazer", radius=6.0)
        if neighbour is not None:
            nx, ny, _nz = world.pos(neighbour)
            dx, dy = x - nx, y - ny
            d = (dx * dx + dy * dy) ** 0.5
            if d > 0.0:
                world.move(grazer, dx / d * 0.6 + world.rng.uniform(-0.6, 0.6),
                           dy / d * 0.6 + world.rng.uniform(-0.6, 0.6))
                continue
        speed = 1.6 if world.flora_at(x, y) < 0.1 else 0.9
        world.move(grazer, world.rng.uniform(-1, 1) * speed,
                   world.rng.uniform(-1, 1) * speed)

        # Reproduction takes TIME and happens ONE pregnancy at a time (the
        # gestation prop is a single countdown — a pregnant grazer can't start
        # another). A pregnancy can still yield a LITTER of 1-2 at term. Long
        # gestation + up-front energy cost keeps growth a slow wave, not a boom.
        gestation = world.get(grazer, "gestation")
        if gestation > 0.0:
            world.set(grazer, "gestation", gestation - 1.0)
            if gestation <= 1.0:
                litter = 1 + int(world.rng.integers(0, 2))  # 1 or 2 young
                for _ in range(litter):
                    if population >= 1500:
                        break
                    population += 1
                    world.spawn("grazer", x + world.rng.uniform(-2, 2),
                                y + world.rng.uniform(-2, 2),
                                stratum=world.SURFACE, energy=45.0)
        elif energy > 165.0 and world.get(grazer, "age") > 200 and population < 1500:
            world.set(grazer, "gestation", 160.0)   # ~1.3 days of pregnancy
            world.set(grazer, "energy", energy - 55.0)
