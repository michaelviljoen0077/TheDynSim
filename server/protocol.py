"""Binary frame encoders per docs/protocol.md. All little-endian.

Encoding is NumPy array packing — near-zero cost — but entity/field reads must
happen under the runner lock; callers pass a locked world and we copy out fast.
"""

from __future__ import annotations

import numpy as np

from engine import World

KIND_TERRAIN = 1
KIND_ENTITIES = 2
KIND_FIELD = 3

FIELD_FLORA = 0
FIELD_TEMPERATURE = 1
FIELD_MOISTURE = 2


def _header(kind: int, tick: int, epoch: int, n: int) -> bytes:
    return np.array([kind, tick, epoch, n], dtype="<u4").tobytes()


def encode_terrain(world: World) -> bytes:
    t = world.terrain
    size = world.config.size
    return b"".join((
        _header(KIND_TERRAIN, world.tick, world.epoch, size),
        np.ascontiguousarray(t.height, dtype="<f4").tobytes(),
        np.ascontiguousarray(t.water_mask > 0.5).astype(np.uint8).tobytes(),
    ))


def encode_entities(world: World) -> bytes:
    store = world.store
    idx = np.flatnonzero(store.alive)
    n = int(idx.size)
    ids = (idx.astype(np.uint32) << np.uint32(16)) | store.generation[idx].astype(np.uint32)
    return b"".join((
        _header(KIND_ENTITIES, world.tick, world.epoch, n),
        ids.astype("<u4").tobytes(),
        store.px[idx].astype("<f4").tobytes(),
        store.py[idx].astype("<f4").tobytes(),
        store.pz[idx].astype("<f4").tobytes(),
        store.energy[idx].astype("<f4").tobytes(),
        store.species_id[idx].astype("<u2").tobytes(),
        store.stratum[idx].astype(np.uint8).tobytes(),
    ))


def encode_field(world: World, field_id: int) -> bytes:
    size = world.config.size
    if field_id == FIELD_FLORA:
        values = world.flora.density
    elif field_id == FIELD_TEMPERATURE:
        values = (world.weather.temperature + 20.0) / 60.0
    elif field_id == FIELD_MOISTURE:
        values = world.weather.soil_moisture
    else:
        raise ValueError(f"unknown field id {field_id}")
    quantized = np.clip(values * 255.0, 0, 255).astype(np.uint8)
    return b"".join((
        _header(KIND_FIELD, world.tick, world.epoch, size),
        np.array([field_id], dtype="<u4").tobytes(),
        quantized.tobytes(),
    ))


def sync_message(world: World) -> dict:
    sea = float(np.quantile(world.terrain.height, world.config.sea_level_quantile))
    return {
        "t": "sync",
        "protocol": 1,
        "tick": world.tick,
        "epoch": world.epoch,
        "size": world.config.size,
        "seaLevel": sea,
        "heightScale": 24,
        "species": [
            {"id": s.id, "name": s.name, "color": s.color, "size": s.size, "plugin": s.plugin}
            for s in world.registry.by_id
        ],
    }


def frame_message(world: World, measured_tps: float) -> dict:
    w = world.weather
    return {
        "t": "frame",
        "tick": world.tick,
        "epoch": world.epoch,
        "tps": round(measured_tps, 1),
        "entities": world.store.count,
        "weather": {
            "temp": round(float(w.temperature.mean()), 2),
            "precip": round(float(w.precipitation.mean()), 4),
            "windX": round(float(w.wind[0]), 2),
            "windY": round(float(w.wind[1]), 2),
        },
        "clock": {
            "dayFrac": round(world.day_frac, 4),
            "seasonFrac": round(world.season_frac, 4),
            "seasonIndex": world.season_index,
        },
    }
