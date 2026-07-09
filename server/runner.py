"""EngineRunner: the live sim loop on a dedicated thread.

The tick loop's only streaming obligation is to exist behind `self.lock`;
encoding happens on the streamer side under brief lock acquisitions
(GIL discipline, docs/architecture.md). Control ops are thread-safe.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

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
        # god-mode acts draw from their OWN rng so a divine intervention never
        # perturbs the sim's deterministic rng stream (weather/flora/sweeps)
        self._god_rng = np.random.default_rng(0xC0FFEE)

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

    # -- god mode (direct divine interventions) -------------------------------
    # These mutate the world directly under the lock (like reset/rollback), at a
    # tick boundary — NOT through the plugin command buffer — so they bypass
    # per-plugin quotas and capability checks. They are the operator's hand, not
    # a plugin. Each returns a small result dict for the API to echo.

    def _random_land_point(self, face: int) -> tuple[float, float]:
        land = (self.world.terrain.land_points(face) if self.world.geom is not None
                else self.world.terrain.land_points)
        gx, gy = land[int(self._god_rng.integers(0, len(land)))]
        size = self.world.config.size
        return (min(float(gx) + float(self._god_rng.uniform(0, 1)), size - 1e-3),
                min(float(gy) + float(self._god_rng.uniform(0, 1)), size - 1e-3))

    def god_spawn(self, species: str, count: int) -> dict:
        """Drop `count` members of a live species onto random land, scattered
        across every face. Attributed to the owning plugin so its on_tick drives
        them; caps are ignored (this is god mode)."""
        with self.lock:
            sp = self.world.registry.by_name.get(species)
            if sp is None:
                return {"error": f"unknown species {species!r}"}
            rec = self.host.plugins.get(sp.plugin)
            plugin_id = rec.plugin_id if rec is not None else -1
            stratum = int(sp.strata[0])
            store = self.world.store
            nfaces = 6 if self.world.geom is not None else 1
            n = max(1, min(int(count), 2000))
            spawned = 0
            for _ in range(n):
                face = int(self._god_rng.integers(0, nfaces))
                if (self.world.terrain.land_points(face) if self.world.geom is not None
                        else self.world.terrain.land_points):
                    x, y = self._random_land_point(face)
                    store.spawn(sp.id, x, y, 0.0, stratum, 100.0, plugin_id, face)
                    spawned += 1
        return {"spawned": spawned, "species": species}

    def god_cull(self, species: str) -> dict:
        """Smite an entire species: remove every living member. If nothing
        respawns it, the species goes extinct and its plugin is reaped."""
        with self.lock:
            sp = self.world.registry.by_name.get(species)
            if sp is None:
                return {"error": f"unknown species {species!r}"}
            store = self.world.store
            rows = store.alive_indices(sp.id)
            for h in store.handles_of(rows):
                store.remove(h)
            removed = int(rows.size)
        return {"culled": removed, "species": species}

    def god_set_caps(self, enabled: bool) -> dict:
        """Toggle the engine population ceilings + crowding stress. Off = let
        populations grow until food/predation limits them (experiment mode)."""
        with self.lock:
            self.world.caps_enabled = bool(enabled)
        return {"capsEnabled": self.world.caps_enabled}

    def god_flora(self, mode: str, amount: float = 0.5) -> dict:
        """Bloom (raise flora everywhere on land) or scorch (knock it down).
        Both are transient — the flora field's growth/regrowth dynamics pull it
        back toward equilibrium over the following ticks."""
        amount = max(0.0, min(float(amount), 1.0))
        with self.lock:
            d = self.world.flora.density
            water = self.world.terrain.water_mask
            if mode == "bloom":
                d[:] = np.clip(d + amount, 0.0, 1.0) * (1.0 - water)
            elif mode == "scorch":
                d[:] = d * (1.0 - amount)
            else:
                return {"error": f"unknown flora mode {mode!r}"}
        return {"mode": mode, "amount": amount}

    # -- promotion & rollback (Story 2.4) --------------------------------------

    def promote(self, source: str) -> dict:
        """Pre-promotion snapshot -> hot-load one plugin. Raises PluginInstallError
        on rejection. Thin wrapper over promote_changeset for the single-plugin case."""
        info = self.promote_changeset([source])
        return {"installed": info["installed"][0], "snapshot": info["snapshot"]}

    def promote_changeset(self, sources: list[str]) -> dict:
        """Pre-promotion snapshot -> hot-load a CHANGESET of plugins atomically.

        One snapshot covers the whole set, so a rollback reverts every change
        together. Sources install in order under the lock (raising
        PluginInstallError aborts the rest); the snapshot is written to disk
        OUTSIDE the lock — tens of MB of disk I/O must never stall the tick loop
        (NFR6). The changeset was already installed in this exact order during the
        shadow run, so a live install failure here is highly unlikely.
        """
        with self.lock:
            cap = capture(self.world)
            path = SNAPSHOT_DIR / f"pre-{self.world.epoch}-{self.world.tick}.npz"
            installed = [self.host.install(src).name for src in sources]
        write_capture(cap, path)
        self.last_promotion_snapshot = path
        return {"installed": installed, "snapshot": path.name}

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
                "capsEnabled": w.caps_enabled,
            }
