"""Story 2.4 integration: promote -> quarantine -> rollback, all via the API."""

import time

import pytest
from fastapi.testclient import TestClient

from server.app import create_app

BAD_AT_RUNTIME = '''
PLUGIN_META = {"name": "saboteur", "contract": 1, "species": ["imp"], "lineage_parent": None}

def setup(world):
    world.register_species("imp")
    world.spawn("imp", 5.0, 5.0)

def on_tick(world):
    raise ValueError("latent failure")
'''

STATIC_BAD = "import os\nPLUGIN_META = {}\n"


@pytest.fixture()
def client():
    with TestClient(create_app(seed=31)) as c:
        yield c


def test_static_reject_no_snapshot_no_change(client):
    plugins0 = {p["name"] for p in client.get("/api/plugins").json()}
    r = client.post("/api/plugins/install", json={"source": STATIC_BAD}).json()
    assert r["error"] == "rejected"
    assert any(v["code"] == "banned-import" for v in r["reasons"])
    assert {p["name"] for p in client.get("/api/plugins").json()} == plugins0


def test_promote_quarantine_rollback(client):
    base = {p["name"] for p in client.get("/api/plugins").json()}
    assert base == {"grazer_herd", "wolf_pack", "sky_flock"}

    r = client.post("/api/plugins/install", json={"source": BAD_AT_RUNTIME}).json()
    assert r.get("installed") == "saboteur"

    deadline = time.time() + 10
    while time.time() < deadline:
        plugins = {p["name"]: p for p in client.get("/api/plugins").json()}
        if plugins["saboteur"]["status"] == "quarantined":
            break
        time.sleep(0.2)
    else:
        pytest.fail("saboteur was never quarantined")
    assert "latent failure" in plugins["saboteur"]["lastError"]

    t0 = time.perf_counter()
    rb = client.post("/api/control/rollback").json()
    elapsed = time.perf_counter() - t0
    assert rb["epoch"] == 1
    assert elapsed < 5.0, f"rollback took {elapsed:.2f}s (NFR9 is < 5s)"

    after = {p["name"] for p in client.get("/api/plugins").json()}
    assert after == base  # saboteur gone, world restored
    assert client.get("/api/state").json()["epoch"] == 1
