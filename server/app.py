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


def create_app(seed: int = 424242) -> FastAPI:
    sources = [(PLUGINS_DIR / name).read_text() for name in BASE_PLUGINS]
    runner = EngineRunner(WorldConfig(seed=seed), plugin_sources=sources)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        runner.start_thread()
        runner.start()
        yield
        runner.shutdown()

    app = FastAPI(title="Genesis v2", lifespan=lifespan)
    app.state.runner = runner

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
