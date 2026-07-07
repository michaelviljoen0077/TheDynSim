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
    }


def _arrays(world: World) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    arrays.update(world.store.to_arrays())
    arrays.update(world.terrain.to_arrays())
    arrays.update(world.weather.to_arrays())
    arrays.update(world.flora.to_arrays())
    return arrays


def save_snapshot(world: World, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix != ".npz":
        path = path.with_suffix(path.suffix + ".npz")
    arrays = _arrays(world)
    header = np.frombuffer(json.dumps(_header(world), sort_keys=True).encode(), dtype=np.uint8)
    np.savez(path, __header__=header, **arrays)  # uncompressed: FR "< 2 s" beats file size
    return path


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
    world.terrain = Terrain.from_arrays(arrays)
    world.weather = Weather.from_arrays(arrays, config.size)
    world.flora = Flora.from_arrays(arrays, config.size)
    for name, state in header["plugin_rng_states"].items():
        g = np.random.default_rng()
        g.bit_generator.state = state
        world.plugin_rngs[name] = g
    world.plugin_stores = {k: dict(v) for k, v in header["plugin_stores"].items()}
    return world


def state_hash(world: World) -> str:
    """SHA-256 over the full canonical state; equal hash == equal world."""
    h = hashlib.sha256()
    h.update(json.dumps(_header(world), sort_keys=True, default=str).encode())
    arrays = _arrays(world)
    for name in sorted(arrays):
        h.update(name.encode())
        h.update(np.ascontiguousarray(arrays[name]).tobytes())
    return h.hexdigest()
