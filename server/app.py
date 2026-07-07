"""FastAPI front door: REST control + WebSocket world streaming (docs/protocol.md).

One process: engine loop on a dedicated thread, this app on the async loop.
Slow clients get dropped (latest-wins), never block the sim (NFR6).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine import WorldConfig
from engine.plugin_host import PluginInstallError
from server import protocol
from server.runner import EngineRunner

PLUGINS_DIR = Path(__file__).resolve().parent.parent / "plugins_examples"
BASE_PLUGINS = ("grazer.py", "predator.py", "birds.py")

log = logging.getLogger("genesis.server")

STREAM_HZ = 10.0
FIELD_EVERY = 5          # every 5th frame carries one field, round-robin
SEND_TIMEOUT_S = 0.5     # a client slower than this gets dropped

FIELDS = (protocol.FIELD_FLORA, protocol.FIELD_TEMPERATURE, protocol.FIELD_MOISTURE)


class SpeedBody(BaseModel):
    tps: float


class InstallBody(BaseModel):
    source: str


def create_app(seed: int = 424242, world_size: int = 640) -> FastAPI:
    sources = [(PLUGINS_DIR / name).read_text() for name in BASE_PLUGINS]
    runner = EngineRunner(WorldConfig(seed=seed, size=world_size), plugin_sources=sources)

    @contextlib.asynccontextmanager
    async def lifespan(app_: FastAPI):
        runner.start_thread()
        runner.start()

        async def cadence_loop() -> None:
            """Automatic evolution cadence (Story 3.6): fire a cycle when due."""
            while True:
                await asyncio.sleep(5.0)
                orch = getattr(app_.state, "orchestrator", None)
                if orch is not None and runner.running and orch.due():
                    log.info("cadence: triggering evolution cycle at tick %d",
                             runner.world.tick)
                    orch.run_cycle_async()

        cadence = asyncio.create_task(cadence_loop())
        yield
        cadence.cancel()
        runner.shutdown()

    app = FastAPI(title="Genesis v2", lifespan=lifespan)
    app.state.runner = runner

    # governor wiring: live Ollama if reachable, else endpoints report unconfigured
    from governor.llm import OllamaProvider
    from governor.notebook import Notebook
    from governor.orchestrator import Orchestrator

    provider = OllamaProvider()
    if provider.available():
        notebook = Notebook(Path(__file__).resolve().parent.parent / "data" / "run.db")
        if notebook.resume_latest_run() is None:
            notebook.start_run(seed, runner.config.to_json())
        app.state.orchestrator = Orchestrator(runner, notebook, provider)
        log.info("governor configured with %s", provider.name)
    else:
        app.state.orchestrator = None
        log.warning("Ollama unreachable or model missing — governor disabled for this run")

    # -- REST control -------------------------------------------------------

    @app.get("/api/state")
    def get_state() -> dict:
        return runner.state()

    @app.post("/api/control/start")
    def control_start() -> dict:
        runner.start()
        return runner.state()

    @app.post("/api/control/pause")
    def control_pause() -> dict:
        runner.pause()
        return runner.state()

    @app.post("/api/control/step")
    def control_step() -> dict:
        runner.step()
        return runner.state()

    @app.post("/api/control/reset")
    def control_reset() -> dict:
        runner.reset()
        return runner.state()

    @app.post("/api/control/speed")
    def control_speed(body: SpeedBody) -> dict:
        runner.set_tps(body.tps)
        return runner.state()

    # -- plugins & rollback (Story 2.4) ---------------------------------------

    @app.get("/api/plugins")
    def get_plugins() -> list[dict]:
        return runner.host.state()

    @app.post("/api/plugins/install")
    def install_plugin(body: InstallBody) -> dict:
        try:
            return runner.promote(body.source)
        except PluginInstallError as e:
            return {"error": "rejected", "reasons": e.reasons}

    @app.post("/api/control/rollback")
    def control_rollback() -> dict:
        try:
            return runner.rollback()
        except FileNotFoundError as e:
            return {"error": str(e)}

    # -- governor (Epic 3) ------------------------------------------------------

    @app.post("/api/governor/cycle")
    def trigger_cycle() -> dict:
        orch = getattr(app.state, "orchestrator", None)
        if orch is None:
            return {"error": "governor not configured (no provider available)"}
        started = orch.run_cycle_async()
        return {"started": started, "status": orch.status.__dict__}

    @app.get("/api/governor/status")
    def governor_status() -> dict:
        orch = getattr(app.state, "orchestrator", None)
        if orch is None:
            return {"configured": False}
        return {"configured": True, "provider": orch.provider.name, **orch.status.__dict__}

    @app.get("/api/cycles")
    def get_cycles() -> list[dict]:
        orch = getattr(app.state, "orchestrator", None)
        return orch.notebook.cycles() if orch else []

    @app.get("/api/cycles/{cycle_id}/candidates")
    def get_candidates(cycle_id: str) -> list[dict]:
        orch = getattr(app.state, "orchestrator", None)
        return orch.notebook.candidates_for(cycle_id) if orch else []

    # -- observatory: metrics, lab & inspection (Epic 4) ----------------------

    @app.get("/api/metrics")
    def get_metrics() -> dict:
        """Live ecosystem snapshot for the charts panel (Story 4.1).

        No time-series is stored server-side for the live run; the client polls
        this and accumulates its own rolling window.
        """
        from engine.reporter import build_report
        with runner.lock:
            report = build_report(runner.world)
        deaths: dict[str, int] = {}
        for by_cause in report["deaths_by_cause"].values():
            for cause, n in by_cause.items():
                deaths[cause] = deaths.get(cause, 0) + int(n)
        return {
            "tick": report["tick"],
            "epoch": report["epoch"],
            "populations": {name: p["total"] for name, p in report["populations"].items()},
            "shannonDiversity": report["shannon_diversity"],
            "floraDensity": report["flora"]["mean_density"],
            "deathsByCause": deaths,
        }

    @app.get("/api/interventions")
    def get_interventions() -> list[dict]:
        orch = getattr(app.state, "orchestrator", None)
        return orch.notebook.interventions() if orch else []

    @app.get("/api/lab/plugins")
    def lab_plugins() -> list[dict]:
        """Every plugin the run has seen — live/quarantined + all candidates (Story 4.3)."""
        out: list[dict] = []
        with runner.lock:
            for name in runner.host.order:
                r = runner.host.plugins[name]
                out.append({
                    "key": f"live:{name}",
                    "name": name,
                    "source": r.source,
                    "fate": r.status,  # live | quarantined
                    "species": r.meta.get("species", []),
                    "lineageParent": r.meta.get("lineage_parent"),
                    "origin": "live",
                    "fitness": None,
                    "candidateId": None,
                })
        orch = getattr(app.state, "orchestrator", None)
        if orch is not None:
            for c in orch.notebook.all_candidates():
                meta = c["meta"]
                out.append({
                    "key": f"cand:{c['id']}",
                    "name": meta.get("name") or c["label"],
                    "source": c["source"],
                    "fate": c["fate"],
                    "species": meta.get("species", []),
                    "lineageParent": meta.get("lineage_parent"),
                    "origin": "candidate",
                    "fitness": c["fitness"],
                    "candidateId": c["id"],
                })
        return out

    @app.get("/api/entity/{eid}")
    def get_entity(eid: int) -> dict:
        """Inspector detail for one entity (Story 4.4). eid = (index << 16) | generation."""
        index = eid >> 16
        generation = eid & 0xFFFF
        with runner.lock:
            store = runner.world.store
            if index < 0 or index >= store.alive.size or not bool(store.alive[index]) \
                    or int(store.generation[index]) != generation:
                return {"error": "not found or stale"}
            sid = int(store.species_id[index])
            species = next((s for s in runner.world.registry.by_id if s.id == sid), None)
            return {
                "id": eid,
                "species": species.name if species else f"#{sid}",
                "speciesId": sid,
                "plugin": species.plugin if species else "",
                "energy": round(float(store.energy[index]), 2),
                "age": int(store.age[index]),
                "x": round(float(store.px[index]), 2),
                "y": round(float(store.py[index]), 2),
                "z": round(float(store.pz[index]), 2),
                "stratum": int(store.stratum[index]),
            }

    # -- WebSocket stream ----------------------------------------------------

    @app.websocket("/ws")
    async def ws_stream(ws: WebSocket) -> None:
        await ws.accept()
        interval = 1.0 / STREAM_HZ
        frame_no = 0
        try:
            with runner.lock:
                sync = protocol.sync_message(runner.world)
                terrain = protocol.encode_terrain(runner.world)
            await ws.send_json(sync)
            await ws.send_bytes(terrain)
            species_count = len(sync["species"])
            while True:
                loop_t0 = asyncio.get_event_loop().time()
                resync = None
                with runner.lock:  # encode fast, never await under the lock
                    world = runner.world
                    entities = protocol.encode_entities(world)
                    frame = protocol.frame_message(world, runner.measured_tps)
                    field_frame = None
                    if frame_no % FIELD_EVERY == 0:
                        field_id = FIELDS[(frame_no // FIELD_EVERY) % len(FIELDS)]
                        field_frame = protocol.encode_field(world, field_id)
                    if len(world.registry.by_id) != species_count:
                        species_count = len(world.registry.by_id)
                        resync = protocol.sync_message(world)
                async with asyncio.timeout(SEND_TIMEOUT_S):
                    if resync is not None:
                        await ws.send_json(resync)
                    await ws.send_bytes(entities)
                    await ws.send_json(frame)
                    if field_frame is not None:
                        await ws.send_bytes(field_frame)
                frame_no += 1
                elapsed = asyncio.get_event_loop().time() - loop_t0
                await asyncio.sleep(max(0.0, interval - elapsed))
        except (WebSocketDisconnect, TimeoutError):
            with contextlib.suppress(Exception):
                await ws.close()
            log.info("client disconnected (or too slow — dropped)")

    # -- static frontend (web/dist) -------------------------------------------

    dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(dist / "index.html")

    return app


app = create_app()
