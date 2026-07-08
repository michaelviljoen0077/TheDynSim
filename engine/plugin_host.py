"""PluginHost: loads validated plugins, runs them in error boundaries, quarantines.

Structural refusals (FR7): install() re-validates every source — there is no
code path by which unvalidated source executes. Plugins run in a namespace with
a restricted builtins set (belt-and-braces under the accidental-damage threat
model; the validator is the primary gate).

The live plugin set lives in world.plugin_manifest (snapshot-included), so
shadow forks and rollback restore world + plugins through the one snapshot
mechanism. Restoring rebinds code WITHOUT re-running setup — entity state is
already in the snapshot.
"""

from __future__ import annotations

import math
import time
import typing
from dataclasses import dataclass, field

from engine.core import World
from engine.validator import validate_plugin
from engine.world_api import PluginError, WorldAPI

ERROR_QUARANTINE_THRESHOLD = 5
SLOW_TICK_BUDGET_S = 0.25       # single on_tick call over this counts a slow strike
SLOW_STRIKE_THRESHOLD = 8       # consecutive-ish strikes before quarantine
REAP_EVERY = 500               # ticks between extinction/cleanup sweeps
EXTINCTION_GRACE = 300         # a plugin must live this long before it can be declared extinct
PRUNE_AGE = 5000               # dead/extinct plugins older than this are removed from the manifest

SAFE_BUILTINS = {
    name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    for name in (
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float",
        "int", "isinstance", "len", "list", "map", "max", "min", "range", "reversed",
        "round", "sorted", "str", "sum", "tuple", "zip", "ValueError", "TypeError",
        "KeyError", "IndexError", "ZeroDivisionError", "Exception", "StopIteration", "True",
        "False", "None",
    )
    if (isinstance(__builtins__, dict) and name in __builtins__)
    or (not isinstance(__builtins__, dict) and hasattr(__builtins__, name))
}

_IMPORTABLE = {"math": math, "typing": typing}


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    """The only import path inside a plugin namespace — mirrors the validator allowlist."""
    module = _IMPORTABLE.get(name.split(".")[0])
    if module is None or level != 0:
        raise ImportError(f"plugin sandbox: import of {name!r} is not allowed")
    return module


SAFE_BUILTINS["__import__"] = _restricted_import


@dataclass
class PluginRecord:
    name: str
    plugin_id: int
    source: str
    meta: dict
    api: WorldAPI
    setup_fn: typing.Callable
    on_tick_fn: typing.Callable
    status: str = "live"                # live | quarantined | retired | extinct
    error_count: int = 0
    slow_strikes: int = 0
    last_error: str = ""
    tick_time_ema: float = 0.0
    promoted_tick: int = 0
    ever_populated: bool = False        # became True once its species had entities
    events: list[dict] = field(default_factory=list)


class PluginInstallError(Exception):
    def __init__(self, reasons: list[dict]) -> None:
        super().__init__(f"plugin failed validation/setup: {reasons}")
        self.reasons = reasons


class PluginHost:
    def __init__(self, world: World) -> None:
        self.world = world
        self.plugins: dict[str, PluginRecord] = {}
        self.order: list[str] = []      # deterministic execution order = install order
        world.tick_hooks.append(self._tick_all)

    # -- install / rebind -------------------------------------------------------

    def install(self, source: str, run_setup: bool = True) -> PluginRecord:
        """Validate -> load -> (setup) -> record in manifest. The only way in."""
        result = validate_plugin(source)
        if not result.ok:
            raise PluginInstallError([v.as_dict() for v in result.errors])
        meta = result.meta or {}
        name = meta["name"]
        if name in self.plugins:
            raise PluginInstallError([{"code": "duplicate-plugin", "message": f"plugin {name!r} already installed", "line": 0}])

        # lineage replacement (refinement, not addition): a candidate whose
        # lineage_parent names an installed plugin REPLACES it — the parent is
        # retired and the child adopts its species (entities live on, prop
        # layout preserved). This is how the governor reworks existing systems.
        replacing: PluginRecord | None = None
        adoptable: set[str] = set()
        if run_setup and meta.get("lineage_parent"):
            parent = self.plugins.get(meta["lineage_parent"])
            if parent is not None and parent.status in ("live", "quarantined"):
                replacing = parent
                adoptable = set(parent.meta.get("species", [])) & set(meta["species"])

        namespace = {"__builtins__": dict(SAFE_BUILTINS), "math": math, "typing": typing}
        code = compile(source, f"<plugin:{name}>", "exec")
        try:
            exec(code, namespace)  # noqa: S102 — source passed the AST gate above
        except Exception as e:  # noqa: BLE001 — module-exec is a boundary too
            raise PluginInstallError(
                [{"code": "exec-error", "message": f"{type(e).__name__}: {e}", "line": 0}]
            ) from e
        for fn in ("setup", "on_tick"):
            if not callable(namespace.get(fn)):
                raise PluginInstallError(
                    [{"code": "contract-missing", "message": f"{fn} is not callable after exec", "line": 0}]
                )

        plugin_id = len(self.order)
        api = WorldAPI(self.world, name, plugin_id, list(meta["species"]),
                       adoptable_species=adoptable)
        record = PluginRecord(
            name=name, plugin_id=plugin_id, source=source, meta=meta, api=api,
            setup_fn=namespace["setup"], on_tick_fn=namespace["on_tick"],
            promoted_tick=self.world.tick,
        )
        if run_setup:
            api.on_tick_begin()
            try:
                record.setup_fn(api)
            except Exception as e:  # noqa: BLE001 — error boundary, recorded
                raise PluginInstallError(
                    [{"code": "setup-error", "message": f"{type(e).__name__}: {e}", "line": 0}]
                ) from e
            # setup runs at promotion time, outside a tick: apply its buffered
            # effects now so the plugin's initial population exists atomically
            self.world.commands.apply(self.world.store, float(self.world.config.size),
                                      flora=self.world.flora.density,
                                      speeds=self.world.registry.speeds_array(),
                                      water=self.world.terrain.water_mask,
                                      swim_speeds=self.world.registry.swim_speeds_array(),
                                      wrap=self.world.config.wrap, geom=self.world.geom)
            # mark whether setup established a population, so a species that lives
            # and dies entirely between reap sweeps is still seen as having existed
            record.ever_populated = any(
                int(self.world.store.alive_indices(sp.id).size) > 0
                for s in meta.get("species", [])
                if (sp := self.world.registry.by_name.get(s)) is not None
            )
            if replacing is not None:
                replacing.status = "retired"
                replacing.events.append(
                    {"tick": self.world.tick, "retired": f"replaced by {name}"})
        self.plugins[name] = record
        self.order.append(name)
        self._sync_manifest()
        return record

    @classmethod
    def rebind(cls, world: World) -> PluginHost:
        """Reconstruct a host from world.plugin_manifest after snapshot restore.

        Code is reloaded and hooks rebound; setup is NOT re-run (state is in the
        snapshot). Quarantined plugins stay quarantined.
        """
        host = cls(world)
        manifest = list(world.plugin_manifest)
        for entry in manifest:
            record = host.install(entry["source"], run_setup=False)
            record.status = entry.get("status", "live")
            record.error_count = entry.get("error_count", 0)
            record.promoted_tick = entry.get("promoted_tick", 0)
            record.ever_populated = entry.get("ever_populated", False)
        host._sync_manifest()
        return host

    def _sync_manifest(self) -> None:
        self.world.plugin_manifest = [
            {
                "name": r.name,
                "source": r.source,
                "meta": r.meta,
                "status": r.status,
                "error_count": r.error_count,
                "promoted_tick": r.promoted_tick,
                "ever_populated": r.ever_populated,
            }
            for name in self.order
            for r in (self.plugins[name],)
        ]

    # -- extinction & cleanup -----------------------------------------------------

    def reap(self) -> None:
        """Retire spent plugins and record extinctions (called on a cadence).

        A live plugin whose species once had entities but are now all gone is
        marked `extinct` (its species are moved to the world extinction ledger).
        Long-dead retired/quarantined/extinct plugins with no live entities are
        pruned from the manifest so the live plugin set stays lean over a long run.
        """
        store = self.world.store
        reg = self.world.registry

        def alive_of(rec: PluginRecord) -> int:
            return sum(
                int(store.alive_indices(sp.id).size)
                for s in rec.meta.get("species", [])
                if (sp := reg.by_name.get(s)) is not None
            )

        for name in self.order:
            r = self.plugins[name]
            if r.status != "live":
                continue
            n = alive_of(r)
            if n > 0:
                r.ever_populated = True
            elif r.ever_populated and self.world.tick - r.promoted_tick > EXTINCTION_GRACE:
                r.status = "extinct"
                r.events.append({"tick": self.world.tick, "extinct": True})
                for s in r.meta.get("species", []):
                    self.world.record_extinction(s, name)

        # prune plugins that are done: not live, no entities, dead a while
        prunable = [
            name for name in self.order
            if self.plugins[name].status in ("retired", "quarantined", "extinct")
            and alive_of(self.plugins[name]) == 0
            and self.world.tick - self.plugins[name].promoted_tick > PRUNE_AGE
        ]
        for name in prunable:
            del self.plugins[name]
            self.order.remove(name)
        self._sync_manifest()

    # -- runtime ------------------------------------------------------------------

    def _tick_all(self, world: World) -> None:
        if world.tick % REAP_EVERY == 0 and world.tick > 0:
            self.reap()
        for name in self.order:
            record = self.plugins[name]
            if record.status != "live":
                continue
            record.api.on_tick_begin()
            t0 = time.perf_counter()
            try:
                record.on_tick_fn(record.api)
            except PluginError as e:
                self._record_error(record, f"{e.code}: {e}")
            except Exception as e:  # noqa: BLE001 — error boundary, recorded
                self._record_error(record, f"{type(e).__name__}: {e}")
            dt = time.perf_counter() - t0
            record.tick_time_ema = dt if record.tick_time_ema == 0.0 \
                else record.tick_time_ema * 0.95 + dt * 0.05
            # slow-strike containment: a live plugin can't be preempted mid-call
            # (in-process Python), but SUSTAINED over-budget ticks get it
            # quarantined before it drags the whole sim down
            if dt > SLOW_TICK_BUDGET_S:
                record.slow_strikes += 1
                if record.slow_strikes >= SLOW_STRIKE_THRESHOLD and record.status == "live":
                    self.quarantine(
                        record.name,
                        f"slow-tick threshold: {record.slow_strikes} ticks over "
                        f"{SLOW_TICK_BUDGET_S * 1000:.0f} ms (last {dt * 1000:.0f} ms)",
                    )
            else:
                record.slow_strikes = max(0, record.slow_strikes - 1)

    def _record_error(self, record: PluginRecord, message: str) -> None:
        record.error_count += 1
        record.last_error = message
        record.events.append({"tick": self.world.tick, "error": message})
        if record.error_count >= ERROR_QUARANTINE_THRESHOLD and record.status == "live":
            self.quarantine(record.name, f"error threshold reached: {message}")
        self._sync_manifest()

    def quarantine(self, name: str, reason: str) -> None:
        record = self.plugins[name]
        record.status = "quarantined"
        record.events.append({"tick": self.world.tick, "quarantined": reason})
        self._sync_manifest()

    def state(self) -> list[dict]:
        return [
            {
                "name": r.name,
                "status": r.status,
                "species": r.meta.get("species", []),
                "errors": r.error_count,
                "lastError": r.last_error,
                "tickMsEma": round(r.tick_time_ema * 1000, 3),
                "spawnDrops": r.api.spawn_drops,
            }
            for name in self.order
            for r in (self.plugins[name],)
        ]
