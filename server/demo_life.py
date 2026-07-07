"""Demo life: engine-side placeholder fauna so the Epic 1 world view is alive.

Replaced by real plugins in Epic 2 — these hooks deliberately use the same
machinery plugins will (tick hooks, plugin RNG streams, command buffer, tick-start
spatial reads) so the perf profile and determinism story match the real thing.
"""

from __future__ import annotations

import numpy as np

from engine import World
from engine.entities import SKY, SURFACE

GRAZER_START = 900
GRAZER_CAP = 2500
BIRD_COUNT = 250


def setup(world: World) -> None:
    grazer = world.registry.register(
        "grazer", plugin="demo", size=1.6, color="#c9a35c", strata=(SURFACE,)
    )
    bird = world.registry.register(
        "bird", plugin="demo", size=1.1, color="#7fd4ff", strata=(SKY,)
    )
    rng = world.plugin_rng("demo_grazer")
    size = float(world.config.size)
    land = np.argwhere(world.terrain.water_mask < 0.5)
    picks = land[rng.integers(0, len(land), GRAZER_START)]
    for gx, gy in picks.tolist():
        world.store.spawn(
            grazer.id,
            min(gx + float(rng.uniform(0, 1)), size - 1e-3),
            min(gy + float(rng.uniform(0, 1)), size - 1e-3),
            0.0, SURFACE, energy=float(rng.uniform(80, 130)),
        )
    brng = world.plugin_rng("demo_bird")
    for _ in range(BIRD_COUNT):
        world.store.spawn(
            bird.id,
            float(brng.uniform(0, size)), float(brng.uniform(0, size)),
            float(brng.uniform(2, 6)), SKY, energy=100.0,
        )
    world.tick_hooks.append(lambda w: _grazer_tick(w, grazer.id))
    world.tick_hooks.append(lambda w: _bird_tick(w, bird.id))


def _grazer_tick(world: World, species_id: int) -> None:
    """Scalar 'plugin-shaped' loop: graze, starve, disperse, reproduce."""
    store = world.store
    spatial = world.spatial
    commands = world.commands
    rng = world.plugin_rng("demo_grazer")
    flora = world.flora.density
    rows = store.alive_indices(species_id)
    handles = store.handles_of(rows)
    xs, ys = spatial.xs, spatial.ys
    energy = store.energy
    count = len(handles)
    jitter = rng.uniform(-0.8, 0.8, (count, 2)).tolist()
    for n, (row, hnd) in enumerate(zip(rows.tolist(), handles, strict=True)):
        x, y = xs[row], ys[row]
        ix, iy = int(x), int(y)
        e = float(energy[row]) - 0.06
        # graze: transfer flora density to energy
        avail = float(flora[ix, iy])
        if avail > 0.05:
            bite = min(avail * 0.25, 0.03)
            flora[ix, iy] = avail - bite
            e += bite * 60.0
        if e <= 0.0:
            commands.remove(hnd)
            continue
        commands.set_energy(hnd, min(e, 200.0))
        # disperse from the nearest neighbour; wander harder on bare ground
        j = spatial.nearest(store, x, y, 6.0, SURFACE, species_id=species_id, exclude_row=row)
        if j >= 0:
            dx, dy = x - xs[j], y - ys[j]
            d = (dx * dx + dy * dy) ** 0.5 or 1.0
            commands.move(hnd, dx / d * 0.6 + jitter[n][0], dy / d * 0.6 + jitter[n][1])
        else:
            speed = 1.6 if avail < 0.1 else 1.0
            commands.move(hnd, jitter[n][0] * speed, jitter[n][1] * speed)
        if e > 150.0 and count < GRAZER_CAP:
            commands.set_energy(hnd, e * 0.5)
            commands.spawn(species_id, x + jitter[n][0], y + jitter[n][1], 0.0,
                           SURFACE, e * 0.5, -1)


def _bird_tick(world: World, species_id: int) -> None:
    """Vectorized bulk-idiom flock: drifting headings, gentle altitude waves."""
    store = world.store
    rng = world.plugin_rng("demo_bird")
    rows = store.alive_indices(species_id)
    n = rows.size
    if n == 0:
        return
    st = world.plugin_stores["demo_bird"]
    heading = st.get("heading", 0.0) + float(rng.normal(0.0, 0.05))
    st["heading"] = heading
    spread = rng.normal(0.0, 0.4, n)
    angles = heading + spread
    size = float(world.config.size)
    store.px[rows] = np.clip(
        store.px[rows] + np.cos(angles).astype(np.float32) * 0.9, 0.0, size - 1e-3
    )
    store.py[rows] = np.clip(
        store.py[rows] + np.sin(angles).astype(np.float32) * 0.9, 0.0, size - 1e-3
    )
    phase = world.tick / 40.0
    store.pz[rows] = (4.0 + 2.0 * np.sin(phase + rows / 17.0)).astype(np.float32)
