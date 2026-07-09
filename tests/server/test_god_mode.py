"""God-mode operator interventions + the auto-evolve switch, all via the API."""

import pytest
from fastapi.testclient import TestClient

from server.app import create_app


@pytest.fixture()
def client():
    # small flat world so the base plugins settle fast and tests stay quick
    with TestClient(create_app(seed=7, world_size=64, topology="flat")) as c:
        yield c


def test_god_spawn_then_cull_a_species(client):
    r = client.post("/api/god/spawn", json={"species": "grazer", "count": 50}).json()
    assert r["spawned"] == 50
    before = client.get("/api/metrics").json()["populations"].get("grazer", 0)
    assert before >= 50

    # cull removes HALF (a shock, not extinction): survivors remain
    r2 = client.post("/api/god/cull", json={"species": "grazer"}).json()
    assert r2["culled"] > 0
    after = client.get("/api/metrics").json()["populations"].get("grazer", 0)
    assert 0 < after < before


def test_god_spawn_unknown_species_is_an_error(client):
    r = client.post("/api/god/spawn", json={"species": "dragon", "count": 5}).json()
    assert "error" in r


def test_god_flora_bloom_raises_and_scorch_lowers_density(client):
    d0 = client.get("/api/metrics").json()["floraDensity"]
    client.post("/api/god/flora", json={"mode": "bloom", "amount": 0.5})
    d1 = client.get("/api/metrics").json()["floraDensity"]
    assert d1 > d0
    client.post("/api/god/flora", json={"mode": "scorch", "amount": 0.9})
    d2 = client.get("/api/metrics").json()["floraDensity"]
    assert d2 < d1


def test_god_flora_unknown_mode_is_an_error(client):
    r = client.post("/api/god/flora", json={"mode": "smite", "amount": 0.5}).json()
    assert "error" in r


def test_auto_evolve_toggle_endpoint_reports_state(client):
    # governor may be offline in CI (no Ollama); either way the switch responds
    # with the autoEvolve field and never errors.
    r = client.post("/api/governor/auto", json={"enabled": False}).json()
    assert "autoEvolve" in r
    status = client.get("/api/governor/status").json()
    assert "autoEvolve" in status
