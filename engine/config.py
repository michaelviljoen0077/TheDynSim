"""World configuration. Plain dataclass — the engine stays pydantic-free."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class WorldConfig:
    seed: int = 424242
    size: int = 256                  # world is size x size columns
    topology: str = "flat"           # "flat" (walled) | "wrap" (toroidal) | "cube" (6-face sphere)
    initial_capacity: int = 16384    # entity store starting capacity
    max_prop_slots: int = 8          # per-species named float slots
    field_step_every: int = 1        # step weather/flora every N ticks (perf: >1 on big cubes)

    # calendar: 600 ticks/day, 30 days/month, 4 seasons x 90 days = 360-day year.
    # (Long enough that day/month/year read believably; seasons are slow — the
    # "elliptical orbit" hand-wave.)
    ticks_per_day: int = 1200
    days_per_month: int = 30
    days_per_season: int = 90
    seasons_per_year: int = 4

    # terrain / hydrology
    sea_level_quantile: float = 0.30
    terrain_octaves: int = 5

    # per-plugin quotas (enforced by WorldAPI; here so shadow == live)
    max_entities_per_plugin: int = 4000
    max_spawns_per_tick: int = 200
    max_store_keys: int = 64
    # hard ceiling per species — the many-species end-goal needs each species
    # bounded so total entity count (the real perf driver) stays sane
    max_entities_per_species: int = 500

    # density-dependent crowding stress: the non-predator overpopulation control.
    # An entity with more than `crowding_softcap` same-species neighbours within
    # `crowding_radius` loses `crowding_penalty` energy per excess neighbour each
    # tick — models competition/disease/stress. Proportional (not catastrophic),
    # so it throttles growth smoothly instead of causing boom-bust collapse.
    crowding_radius: float = 6.0
    crowding_softcap: int = 6
    crowding_penalty: float = 0.20

    extra: dict = field(default_factory=dict)  # forward-compatible bag

    @property
    def wrap(self) -> bool:
        return self.topology == "wrap"

    @property
    def cube(self) -> bool:
        return self.topology == "cube"

    @property
    def ticks_per_season(self) -> int:
        return self.ticks_per_day * self.days_per_season

    @property
    def ticks_per_year(self) -> int:
        return self.ticks_per_season * self.seasons_per_year

    @property
    def days_per_year(self) -> int:
        return self.days_per_season * self.seasons_per_year

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> WorldConfig:
        return cls(**json.loads(s))
