"""Story 1.5 integration: REST control + WS binary protocol + GIL-contention gate.

Runs the app in-process via FastAPI's TestClient (no network, no uvicorn),
decodes real frames per docs/protocol.md, and measures streaming-on tick rate.
"""

import struct
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.app import create_app


@pytest.fixture()
def client():
    # flat/wrap single-face world: exercises the protocol-layout assertions below
    app = create_app(seed=99, topology="wrap")
    with TestClient(app) as c:
        yield c


def read_binary(ws) -> bytes:
    while True:
        msg = ws.receive()
        if "bytes" in msg and msg["bytes"] is not None:
            return msg["bytes"]


def read_json(ws, want_t: str) -> dict:
    import json
    while True:
        msg = ws.receive()
        if "text" in msg and msg["text"] is not None:
            data = json.loads(msg["text"])
            if data.get("t") == want_t:
                return data


def test_rest_control(client):
    state = client.get("/api/state").json()
    assert state["running"] is True
    assert client.post("/api/control/pause").json()["running"] is False
    t0 = client.get("/api/state").json()["tick"]
    client.post("/api/control/step")
    time.sleep(0.1)
    assert client.get("/api/state").json()["tick"] == t0 + 1
    assert client.post("/api/control/speed", json={"tps": 30}).json()["targetTps"] == 30
    assert client.post("/api/control/start").json()["running"] is True


def test_ws_sync_terrain_and_entity_frames(client):
    with client.websocket_connect("/ws") as ws:
        sync = read_json(ws, "sync")
        assert sync["protocol"] == 2
        size = sync["size"]
        names = {s["name"] for s in sync["species"]}
        assert {"grazer", "bird"} <= names

        assert sync["protocol"] == 2 and sync["faces"] == 1  # flat world: one face

        terrain = read_binary(ws)
        kind, tick, epoch, n = struct.unpack_from("<4I", terrain, 0)
        (face,) = struct.unpack_from("<I", terrain, 16)
        assert kind == 1 and n == size and face == 0
        assert len(terrain) == 20 + size * size * 4 + size * size  # header + face word + planes

        heights = np.frombuffer(terrain, dtype="<f4", count=size * size, offset=20)
        assert 0.0 <= heights.min() and heights.max() <= 1.0

        entities = read_binary(ws)
        kind, tick, epoch, n = struct.unpack_from("<4I", entities, 0)
        assert kind == 2 and n >= 150  # grazers + wolves + birds (halved initial populations)
        assert len(entities) == 16 + n * (4 + 4 * 4 + 2 + 1 + 1)  # +face u8
        off = 16 + n * 4
        xs = np.frombuffer(entities, dtype="<f4", count=n, offset=off)
        assert 0.0 <= xs.min() and xs.max() < size

        frame = read_json(ws, "frame")
        assert frame["entities"] == n
        assert "temp" in frame["weather"] and "dayFrac" in frame["clock"]


def test_field_frame_arrives(client):
    with client.websocket_connect("/ws") as ws:
        read_json(ws, "sync")
        deadline = time.time() + 5
        while time.time() < deadline:
            data = read_binary(ws)
            kind = struct.unpack_from("<I", data, 0)[0]
            if kind == 3:
                _, tick, epoch, size = struct.unpack_from("<4I", data, 0)
                field_id, face = struct.unpack_from("<2I", data, 16)
                assert field_id in (0, 1, 2) and face == 0
                assert len(data) == 24 + size * size  # header + field_id + face + plane
                return
        pytest.fail("no field frame within 5s")


def test_streaming_gil_gate(client):
    """NFR6 gate: streaming-on tick rate >= 80% of headless-equivalent rate.

    Full protocol measurement lives in scripts/bench_engine.py + nightly; this
    is the fast regression tripwire: tick progress must not collapse when a
    client is attached and consuming frames.
    """
    runner = client.app.state.runner
    client.post("/api/control/speed", json={"tps": 240})  # unconstrained-ish

    def measure() -> tuple[float, float]:
        time.sleep(1.5)
        t0 = runner.world.tick
        time.sleep(3.0)
        headless = (runner.world.tick - t0) / 3.0
        with client.websocket_connect("/ws") as ws:
            read_json(ws, "sync")
            t0 = runner.world.tick
            end = time.time() + 3.0
            while time.time() < end:
                ws.receive()  # keep consuming so the server keeps sending
            streaming = (runner.world.tick - t0) / 3.0
        return headless, streaming

    # two attempts: this is a regression tripwire, not the protocol benchmark —
    # a single window on a loaded CI box can catch a scheduling hiccup
    for _attempt in (1, 2):
        headless_rate, streaming_rate = measure()
        if streaming_rate >= 0.8 * headless_rate:
            return
    raise AssertionError(
        f"streaming {streaming_rate:.1f} tps < 80% of headless {headless_rate:.1f} tps (2 attempts)"
    )
