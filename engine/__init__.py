"""Genesis v2 world kernel — a pure library.

No I/O, no globals, no imports from server/ or governor/. Both the live server
and shadow workers just instantiate `World`.
"""

from engine.config import WorldConfig
from engine.core import World
from engine.entities import EntityStore, SpeciesRegistry
from engine.snapshot import load_snapshot, save_snapshot, state_hash
from engine.spatial import SpatialHash

__all__ = [
    "EntityStore",
    "SpatialHash",
    "SpeciesRegistry",
    "World",
    "WorldConfig",
    "load_snapshot",
    "save_snapshot",
    "state_hash",
]
