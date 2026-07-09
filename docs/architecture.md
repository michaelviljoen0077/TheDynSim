# Genesis v2 Architecture Document

**Version:** 1.3
**Date:** 2026-07-07
**Author:** BMAD Architect (Claude) with Michael
**Inputs:** `docs/brief.md`, `docs/prd.md`

---

## Introduction

This document defines the full-stack architecture for Genesis v2: a self-evolving 3D artificial-life simulator where an LLM governor generates candidate world-plugins, tests them in sandboxed shadow simulations, promotes winners by measured fitness, and remembers everything. It is the single technical source of truth for implementation.

**Reference machine (design input):** Windows 11, RTX 4080 (16 GB VRAM), 64 GB RAM, 8+ core CPU. The architecture must exploit this (local GPU inference, parallel shadow workers) without hard-depending on it.

### Change Log

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-07 | 1.0 | Initial architecture from PRD v1.0 | Architect (Claude) |
| 2026-07-07 | 1.1 | Purely-local AI pivot: Anthropic cloud tier removed from MVP; Ollama is the sole generation tier; local-only escalation policy | Architect (Claude) |
| 2026-07-07 | 1.2 | Consistency/performance hardening: local-only component cleanup, deterministic RNG enforcement, benchmark protocol, testable worker isolation | Architect (Claude) |
| 2026-07-07 | 1.3 | Execution-hardening review: plugin state made snapshot-complete (`world.store`, module-state ban), command-buffer writes + generational entity IDs, intervention log & replayable determinism, pipelined cycle with honest generation-time budget, baseline control shadow run, binary position streaming from MVP, Windows budget enforcement named, epoch-aware history across rollbacks, WorldAPI quotas, MVP novelty definition, day-night visual polish, feasibility spikes | Architect (Claude) |

---

## High-Level Architecture

### Technical Summary

Genesis v2 is a local-first monorepo with four runtime domains: a **deterministic vectorized simulation engine** (Python/NumPy) hosting the live world; a **governor** process-pool that runs the evolution loop (observe → recall → generate → shadow-test → score → promote); a **FastAPI server** exposing REST control + WebSocket world-delta streaming; and a **React/Three.js Observatory** rendering the 3D world and the evolutionary story. Safety is structural: AI-generated plugins pass an AST validation gate and parallel sandboxed shadow simulations (isolated `spawn` processes with resource budgets, fed by byte-exact world snapshots) before a single line runs in the live process. All history — candidates, fitness scores, promotions, outcomes, plugin lineage — persists to SQLite.

### Architecture Diagram

```
┌─────────────────────────────  Browser (Observatory)  ─────────────────────────────┐
│  React + TS + Vite  │  three.js/r3f 3D world  │  Recharts  │  timeline / code lab │
└──────────────▲───────────────────────▲───────────────────────────▲────────────────┘
               │ WebSocket (deltas)    │ REST (control)            │ REST (history)
┌──────────────┴───────────────────────┴───────────────────────────┴────────────────┐
│                              FastAPI Server (server/)                             │
│      WorldStreamer      ControlAPI      HistoryAPI      GovernorAPI               │
└───────▲──────────────────────▲──────────────────────────────▲─────────────────────┘
        │ ring buffer          │ commands                     │ read
┌───────┴──────────────────────┴─────────┐      ┌─────────────┴─────────────────────┐
│         Live Engine (engine/)          │      │        SQLite (data/run.db)       │
│  fixed-tick loop · NumPy entity store  │      │  runs · cycles · candidates ·     │
│  spatial hash · strata/terrain/weather │      │  fitness · promotions · outcomes  │
│  flora · snapshots · plugin host       │      │  lineage · metrics time-series    │
└───────▲───────────────┬────────────────┘      └─────────────▲─────────────────────┘
        │ promote       │ snapshot                            │ write
┌───────┴───────────────▼────────────────────────────────────┴──────────────────────┐
│                            Governor (governor/)                                   │
│  Orchestrator → Reporter → Notebook.recall → Generator(LLM) → Validator(AST)      │
│      → ShadowPool (3–5 sandboxed worker processes) → FitnessEngine → Selector     │
│                                                                                   │
│  LLMProvider ──► OllamaProvider (local GPU: RTX 4080, Qwen-coder class — sole    │
│              │                   production tier; purely local)                   │
│              └─► ReplayProvider (recorded fixtures for tests/CI)                  │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### Architectural Patterns

- **Kernel + evolvable plugins:** the engine simulates only abiotic physics/ecology; *all* life behaviour above base flora lives in plugins — the surface the AI evolves. Contract is versioned and capability-scoped.
- **Generate–Test–Select (evolutionary loop):** N candidates per cycle, shadow-tested in parallel, promoted by explicit fitness. The LLM is the *mutation operator*, not the deployer.
- **Snapshot-based forking:** shadow sims and rollback both derive from one byte-exact snapshot mechanism — one code path, heavily tested, trusted everywhere.
- **Defense in depth (accidental-damage threat model):** schema-validated LLM output → AST allowlist gate → subprocess sandbox with budgets → quarantine → snapshot rollback. Each layer assumes the previous one leaked.
- **Structure-of-arrays ECS-lite:** entity state in NumPy arrays for vectorized per-tick updates; plugin API mediates row-level access so plugins stay simple while the core stays fast.
- **Command-buffer world mutations:** plugin writes (spawn/remove/move/props) are queued during `on_tick` and applied at tick end; reads see tick-start state. Iteration is always safe against mutation, plugin execution order is irrelevant to within-tick reads, and determinism proofs get dramatically simpler.
- **Replayable runs:** determinism is defined as *seed + intervention log ⇒ identical state*. Every promotion, rollback, and config change is recorded with its tick in an intervention log, so any run — including one steered live from the UI — can be replayed exactly. (Replay *export as video* stays post-MVP; replay of the sim itself falls out of this design.)
- **Event-sourced history:** every cycle appends immutable records; UI timeline, phylogeny, and the governor's own memory are all views over the same SQLite tables.

---

## Tech Stack

| Category | Technology | Version | Purpose | Rationale |
|---|---|---|---|---|
| Backend language | Python | 3.12+ | Engine, governor, server | LLM writes Python plugins; NumPy/multiprocessing ecosystem; team familiarity |
| Numerics | NumPy | 2.x | Vectorized entity store, terrain/weather fields | 10k+ entities at 60 tps without native code |
| API framework | FastAPI + uvicorn | latest | REST + WebSocket | Async-native, typed, already known from v1 |
| Validation/schemas | Pydantic | v2 | Config, reports, LLM output schemas, wire types | One schema system across API + LLM + DB boundaries |
| Persistence | SQLite (SQLAlchemy Core) | 3.45+ / 2.x | Notebook, lineage, metrics | Zero-ops, single-file, plenty for one machine |
| Snapshots | NumPy `.npz` + msgpack header | — | World state serialization | Fast, compact, byte-exact restore |
| Parallelism | `multiprocessing` (spawn) | stdlib | Shadow worker pool | Windows-compatible isolation boundary |
| LLM inference | Ollama HTTP API | latest | Sole generation tier (purely local) | Runs quantized 14B–32B coder models on the 4080; zero cloud dependency; schema-constrained decoding |
| Frontend framework | React + TypeScript | 18 / 5.x | Observatory UI | Ecosystem, typing |
| Build tool | Vite | 5.x | Frontend dev/build | Fast HMR |
| 3D rendering | three.js via @react-three/fiber + drei | latest | 3D world view | Declarative Three.js; InstancedMesh for 10k entities |
| UI state | Zustand | 4.x | Client state + WS ingestion | Minimal, performant with high-frequency updates |
| Charts | Recharts | 2.x | Metric time-series | Simple, adequate |
| Backend tests | pytest (+ hypothesis for determinism) | latest | Engine/governor tests | Property tests catch nondeterminism |
| Frontend tests | Vitest + Playwright | latest | Unit + smoke | Standard |
| Lint/format | ruff / prettier + eslint | latest | Consistency | Fast |

**Explicitly rejected:** unofficial Copilot endpoints (v1's tiers 2–3) — official, documented APIs only. **Cloud LLM tier in MVP** — purely local inference is a product goal (G4); the `LLMProvider` interface is designed so a cloud provider can be added post-MVP without touching the generation pipeline. Rust/C# engine rewrite — vectorized Python meets the perf targets and keeps the plugin language uniform. Vector DB for memory — recency + tag retrieval over SQLite is sufficient at MVP scale.

---

## World Model & Data Models

### World state (engine-owned)

- **Terrain:** heightmap `H[W,D]` (float32), water table `Wt[W,D]`, surface water mask, soil fertility `F[W,D]`, underground mineral/aquifer fields, sky wind/cloud fields. World size default 256×256 columns × 3 strata.
- **Strata:** discrete layers (UNDERGROUND=0, SURFACE=1, SKY=2) over continuous `(x, y)`; entity `z` is continuous within a stratum band. Full 3D positions, layered 3D world — cheap to simulate, real to render. *(Descoped: UNDERGROUND is kept as an internal enum value but the live world uses only SURFACE/SKY — see PRD FR2. The world is now a cube-sphere planet.)*
- **Weather:** temperature/humidity/wind/precipitation fields evolving per tick; day-night + season scalar clock.
- **Entities (SoA arrays):** `id, generation, species_id, pos(x,y,z), stratum, energy, age, alive, plugin_id` + a small float property bank per species (K named slots, mapped in species metadata) so plugins get custom state without dict-per-entity. Entity handles are **generational** (`id` + `generation`): the freelist recycles rows, and a stale handle held by a plugin (e.g. a remembered mate that died) fails a generation check loudly instead of silently addressing a recycled stranger.
- **Plugin state (snapshot-complete):** plugins hold *no* Python-side state. Besides entity prop slots, each plugin gets `world.store` — a small typed key-value store (str/int/float values, capped size) owned by the engine and serialized in every snapshot. Module-level mutable state is a validation failure (see validator rules); this is what makes shadow forks and rollback actually byte-faithful, not just world-array-faithful.

### Key domain records (Pydantic ⇄ SQLite)

- **Run** — id, seed, config JSON, started/ended, notes.
- **Intervention** — run_id, epoch, tick, kind (`promotion|rollback|quarantine|config_change`), plugin_id/details JSON. The replay log: seed + this table reproduces the run.
- **Cycle** — id, run_id, epoch, tick, observation report JSON, decision (`promoted|no_change|rolled_back`), timings, provider usage.
- **Candidate** — id, cycle_id, source code, meta (analysis, hypothesis, expected outcome, lineage_parent_id, confidence), validation result JSON, shadow metrics JSON, fitness sub-scores JSON, fitness total, fate (`promoted|rejected_validation|rejected_shadow|rejected_score`).
- **Plugin** — id, candidate_id, status (`live|quarantined|retired`), promoted_at_tick, quarantined reason.
- **Outcome** — cycle_id, expected vs measured deltas, verdict tag (`as_expected|better|worse|catastrophic`).
- **MetricSample** — run_id, epoch, tick, series name, value (downsampled tiers for long runs).

**Epochs:** rollback rewinds `tick`, making it non-monotonic within a run. Every rollback increments an `epoch` counter; all tick-keyed history (metrics, cycles, interventions, WS frames) is keyed `(epoch, tick)` so charts, outcome comparisons, and replay never see ambiguous duplicate ticks.
- **LineageEdge** — parent_plugin_id → child_candidate_id, edge type (`mutation|inspired_by`).

---

## Components

### engine/ — the world kernel
`Engine` (tick loop, RNG, clock) · `EntityStore` (SoA arrays, freelist with **generational IDs**, species registry) · `SpatialHash` (uniform grid per stratum) · `Terrain`, `Weather`, `Flora` (vectorized field systems) · `SnapshotService` (save/load/hash — covers entities, fields, tick/epoch, engine RNG, **every per-plugin RNG stream**, and **every plugin `world.store`**) · `PluginHost` (loads validated plugins, calls `setup`/`on_tick` in **deterministic promotion order** inside per-plugin error boundaries + time meters, quarantine logic; applies each plugin's command buffer at tick end) · `WorldAPI` (the *only* object plugins receive; capability-scoped facade over store/index/fields with per-plugin RNG seeded from `(run_seed, plugin_id)`, write-permission checks, deferred-write command buffer, and **quotas**: per-plugin entity cap, per-tick spawn cap, `world.store` size cap — violations return machine-readable errors the repair loop can consume) · `Reporter` (builds the observation report incl. death-cause ledger).

The engine is a pure library: no I/O, no globals, no awareness of server or governor. Both the live server and the shadow worker just instantiate it.

### governor/ — the evolution loop
`Orchestrator` (async state machine: idle → reporting → generating → validating → testing → scoring → committing; generation and shadow evaluation are **pipelined** — candidate k is shadow-testing while candidate k+1 generates, since the GPU generates while the CPUs simulate) · `Notebook` (SQLite read/write + relevance retrieval) · `Generator` (prompt assembly + N LLM calls + one repair round-trip on validation failure) · `Validator` (AST allowlist gate; machine-readable reasons) · `ShadowPool` (persistent pool of 3–5 spawn processes; job = snapshot path + **current live plugin set** + candidate source + tick budget; one slot per cycle runs the **baseline control** — same snapshot and plugin set, *no* candidate; enforces wall/memory/tick budgets; returns metrics or disqualification) · `FitnessEngine` (pure function: shadow metrics → sub-scores → weighted total, scored as **delta vs the control run**, not absolute) · `Selector` (must beat control + threshold, tie-break by stability, emits decision) · `llm/` (`LLMProvider` protocol, `OllamaProvider`, `ReplayProvider`, usage ledger).

### server/ — the front door
`ControlAPI` (start/pause/step/speed/reset/rollback/trigger-cycle/new-run) · `WorldStreamer` (drains engine delta ring-buffer → WebSocket at client rate; full sync on connect; slow clients dropped, never block the sim) · `HistoryAPI` (cycles, candidates, plugins, diffs, lineage, metrics) · `GovernorAPI` (cycle status, provider config, budget).

Process model: **one process** hosts engine loop (dedicated thread) + FastAPI (async event loop) + orchestrator (async task); shadow workers are child processes; SQLite writes serialized through a single writer.

**GIL discipline (load-bearing):** the tick loop, plugin `on_tick`, and frame encoding all contend for one GIL in this model. The tick loop's only streaming obligation is a cheap memcpy of dirty state into a preallocated ring-buffer slot; *all* encoding happens on the streamer side, and entity positions ship as **binary frames** (quantized arrays, near-zero encode cost) rather than per-entity JSON. NFR6's "a slow stage cannot stall the sim" is verified by a benchmark gate: streaming-on tick rate must stay ≥ 80% of headless tick rate under the benchmark protocol. If that gate fails, the fallback (engine in its own process with a shared-memory ring) is a contained change behind the same streamer interface.

### web/ — the Observatory
`WorldCanvas` (r3f scene: terrain mesh from heightmap, water plane, flora as instanced billboards/density texture, entities as per-species `InstancedMesh` updated from delta buffer; stratum toggles = layer groups + x-ray shader for underground) · `Hud` (tick/weather/counts/controls) · `ChartsPanel` · `EvolutionTimeline` + `CandidateCard` · `CodeLab` (source viewer, diff via `diff` lib, phylogeny via layered DAG layout) · `RunSetup` · `wsClient` (binary + JSON delta ingestion → Zustand → render refs, decoupled so React re-renders stay off the hot path).

**Visual identity (the "wow" is specified, not hoped for):** the sim clock drives the scene — sun direction/color and ambient light follow the day-night cycle, sky gradient and fog respond to weather and season, water is an animated shader plane, night renders with subtle emissive entity glow. One post-processing pass (selective bloom + vignette via `@react-three/postprocessing`) gives the instrument-panel look. All of it sits behind a quality toggle (High/Balanced/Performance) and is budgeted *inside* NFR8's 30 FPS floor — polish that costs the frame rate gets cut by the toggle, not shipped broken. Entity motion is interpolated between 10 Hz deltas so movement reads smooth at 60 FPS render.

---

## Core Workflow: one evolution cycle

```
Orchestrator          Engine              LLM                ShadowPool           Notebook/DB
     │  every T ticks   │                   │                     │                    │
     ├─ report ────────►│ Reporter          │                     │                    │
     ├─ recall ─────────┼───────────────────┼─────────────────────┼───────────────────►│
     ├─ generate(N) ────┼──────────────────►│ (parallel, schema)  │                    │
     ├─ validate (AST) — repair round-trip on failure ──►│        │                    │
     ├─ snapshot ──────►│ SnapshotService   │                     │                    │
     ├─ evaluate ───────┼───────────────────┼────────────────────►│ N workers, budgets │
     ├─ score + select (FitnessEngine)      │                     │                    │
     ├─ promote winner ►│ PluginHost.load   │                     │                    │
     └─ record all ─────┼───────────────────┼─────────────────────┼───────────────────►│
        (next cycle: measure outcome of this promotion → append)
```

**Pipelining (the real wall-time budget):** generation and shadow evaluation overlap — as soon as candidate 1 passes validation it enters a shadow worker while candidate 2 is still generating (the GPU and the CPU pool are independent resources). The baseline **control run** (same snapshot + live plugins, no candidate) launches first, so every candidate's fitness is a measured delta against "do nothing." At ~40 tok/s a candidate costs ~50–75 s to generate, so a 3-candidate cycle is ≈ `max(3 × generation, shadow batch) + tail` ≈ **≤ 5 min end-to-end**, not the naive `generation + shadow` sum.

Failure semantics: any stage failing degrades to `no_change` + full record; the live sim never waits on the cycle (async, own executor); LLM timeouts/budget-cap breaches abort generation only. A control-run crash means a *live* plugin is failing in shadow conditions — recorded as its own notebook finding, never blamed on a candidate.

---

## Plugin Contract (v1)

```python
PLUGIN_META = {
    "name": "burrowing_vole",
    "contract": 1,
    "species": ["vole"],              # entity types this plugin owns
    "lineage_parent": "plugin_0042",  # or None
}

def setup(world):
    """Called once on promotion. Register species, spawn initial population."""
    world.register_species("vole", shape="circle", size=5, color="#8a6f4d",
                           glyph="🐀", strata=[world.UNDERGROUND, world.SURFACE],
                           props=["hunger", "burrow_x", "burrow_y"])
    for _ in range(30):
        x, y = world.random_surface_point()
        world.spawn("vole", x=x, y=y, stratum=world.SURFACE, energy=150)

def on_tick(world):
    """Called every tick. Only entities of owned species are writable."""
    for vole in world.entities("vole"):
        food = world.nearest(vole, kind="flora", radius=25)
        ...
```

`WorldAPI` capabilities: `register_species`, `spawn`, `remove`, `entities(species)`, `nearest`/`within(radius)` (spatial hash), `move`, property get/set (declared slots only), environment reads (`weather()`, `flora_at`, `water_at`, `height_at`, `season()`), `world.rng` (per-plugin, seeded from `(run_seed, plugin_id)`), `world.store` (plugin-scoped persistent key-value state, snapshot-included), stratum constants and mediated `set_stratum`, plus **bulk vectorized helpers** (`world.positions(species)`, batched `world.nearest_many`, array-valued environment sampling) so hot loops over large populations don't have to be scalar Python. **Not exposed:** other plugins' species writes, engine internals, I/O of any kind, unbounded iteration helpers.

**Write semantics (command buffer):** all mutating calls (`spawn`, `remove`, `move`, property sets, `set_stratum`) are *deferred* — queued during `on_tick` and applied by the engine at tick end. Reads always see tick-start state, so iterating `world.entities(...)` while spawning/removing is safe by construction (the single most common LLM-generated bug class in v1). Entity handles are generational; operating on a stale handle is a recorded no-op, not corruption.

**Quotas (enforced by the API, not just worker budgets):** per-plugin live-entity cap, per-tick spawn cap, `world.store` size cap, per-species prop-slot count. Violations return machine-readable errors (surfaced to the repair round-trip and the notebook) instead of letting a runaway `setup()` meet the OOM killer.

**Validator allowlist:** imports {`math`, `typing`}; banned nodes: `Import`/`ImportFrom` outside allowlist, `exec`/`eval`/`compile`/`__import__`/`open`, attribute access to dunders, direct calls to module/global RNG APIs (`random.*`, `numpy.random.*`), `global`/`nonlocal` anywhere, `while True` without a validator-visible bound (soft warning → shadow budgets are the hard stop). Plugins must use `world.rng` for all stochastic behavior.

**Module top-level is declarations only:** the only statements allowed at module level are the allowlisted imports, `PLUGIN_META`, and `def`. Any other module-level assignment or mutable container is a validation failure — plugin state lives exclusively in entity prop slots and `world.store`, which is what keeps snapshots, shadow forks, and rollback *complete*. (A plugin that counts ticks in a module global would silently desynchronize from every restored snapshot.)

v1's prompt "HARD RULES" become: contract enforcement (rules 3,6,10), validator checks (rules 1,2), WorldAPI design (rules 4,5,9), fitness function (rules 7,8) — machinery, not prose.

---

## LLM Layer

### Provider interface

```python
class LLMProvider(Protocol):
    def generate_candidate(self, ctx: GenerationContext) -> CandidateProposal: ...
    # CandidateProposal is a Pydantic model; providers must return schema-valid data
```

### Purely local by design

All production inference runs on the operator's GPU via Ollama. No cloud SDK ships in MVP; no API keys, no network dependency, no per-token cost. The `LLMProvider` protocol is the seam where a cloud tier could be added post-MVP — nothing downstream of it may know which provider produced a candidate.

### OllamaProvider (sole generation tier)

- Ollama HTTP `/api/chat` with `format: <json schema>` for constrained decoding — schema-guaranteed structure, no regex parsing anywhere in the pipeline.
- **Single resident model by design:** one configured model per run, sized to stay fully on the 4080's 16 GB VRAM (Qwen-coder-class ~14B at Q5 ≈ 10–11 GB, ~40+ tok/s, leaving headroom for context KV cache). Ollama `keep_alive` pins it warm for the run's lifetime — no load/unload churn between cycles, no KV-cache eviction, no CPU-offload penalty. Model choice is a run-config setting, changed only between runs.
- JSON repair-retry loop (bounded, 2 attempts) since local schema adherence is weaker than constrained decoding promises on edge cases; failures recorded, never propagated as free text.
- Candidates generated sequentially (VRAM-bound); **generation is the dominant cycle cost, not shadow testing** — at ~40 tok/s a candidate (analysis + hypothesis + ~100 lines of code) is ~2,000–3,000 output tokens ≈ 50–75 s, so 3 candidates ≈ 2.5–4 min sequential. The cycle stays inside its ≤ 5 min envelope only because generation and shadow evaluation are *pipelined* (candidate k shadow-tests while k+1 generates); the per-cycle generation time budget (NFR10) is the hard stop.
- Startup check: configured model present in the Ollama library, with a pull prompt if missing; degraded-mode banner if Ollama itself is unreachable.
- Weak-model compensation is a pipeline property, not a prompt property: schema constraints + validation repair round-trip + shadow testing + fitness threshold mean a weaker model simply gets more candidates rejected — the live world never sees the difference.

### ReplayProvider

Records/replays real provider transcripts as fixtures → whole-loop tests run offline and deterministic in CI.

**Escalation policy (config):** after K consecutive no-promotion cycles, widen N, extend repair round-trips, and raise per-candidate context (more notebook history, more targeted failure feedback) — all with the same resident model. Escalation never swaps models mid-run: a 14B-class model's weakness is compensated by more attempts through the rejection pipeline, which is cheaper than paying VRAM eviction + reload (or 32B CPU-offload) every escalated cycle.

---

## Sandbox & Safety Design

| Layer | Mechanism | Catches |
|---|---|---|
| 1. Schema | Structured output validation | Malformed/free-text responses |
| 2. Static gate | AST allowlist validator | I/O, imports, eval, contract violations |
| 3. Shadow sandbox | `spawn` subprocess with worker bootstrap socket-block (deny outbound socket creation/connect in worker runtime), outbound-attempt audit event, scratch-dir-only writes, budgets: wall 60 s, RSS 1 GB, per-tick 50 ms. **Windows enforcement is named, not assumed:** `resource.setrlimit` does not exist on Windows — budgets are enforced by a parent-side watchdog (psutil) polling worker RSS + wall-clock at ~250 ms and hard-killing on breach; per-tick time is metered in-worker. | Crashes, hangs, memory bombs, accidental network use, ecological failure |
| 4. Fitness threshold | Promotion requires score ≥ threshold | "Runs but harms the world" |
| 5. Live containment | Per-plugin error boundary + time meter → quarantine | Latent runtime failures |
| 6. Rollback | Pre-promotion snapshot, < 5 s restore | Everything above leaking |

Threat model is **buggy**, not adversarial, code (single-operator local tool). Documented explicitly so nobody mistakes layer 3 for a security boundary against a malicious actor.

---

## Database Schema (SQLite)

```sql
CREATE TABLE runs      (id TEXT PK, seed INTEGER, config JSON, created_at, ended_at, notes TEXT);
CREATE TABLE interventions (run_id REF, epoch INTEGER, tick INTEGER, kind TEXT,  -- promotion|rollback|quarantine|config_change
                        plugin_id REF NULL, details JSON, created_at);           -- the replay log
CREATE TABLE cycles    (id TEXT PK, run_id REF, epoch INTEGER, tick INTEGER, report JSON, decision TEXT,
                        provider TEXT, tokens_in INTEGER, tokens_out INTEGER,
                        started_at, finished_at);
CREATE TABLE candidates(id TEXT PK, cycle_id REF, source TEXT, meta JSON,
                        validation JSON, shadow_metrics JSON,
                        fitness_breakdown JSON, fitness REAL, fate TEXT);
CREATE TABLE plugins   (id TEXT PK, candidate_id REF, status TEXT,
                        promoted_tick INTEGER, quarantine_reason TEXT);
CREATE TABLE lineage   (parent_plugin_id REF, child_candidate_id REF, kind TEXT);
CREATE TABLE outcomes  (cycle_id REF PK, expected JSON, measured JSON, verdict TEXT);
CREATE TABLE metrics   (run_id REF, epoch INTEGER, tick INTEGER, series TEXT, value REAL);
CREATE INDEX metrics_series ON metrics(run_id, series, epoch, tick);
```

SQLite runs in WAL mode with one writer task; metric inserts are batched per second, not per tick. Snapshots live on disk: `data/snapshots/<run>/<epoch>-<tick>.npz` (retention: last 10 + every promotion). Snapshots include entities, fields, tick/epoch, engine RNG state, all per-plugin RNG streams, and all plugin `world.store` contents — a restore is *complete*, not world-arrays-only.

---

## Wire Protocols

**WebSocket world stream** (hybrid binary + JSON from MVP — *not* deferred as an upgrade path; retrofitting the wire format later touches engine, streamer, and client at once):
- On connect: JSON `{"t":"sync", epoch, tick, terrain_ref, species[], fields{}}` (terrain sent once as a binary side-fetch), followed by a binary full-entity frame.
- Per frame (default 10 Hz): one **binary frame** for the hot data — packed arrays of `(id:uint32, gen:uint16, species:uint16, x/y/z:uint16-quantized, energy:uint8)` for spawned/moved entities plus a removed-id array — and a small JSON frame for cold data (`props_changed`, `fields_dirty`, `metrics`, epoch/tick header). Encode cost on the server is a NumPy pack, not 10k JSON objects; decode on the client is a `DataView` walk into preallocated typed arrays feeding `InstancedMesh` directly.
- Client interpolates positions between frames for smooth 60 FPS motion from a 10 Hz stream.
- Server drops frames for slow clients (latest-wins), never buffers unboundedly.

**REST:** `/api/control/*`, `/api/runs`, `/api/cycles`, `/api/candidates/:id`, `/api/plugins/:id(/diff)`, `/api/lineage`, `/api/metrics?series=…`, `/api/governor/(status|config)`.

---

## Source Tree

```
evosquared/
├── docs/                    # brief.md, prd.md, architecture.md
├── engine/                  # world kernel (pure library)
│   ├── core.py              # Engine, tick loop, clock, RNG
│   ├── entities.py          # EntityStore (SoA), species registry
│   ├── spatial.py           # SpatialHash
│   ├── fields.py            # Terrain, Weather, Flora
│   ├── snapshot.py          # SnapshotService
│   ├── plugin_host.py       # PluginHost, quarantine
│   ├── world_api.py         # WorldAPI (plugin capability surface)
│   └── reporter.py          # observation report + death ledger
├── governor/
│   ├── orchestrator.py
│   ├── notebook.py          # SQLite memory + retrieval
│   ├── generator.py         # prompt assembly, N-candidate generation
│   ├── validator.py         # AST gate
│   ├── shadow.py            # ShadowPool + worker entrypoint
│   ├── fitness.py           # FitnessEngine (pure)
│   └── llm/                 # provider protocol, ollama.py, replay.py
├── server/
│   ├── app.py               # FastAPI wiring, lifespan, threads
│   ├── control.py  history.py  streamer.py
│   └── schemas.py           # wire Pydantic models
├── web/
│   └── src/
│       ├── world/           # WorldCanvas, instancing, strata, inspector
│       ├── panels/          # Hud, Charts, Timeline, CodeLab, RunSetup
│       ├── state/           # Zustand stores, wsClient
│       └── api/
├── data/                    # run.db, snapshots/ (gitignored)
├── plugins_examples/        # hand-written reference plugins (Epic 2)
├── tests/                   # engine/, governor/, integration/
└── legacy/                  # v1 code, frozen
```

---

## Performance Engineering (reference machine)

- **Live sim:** vectorized field updates (whole-array NumPy ops); plugin `on_tick` is the scalar hot path and **the honest bottleneck**: 10k scalar entities × per-entity spatial queries × 60 tps ≈ 600k Python-level calls/s, which CPython will not do. The design answer is threefold: (1) WorldAPI bulk helpers (`positions()`, `nearest_many`, array environment sampling) so well-written plugins vectorize their hot loops — the LLM prompt and API reference teach this idiom; (2) per-species **behavior cadence** in the contract (a species may act every K ticks, K=1–4) to amortize scalar cost; (3) the benchmark's entity split (below) states the scalar-fauna number the 60 tps claim is actually made for. The per-plugin time meter surfaces slow plugins (also a fitness penalty in shadow runs).
- **Shadow pool:** 4 workers × ~1.5 GB peak ≈ 6 GB — trivial inside 64 GB; workers persist across cycles (spawn cost paid once); snapshots passed by file path, not pipe.
- **Local LLM:** 14B Q5 fully resident in 16 GB VRAM alongside nothing else (sim is CPU-bound) — GPU is otherwise idle, so inference is "free" concurrency.
- **Rendering:** one `InstancedMesh` per species (matrix + color attribute updates only), terrain as static geometry, flora as a density texture on the terrain material — 10k entities ≈ 20 draw calls.
- **Budgets:** engine tick ≤ 16 ms at 10k entities; report ≤ 100 ms; snapshot ≤ 2 s; delta frame encode ≤ 5 ms.

### Benchmark Protocol (authoritative)

- **Fixture world:** 256x256 columns, three strata enabled, deterministic seed `424242`, weather/flora active.
- **Entity workload:** 10,000 entities, at least 6 species, mixed movement and interaction profiles (grazer/predator/flyer/burrower/environmental engineer/static flora-like agent). **Fixed split:** ≥ 2,000 scalar plugin-driven fauna (per-entity Python logic with spatial queries) + ~8,000 engine-vectorized or bulk-API-driven entities. The 60 tps target is defined against this split; a benchmark that hits 60 tps with vectorized-only load proves nothing about Epic 2.
- **Plugin workload:** one baseline stable plugin set for engine/stream tests; one cycle-test set with 4 candidates x 2,000 shadow ticks.
- **Streaming profile:** headless benchmark at 0 Hz stream; streaming benchmark at 10 Hz delta push to one client. **GIL-contention gate:** streaming-on tick rate must be ≥ 80% of headless tick rate; failing this gate triggers the engine-process split fallback, not a target renegotiation.
- **Shadow-horizon rule:** shadow tick budget must cover ≥ 1 full day-night cycle and ≥ 1 season transition of the configured clock (else a candidate that starves every winter promotes cleanly and dies in production); config validation rejects combinations that violate this.
- **Timing method:** 30 s warmup, then 180 s measurement window. Report p50/p95 tick-time and effective ticks/s.
- **Pass criteria:** must satisfy PRD targets for FR4/NFR2/NFR8 under this exact protocol; deviations require a new versioned protocol file.

---

## Error Handling & Observability

- Structured logging (`structlog`-style JSON) with cycle-id/candidate-id correlation across engine, governor, workers.
- Every degradation is a *recorded decision*: validation failure, shadow disqualification, low fitness, quarantine, rollback — all land in SQLite and surface on the timeline. No silent failure paths.
- Watchdogs: sim-loop heartbeat, cycle-stage timeouts, WS backpressure counters — exposed at `/api/health`.

## Testing Strategy

- **Determinism suite (first-class):** same-seed state-hash equality at 10k ticks; snapshot/restore round-trip hashes (including per-plugin RNG streams and `world.store`); **replay test**: a run with mid-run promotions/rollbacks, re-executed from seed + intervention log, reaches identical state hashes; runs in CI on every change to `engine/`.
- **Validator battery:** fixture corpus of hostile/broken plugins (from v1's failure history) — all must be rejected with correct machine-readable reasons.
- **Shadow-pool torture tests:** infinite loop, memory bomb, crash, fork-bomb-ish plugin — pool must contain and survive all.
- **Isolation tests:** worker attempts outbound socket connect and write outside scratch dir; both must fail and produce machine-readable disqualification reasons.
- **Loop integration:** full cycle against `ReplayProvider` fixtures, asserting notebook rows and promotion behavior; 8-hour soak test (accelerated) before release milestones.
- **Frontend:** Vitest units for delta-ingestion reducer; Playwright smoke (connect → see world → open timeline).

## Coding Standards (critical rules for AI+human contributors)

1. Engine is pure: no I/O, no globals, no imports from `server/` or `governor/`.
2. Plugins receive **only** `WorldAPI`; never widen its surface without updating validator, docs, and LLM context together (they must stay in lockstep).
3. All cross-boundary data (LLM, WS, DB JSON columns) goes through Pydantic models — no raw dicts.
4. Every random draw goes through the engine RNG (or plugin-scoped RNG); `random.random()` in engine code is a determinism bug.
5. Any new failure path must write a record (notebook or log) — silent `except: pass` is banned.
6. All plugin-visible mutations go through the command buffer; engine code must never expose a write path that applies mid-tick.
7. Anything keyed by tick must also carry epoch; a bare tick key in a new table or protocol field is a review-blocking bug.
8. State that survives a tick must live in a snapshot-covered structure (entity arrays, fields, `world.store`, RNG streams); if snapshot/restore wouldn't reproduce it, it doesn't ship.

---

## Next Steps

1. Operator review of all three documents; adjust fitness weights / world defaults as taste dictates.
2. **Run the three feasibility spikes (Story 1.0) before building on the numbers:** (a) scalar-plugin perf spike — SoA store + spatial hash + a Python per-entity loop, measured at 2k/5k/10k entities on the reference machine, to set FR4's honest split; (b) LLM spike — Ollama + the chosen Qwen-coder model + the real `CandidateProposal` schema generating a working grazer plugin, measuring tok/s, schema-adherence and repair-loop rates; (c) sandbox spike — spawn worker on Windows with psutil watchdog budgets killing an infinite loop and a memory bomb, socket-block verified. Each spike is 1–2 days and, if it fails, changes the architecture — buy the information first.
3. Begin Epic 1 Story 1.1 (scaffold). Recommended first milestone: Stories 1.1–1.4 (deterministic core + snapshots) since everything else trusts them.
4. Optional: shard this document + PRD epics into per-story implementation files if using BMAD's SM/dev-agent flow.
