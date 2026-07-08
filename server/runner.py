"""EngineRunner: the live sim loop on a dedicated thread.

The tick loop's only streaming obligation is to exist behind `self.lock`;
encoding happens on the streamer side under brief lock acquisitions
(GIL discipline, docs/architecture.md). Control ops are thread-safe.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from engine import World, WorldConfig, load_snapshot
from engine.plugin_host import PluginHost
from engine.snapshot import capture, write_capture

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots" / "live"


class EngineRunner:
    def __init__(self, config: WorldConfig, plugin_sources: list[str] | None = None) -> None:
        self.config = config
        self._initial_sources = plugin_sources or []
        self.lock = threading.Lock()
        self.host: PluginHost
        self.world = self._make_world()
        self.running = False
        self.target_tps = 60.0
        self.measured_tps = 0.0
        self.last_promotion_snapshot: Path | None = None
        self._stop = threading.Event()
        self._step_once = threading.Event()
        self._thread: threading.Thread | None = None

    def _make_world(self) -> World:
        world = World(self.config)
        self.host = PluginHost(world)
        for source in self._initial_sources:
            self.host.install(source)
        return world

    # -- lifecycle -----------------------------------------------------------

    def start_thread(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="engine-loop", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        ema = 0.0
        next_deadline = time.perf_counter()
        while not self._stop.is_set():
            if not self.running:
                if self._step_once.is_set():
                    self._step_once.clear()
                    with self.lock:
                        self.world.step()
                time.sleep(0.02)
                next_deadline = time.perf_counter()
                continue
            t0 = time.perf_counter()
            with self.lock:
                self.world.step()
            dt = time.perf_counter() - t0
            ema = dt if ema == 0.0 else ema * 0.95 + dt * 0.05
            self.measured_tps = 1.0 / ema if ema > 0 else 0.0
            next_deadline += 1.0 / self.target_tps
            now = time.perf_counter()
            if next_deadline < now - 0.5:  # fell far behind: resync instead of sprinting
                next_deadline = now
            else:
                sleep_for = next_deadline - now
                if sleep_for > 0:
                    time.sleep(sleep_for)

    # -- control -------------------------------------------------------------

    def start(self) -> None:
        self.running = True

    def pause(self) -> None:
        self.running = False

    def step(self) -> None:
        if not self.running:
            self._step_once.set()

    def set_tps(self, tps: float) -> None:
        self.target_tps = max(1.0, min(240.0, float(tps)))

    def reset(self) -> None:
        with self.lock:
            self.world = self._make_world()
            # a fresh run must not roll back into the discarded one
            self.last_promotion_snapshot = None

    # -- promotion & rollback (Story 2.4) --------------------------------------

    def promote(self, source: str) -> dict:
        """Pre-promotion snapshot -> hot-load. Raises PluginInstallError on rejection.

        The world state is *captured* (in-memory copy) under the lock, then the
        install runs under the lock, then the snapshot is written to disk OUTSIDE
        the lock — tens of MB of disk I/O must never stall the tick loop (NFR6).
        """
        with self.lock:
            cap = capture(self.world)
            path = SNAPSHOT_DIR / f"pre-{self.world.epoch}-{self.world.tick}.npz"
            record = self.host.install(source)  # raises PluginInstallError -> nothing written
        write_capture(cap, path)
        self.last_promotion_snapshot = path
        return {"installed": record.name, "snapshot": path.name}

    def rollback(self) -> dict:
        """Restore the pre-promotion snapshot (world + plugin set), bump epoch (NFR9)."""
        if self.last_promotion_snapshot is None or not self.last_promotion_snapshot.exists():
            raise FileNotFoundError("no promotion snapshot available to roll back to")
        t0 = time.perf_counter()
        with self.lock:
            epoch = self.world.epoch + 1
            world = load_snapshot(self.last_promotion_snapshot)
            world.epoch = epoch
            self.host = PluginHost.rebind(world)
            self.world = world
        return {
            "restoredTick": self.world.tick,
            "epoch": self.world.epoch,
            "seconds": round(time.perf_counter() - t0, 3),
        }

    def state(self) -> dict:
        with self.lock:  # consistent read: reset/rollback swap self.world, step mutates it
            w = self.world
            return {
                "running": self.running,
                "tick": w.tick,
                "epoch": w.epoch,
                "tps": round(self.measured_tps, 1),
                "entities": w.store.count,
                "targetTps": self.target_tps,
            }
