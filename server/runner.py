"""EngineRunner: the live sim loop on a dedicated thread.

The tick loop's only streaming obligation is to exist behind `self.lock`;
encoding happens on the streamer side under brief lock acquisitions
(GIL discipline, docs/architecture.md). Control ops are thread-safe.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from engine import World, WorldConfig


class EngineRunner:
    def __init__(self, config: WorldConfig, setup: Callable[[World], None] | None = None) -> None:
        self.config = config
        self._setup = setup
        self.lock = threading.Lock()
        self.world = self._make_world()
        self.running = False
        self.target_tps = 60.0
        self.measured_tps = 0.0
        self._stop = threading.Event()
        self._step_once = threading.Event()
        self._thread: threading.Thread | None = None

    def _make_world(self) -> World:
        world = World(self.config)
        if self._setup is not None:
            self._setup(world)
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

    def state(self) -> dict:
        w = self.world
        return {
            "running": self.running,
            "tick": w.tick,
            "epoch": w.epoch,
            "tps": round(self.measured_tps, 1),
            "entities": w.store.count,
            "targetTps": self.target_tps,
        }
