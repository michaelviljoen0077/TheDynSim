# Genesis v2 Product Requirements Document (PRD)

**Version:** 1.3
**Date:** 2026-07-07
**Author:** BMAD PM (Claude) with Michael
**Input:** `docs/brief.md`

---

## Goals and Background Context

### Goals

- Deliver a fully 3D layered artificial-life world (underground / surface / sky) that runs deterministically and fast on a single high-end desktop.
- Replace v1's "generate and pray" loop with an evolutionary pipeline: multi-candidate generation → sandboxed shadow testing → fitness selection → promotion → outcome feedback.
- Guarantee no unvalidated AI code ever executes in the live simulation process.
- Give the governor persistent memory (lab notebook + plugin lineage) so it learns across cycles and across runs.
- Run fully offline on purely local GPU inference (Ollama on the RTX 4080); no cloud AI dependency in MVP.
- Make the whole evolutionary story observable: what was tried, what scored what, what was promoted, and whether it worked.

### Background Context

Genesis v1 (this repository) proved the concept of an LLM evolving a live ecosystem by writing plugins, and equally proved that shipping untested generated code into a live world produces perpetual collapse. Its accumulated prompt rules are a fossil record of every bug class encountered. v2 converts those lessons from prompt text into *enforced machinery*: validation rules, sandbox boundaries, fitness metrics, and a memory the AI actually consults. Full details and lessons learned: `docs/brief.md`.

### Change Log

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-07-07 | 1.0 | Initial PRD from approved brief | PM (Claude) |
| 2026-07-07 | 1.1 | Pivot to purely local AI: cloud provider removed from MVP scope (FR18, NFR10, Stories 3.3/3.6/4.5) | PM (Claude) |
| 2026-07-07 | 1.2 | Consistency/performance hardening: deterministic plugin RNG rule, testable worker isolation wording, benchmark protocol requirement | PM (Claude) |
| 2026-07-07 | 1.3 | Execution-hardening review: snapshot-complete plugin state, command-buffer writes, generational IDs, intervention log & replay, baseline control run, pipelined cycle budget, binary streaming, Windows budget mechanism, epoch-aware history, quotas, novelty definition, feasibility spikes (Story 1.0), baseline-ecology soak (Story 2.5), visual polish ACs | PM (Claude) |

---

## Requirements

### Functional Requirements

**World & Simulation**

- **FR1:** The simulation runs a fixed-tick, deterministic loop from a seeded RNG. Because promotions, rollbacks, and config changes land at operator/governor-driven moments, every such event is recorded in an **intervention log** (tick, epoch, kind, plugin); determinism is defined as *identical seed + identical intervention log ⇒ identical world state at tick N*, which makes any run — including one steered live — exactly replayable.
- **FR2:** The world comprises three strata — underground, surface, sky — over a procedurally generated terrain heightmap with water bodies; entities have continuous `(x, y, z)` positions and a stratum attribute.
- **FR3:** The engine natively simulates the abiotic base layer (weather system, water cycle, base flora growth/decay, day-night & season cycle); all fauna and higher flora behaviour comes from plugins.
- **FR4:** The engine supports ≥ 10,000 concurrently active entities at ≥ 60 headless ticks/s on the reference machine, under the benchmark protocol's fixed workload split: ≥ 2,000 scalar plugin-driven fauna + ~8,000 engine-vectorized/bulk-API entities (the scalar plugin path is the honest bottleneck; the target is meaningless without the split).
- **FR5:** Full world state can be snapshotted to disk and restored byte-identically — including engine RNG state, every per-plugin RNG stream, and every plugin's `world.store` (a restore is complete, not world-arrays-only); snapshots are taken automatically before every promotion.
- **FR6:** The engine can run in *shadow mode*: headless, accelerated, from a supplied snapshot, in an isolated worker process, reporting metrics without any UI or persistence side effects.

**Plugin System**

- **FR7:** Exactly one versioned plugin contract exists (`setup(world)` / `on_tick(world)` against a capability-scoped API object); the engine refuses plugins declaring an unknown contract version.
- **FR8:** Plugins interact with the world only through the provided API object (spawn, query via spatial index, move, modify properties, remove, read environment, plugin-scoped persistent `world.store`, bulk vectorized query helpers); direct access to engine internals is not exposed. All mutating calls are **deferred via a command buffer** (applied at tick end; reads see tick-start state) so iteration during mutation is safe by construction; entity handles are generational so stale handles fail loudly. The API enforces **quotas** (per-plugin entity cap, per-tick spawn cap, store size cap) with machine-readable violations.
- **FR9:** The plugin loader enforces static validation before any execution: AST parse, banned-construct check (imports outside allowlist, file/network/process access, `eval`/`exec`), contract conformance, per-plugin declared entity types, determinism guardrails (plugins may use only `world.rng` for randomness), and a **module-top-level rule**: only allowlisted imports, `PLUGIN_META`, and function definitions — no module-level mutable state, because state outside snapshots breaks shadow forks and rollback.
- **FR10:** A promoted plugin that raises repeatedly at runtime is quarantined (hooks disabled) and the event is recorded in the notebook; the operator can trigger rollback to the pre-promotion snapshot.

**Meta-AI Evolution Loop**

- **FR11:** Each cycle produces a structured observation report: populations by species and stratum, birth/death counts *by cause* (starvation, predation, age, plugin error), resource levels, weather, spatial summaries, and trend deltas since the last cycle.
- **FR12:** The governor retrieves relevant lab-notebook entries (prior experiments, outcomes, active lineages) and includes them in generation context.
- **FR13:** The LLM adapter produces N (configurable, default 3) candidate plugins per cycle as schema-validated structured output (analysis, hypothesis, plugin source, expected outcome, lineage parent if mutating an existing plugin).
- **FR14:** Each candidate passing static validation is evaluated in its own shadow simulation (default 2,000+ ticks from the live snapshot **plus the current live plugin set**; the shadow horizon must cover ≥ 1 day-night cycle and ≥ 1 season transition) under CPU-time and memory budgets; candidates that crash, stall, or violate limits are disqualified with the reason recorded. Each cycle also runs a **baseline control shadow** (same snapshot and plugin set, no candidate) so scores are deltas against "do nothing" and a failing *live* plugin is never misattributed to a candidate.
- **FR15:** Surviving candidates are scored by a configurable fitness function combining at minimum: species diversity (Shannon index), population stability (low volatility, no extinctions), trophic balance, and novelty — all computed **relative to the control run**; scores and sub-scores are persisted per candidate. Novelty has a concrete MVP definition: species-set overlap with existing plugins + AST-normalized source similarity to prior promoted plugins (an undefined novelty score is a random number the selector would trust).
- **FR16:** The highest-scoring candidate that beats the control run by at least the promotion threshold is hot-loaded into the live world; if none qualifies, the cycle records "no change" with rationale. Every candidate (promoted or rejected) is written to the lab notebook with its full artifact chain.
- **FR17:** At the following cycle, the live outcome of the last promotion is measured against its expected outcome and appended to that notebook entry (closing the feedback loop).
- **FR18:** The LLM adapter exposes a provider-agnostic interface; MVP ships two implementations — `OllamaProvider` (local GPU inference, the sole production tier) and `ReplayProvider` (recorded fixtures for tests/CI) — selectable per run, with per-call logging of provider, model, latency, and token usage. The interface must allow adding a cloud provider post-MVP without changes to the generation pipeline.

**Observatory (Frontend)**

- **FR19:** A browser UI renders the live world in 3D (orbit/pan/zoom camera) with per-stratum visibility toggles, species legend with show/hide, and click-to-inspect entity details.
- **FR20:** The UI streams world updates over WebSocket as deltas at a configurable rate decoupled from the sim tick rate; entity position/spawn/remove data ships as **binary frames** (quantized typed arrays) from MVP, with JSON reserved for cold metadata — encode cost must stay off the sim hot path, and the client interpolates between frames for smooth motion.
- **FR21:** Live charts display population per species, diversity index, and fitness-relevant metrics over time.
- **FR22:** An intervention timeline lists every evolution cycle: analysis, candidates with fitness scores, the promotion decision, and (once known) the measured outcome.
- **FR23:** A plugin browser shows the code of every plugin (live, rejected, retired), a diff view against its lineage parent, and a phylogeny tree of plugin ancestry.
- **FR24:** Run controls: start/pause/step, simulation speed, trigger-evolution-now, rollback-to-snapshot, and new-run (seed + config).

### Non-Functional Requirements

- **NFR1:** No AI-generated code executes in the live server process until it has passed static validation *and* shadow evaluation; shadow workers run with network access disabled by worker bootstrap controls (socket-blocking plus outbound-attempt audit) and no filesystem write access outside their scratch directory.
- **NFR2:** A full evolution cycle (generation + validation + shadow evaluation + scoring) completes in ≤ 5 minutes wall-time on the reference machine (RTX 4080, 64 GB RAM). Generation is the dominant cost at local token rates (~50–75 s per candidate at ~40 tok/s), so generation and shadow evaluation must be **pipelined** (candidate k shadow-tests while k+1 generates); the shadow batch itself completes in ≤ 3 minutes using parallel worker processes.
- **NFR3:** The system is fully functional with zero internet connectivity when a local model is installed; no unofficial/undocumented third-party APIs anywhere in the stack.
- **NFR4:** All LLM interactions use structured output with schema validation; a malformed response is retried (bounded) and then logged as a failed generation — it can never inject free text into the pipeline.
- **NFR5:** All run artifacts (notebook, lineage, metrics, snapshots, config, seeds, intervention log) persist across process restarts; a run can be resumed. Rollback makes tick non-monotonic, so all tick-keyed history carries an **epoch counter** (incremented per rollback) — charts, outcomes, and replay never see ambiguous duplicate ticks.
- **NFR6:** The live sim loop, the WebSocket streamer, the shadow-evaluation pool, and LLM calls are isolated such that a stall in any one cannot freeze the others (process/thread boundaries, timeouts everywhere). Because sim thread, server, and orchestrator share one Python process (and one GIL), this is verified by measurement, not assertion: the benchmark's streaming-on tick rate must be ≥ 80% of headless.
- **NFR7:** Works on Windows 11 natively (multiprocessing spawn-safe, no Unix-only syscalls in core paths). Shadow-worker budgets are enforced by a named Windows-compatible mechanism: parent-side watchdog (psutil) polling worker RSS and wall-clock with hard-kill on breach; in-worker per-tick metering (`resource.setrlimit` does not exist on Windows and must not be assumed).
- **NFR8:** Frontend maintains ≥ 30 FPS rendering 10,000 entities via instanced meshes on the reference GPU.
- **NFR9:** Rollback from a bad promotion restores the live world in < 5 seconds.
- **NFR10:** LLM usage is visible: cumulative tokens, generation latency, and per-cycle model/settings displayed in the UI; a per-cycle generation time budget in config keeps slow local inference from stalling the cadence.
- **NFR11:** Performance and throughput claims are validated against a fixed benchmark protocol (world size, species mix, stream rate, warmup, run length, percentile metric) checked into the repo and run in CI/nightly.

---

## UI Design Goals

- **Overall vision:** "Mission control for a living world" — a dark, instrument-panel aesthetic where the 3D world is the hero and the evolutionary story is one glance away.
- **Key screens:** (1) World view (3D canvas + overlay HUD of key metrics), (2) Evolution panel (timeline + candidate cards with fitness breakdowns), (3) Code lab (plugin browser, diff, phylogeny), (4) Run setup/settings.
- **Interaction paradigms:** orbitable 3D camera with sensible defaults; stratum toggles as layered "x-ray" controls; timeline entries expand into full candidate detail; everything the AI writes is one click from readable.
- **Accessibility:** keyboard shortcuts for sim controls; charts readable in both dark and light contexts; not targeting WCAG certification for MVP.
- **Target platform:** desktop browsers (Chromium primary), WebGL2 required.

---

## Technical Assumptions

- **Repository:** monorepo (`engine/`, `governor/`, `server/`, `web/`, `docs/`), replacing the v1 layout; v1 code retired to `legacy/` or a git tag.
- **Backend:** Python 3.12+, FastAPI + uvicorn, NumPy-vectorized entity storage, `multiprocessing` (spawn) for shadow workers, SQLite via SQLAlchemy Core.
- **Frontend:** React 18 + TypeScript + Vite, react-three-fiber (Three.js) with instancing, Zustand for state, Recharts for charts.
- **LLM:** **purely local** — Ollama HTTP API with JSON-schema-constrained decoding (target: Qwen-coder-class 14B–32B quantized on the RTX 4080); single `LLMProvider` interface so a cloud tier can be added post-MVP without pipeline changes. No cloud SDKs ship in MVP.
- **Testing:** pytest for engine/governor (determinism tests are first-class); Vitest + Playwright smoke for web; a recorded "canned LLM" fixture provider so the whole loop is testable offline and in CI.
- **No cloud infrastructure:** local desktop deployment only; `docker compose` optional convenience, not required.

---

## Epic List

| # | Epic | Goal |
|---|---|---|
| 1 | **World Engine & Foundation** | Deterministic 3D layered simulation core with snapshots, metrics, streaming server, and a first visible 3D world. |
| 2 | **Plugin Runtime & Safety Pipeline** | One plugin contract, static validation, sandboxed shadow execution, quarantine and rollback — the safety spine everything else trusts. |
| 3 | **Meta-AI Evolution Loop** | Observation → recall → N candidates → shadow evaluation → fitness selection → promotion → outcome feedback, with persistent lab notebook and lineage. |
| 4 | **The Observatory** | Full mission-control frontend: 3D world, charts, evolution timeline, code lab, run controls. |

Epics are sequential but each delivers something runnable: Epic 1 ends with a watchable abiotic world; Epic 2 with hand-written plugins running safely; Epic 3 with the AI evolving the world headlessly; Epic 4 with the full experience.

---

## Epic 1 — World Engine & Foundation

**Goal:** A deterministic, performant 3D simulation core you can watch in the browser, with the persistence and metrics plumbing every later epic depends on.

### Story 1.0 — Feasibility spikes (before building on the numbers)
As the operator, I want the three riskiest bets measured on the real machine in days, so that the architecture is corrected cheaply if a bet fails.

**Acceptance Criteria**
1. **Perf spike:** throwaway SoA store + spatial hash + scalar Python per-entity loop with neighbor queries; measured ticks/s at 2k / 5k / 10k scalar entities on the reference machine; result recorded and FR4's workload split confirmed or adjusted.
2. **LLM spike:** Ollama + the chosen Qwen-coder-class model + the real `CandidateProposal` JSON schema generates a working grazer plugin; tok/s, schema-adherence rate, and repair-loop frequency recorded (validates the spec's single most load-bearing assumption).
3. **Sandbox spike:** Windows `spawn` worker with parent-side psutil watchdog budgets demonstrably kills an infinite loop and a memory bomb; socket-block verified.
4. Each spike's findings written up (1 page each) with a go/adjust decision; spike code is disposable and does not enter the main tree.

### Story 1.1 — Project scaffold & dev loop
As the operator, I want a clean monorepo with one-command dev startup, so that iteration is fast from day one.

**Acceptance Criteria**
1. Monorepo layout (`engine/`, `governor/`, `server/`, `web/`) with tooling (ruff, pytest, Vite, TypeScript strict) configured.
2. `README` quickstart brings up backend + frontend with two commands on Windows 11.
3. CI-runnable test command executes an initial engine test suite.
4. v1 code moved to `legacy/` (excluded from tooling) or preserved on a git tag.

### Story 1.2 — Deterministic tick engine & entity store
As the system, I want a fixed-tick engine over a vectorized entity store, so that the world is fast and reproducible.

**Acceptance Criteria**
1. Engine advances on fixed ticks from a seeded RNG; two runs with the same seed produce identical state hashes at tick 10,000 (automated test).
2. Entity store holds ≥ 10,000 entities (SoA/NumPy) with create/remove/query APIs, per-entity properties, and **generational IDs** (freelist row reuse bumps a generation counter; stale handles fail a generation check loudly).
3. Spatial hash index answers "neighbours within radius r" without full scans; benchmark test asserts ≥ 60 ticks/s headless at 10k entities **under the protocol's fauna/flora workload split** (FR4).
4. Entities carry `(x, y, z)` and stratum; stratum transitions are engine-mediated.
5. Mutations are applied through a **command buffer** at tick end (reads see tick-start state); an automated test mutates while iterating and observes safe, deterministic behavior.
6. Engine benchmark follows the shared protocol fixture (NFR11) and reports p50/p95 tick-time plus effective ticks/s.

### Story 1.3 — 3D layered world generation & abiotic simulation
As the operator, I want a procedurally generated layered world with living abiotic systems, so that there is an environment worth evolving life into.

**Acceptance Criteria**
1. Seeded terrain heightmap with water bodies; underground stratum with soil/mineral/aquifer fields; sky stratum with wind/cloud fields.
2. Weather system (temperature, humidity, wind, precipitation) evolves over ticks and varies spatially; day-night and season cycles exist.
3. Base flora (grass-equivalent) grows/spreads/dies driven by water, light, and season — engine-native, not a plugin.
4. All abiotic state is included in snapshots and the world report.

### Story 1.4 — Snapshot & restore
As the system, I want byte-exact world snapshot/restore, so that shadow forks and rollback are trustworthy.

**Acceptance Criteria**
1. Snapshot serializes full world state (entities, fields, engine RNG state, **all per-plugin RNG streams, all plugin `world.store` contents**, tick, epoch) to a single file; restore reproduces identical state hash.
2. Snapshot + restore of a 10k-entity world completes in < 2 s each.
3. Automatic snapshot retention policy (keep last K + every promotion snapshot).
4. Replay test: a run with recorded mid-run interventions, re-executed from seed + intervention log, reaches identical state hashes (FR1).

### Story 1.5 — Server & world streaming
As a viewer, I want the live world streamed to my browser, so that I can watch it.

**Acceptance Criteria**
1. FastAPI app exposes REST control endpoints (start/pause/step/speed/reset) and a WebSocket world-delta stream at a configurable rate.
2. Delta protocol sends only changed entities/fields per frame; full-state sync on connect; entity hot data as binary frames, cold data as JSON (FR20) — the sim thread's only streaming work is a memcpy into a preallocated ring buffer, all encoding on the streamer side.
3. Sim loop runs independently of connected clients; a slow client cannot stall the sim (NFR6).
4. GIL-contention gate measured: streaming-on tick rate ≥ 80% of headless under the benchmark protocol; a failure triggers the engine-process-split fallback, not target renegotiation.

### Story 1.6 — First 3D world view
As a viewer, I want a 3D rendering of the world, so that Epic 1 ends with something to watch.

**Acceptance Criteria**
1. React + react-three-fiber scene renders terrain, water, flora density, and entities as instanced meshes with orbit camera.
2. Stratum visibility toggles (underground x-ray, surface, sky).
3. ≥ 30 FPS with 10k entities on the reference GPU (NFR8).
4. HUD shows tick, entity count, weather, and sim controls wired to REST.
5. The scene reads *alive*, not clinical: sun direction/color and ambient light follow the day-night cycle, sky/fog respond to weather, water is an animated shader plane, and entity motion is interpolated between delta frames — all behind a High/Balanced/Performance quality toggle so polish never buys frame-rate debt (NFR8).

---

## Epic 2 — Plugin Runtime & Safety Pipeline

**Goal:** The safety spine: a single enforced plugin contract, static validation, sandboxed shadow execution, quarantine, and rollback — proven with hand-written plugins before any AI writes one.

### Story 2.1 — Plugin contract v1 & capability API
As a plugin author (human or AI), I want a well-defined, capability-scoped API, so that plugins are expressive but cannot touch engine internals.

**Acceptance Criteria**
1. Contract: module-level `PLUGIN_META` (name, version, contract version, declared species, lineage parent, optional behavior cadence) + `setup(world)` + `on_tick(world)`; module top level contains nothing else but allowlisted imports and function defs.
2. `world` API object exposes: spawn/remove entities of declared species, spatial queries, movement, property access, environment reads (weather, flora, water, stratum data), plugin-scoped RNG, plugin-scoped persistent `world.store`, and bulk vectorized helpers — nothing else (FR8). All writes are command-buffered (applied at tick end); quotas enforced with machine-readable violations.
3. API is documented in a single reference page that later doubles as LLM context, including the deferred-write semantics, `world.store`, quota limits, and the vectorized-idiom examples the LLM is expected to imitate.
4. Two example hand-written plugins (a grazer species, a predator species) run and produce a functioning food chain, one of them demonstrating the bulk-helper idiom.

### Story 2.2 — Static validation gate
As the system, I want to reject dangerous or malformed plugins before executing a single line, so that obvious failures never reach a sandbox.

**Acceptance Criteria**
1. Validator checks: parses cleanly; imports restricted to allowlist (`math`, typing); no file/network/process/`eval`/`exec`/dunder access; direct use of module/global RNG APIs disallowed (plugins must use `world.rng`); **module top level restricted to allowlisted imports, `PLUGIN_META`, and `def`** (no module-level mutable state — FR9); contract functions present with correct signatures; declared species names legal.
2. Validator returns machine-readable failure reasons (consumed later by the notebook and by LLM retry prompts).
3. Test suite includes a battery of malicious/broken plugin fixtures, all rejected with correct reasons.

### Story 2.3 — Sandboxed shadow execution
As the system, I want candidate plugins exercised in isolated worker processes on forked world state, so that nothing they do can harm the live world.

**Acceptance Criteria**
1. Shadow worker: separate process (spawn), loads a snapshot **plus the current live plugin set**, installs one candidate (or none — the baseline control run), runs N ticks headless, emits a metrics report over IPC.
2. Enforced budgets: wall-clock timeout, memory ceiling, per-tick time ceiling; breach ⇒ disqualification with reason. Enforcement is Windows-native: parent-side psutil watchdog for RSS/wall-clock with hard-kill, in-worker per-tick metering (NFR7).
3. Workers have no network access and write only to a scratch directory (NFR1).
4. 3–5 workers run in parallel; batch of 4 candidates × 2,000 ticks completes ≤ 3 min on reference machine (NFR2).
5. Worker crash (segfault, OOM, infinite loop) is contained and reported; the pool survives.
6. Isolation test proves worker network disablement by attempting outbound socket calls and asserting failure is logged with candidate disqualification reason (NFR1).

### Story 2.4 — Live promotion, quarantine & rollback
As the operator, I want promotions to be reversible and misbehaving plugins contained, so that the live world is never more than one action from healthy.

**Acceptance Criteria**
1. Promotion path: automatic pre-promotion snapshot → hot-load into live engine → notebook record.
2. A live plugin exceeding an error threshold is quarantined (hooks disabled, world intact) and flagged in UI/notebook (FR10).
3. Rollback restores the pre-promotion snapshot (world + plugin set + plugin stores + RNG streams) in < 5 s (NFR9), available via API and UI; rollback increments the run's epoch counter and is recorded in the intervention log (NFR5).
4. Integration test: deliberately bad plugin passes nothing, quarantine and rollback both exercised; post-rollback metrics and charts are keyed unambiguously by (epoch, tick).

### Story 2.5 — Baseline ecology soak
As the operator, I want the hand-written ecosystem proven stable before any AI touches it, so that G1 (8 h without collapse) is testable independently of the governor.

**Acceptance Criteria**
1. The grazer + predator + base-flora world runs ≥ 4 h headless (accelerated) with no total extinction and no crash; tuning happens here, not in Epic 3.
2. The soak run's metric series (populations, diversity) are recorded and become the reference baseline for Epic 3 fitness sanity checks.
3. If the hand-tuned ecology cannot hold, the finding is escalated as a world-model issue before Epic 3 begins — no fitness function can rescue an unsustainable base ecology.

---

## Epic 3 — Meta-AI Evolution Loop

**Goal:** The governor: observe, recall, propose N candidates, shadow-test, score, promote, and learn — with all history persisted.

### Story 3.1 — Observation report
As the governor, I want a rich structured report of the live world, so that generation is grounded in facts, not guesses.

**Acceptance Criteria**
1. Report (typed schema) includes populations by species × stratum, births/deaths by cause, age distributions, resource/flora/water levels, weather, spatial clustering summaries, and deltas vs previous report (FR11).
2. Death-cause attribution implemented in engine (starvation, predation, age, error).
3. Report generation ≤ 100 ms at 10k entities and covered by tests.

### Story 3.2 — Lab notebook & plugin lineage store
As the governor, I want persistent memory of every experiment, so that I never repeat a recorded failure blindly.

**Acceptance Criteria**
1. SQLite schema: runs, cycles, candidates (source, validation result, shadow metrics, fitness sub-scores, decision), promotions, outcomes, plugin lineage edges (FR16, FR17).
2. Retrieval API: "most relevant prior entries for current world conditions" (recency + species overlap + outcome tags; no vector DB needed for MVP).
3. Notebook survives restart; a resumed run continues the same notebook (NFR5).

### Story 3.3 — LLM adapter with structured output
As the system, I want provider-agnostic, schema-validated LLM calls, so that generation is reliable and swappable.

**Acceptance Criteria**
1. `LLMProvider` interface with implementations: `OllamaProvider` (local GPU, JSON-schema-constrained decoding + repair-retry loop — the sole production tier) and `ReplayProvider` (fixtures) (FR18).
2. Candidate schema: analysis, hypothesis, expected outcome, lineage parent (nullable), plugin source, confidence. Malformed output → bounded retry → failure record (NFR4).
3. Model management: one resident model per run, verified present in Ollama at run start (pull prompt if missing) and pinned warm (`keep_alive`) for the run's lifetime; model choice is a run-config setting, never swapped mid-run.
4. Per-call log: provider, model, tokens, latency; per-cycle generation time budget enforcement (NFR10).

### Story 3.4 — Candidate generation
As the governor, I want to propose N diverse candidates per cycle, so that selection has real choices.

**Acceptance Criteria**
1. Generation context = observation report + retrieved notebook entries + plugin API reference + current live plugin sources.
2. N candidates requested (parallel calls where provider allows); prompts steer diversity (fix/rebalance vs new species vs environmental engineering) and mutation-of-lineage when the notebook suggests it (FR13).
3. Static-validation failures feed one automatic repair round-trip with the machine-readable reasons before the candidate is dropped.

### Story 3.5 — Fitness engine & selection
As the governor, I want candidates scored on explicit ecological fitness, so that promotion is a measurement, not a vibe.

**Acceptance Criteria**
1. Fitness = weighted sub-scores over shadow-run metrics: Shannon diversity, population stability/volatility, extinction events, trophic balance, resource sustainability, novelty bonus; weights in run config (FR15). Sub-scores are computed as **deltas vs the cycle's baseline control run**, not absolute values.
2. Novelty is concretely defined for MVP: species-set overlap + AST-normalized source similarity vs prior promoted plugins (FR15); its computation is unit-tested against fixture pairs.
3. Selection: best candidate that beats the control by ≥ threshold promoted; ties broken by stability; "no promotion" is a valid recorded outcome (FR16). A control-run crash is recorded as a live-plugin finding, never attributed to a candidate.
4. Fitness engine is a pure function over metrics (unit-testable); scores persisted per candidate with sub-score breakdown.

### Story 3.6 — Cycle orchestration & outcome feedback
As the operator, I want the whole loop to run on cadence unattended, so that the world evolves while I'm away.

**Acceptance Criteria**
1. Orchestrator triggers a cycle every T ticks (config) or on demand; runs fully async to the live sim (NFR6).
2. Cycle pipeline: report → recall → generate → validate → shadow-evaluate → score → promote/decline → snapshot → notebook, with generation and shadow evaluation **pipelined** (candidate k shadow-tests while k+1 generates; control run launches first) so the full cycle fits NFR2's ≤ 5 min envelope.
3. Next cycle measures the previous promotion's live outcome vs its expected outcome and appends it to the record (FR17).
4. After K consecutive no-promotion cycles, escalation policy applies (widen N, extend repair round-trips, enrich generation context) — configurable, always local, always the same resident model.
5. Overnight test: 8 h simulated run (canned or local provider) with no crash and ≥ 1 promotion, ≥ 1 rejection recorded.

---

## Epic 4 — The Observatory

**Goal:** The full mission-control experience on top of Epics 1–3.

### Story 4.1 — Metrics & charts
As a viewer, I want live charts of the ecosystem, so that trends are visible at a glance.

**Acceptance Criteria**
1. Time-series charts: population per species, Shannon diversity, births/deaths by cause, resource levels; window + full-run views (FR21).
2. Chart data streams over the existing WebSocket; history backfilled from SQLite on connect.
3. Promotion/rollback events shown as annotated markers on the time axis.

### Story 4.2 — Evolution timeline & candidate cards
As a viewer, I want the story of every cycle, so that I can follow what the AI is doing and whether it works.

**Acceptance Criteria**
1. Timeline lists cycles with status (promoted / no change / rolled back); each expands to candidate cards: analysis, hypothesis, fitness sub-scores, decision, and outcome-vs-expected once measured (FR22).
2. Live "cycle in progress" state visible (generating → validating → shadow-testing → scoring).
3. Deep-links from a candidate to its code in the code lab.

### Story 4.3 — Code lab: plugin browser, diff & phylogeny
As a viewer, I want to read and compare everything the AI wrote, so that the code evolution itself is legible.

**Acceptance Criteria**
1. Plugin browser lists all plugins (live/quarantined/rejected/retired) with syntax-highlighted source (FR23).
2. Diff view renders a plugin against its lineage parent.
3. Phylogeny tree visualizes plugin ancestry across the run; nodes colored by fate; click-through to code.

### Story 4.4 — World inspection & polish
As a viewer, I want to interrogate the 3D world directly, so that the simulation feels tangible.

**Acceptance Criteria**
1. Click an entity → inspector panel (species, age, energy, position/stratum, owning plugin) (FR19).
2. Species legend with counts and show/hide; heat-map overlay toggle for flora/water density.
3. Camera presets per stratum; smooth transitions; performance budget held (NFR8).
4. Presentation pass: selective bloom + vignette post-processing, night-time emissive entity glow, weather-driven sky/fog — gated behind the quality toggle introduced in Story 1.6, with the Performance preset always holding ≥ 30 FPS at 10k entities.

### Story 4.5 — Run management & settings
As the operator, I want run lifecycle and configuration in the UI, so that I never need the terminal mid-session.

**Acceptance Criteria**
1. New-run dialog: seed, world size, cycle cadence, N candidates, fitness weights, resident local model selection (applies for the whole run) and generation time budget (FR24, NFR10).
2. Rollback and trigger-evolution-now controls with confirmation.
3. Cumulative LLM token/latency stats for the current run.

---

## Checklist Results Report

*Self-assessment against BMAD PM checklist:* Goals trace to brief (✔); every FR/NFR maps to at least one story AC (✔ — traced during authoring); epics independently shippable (✔); MVP cut lines explicit in brief (✔); open risk — fitness-function tuning is research-flavored and may need iteration budget inside Epic 3 (flagged to Architect and operator).

## Next Steps

### Architect Prompt
Create `docs/architecture.md` for Genesis v2 from this PRD and `docs/brief.md`. Priorities: (1) the safety pipeline boundaries (validator / sandbox worker / live engine) as the load-bearing design, (2) deterministic vectorized engine design compatible with Windows `spawn` multiprocessing, (3) the purely-local LLM adapter with schema-constrained output via Ollama on an RTX 4080, (4) SQLite schemas for the notebook/lineage, (5) WebSocket delta protocol and Three.js instancing strategy for 10k entities.
