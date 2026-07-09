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
FIELD_PLANKTON = 3


def _header(kind: int, tick: int, epoch: int, n: int) -> bytes:
    return np.array([kind, tick, epoch, n], dtype="<u4").tobytes()


def face_count(world: World) -> int:
    return 6 if world.config.cube else 1


def _face_slice(arr: np.ndarray, world: World, face: int) -> np.ndarray:
    """The 2D (size,size) plane for `face` — arr is (6,size,size) on a cube, else 2D."""
    return arr[face] if world.config.cube else arr


def encode_terrain(world: World, face: int = 0) -> bytes:
    size = world.config.size
    height = _face_slice(world.terrain.height, world, face)
    water = _face_slice(world.terrain.water_mask, world, face)
    return b"".join((
        _header(KIND_TERRAIN, world.tick, world.epoch, size),
        np.array([face], dtype="<u4").tobytes(),
        np.ascontiguousarray(height, dtype="<f4").tobytes(),
        np.ascontiguousarray(water > 0.5).astype(np.uint8).tobytes(),
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
        store.face[idx].astype(np.uint8).tobytes(),
    ))


def encode_field(world: World, field_id: int, face: int = 0) -> bytes:
    size = world.config.size
    if field_id == FIELD_FLORA:
        values = world.flora.density
    elif field_id == FIELD_TEMPERATURE:
        values = (world.weather.temperature + 20.0) / 60.0
    elif field_id == FIELD_MOISTURE:
        values = world.weather.soil_moisture
    elif field_id == FIELD_PLANKTON:
        values = world.plankton.density
    else:
        raise ValueError(f"unknown field id {field_id}")
    plane = _face_slice(values, world, face)
    quantized = np.clip(plane * 255.0, 0, 255).astype(np.uint8)
    return b"".join((
        _header(KIND_FIELD, world.tick, world.epoch, size),
        np.array([field_id, face], dtype="<u4").tobytes(),
        quantized.tobytes(),
    ))


def sync_message(world: World) -> dict:
    h = world.terrain.height
    sea = float(np.quantile(h, world.config.sea_level_quantile))
    return {
        "t": "sync",
        "protocol": 2,
        "tick": world.tick,
        "epoch": world.epoch,
        "size": world.config.size,
        "topology": world.config.topology,
        "faces": face_count(world),
        "seaLevel": sea,
        "heightScale": 24,
        "species": [
            {"id": s.id, "name": s.name, "color": s.color, "size": s.size, "plugin": s.plugin}
            for s in world.registry.by_id
        ],
    }


def frame_message(world: World, measured_tps: float) -> dict:
    w = world.weather
    wind = np.asarray(w.wind).reshape(-1, 2).mean(axis=0)  # (2,) for flat, mean over faces on cube
    return {
        "t": "frame",
        "tick": world.tick,
        "epoch": world.epoch,
        "tps": round(measured_tps, 1),
        "entities": world.store.count,
        "weather": {
            "temp": round(float(w.temperature.mean()), 2),
            "precip": round(float(w.precipitation.mean()), 4),
            "windX": round(float(wind[0]), 2),
            "windY": round(float(wind[1]), 2),
        },
        "clock": {
            "dayFrac": round(world.day_frac, 4),
            "seasonFrac": round(world.season_frac, 4),
            "seasonIndex": world.season_index,
            "calendar": world.calendar,
        },
    }
