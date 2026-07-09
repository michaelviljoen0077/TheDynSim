"""Profile a representative tick to find the hot paths driving the perf roadmap.

Builds the base ecosystem on a cube, settles it, then cProfiles a run of live
ticks and prints the hottest functions (cumulative + total time). Use it to
decide where the next perf work pays off (plugin loops vs spatial vs sweeps vs
fields) and to check a change actually moved the number.

Usage:
    python scripts/profile_tick.py [size=192] [settle=800] [ticks=500]

Run it on an IDLE machine — under other CPU load the numbers are noisy.
"""

import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

from engine import World, WorldConfig
from engine.plugin_host import PluginHost

PLUGINS = Path(__file__).resolve().parent.parent / "plugins_examples"
BASE = ("grazer.py", "predator.py", "birds.py", "fish.py", "shark.py", "raptor.py")


def build(size: int, settle: int) -> World:
    world = World(WorldConfig(seed=42, size=size, topology="cube",
                              initial_capacity=1 << 16, field_step_every=4))
    host = PluginHost(world)
    for name in BASE:
        host.install((PLUGINS / name).read_text())
    for _ in range(settle):
        world.step()
    return world


def main() -> None:
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 192
    settle = int(sys.argv[2]) if len(sys.argv) > 2 else 800
    ticks = int(sys.argv[3]) if len(sys.argv) > 3 else 500

    print(f"building + settling ({size} cube, {settle} ticks)…")
    world = build(size, settle)
    print(f"  entities={world.store.count}  "
          + "  ".join(f"{s.name}={world.store.alive_indices(s.id).size}"
                      for s in world.registry.by_id))

    # wall-clock ms/tick (no profiler overhead)
    t0 = time.perf_counter()
    for _ in range(ticks):
        world.step()
    print(f"\nwall clock: {(time.perf_counter() - t0) / ticks * 1000:.2f} ms/tick "
          f"over {ticks} ticks\n")

    # cProfile the same workload for the hot-function breakdown
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(ticks):
        world.step()
    pr.disable()
    s = io.StringIO()
    stats = pstats.Stats(pr, stream=s).sort_stats("tottime")
    stats.print_stats(25)
    print(s.getvalue())


if __name__ == "__main__":
    main()
