"""Wolf: surface predator. Hunts grazers via engine-mediated attack, starves, reproduces."""

PLUGIN_META = {
    "name": "wolf_pack",
    "contract": 1,
    "species": ["wolf"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "wolf", size=2.2, color="#8a4a4a",
        strata=(world.SURFACE,), props=(),
    )
    for _ in range(10):
        x, y = world.random_surface_point()
        world.spawn("wolf", x, y, stratum=world.SURFACE, energy=140.0)


def on_tick(world):
    for wolf in world.entities("wolf"):
        energy = world.get(wolf, "energy") - 0.22
        x, y, _z = world.pos(wolf)

        prey = world.nearest(wolf, species="grazer", radius=10.0)
        if prey is not None:
            px, py, _pz = world.pos(prey)
            dx, dy = px - x, py - y
            d = (dx * dx + dy * dy) ** 0.5
            if d < 1.8:
                energy += world.attack(prey, 60.0) * 0.8
            else:
                world.move(wolf, dx / d * 1.6, dy / d * 1.6)
        else:
            world.move(wolf, world.rng.uniform(-1.2, 1.2), world.rng.uniform(-1.2, 1.2))

        world.set(wolf, "energy", min(energy, 260.0))

        if energy > 220.0 and world.count("wolf") < 12:
            world.set(wolf, "energy", energy * 0.5)
            world.spawn("wolf", x + world.rng.uniform(-2, 2), y + world.rng.uniform(-2, 2),
                        stratum=world.SURFACE, energy=energy * 0.5)
