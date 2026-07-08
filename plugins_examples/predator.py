"""Wolf: surface predator. Hunts grazers via engine-mediated attack, starves, reproduces."""

PLUGIN_META = {
    "name": "wolf_pack",
    "contract": 1,
    "species": ["wolf"],
    "lineage_parent": None,
}


def setup(world):
    world.register_species(
        "wolf", size=1.4, color="#8a4a4a", speed=1.9, swim_speed=0.9, lifespan=6000,
        strata=(world.SURFACE,), props=("gestation",),
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

        # wide scent radius so a pack reliably finds prey on its face; when a face
        # empties, roam in long strides to migrate toward prey on neighbouring faces
        prey = world.nearest(wolf, species="grazer", radius=45.0)
        if prey is not None:
            px, py, _pz = world.pos(prey)
            dx, dy = world.wrap_delta(x, px), world.wrap_delta(y, py)  # pursue, seam-aware
            d = (dx * dx + dy * dy) ** 0.5
            if d < 1.8:
                energy += world.attack(prey, 60.0) * 0.8
            else:
                world.move(wolf, dx / d * 1.7, dy / d * 1.7)
        else:
            world.move(wolf, world.rng.uniform(-2.4, 2.4), world.rng.uniform(-2.4, 2.4))

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
