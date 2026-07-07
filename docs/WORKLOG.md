# Worklog — Epic 4 (The Observatory) completion

Date: 2026-07-07
Branch: `master`

This log records everything done in the session that finished the frontend and built out
the remaining Epic 4 stories. All work is additive; no existing backend behaviour was changed.

---

## Summary

- Fixed a broken frontend build and wired the Observatory UI together.
- Built the four remaining Epic 4 stories (4.1 metrics/charts, 4.2 deep-links,
  4.3 code lab, 4.4 world inspection) plus their supporting backend API.
- Verified: 74 Python tests pass, `ruff` clean, `tsc --noEmit` clean, `vite build` clean,
  live LLM evolution confirmed end-to-end against Ollama.

---

## Commits (in order landed on top of `350220f`)

| Commit    | Story / scope                                                        |
|-----------|----------------------------------------------------------------------|
| `b5623df` | Epic 3 backend: reporter, notebook, LLM adapter, fitness, orchestrator + server API |
| `b9f3aac` | Epic 4: Evolution panel (CandidateCard fix + App wiring)             |
| `4568e16` | Epic 4 backend: observatory API (metrics, code lab, entity, interventions) + 6 tests |
| `5cee905` | Story 4.1: live metrics & charts panel                               |
| `13664ef` | Story 4.4: world inspection (entity picker & heat-map overlays)      |
| `6df844b` | Story 4.3: code lab (plugin browser, diff, phylogeny)               |

---

## Problem fixes

- **Broken build**: `EvolutionPanel.tsx` imported `./CandidateCard`, which did not exist
  (a prior session ended mid-implementation). Created `web/src/ui/CandidateCard.tsx` to match
  the existing `evolution.css` classes and `Candidate` type.
- **Unwired panel**: `App.tsx` did not render `EvolutionPanel`. Wired it (and later the
  `MetricsPanel`, `InspectorPanel`, and `CodeLab`).
- **Unused import**: `MetricsPanel.tsx` had an unused `useRef` (TS6133) — removed.
- **Metrics table unused**: The orchestrator never writes the SQLite `metrics` table, so the
  charts accumulate samples client-side via 1 Hz REST polling instead of the PRD's
  "WS stream + SQLite backfill". Deliberate, documented deviation.

---

## Files created

- `web/src/ui/CandidateCard.tsx` — candidate fate badge, fitness bars (SVG), hypothesis,
  expandable details, validation/rejection reasons, "view source" and "open in code lab" links.
- `web/src/net/api.ts` — shared `fetchJson<T>` + types (`MetricsSnapshot`, `Intervention`,
  `LabPlugin`, `EntityDetail`, `STRATUM_NAMES`).
- `web/src/ui/Chart.tsx` — dependency-free SVG line chart (auto-scaled, event markers).
- `web/src/ui/MetricsPanel.tsx` + `metrics.css` — Story 4.1 charts panel.
- `web/src/ui/InspectorPanel.tsx` + `inspector.css` — Story 4.4 entity inspector.
- `web/src/ui/CodeLab.tsx` + `codelab.css` — Story 4.3 code lab (browser / source / diff / tree).
- `tests/server/test_observatory.py` — 6 tests covering the new API surface.
- `docs/WORKLOG.md` — this file.

## Files modified

- `web/src/App.tsx` — renders WorldCanvas, Hud, EvolutionPanel, MetricsPanel, InspectorPanel, CodeLab.
- `web/src/state/store.ts` — added `overlay`, `selectedEntity`, `labFocus` state + setters.
- `web/src/world/Entities.tsx` — raycast entity picker (instance slot → generational id → select).
- `web/src/world/Terrain.tsx` — flora/water heat-map overlays with live retint.
- `web/src/ui/Hud.tsx` + `hud.css` — overlay toggle row (Terrain / Flora / Water).
- `server/app.py` — added `/api/metrics`, `/api/interventions`, `/api/lab/plugins`, `/api/entity/{eid}`.
- `governor/notebook.py` — added `all_candidates()` and `interventions(limit=200)` reads.

---

## Story coverage

- **4.1 Metrics & charts** — population per species, Shannon diversity, flora density,
  deaths by cause; 1 Hz rolling window; promotion/rollback markers from `/api/interventions`.
- **4.2 Evolution timeline + deep-links** — evolution panel (pre-existing) plus candidate → code lab
  deep-linking from `CandidateCard`.
- **4.3 Code lab** — filterable plugin browser, source view, LCS line-diff vs lineage parent,
  fate-colored phylogeny tree with click-through.
- **4.4 World inspection** — click an entity to inspect (species, plugin, energy, age, position,
  stratum); flora/water heat-map overlays; species legend (pre-existing).

---

## Verification

- `python -m pytest -q` → **74 passed** (68 existing + 6 new).
- `python -m ruff check engine governor server tests scripts -q` → clean.
- `cd web && npm run build` (`tsc --noEmit && vite build`) → clean.
- Live evolution against Ollama `qwen3-coder:30b` verified end-to-end
  (a vole plugin promoted at +1.09; a sky-predator candidate rejected at -4.15).

### Known notes

- `tests/server/test_stream.py::test_streaming_gil_gate` is an intentional performance tripwire.
  It is environment-sensitive and can fail only when a heavy build runs concurrently;
  it passes in isolation. Left as-is (already retry-tolerant).
- `docs/brief.md` carries a one-word edit made outside this work — left untouched/uncommitted.
- All commits are local; nothing has been pushed to a remote.

---

## Post-review critique fixes (2026-07-07)

Two correctness issues raised in code review, both addressed:

### 1. `multiprocessing.Queue` deadlock — `governor/shadow.py`
The parent only read the worker's result *after* `not proc.is_alive()`. A metrics
payload larger than the OS pipe buffer (~64 KB) blocks the worker's feeder thread
until the parent reads it, so the worker never exits — the watchdog then spuriously
wall-budget-killed successful runs whose payload happened to be large.

- **Fix**: added `_drain(queue, timeout)` and now read the queue *eagerly on every
  poll while the worker is alive*. Budget checks only run when no payload is waiting;
  a dead worker still gets one short blocking drain before being declared `worker-died`.
  Also kill after a join timeout to avoid zombies.
- **Test**: `tests/governor/test_shadow.py::test_large_payload_is_drained_before_exit`
  spawns a worker emitting a 500 KB payload and asserts it can only exit once drained.

### 2. Determinism loophole — `engine/validator.py`
The validator banned `random`/`os`/`sys` etc. but not Python built-ins whose results
vary between processes: `hash()` and `id()` (hash randomization / object identity),
and `set` iteration order (hash-based). A plugin using these could pass a shadow test
yet diverge on snapshot replay.

- **Fix**: added a `NONDETERMINISTIC_CALLS = {set, frozenset, hash, id}` gate plus
  detection of set literals (`ast.Set`) and set comprehensions (`ast.SetComp`),
  reported with a new machine-readable `non-deterministic` code. `world.set(...)`
  (an attribute call) is unaffected, so example plugins still validate.
- **Tests**: six new hostile fixtures in `tests/engine/test_validator.py` covering
  `set()`, `frozenset()`, `hash()`, `id()`, set literals, and set comprehensions.

Full suite green after both fixes (`ruff` clean, all tests pass).

---

## Deep-review fixes (2026-07-07)

A full read of the engine, governor, server, and the binary protocol (both ends).
The wire protocol matched byte-for-byte, determinism held up, and the command-buffer /
generational-handle design was sound. Six findings resolved:

### 1. SQLite shared connection — thread safety (`governor/notebook.py`)
One `sqlite3.Connection` was shared by the governor's cycle thread (writes) and
FastAPI's threadpool (reads) with no serialization.
- **Fix**: added a `threading.Lock` guarding every DB access. Added
  `set_candidate_fate()` and routed the orchestrator's two direct `notebook.db.execute`
  writes through it, so all access is centralized behind the lock.

### 2. Server security posture (`README.md`)
`/api/plugins/install` compiles and `exec`s plugin source behind an AST validator +
restricted builtins — an accidental-damage gate, not a hardened sandbox — and control
endpoints are unauthenticated.
- **Fix**: documented the constraint prominently; keep the server on `localhost`
  (uvicorn default), never `--host 0.0.0.0`. No code change — the default binding is
  already safe.

### 3. `eat_flora` immediate mutation (`engine/world_api.py`)
The flora field is consumed immediately rather than through the command buffer,
which the module docstring's "all reads see tick-start state" wording contradicted.
- **Fix**: this is intentional (simultaneous grazers share finite grass instead of
  each harvesting it in full — no resource double-spend). Corrected the docstrings to
  describe flora as the deliberate immediate-consume exception; behaviour unchanged.

### 4. `set` builtin vs. validator ban (`engine/plugin_host.py`)
`SAFE_BUILTINS` still exposed `set`, now inconsistent with the determinism ban.
- **Fix**: removed `set` from the sandbox builtins.

### 5. `get_entity` index guard (`server/app.py`)
`store.alive[index]` ran after only an upper-bound check (negative index unreachable
via the route, but defensively loose).
- **Fix**: added an explicit `index < 0` guard.

### 6. Snapshot header serialization asymmetry (`engine/snapshot.py`)
`state_hash` used `json.dumps(..., default=str)` while `save_snapshot` did not.
- **Fix**: both now serialize the header through one shared `_dump_header()` helper,
  so save and hash agree byte-for-byte and would fail loudly on a non-serializable field.

Determinism architecture verified sound: per-plugin RNG streams seeded from
`(seed, FNV(name))`, `world.rng` draw order independent of loaded plugins (which is
what makes control-vs-candidate delta scoring valid), and snapshots are state-complete.

Full suite green after all fixes (`ruff` clean, all tests pass).

---

## Second strict-review fixes (2026-07-07)

A further pass flagged two issues; both accepted and fixed. Note this **reverses the
deep-review's finding #3** — the reviewer was right that immediate flora consumption is
inconsistent with the engine's own command-buffer invariant.

### 3 (revised). `eat_flora` now command-buffered (`engine/world_api.py`, `engine/commands.py`)
Immediate flora mutation contradicted the "every mutation goes through the command
buffer / all reads see tick-start state" invariant, and was inconsistent with how
`attack` already handles a shared, over-subscribable resource (buffered `drain_energy`
returning a tick-start estimate, clamped at apply).
- **Fix**: `eat_flora` now returns a bite estimated against **tick-start** density and
  queues the drain via a new `CommandBuffer.eat_flora`. `CommandBuffer.apply` takes an
  optional `flora` array and drains bites at tick end, in submission order, clamped so
  the field can never go negative (mirrors `drain_energy`). The two `apply` call sites
  (`World.step`, `PluginHost.install`) now pass `flora=...`.
- **Result**: all grazers in a tick read the same tick-start grass; execution order no
  longer leaks between plugins; double-spend is still prevented — now via the clamp at
  apply rather than in-tick mutation. Module/method docstrings updated to match.
- **Test**: `tests/engine/test_commands.py::test_flora_consumption_is_deferred_and_clamped`
  — two same-cell bites totalling more than the available grass; density unchanged until
  `apply`, then drained to exactly `0.0` (never negative).

### 7. Spatial `within(species=None)` ordering leak (`engine/spatial.py`)
`rebuild()` appended per-`(stratum, species)` buckets to each stratum's layer list in
first-appearance order, so the layer order — and thus the result order of
`within(species=None)` — depended on **which species happened to own the lowest alive
row**. An unrelated plugin spawning/dying could silently reorder another plugin's
neighbor list, a subtle determinism-adjacent hazard.
- **Fix**: build layers in `sorted((stratum, species_id))` order, so ordering is
  species-major by id and fully decoupled from row allocation (matches the documented
  "species-major" intent). `nearest` was already order-immune (global min, ties → lowest
  row); only `within` was affected.
- **Test**: `tests/engine/test_spatial.py::test_within_order_tracks_species_id_not_row_allocation`
  — species 1 placed at the lower row, species 0 at the higher row; `within` returns
  species 0's row first, proving order tracks species id, not allocation.

Full suite green after both fixes (`ruff` clean, all 83 tests pass).


