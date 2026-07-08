"""FitnessEngine: pure function over shadow metrics — promotion is a measurement,
not a vibe (Story 3.5). Every sub-score is a DELTA against the cycle's baseline
control run; a candidate must make the world measurably better than doing nothing.
"""

from __future__ import annotations

import difflib
import math
from dataclasses import dataclass, field


@dataclass
class FitnessWeights:
    diversity: float = 2.0
    stability: float = 1.5
    extinctions: float = 3.0       # penalty weight
    trophic: float = 1.0
    sustainability: float = 1.0
    novelty: float = 0.5
    biomass_cost: float = 1.0     # penalty for piling on entities (perf + realism)


@dataclass
class FitnessScore:
    total: float
    breakdown: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"total": round(self.total, 4),
                "breakdown": {k: round(v, 4) for k, v in self.breakdown.items()}}


def shannon(populations: dict[str, int]) -> float:
    counts = [n for n in populations.values() if n > 0]
    total = sum(counts)
    if total == 0 or len(counts) <= 1:
        return 0.0
    return -sum((n / total) * math.log(n / total) for n in counts)


def _volatility(samples: list[dict]) -> float:
    """Mean per-species coefficient of variation across the shadow run."""
    if len(samples) < 3:
        return 0.0
    series: dict[str, list[int]] = {}
    for s in samples:
        for name, n in s["populations"].items():
            series.setdefault(name, []).append(n)
    cvs = []
    for values in series.values():
        mean = sum(values) / len(values)
        if mean < 1.0:
            continue
        var = sum((v - mean) ** 2 for v in values) / len(values)
        cvs.append(math.sqrt(var) / mean)
    return sum(cvs) / len(cvs) if cvs else 0.0


def _flora_trend(samples: list[dict]) -> float:
    if len(samples) < 2:
        return 0.0
    return samples[-1]["flora_mean"] - samples[0]["flora_mean"]


def source_novelty(candidate_source: str, existing_sources: list[str]) -> float:
    """1 - max similarity to any existing plugin source (AST-normalized-ish:
    whitespace-stripped line comparison via difflib)."""
    def normalize(src: str) -> list[str]:
        return [ln.strip() for ln in src.splitlines() if ln.strip() and not ln.strip().startswith("#")]

    cand = normalize(candidate_source)
    best = 0.0
    for src in existing_sources:
        ratio = difflib.SequenceMatcher(None, cand, normalize(src)).ratio()
        best = max(best, ratio)
    return 1.0 - best


def score_candidate(candidate_metrics: dict, control_metrics: dict,
                    candidate_source: str = "", existing_sources: list[str] | None = None,
                    candidate_species: list[str] | None = None,
                    weights: FitnessWeights | None = None) -> FitnessScore:
    w = weights or FitnessWeights()
    breakdown: dict[str, float] = {}

    cand_final = candidate_metrics.get("final_populations", {})
    ctrl_final = control_metrics.get("final_populations", {})

    breakdown["diversity"] = w.diversity * (shannon(cand_final) - shannon(ctrl_final))

    cand_vol = _volatility(candidate_metrics.get("samples", []))
    ctrl_vol = _volatility(control_metrics.get("samples", []))
    breakdown["stability"] = w.stability * (ctrl_vol - cand_vol)  # lower volatility is better

    cand_ext = len(candidate_metrics.get("extinctions", []))
    ctrl_ext = len(control_metrics.get("extinctions", []))
    breakdown["extinctions"] = -w.extinctions * max(0, cand_ext - ctrl_ext)
    # a candidate that *prevents* a control-run extinction earns the mirror bonus
    breakdown["extinctions"] += w.extinctions * 0.5 * max(0, ctrl_ext - cand_ext)

    # trophic balance: predation should exist but not dominate deaths
    cand_deaths = candidate_metrics.get("deaths", {})
    total_pred = sum(d.get("predation", 0) for d in cand_deaths.values())
    total_deaths = sum(sum(d.values()) for d in cand_deaths.values()) or 1
    pred_share = total_pred / total_deaths
    breakdown["trophic"] = w.trophic * (1.0 - abs(pred_share - 0.5)) if total_pred else 0.0

    breakdown["sustainability"] = w.sustainability * 10.0 * (
        _flora_trend(candidate_metrics.get("samples", []))
        - _flora_trend(control_metrics.get("samples", []))
    )

    # own-species survival: a species that dies out in its own shadow run is a bad bet
    if candidate_species:
        survived = all(cand_final.get(s, 0) > 0 for s in candidate_species)
        breakdown["own_survival"] = 0.0 if survived else -2.0

    if candidate_source and existing_sources is not None:
        breakdown["novelty"] = w.novelty * source_novelty(candidate_source, existing_sources)

    # runtime hygiene: plugin errors in shadow are a direct penalty
    errors = sum(candidate_metrics.get("plugin_errors", {}).values())
    if errors:
        breakdown["errors"] = -0.5 * errors

    # biomass parsimony: reward diversity gained per entity ADDED, so a lean
    # species filling a niche beats a huge herd of one animal. Total entity count
    # is the real perf ceiling, so cheap biodiversity is what we want to select.
    cand_total = sum(cand_final.values())
    ctrl_total = sum(ctrl_final.values())
    added = cand_total - ctrl_total
    if added > 0:
        # penalty grows with how much biomass the candidate piles on beyond a
        # modest budget — nudges the governor toward small, varied populations
        breakdown["biomass_cost"] = -w.biomass_cost * max(0.0, (added - 400) / 400.0)

    return FitnessScore(total=sum(breakdown.values()), breakdown=breakdown)
