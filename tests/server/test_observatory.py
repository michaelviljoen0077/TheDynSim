"""Epic 4 observatory endpoints: metrics, code lab, entity inspector."""

import struct

import pytest
from fastapi.testclient import TestClient

from server.app import create_app

VOLE = '''
PLUGIN_META = {"name": "test_vole", "contract": 1, "species": ["vole"], "lineage_parent": None}

def setup(world):
    world.register_species("vole")
    world.spawn("vole", 5.0, 5.0)

def on_tick(world):
    pass
'''


@pytest.fixture()
def client():
    with TestClient(create_app(seed=77)) as c:
        yield c


def test_metrics_snapshot_shape(client):
    m = client.get("/api/metrics").json()
    assert isinstance(m["tick"], int)
    assert isinstance(m["populations"], dict)
    assert isinstance(m["shannonDiversity"], (int, float))
    assert isinstance(m["floraDensity"], (int, float))
    assert isinstance(m["deathsByCause"], dict)
    # base ecosystem registers the three example species
    assert set(m["populations"]) >= {"grazer", "wolf", "bird"}


def test_lab_lists_live_plugins_with_source(client):
    plugins = client.get("/api/lab/plugins").json()
    live = {p["name"]: p for p in plugins if p["origin"] == "live"}
    assert {"grazer_herd", "wolf_pack", "sky_flock"} <= set(live)
    for p in live.values():
        assert p["fate"] in ("live", "quarantined")
        assert "PLUGIN_META" in p["source"]
        assert isinstance(p["species"], list)


def test_lab_includes_promoted_candidate_after_install(client):
    client.post("/api/plugins/install", json={"source": VOLE})
    plugins = client.get("/api/lab/plugins").json()
    assert any(p["name"] == "test_vole" and "PLUGIN_META" in p["source"] for p in plugins)


def test_entity_inspector_roundtrip(client):
    # pull a live entity id straight off the wire, then inspect it
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()          # sync
        ws.receive_bytes()         # terrain
        eid = None
        for _ in range(20):
            data = ws.receive_bytes()
            kind, _tick, _epoch, n = struct.unpack_from("<4I", data, 0)
            if kind == 2 and n > 0:
                (eid,) = struct.unpack_from("<I", data, 16)
                break
        assert eid is not None, "no entity frame with entities arrived"

    detail = client.get(f"/api/entity/{eid}").json()
    assert detail["id"] == eid
    assert detail["stratum"] in (0, 1, 2)
    assert isinstance(detail["energy"], (int, float))
    assert isinstance(detail["age"], int)
    assert detail["plugin"] != ""


def test_entity_inspector_stale_id(client):
    bad = client.get("/api/entity/4294901760").json()  # huge index, generation 0
    assert bad.get("error")


def test_interventions_endpoint_ok(client):
    # governor may be unconfigured (no Ollama) — endpoint must still be a list
    assert isinstance(client.get("/api/interventions").json(), list)
