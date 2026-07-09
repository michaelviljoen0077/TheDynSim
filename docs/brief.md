# Project Brief: Genesis v2 — "The Living World"

**Version:** 1.3 (execution-hardening review: snapshot-complete plugin state, replayable determinism, pipelined cycle budget, control-run fitness baseline, binary streaming, feasibility spikes)
**Date:** 2026-07-07
**Author:** BMAD Analyst (Claude) with Michael
**Status:** Approved for handover

---

## Executive Summary

Genesis v2 is a self-evolving artificial-life simulator: a fully 3D layered world (underground, surface, sky) whose ecology is authored not by hand-written rules but by an **AI governor that writes, tests, and evolves the simulation's own code while it runs**.

> **Descope note (implemented state):** the world became a cube-sphere planet with **two** active strata (surface & sky). The underground layer was cut — it read poorly and the governor over-fixated on it; creatures now "go to ground" via a `hide()`/burrow state. Underground references throughout this brief are historical.

Where v1 let an LLM inject untested Python straight into a live simulation and hoped for the best, v2 turns the loop into a genuine **evolutionary process for code**: each cycle the governor generates several candidate plugins, runs each in a parallel *shadow simulation* (a forked, headless copy of the world), scores the outcomes against ecological fitness metrics, promotes only the winner, and records everything — hypothesis, experiment, result — in a persistent lab notebook it consults on every future cycle.

The product is both a mesmerizing thing to watch (a 3D world visibly growing more complex over hours) and a serious experiment platform for LLM-driven code evolution.

---

## Problem Statement

### The idea is proven; the v1 implementation collapses under it

The v1 prototype (old repo) demonstrated the core magic — an LLM observing a live ecosystem and hot-loading new Python behaviour into it — but every structural weakness eventually surfaced as mass extinction, crashed plugins, or a stagnant world:

1. **No validation loop.** Generated code went LLM → disk → `exec()` in-process on the live world. The only quality gates were a syntax check and "quarantine after 3 runtime errors." A plugin's *ecological* quality (do the animals survive?) was never tested before deployment — the live world **was** the test environment.
2. **Prompt-as-bugfix-graveyard.** Every failure mode was patched by adding rules to a giant prompt ("HARD RULES 1–10", "CRITICAL — WHY ANIMALS KEEP DYING"). This is fighting symptoms; the ecosystem kept collapsing anyway because nothing ever *measured* whether a generated plugin worked before it shipped.
3. **No memory, no fitness signal.** The governor saw only the last 3 iterations and a single vague "stability score." It could not learn across runs, could not diagnose *why* a species died, and repeated failed strategies.
4. **Two incompatible generations of Meta-AI coexist** (`backend/meta_ai.py` tier system vs `meta_ai/` LLM pipeline) with two different plugin contracts (`register/on_tick` vs `setup/on_tick`) — architectural drift with no single source of truth.
5. **Fragile LLM plumbing.** A 4-tier fallback chain through Ollama and *unofficial* GitHub Copilot endpoints, parsing free-text responses with regexes. Any format drift breaks the loop silently.
6. **Unsafe by construction.** AI-generated code executed with full interpreter privileges in the server process. "Safety" was zipping the whole codebase into `backups/` before each change.
7. **Performance ceiling.** Dict-based entities with O(n²) nearest-neighbour scans on a 100×100 grid. No headroom for a bigger world, more species, or faster ticks — let alone parallel evaluation.

### Why now / why rebuild

The available hardware (RTX 4080 16GB, 64GB RAM) removes the two constraints that shaped v1's compromises: LLM access (a capable coder model can now run *locally* on the GPU, making many-candidate generation effectively free) and compute (64GB RAM comfortably hosts several parallel shadow simulations). The lessons from v1 are clear enough that patching it would cost more than rebuilding on a clean architecture.

---

## Proposed Solution

### Core concept

**Evolve code the way nature evolves organisms.** The unit of evolution is the *plugin* (a Python module implementing world behaviour). Each Meta-AI cycle:

1. **Observe** — collect a rich, structured world report: populations, births/deaths by cause, spatial distribution across strata, trophic flows, weather, resource levels.
2. **Recall** — query the lab notebook (persistent DB of every past hypothesis, plugin, and measured outcome, including across restarts) for relevant history.
3. **Propose** — generate *N* candidate plugins (new species, rebalances of existing code, environmental engineering). Candidates may be mutations of prior successful plugins — true lineage.
4. **Test** — run each candidate in its own **shadow simulation**: a forked copy of the live world, headless, fast-forwarded a few thousand ticks in a sandboxed worker process. Crashes, timeouts, and rule violations disqualify.
5. **Select** — score surviving candidates with an explicit **fitness function** (biodiversity, population stability, trophic balance, novelty). Promote the best; reject the rest but *record why*.
6. **Commit & watch** — hot-load the winner into the live world, snapshot state for instant rollback, and evaluate real-world outcome at the next cycle, closing the loop.

### The world itself

A fully 3D layered world — **underground** (burrows, roots, aquifers, minerals), **surface** (terrain heightmap, water bodies, flora, fauna), and **sky** (fliers, weather systems) — with entities holding true `(x, y, z)` positions and stratum-aware behaviours. Rendered live in the browser as an orbitable 3D scene with per-stratum visibility toggles.

### Key differentiators from v1

| | v1 | v2 |
|---|---|---|
| Code deployment | Straight to live world | Shadow-tested, fitness-scored, then promoted |
| Candidates per cycle | 1 | N (parallel evaluation) |
| Safety | Prompt rules + error counter | AST validation → sandboxed subprocess → resource limits → snapshot rollback |
| Memory | Last 3 iterations in RAM | Persistent lab notebook + plugin lineage graph (SQLite) |
| LLM output | Free text, regex-parsed | Schema-validated structured output |
| LLM providers | Unofficial Copilot endpoints | Purely local: Ollama on the 4080 behind a pluggable adapter (cloud tiers possible post-MVP) |
| World | 2D 100×100 grid, dict entities | 3D strata, vectorized entity store (NumPy), spatial hashing |
| Observability | Text log + plugin viewer | 3D viewer, metric charts, intervention timeline, code phylogeny tree, diff viewer, replay |

---

## Target Users

**Primary: Michael (builder-operator).** A developer running Genesis on his own machine (RTX 4080 / 64GB), fascinated by emergent systems and LLM-driven code evolution. Wants to leave it running for hours and come back to a visibly richer world with a legible history of what the AI did and why.

**Secondary: viewers/demos.** People shown the running world — need the 3D view and the "what just happened" narrative to be self-explanatory within a minute.

**Tertiary (post-MVP): experimenters.** People who want to swap fitness functions, prompts, or models and compare evolutionary outcomes across runs.

---

## Goals & Success Metrics

### Product goals

- G1 — A world that runs unattended for **8+ hours** without collapse (no total extinction, no crash) while visibly gaining complexity.
- G2 — Every AI intervention is **tested before it touches the live world**; zero unvalidated code executes in the live process.
- G3 — The governor demonstrably **learns**: it does not repeat a strategy the notebook records as failed for the same conditions.
- G4 — The whole system runs **fully offline** on the local GPU; no cloud AI dependency anywhere in the loop.
- G5 — A newcomer watching the UI can answer "what did the AI just do, and did it work?" without reading code.

### Measurable KPIs

| Metric | Target |
|---|---|
| Live-sim tick rate at 10k entities (≥ 2k scalar plugin-driven fauna per benchmark protocol) | ≥ 60 ticks/s headless; streaming-on ≥ 80% of headless |
| Full evolution cycle (generate + validate + shadow-test + score, pipelined) | 3–5 candidates, each ≥ 2,000 shadow ticks, ≤ 5 min wall-time (local model; generation dominates at ~40 tok/s) |
| Candidate rejection catch rate | ≥ 90% of plugins that would crash/violate rules are caught pre-promotion |
| Mean time between total-extinction events | > 8 h of continuous running |
| Shannon diversity index trend over 4 h | Non-decreasing on median run |
| Rollback time after a bad promotion | < 5 s to previous snapshot |

---

## MVP Scope

### In scope

1. **Deterministic 3D simulation core** — seeded RNG, fixed-tick loop, three strata, terrain heightmap, water, weather, flora growth; vectorized entity store; snapshot/restore of full world state.
2. **Plugin runtime + safety pipeline** — single versioned plugin contract; AST/static validation; sandboxed shadow execution in worker processes with time/memory budgets; quarantine; one-click and automatic rollback.
3. **Meta-AI evolution loop** — structured observation report; LLM adapter (`OllamaProvider` local production tier + `ReplayProvider` test tier) with JSON-schema-validated output; N-candidate generation; parallel shadow evaluation; fitness scoring; promotion; lab notebook (SQLite) with plugin lineage.
4. **Observatory frontend** — Three.js 3D world view with stratum toggles and entity inspection; live metric charts; intervention timeline with analysis/outcome per cycle; plugin code + diff viewer; run controls (start/pause/speed/reset/rollback).

### Out of scope for MVP (post-MVP candidates)

- Multi-run experiment comparison dashboards & fitness-function A/B testing
- Evolution of the *fitness function itself* or of the governor's prompt (meta-meta-AI)
- Civilizational layer (agents building structures, economies, cultures)
- Multiplayer / hosted deployment / user accounts
- Replay export as video; world persistence across schema changes
- Crossover between plugin lineages (MVP does mutation + fresh generation only)

### MVP success criteria

A fresh clone + `docker compose up` (or two commands) reaches a running 3D world; within 30 minutes the governor has completed ≥ 5 evolution cycles with at least one rejected candidate visibly logged with its fitness scores; the world survives an overnight run.

---

## Technical Considerations (input to Architecture)

- **Host hardware is a design input:** RTX 4080 (16 GB VRAM) → local inference of quantized 14B–32B coder models via Ollama at interactive speed; 64 GB RAM → 3–5 concurrent shadow-sim worker processes plus the live sim are comfortable.
- **Backend Python 3.12+** — kept deliberately, because the LLM writes Python plugins and the ecosystem (NumPy, FastAPI, multiprocessing) fits. Performance comes from vectorization and process parallelism, not language change.
- **Frontend React + TypeScript + Vite + Three.js** (react-three-fiber, instanced rendering) — WebSocket delta streaming.
- **LLM layer** — **purely local**: Ollama HTTP API running quantized coder models on the RTX 4080, behind a provider-agnostic adapter interface with structured-output enforcement. No cloud SDKs in MVP; the adapter interface keeps a cloud tier possible post-MVP without redesign.
- **Persistence** — SQLite for notebook/lineage/metrics; binary world snapshots on disk.
- v1 code is treated as a **reference prototype**: nothing is migrated wholesale; the plugin *concept* and hard-won prompt lessons carry forward as validation rules and fitness criteria instead of prompt text.

---

## Constraints & Assumptions

- Single-machine, single-operator deployment (Windows 11 host; WSL2 or native both acceptable — architecture must not assume Unix-only primitives for the sandbox).
- AI inference is **purely local** (Ollama on the RTX 4080): zero cloud spend, zero external AI dependencies, works with the network cable unplugged. Cloud providers are explicitly out of scope for MVP.
- The LLM writes **Python plugins against a fixed API**; we do not attempt to evolve the engine itself in MVP.
- Assumption: a quantized local coder model (e.g. Qwen-coder class) is good enough to produce *viable candidates* when the contract is enforced by schema + validation + shadow testing — the pipeline compensates for weaker models by rejecting more.

## Risks & Open Questions

| Risk | Mitigation |
|---|---|
| Python sandboxing is famously leaky | Defense in depth: AST allowlist + subprocess isolation + resource limits + no-network workers + snapshots. Threat model is *accidental* damage (buggy AI code), not adversarial code. |
| Shadow-sim outcomes diverge from live outcomes | Shadow forks start from the *live* snapshot; fitness weights tuned against observed divergence; live outcome recorded and fed back. |
| Local model too weak → all candidates rejected | Cycle degrades gracefully to "no change + notebook entry"; escalation ladder stays local and keeps the same resident model: widen N, extend the repair round-trip, enrich generation context after K failed cycles. The pipeline is designed to compensate for weaker models by rejecting more — a fully-resident ~14B coder model is the sweet spot for the 16 GB GPU. |
| 3D scope inflates the sim core | Strata are discrete layers over a heightmap, not free-voxel space — full 3D *positions*, constrained 3D *world*, cheap to simulate. |
| Fitness function encodes the wrong goal | Fitness weights are config, logged per run; candidates are scored as deltas vs a no-candidate control shadow run each cycle; post-MVP A/B machinery planned. |
| Scalar Python plugin loops can't hit 60 tps at 10k entities | Honest benchmark split (≥ 2k scalar fauna + ~8k vectorized), bulk vectorized WorldAPI helpers taught to the LLM, per-species behavior cadence, and a Story 1.0 perf spike before targets are trusted. |
| GIL contention: sim thread + streaming + orchestrator share one process | Binary delta frames (encode ≈ NumPy pack, off the tick loop), measured ≥ 80% streaming-on gate, and a contained engine-process-split fallback behind the streamer interface. |
| Plugin state outside snapshots silently breaks shadow forks & rollback | Contract bans module-level mutable state (validator-enforced); all plugin state lives in entity props + snapshot-included `world.store`. |

---

## Next Steps

1. Review this brief; correct anything that mis-states the vision.
2. Proceed to PRD (`docs/prd.md`) — functional/non-functional requirements, epics, stories.
3. Proceed to Architecture (`docs/architecture.md`) — stack decisions, component design, plugin contract, schemas.
4. Lock an implementation gate before coding: (a) deterministic RNG policy for plugins (`world.rng` only), (b) benchmark protocol and fixtures for all KPI claims, (c) testable shadow-worker isolation checks.
5. Run the three Story 1.0 feasibility spikes (scalar-loop performance, local-model candidate generation against the real schema, Windows sandbox budgets) before building on the numbers — each is 1–2 days and each, if it fails, changes the architecture.
