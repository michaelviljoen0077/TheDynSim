"""Snapshot service: byte-exact save / restore / state-hash of a full World.

One mechanism trusted everywhere: shadow forks and rollback both come through
here. A snapshot is COMPLETE — entities, fields, tick/epoch, engine RNG state,
every per-plugin RNG stream, every plugin `world.store` (FR5). If snapshot/
restore wouldn't reproduce a piece of state, that state doesn't ship (standard #8).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from engine.config import WorldConfig
from engine.core import World
from engine.entities import EntityStore, SpeciesRegistry
from engine.fields import Flora, Terrain, Weather

FORMAT_VERSION = 1


def _header(world: World) -> dict:
    return {
        "format": FORMAT_VERSION,
        "tick": world.tick,
        "epoch": world.epoch,
        "config": world.config.to_json(),
        "rng_state": world.rng.bit_generator.state,
        "plugin_rng_states": {
            name: g.bit_generator.state for name, g in sorted(world.plugin_rngs.items())
        },
        "plugin_stores": {k: world.plugin_stores[k] for k in sorted(world.plugin_stores)},
        "species": world.registry.to_state(),
        "deaths": world.deaths,
        "plugin_manifest": world.plugin_manifest,
    }


def _arrays(world: World) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    arrays.update(world.store.to_arrays())
    arrays.update(world.terrain.to_arrays())
    arrays.update(world.weather.to_arrays())
    arrays.update(world.flora.to_arrays())
    return arrays


def _dump_header(world: World) -> str:
    """Canonical header serialization shared by save and hash (so they agree byte-for-byte)."""
    return json.dumps(_header(world), sort_keys=True)


def capture(world: World) -> dict:
    """Copy the full world state into memory — fast (memcpy-scale), safe to run
    under the engine lock. Pair with write_capture() OUTSIDE the lock so tens of
    MB of disk I/O never stall the tick loop (NFR6)."""
    return {
        "header": _dump_header(world),
        "arrays": {k: v.copy() for k, v in _arrays(world).items()},
    }


def write_capture(cap: dict, path: str | Path) -> Path:
    """Write a capture() to disk. Slow (disk I/O) — never call under the engine lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix != ".npz":
        path = path.with_suffix(path.suffix + ".npz")
    header = np.frombuffer(cap["header"].encode(), dtype=np.uint8)
    np.savez(path, __header__=header, **cap["arrays"])  # uncompressed: "< 2 s" beats size
    return path


def save_snapshot(world: World, path: str | Path) -> Path:
    """Capture + write in one call — for contexts that don't hold the engine lock."""
    return write_capture(capture(world), path)


def load_snapshot(path: str | Path) -> World:
    with np.load(Path(path)) as data:
        header = json.loads(bytes(data["__header__"]).decode())
        arrays = {k: data[k] for k in data.files if k != "__header__"}
    if header["format"] != FORMAT_VERSION:
        raise ValueError(f"snapshot format {header['format']} != supported {FORMAT_VERSION}")
    config = WorldConfig.from_json(header["config"])
    world = World(config, _generate=False)
    world.tick = header["tick"]
    world.epoch = header["epoch"]
    world.rng = np.random.default_rng()
    world.rng.bit_generator.state = header["rng_state"]
    world.registry = SpeciesRegistry.from_state(header["species"], config.max_prop_slots)
    world.store = EntityStore.from_arrays(arrays, config.max_prop_slots)
    if config.cube:
        from engine.cube_fields import CubeFlora, CubeTerrain, CubeWeather
        world.terrain = CubeTerrain.from_arrays(arrays)
        world.weather = CubeWeather.from_arrays(arrays, config.size)
        world.flora = CubeFlora.from_arrays(arrays, config.size)
    else:
        world.terrain = Terrain.from_arrays(arrays)
        world.weather = Weather.from_arrays(arrays, config.size)
        world.flora = Flora.from_arrays(arrays, config.size)
    for name, state in header["plugin_rng_states"].items():
        g = np.random.default_rng()
        g.bit_generator.state = state
        world.plugin_rngs[name] = g
    world.plugin_stores = {k: dict(v) for k, v in header["plugin_stores"].items()}
    world.deaths = {k: dict(v) for k, v in header.get("deaths", {}).items()}
    world.plugin_manifest = list(header.get("plugin_manifest", []))
    return world


def state_hash(world: World) -> str:
    """SHA-256 over the full canonical state; equal hash == equal world."""
    h = hashlib.sha256()
    h.update(_dump_header(world).encode())
    arrays = _arrays(world)
    for name in sorted(arrays):
        h.update(name.encode())
        h.update(np.ascontiguousarray(arrays[name]).tobytes())
    return h.hexdigest()
