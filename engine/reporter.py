"""Observation report: the structured world summary the governor reasons over (FR11).

Pure read — no side effects. Deltas are computed against the previous report so
the LLM sees trends, not just a snapshot.
"""

from __future__ import annotations

import numpy as np

from engine.core import World

STRATUM_NAMES = {0: "underground", 1: "surface", 2: "sky"}


def build_report(world: World, prev: dict | None = None) -> dict:
    store = world.store
    populations: dict[str, dict] = {}
    for sp in world.registry.by_id:
        rows = store.alive_indices(sp.id)
        if rows.size == 0 and sp.plugin == "":
            continue
        by_stratum: dict[str, int] = {}
        if rows.size:
            strata, counts = np.unique(store.stratum[rows], return_counts=True)
            by_stratum = {
                STRATUM_NAMES[int(s)]: int(c)
                for s, c in zip(strata.tolist(), counts.tolist(), strict=True)
            }
        entry = {
            "total": int(rows.size),
            "by_stratum": by_stratum,
            "mean_energy": round(float(store.energy[rows].mean()), 1) if rows.size else 0.0,
            "mean_age": round(float(store.age[rows].mean()), 1) if rows.size else 0.0,
            "plugin": sp.plugin,
        }
        # evolved traits: mean of each heritable gene across the living population,
        # so the governor can SEE a species adapting (drifting from its founder value)
        if sp.gene_slots and rows.size:
            entry["genes"] = {
                name: round(float(store.genome[rows, slot].mean()), 3)
                for name, slot in sp.gene_slots.items()
            }
        populations[sp.name] = entry

    w = world.weather
    report = {
        "tick": world.tick,
        "epoch": world.epoch,
        "season_index": world.season_index,
        "day_frac": round(world.day_frac, 3),
        "populations": populations,
        "deaths_by_cause": {k: dict(v) for k, v in world.deaths.items()},
        "flora": {
            "mean_density": round(float(world.flora.density.mean()), 4),
            "coverage_over_10pct": round(float((world.flora.density > 0.1).mean()), 4),
        },
        "weather": {
            "temperature_mean": round(float(w.temperature.mean()), 2),
            "temperature_min": round(float(w.temperature.min()), 2),
            "temperature_max": round(float(w.temperature.max()), 2),
            "precipitation_mean": round(float(w.precipitation.mean()), 5),
            "soil_moisture_mean": round(float(w.soil_moisture.mean()), 4),
        },
        "shannon_diversity": shannon_diversity(
            {name: p["total"] for name, p in populations.items()}
        ),
        "plugins": [
            {"name": m["name"], "status": m["status"], "species": m["meta"].get("species", [])}
            for m in world.plugin_manifest
        ],
        # extinct species the governor should learn from (and not blindly recreate)
        "extinct_species": [
            {"species": e["species"], "plugin": e["plugin"], "tick": e["tick"]}
            for e in world.extinct
        ],
    }
    if prev is not None:
        deltas: dict[str, dict] = {}
        prev_pops = prev.get("populations", {})
        for name, p in populations.items():
            before = prev_pops.get(name, {}).get("total", 0)
            deltas[name] = {"population_change": p["total"] - before}
        report["deltas_since_last_report"] = {
            "ticks_elapsed": world.tick - prev.get("tick", 0),
            "populations": deltas,
            "flora_change": round(
                report["flora"]["mean_density"] - prev.get("flora", {}).get("mean_density", 0.0), 4
            ),
        }
    return report


def shannon_diversity(populations: dict[str, int]) -> float:
    counts = np.array([n for n in populations.values() if n > 0], dtype=np.float64)
    if counts.size <= 1:
        return 0.0
    p = counts / counts.sum()
    return round(float(-(p * np.log(p)).sum()), 4)
