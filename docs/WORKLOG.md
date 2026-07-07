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
