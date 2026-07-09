"""Orchestrator: one evolution cycle, end to end (Story 3.6).

observe -> recall -> generate N (repair round-trip on validation failure)
-> shadow-evaluate candidates against a baseline control run -> score as deltas
-> promote the best candidate that beats control by the threshold -> record
everything -> next cycle measures the previous promotion's real outcome.

Every degradation is a recorded decision; a cycle can never corrupt the live
world (promotion goes through the runner's snapshot-first path).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from engine.reporter import build_report
from engine.snapshot import capture, write_capture
from engine.validator import validate_plugin
from governor.fitness import FitnessWeights, score_candidate
from governor.llm import GenerationError, LLMProvider
from governor.notebook import Notebook
from governor.shadow import Budgets, ShadowJob, run_shadow_batch

log = logging.getLogger("genesis.governor")

API_REFERENCE_PATH = Path(__file__).resolve().parent.parent / "docs" / "plugin_api.md"

# Per-candidate strategy directives: rotated across the batch so N candidates
# explore genuinely different moves instead of converging on one idea. Each
# candidate also sees what its siblings already proposed (see _build_prompt).
STRATEGIES = [
    {"name": "fill an empty niche",
     "directive": "Introduce a NEW species that occupies a stratum or trophic role "
                  "currently unused or thinly populated (e.g. a sky forager, an aquatic "
                  "swimmer with swim_speed>0, a decomposer, or a prey that hides/burrows "
                  "via hide() to evade predators)."},
    {"name": "refine a live plugin",
     "directive": "Pick the WEAKEST existing plugin from the report and improve it via a "
                  "lineage mutation: set PLUGIN_META['lineage_parent'] to its name, declare "
                  "its species, and re-tune the numbers (speed, energy, reproduction). Do "
                  "NOT just add a new species — rework an existing one."},
    {"name": "add a trophic link",
     "directive": "Add a species that connects existing ones into a longer food chain: a "
                  "predator of an unpredated species, or prey for a struggling predator. "
                  "Use world.attack for predation."},
    {"name": "environmental engineer",
     "directive": "Introduce a species whose main effect is on the environment or other "
                  "species indirectly (spreads/consumes flora, migrates between strata) to "
                  "improve stability or diversity without simply adding biomass."},
    {"name": "omnivore / generalist",
     "directive": "Introduce an OMNIVORE that both grazes flora (world.eat_flora) AND hunts "
                  "prey (world.nearest + world.attack) — falling back to plants when prey is "
                  "scarce. A generalist buffers the ecosystem against boom/bust in any single "
                  "food source. Balance both intake rates so it doesn't out-compete specialists."},
]


def _species_of(source: str) -> list[str]:
    """Best-effort species names from a candidate's PLUGIN_META (for sibling summaries)."""
    from engine.validator import validate_plugin as _v
    meta = _v(source).meta or {}
    species = meta.get("species", [])
    return species if isinstance(species, list) else []


@dataclass
class GovernorConfig:
    n_candidates: int = 3
    shadow_ticks: int = 2200          # long enough to expose slow exterminations
    promotion_threshold: float = 0.5     # candidate.total must exceed this (control-relative)
    max_parallel_workers: int = 4
    # automatic cadence: a new cycle starts once this many live-sim ticks have
    # passed since the previous cycle finished (and the governor is idle).
    # 10,000 ticks ~ 2.8 min at 60 tps — roughly one cycle's wall time, so the
    # governor is thinking about as often as it possibly can without queueing.
    cycle_every_ticks: int = 10_000
    budgets: Budgets = field(default_factory=Budgets)
    weights: FitnessWeights = field(default_factory=FitnessWeights)


@dataclass
class CycleStatus:
    stage: str = "idle"   # idle|reporting|generating|validating|shadow|scoring|committing
    cycle_id: str = ""
    detail: str = ""


class Orchestrator:
    def __init__(self, runner, notebook: Notebook, provider: LLMProvider,
                 config: GovernorConfig | None = None,
                 snapshot_dir: str | Path = "data/snapshots/governor") -> None:
        self.runner = runner
        self.notebook = notebook
        self.provider = provider
        self.config = config or GovernorConfig()
        self.snapshot_dir = Path(snapshot_dir)
        self.status = CycleStatus()
        self._busy = threading.Lock()
        # auto-evolve: when False the automatic cadence is suspended (manual
        # "EVOLVE NOW" still works). The operator's on/off switch (god mode).
        self.auto_evolve = True
        self._last_promotion: dict | None = None   # {cycle_id, plugin_name, expected, report}
        # cadence anchor: first automatic cycle waits a full interval from startup
        self.last_cycle_end_tick: int = runner.world.tick if runner is not None else 0

    # -- public -------------------------------------------------------------------

    def reset_state(self) -> None:
        """Forget in-flight/pending cycle state after a world reset (paired with
        notebook.clear_run so the fresh world has no stale evolution history)."""
        self._last_promotion = None
        self.last_cycle_end_tick = self.runner.world.tick
        self.status = CycleStatus()

    def run_cycle_async(self) -> bool:
        """Fire a cycle on a worker thread; returns False if one is already running."""
        if self._busy.locked():
            return False
        threading.Thread(target=self.run_cycle, name="governor-cycle", daemon=True).start()
        return True

    def due(self) -> bool:
        """True when the automatic cadence says a new cycle should start (Story 3.6)."""
        if self._busy.locked():
            return False
        ticks_since = self.runner.world.tick - self.last_cycle_end_tick
        return ticks_since >= self.config.cycle_every_ticks

    def run_cycle(self) -> str:
        with self._busy:
            try:
                return self._cycle()
            except Exception as e:  # noqa: BLE001 — a cycle failure must never propagate
                log.exception("cycle failed")
                self.status = CycleStatus("idle", detail=f"cycle error: {e}")
                return "error"
            finally:
                self.last_cycle_end_tick = self.runner.world.tick

    # -- the cycle ------------------------------------------------------------------

    def _cycle(self) -> str:
        cfg = self.config
        self.status = CycleStatus("reporting")
        with self.runner.lock:
            world = self.runner.world
            report = build_report(world)
            live_sources = [m["source"] for m in world.plugin_manifest]
            live_species = [
                s for m in world.plugin_manifest for s in m["meta"].get("species", [])
            ]
            snap_path = self.snapshot_dir / f"cycle-{world.epoch}-{world.tick}.npz"
            cap = capture(world)  # fast in-memory copy under the lock
        write_capture(cap, snap_path)  # slow disk write outside the lock (NFR6)

        cycle_id = self.notebook.start_cycle(report["epoch"], report["tick"], report,
                                             self.provider.name)
        self.status = CycleStatus("generating", cycle_id)

        # close the loop on the previous promotion (FR17)
        self._measure_previous_outcome(report)

        recall = self.notebook.recall(live_species)

        tokens_in = tokens_out = 0
        proposals = []
        siblings: list[dict] = []  # what earlier candidates THIS cycle already proposed
        for i in range(cfg.n_candidates):
            # each candidate gets a distinct strategy directive + the siblings
            # already proposed, so the batch diverges instead of collapsing to
            # three near-identical ideas
            strategy = STRATEGIES[i % len(STRATEGIES)]
            prompt = self._build_prompt(report, recall, live_sources, strategy, siblings)
            try:
                proposal, usage = self.provider.generate(prompt)
                tokens_in += usage.tokens_in
                tokens_out += usage.tokens_out
            except GenerationError as e:
                self.notebook.record_candidate(
                    cycle_id, f"cand-{i}", "", {}, {"ok": False, "errors": [
                        {"code": "generation-failed", "message": str(e), "line": 0}]},
                    {}, {}, 0.0, "rejected_generation")
                continue
            siblings.append({"strategy": strategy["name"],
                             "species": _species_of(proposal.plugin_source),
                             "hypothesis": proposal.hypothesis[:160]})
            proposals.append((f"cand-{i}", proposal))

        # validate, with one repair round-trip (Story 3.4 AC3)
        self.status = CycleStatus("validating", cycle_id)
        validated = []
        base_prompt = self._build_prompt(report, recall, live_sources, STRATEGIES[0], [])
        for label, proposal in proposals:
            result = validate_plugin(proposal.plugin_source)
            if not result.ok:
                repair_prompt = self._build_repair_prompt(base_prompt, proposal, result)
                try:
                    proposal2, usage2 = self.provider.generate(repair_prompt)
                    tokens_in += usage2.tokens_in
                    tokens_out += usage2.tokens_out
                    result2 = validate_plugin(proposal2.plugin_source)
                    if result2.ok:
                        validated.append((label, proposal2, result2))
                        continue
                    result = result2
                    proposal = proposal2
                except GenerationError:
                    pass
                self.notebook.record_candidate(
                    cycle_id, label, proposal.plugin_source, proposal.as_dict(),
                    result.as_dict(), {}, {}, 0.0, "rejected_validation")
                continue
            validated.append((label, proposal, result))

        if not validated:
            self.notebook.finish_cycle(cycle_id, "no_change", tokens_in, tokens_out)
            self.status = CycleStatus("idle", cycle_id, "no candidates survived validation")
            return "no_change"

        # shadow-evaluate: control + candidates in one parallel batch (FR14)
        self.status = CycleStatus("shadow", cycle_id, f"{len(validated)} candidates + control")
        jobs = [ShadowJob(str(snap_path), None, cfg.shadow_ticks, cfg.budgets, "control")]
        jobs += [
            ShadowJob(str(snap_path), proposal.plugin_source, cfg.shadow_ticks, cfg.budgets, label)
            for label, proposal, _v in validated
        ]
        results = {r.label: r for r in run_shadow_batch(jobs, cfg.max_parallel_workers)}
        control = results["control"]
        if not control.ok:
            # a live plugin is failing in shadow conditions: that's a finding, not a candidate's fault
            for label, proposal, v in validated:
                self.notebook.record_candidate(
                    cycle_id, label, proposal.plugin_source, proposal.as_dict(),
                    v.as_dict(), {}, {}, 0.0, "rejected_no_control")
            self.notebook.record_intervention(report["epoch"], report["tick"],
                                              "control_failed", details={"reason": control.reason})
            self.notebook.finish_cycle(cycle_id, "no_change", tokens_in, tokens_out)
            self.status = CycleStatus("idle", cycle_id, f"control run failed: {control.reason}")
            return "no_change"

        # score survivors as deltas vs control (FR15)
        self.status = CycleStatus("scoring", cycle_id)
        best = None
        for label, proposal, v in validated:
            res = results[label]
            if not res.ok:
                self.notebook.record_candidate(
                    cycle_id, label, proposal.plugin_source, proposal.as_dict(),
                    v.as_dict(), {"reason": res.reason, **res.metrics}, {}, 0.0, "rejected_shadow")
                continue
            meta = self._proposal_meta(proposal)
            score = score_candidate(
                res.metrics, control.metrics,
                candidate_source=proposal.plugin_source,
                existing_sources=live_sources,
                candidate_species=meta.get("species", []),
                weights=cfg.weights,
            )
            fate = "scored"
            cand_id = self.notebook.record_candidate(
                cycle_id, label, proposal.plugin_source,
                {**proposal.as_dict(), **meta}, v.as_dict(), res.metrics,
                score.as_dict(), score.total, fate)
            if best is None or score.total > best[3].total:
                best = (cand_id, label, proposal, score)

        # select & promote (FR16)
        decision = "no_change"
        if best is not None and best[3].total >= cfg.promotion_threshold:
            cand_id, label, proposal, score = best
            self.status = CycleStatus("committing", cycle_id, label)
            try:
                info = self.runner.promote(proposal.plugin_source)
                decision = "promoted"
                self.notebook.set_candidate_fate(cand_id, "promoted")
                self.notebook.record_intervention(
                    report["epoch"], report["tick"], "promotion",
                    plugin_name=info["installed"],
                    details={"cycle_id": cycle_id, "fitness": score.total})
                self._last_promotion = {
                    "cycle_id": cycle_id,
                    "plugin_name": info["installed"],
                    "expected": proposal.expected_outcome,
                    "report": report,
                }
            except Exception as e:  # noqa: BLE001 — promotion failure degrades to no_change
                log.exception("promotion failed")
                self.notebook.set_candidate_fate(cand_id, "promotion_failed")
                decision = "no_change"
                self.status.detail = f"promotion failed: {e}"

        self.notebook.finish_cycle(cycle_id, decision, tokens_in, tokens_out)
        self.status = CycleStatus("idle", cycle_id, decision)
        return decision

    # -- helpers --------------------------------------------------------------------

    def _proposal_meta(self, proposal) -> dict:
        result = validate_plugin(proposal.plugin_source)
        meta = dict(result.meta or {})
        meta["hypothesis"] = proposal.hypothesis
        return meta

    def _measure_previous_outcome(self, current_report: dict) -> None:
        if self._last_promotion is None:
            return
        prev = self._last_promotion
        before = prev["report"]["populations"]
        after = current_report["populations"]
        plugin = prev["plugin_name"]
        promoted_species = [
            name for name, p in after.items() if p.get("plugin") == plugin
        ]
        measured = {
            "promoted_species_alive": {s: after.get(s, {}).get("total", 0) for s in promoted_species},
            "population_changes": {
                name: after.get(name, {}).get("total", 0) - before.get(name, {}).get("total", 0)
                for name in set(before) | set(after)
            },
            "diversity_change": round(
                current_report["shannon_diversity"] - prev["report"]["shannon_diversity"], 4),
        }
        alive_ok = all(n > 0 for n in measured["promoted_species_alive"].values()) \
            if measured["promoted_species_alive"] else True
        if not alive_ok:
            verdict = "catastrophic"
        elif measured["diversity_change"] > 0.05:
            verdict = "better"
        elif measured["diversity_change"] < -0.05:
            verdict = "worse"
        else:
            verdict = "as_expected"
        self.notebook.record_outcome(prev["cycle_id"], plugin, prev["expected"],
                                     measured, verdict)
        self._last_promotion = None

    def _build_prompt(self, report: dict, recall: list[dict], live_sources: list[str],
                      strategy: dict, siblings: list[dict]) -> str:
        import json as _json
        api_ref = API_REFERENCE_PATH.read_text() if API_REFERENCE_PATH.exists() else ""
        live_code = "\n\n".join(
            f"# ---- live plugin ----\n{src}" for src in live_sources
        )
        recall_txt = _json.dumps(recall, indent=1) if recall else "none yet"
        if siblings:
            sib_txt = ("\n\n## Ideas ALREADY proposed this cycle — you MUST do something "
                       "clearly different (different species, stratum, or trophic role):\n"
                       + _json.dumps(siblings, indent=1))
        else:
            sib_txt = ""
        return f"""You are the governor of a living 3D ecosystem simulation. You evolve the world by
writing ONE new Python plugin per response, following the contract below exactly.

{api_ref}

## Current world state (observation report)
{_json.dumps(report, indent=1)}

## Relevant past experiments (lab notebook)
{recall_txt}

## Currently live plugin source code
{live_code}

## Your assigned strategy for THIS candidate: {strategy["name"]}
{strategy["directive"]}{sib_txt}

## Your task
Follow your assigned strategy above. Design ONE new plugin that makes the ecosystem
measurably richer or more stable. Analyze the observation report first — target a
real weakness (an empty stratum, a fragile or missing trophic level, low diversity).

Name the plugin and species yourself — do NOT reuse the reference's placeholder
names ("example_plugin"/"example_species") and do NOT default to a "vole". Pick a
creature that genuinely fits the niche you are filling.

Your candidate will be tested in a shadow simulation and scored on diversity,
stability, extinction avoidance, trophic balance, and sustainability — RELATIVE to
a control run without it. It must beat "do nothing" to be promoted. Your own
species going extinct in the shadow run is heavily penalized: make sure it can
feed itself and reproduce sustainably (reproduction should cost energy and take
time — runaway breeders overcrowd and score badly).

Respond with analysis, hypothesis, expected_outcome (concrete and measurable),
confidence (0-1), lineage_parent (or null), and the complete plugin_source."""

    def _build_repair_prompt(self, original_prompt: str, proposal, result) -> str:
        import json as _json
        return f"""{original_prompt}

## Your previous attempt FAILED static validation. Fix it.

### Your code
```python
{proposal.plugin_source}
```

### Machine-readable validation errors (fix every one)
{_json.dumps(result.as_dict()["errors"], indent=1)}

Return the complete corrected proposal, changing only what is needed to pass."""
