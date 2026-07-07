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
        strata=(world.SURFACE,), props=("maturity",),
    )
    for _ in range(120):
        x, y = world.random_surface_point()
        world.spawn("grazer", x, y, stratum=world.SURFACE,
                    energy=80.0 + 50.0 * world.rng.random())


def on_tick(world):
    for grazer in world.entities("grazer"):
        energy = world.get(grazer, "energy") - 0.06
        x, y, _z = world.pos(grazer)

        # in water: drowning drains fast; scramble uphill toward the shore
        if world.water_at(x, y):
            world.set(grazer, "energy", energy - 1.5)
            best_dx, best_dy, best_h = 0.0, 0.0, world.height_at(x, y)
            for ddx, ddy in ((3.0, 0.0), (-3.0, 0.0), (0.0, 3.0), (0.0, -3.0)):
                h = world.height_at(x + ddx, y + ddy)
                if h > best_h:
                    best_dx, best_dy, best_h = ddx, ddy, h
            world.move(grazer, best_dx / 2.0 + world.rng.uniform(-0.2, 0.2),
                       best_dy / 2.0 + world.rng.uniform(-0.2, 0.2))
            continue

        eaten = world.eat_flora(x, y, 0.03)
        energy += eaten * 60.0
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

        if energy > 150.0:
            world.set(grazer, "energy", energy * 0.5)
            world.spawn("grazer", x + world.rng.uniform(-2, 2),
                        y + world.rng.uniform(-2, 2),
                        stratum=world.SURFACE, energy=energy * 0.5)
