"""Benchmark protocol runner (docs/architecture.md — "Benchmark Protocol (authoritative)").

Fixture: 256x256 world, seed 424242, weather/flora active, 10,000 entities with the
fixed split: >= 2,000 scalar "fauna" (per-entity Python logic + spatial query each
tick, standing in for plugin on_tick until Epic 2) + ~8,000 bulk-driven entities
(vectorized drift via the bulk idiom). Reports p50/p95 tick-time and ticks/s.

Usage: python scripts/bench_engine.py [--quick]
  --quick: 5 s warmup / 20 s window (dev loop). Default: 30 s / 180 s (protocol).
"""

import sys
import time

import numpy as np

from engine import World, WorldConfig
from engine.entities import SURFACE

SCALAR_N = 2000
BULK_N = 8000
RADIUS = 8.0


def build_world() -> tuple[World, int, int]:
    w = World(WorldConfig(seed=424242, size=256, initial_capacity=16384))
    fauna = w.registry.register("bench_fauna", props=("hunger",))
    swarm = w.registry.register("bench_swarm")
    for _ in range(SCALAR_N):
        w.store.spawn(fauna.id, float(w.rng.uniform(0, 256)), float(w.rng.uniform(0, 256)),
                      0.0, SURFACE, 100.0)
    for _ in range(BULK_N):
        w.store.spawn(swarm.id, float(w.rng.uniform(0, 256)), float(w.rng.uniform(0, 256)),
                      0.0, SURFACE, 100.0)
    return w, fauna.id, swarm.id


def scalar_fauna_tick(w: World, species_id: int) -> None:
    """Plugin-shaped scalar hot loop: neighbor query -> disperse/approach/wander -> move.

    Reads use the spatial cache (tick-start positions = command-buffer read semantics).
    Dispersal below a personal-space radius keeps the population spread out — realistic
    grazing behavior, and it keeps the benchmark honestly dense rather than one blob.
    """
    store = w.store
    spatial = w.spatial
    commands = w.commands
    prng = w.plugin_rng("bench_fauna")
    rows = store.alive_indices(species_id)
    handles = store.handles_of(rows)
    xs, ys = spatial.xs, spatial.ys
    jitter = prng.uniform(-1.0, 1.0, (len(handles), 2)).tolist()
    for n, (row, hnd) in enumerate(zip(rows.tolist(), handles, strict=True)):
        x, y = xs[row], ys[row]
        j = spatial.nearest(store, x, y, RADIUS, SURFACE, species_id=species_id,
                            exclude_row=row)
        if j >= 0:
            dx, dy = xs[j] - x, ys[j] - y
            d2 = dx * dx + dy * dy
            d = d2 ** 0.5 or 1.0
            if d < 3.0:      # personal space: disperse
                commands.move(hnd, -dx / d, -dy / d)
            else:            # approach the herd
                commands.move(hnd, dx / d, dy / d)
        else:
            commands.move(hnd, jitter[n][0], jitter[n][1])


def bulk_swarm_tick(w: World, species_id: int) -> None:
    """Vectorized bulk idiom: whole-species array update, no per-entity Python."""
    store = w.store
    rows = store.alive_indices(species_id)
    n = rows.size
    if n == 0:
        return
    drift = w.plugin_rng("bench_swarm").uniform(-0.5, 0.5, (n, 2)).astype(np.float32)
    store.px[rows] = np.clip(store.px[rows] + drift[:, 0], 0.0, 256.0 - 1e-3)
    store.py[rows] = np.clip(store.py[rows] + drift[:, 1], 0.0, 256.0 - 1e-3)


def main() -> None:
    quick = "--quick" in sys.argv
    warmup_s, window_s = (5.0, 20.0) if quick else (30.0, 180.0)
    w, fauna_id, swarm_id = build_world()
    print(f"protocol: 256^2 seed 424242, {SCALAR_N} scalar fauna + {BULK_N} bulk, "
          f"warmup {warmup_s:.0f}s, window {window_s:.0f}s{' (quick)' if quick else ''}")

    w.tick_hooks.append(lambda world: scalar_fauna_tick(world, fauna_id))
    w.tick_hooks.append(lambda world: bulk_swarm_tick(world, swarm_id))

    def one_tick() -> float:
        t0 = time.perf_counter()
        w.step()
        return time.perf_counter() - t0

    t_end = time.perf_counter() + warmup_s
    while time.perf_counter() < t_end:
        one_tick()

    times: list[float] = []
    t_end = time.perf_counter() + window_s
    while time.perf_counter() < t_end:
        times.append(one_tick())

    arr = np.array(times)
    p50, p95 = np.percentile(arr, [50, 95]) * 1000
    tps = 1.0 / float(arr.mean())
    verdict = "PASS" if tps >= 60 else "FAIL"
    print(f"ticks measured: {len(times)}  p50={p50:.2f} ms  p95={p95:.2f} ms  "
          f"effective {tps:.1f} ticks/s  -> FR4 (>=60 tps): {verdict}")
    print(f"entities alive: {w.store.count}  tick: {w.tick}")


if __name__ == "__main__":
    main()
