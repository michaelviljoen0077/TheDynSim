# Genesis v2 — Handover Document

**Date:** 2026-07-07
**From:** Spec/BMAD phase (this repo, `evosquared`, with Claude as Analyst/PM/Architect)
**To:** Implementation process (new repo/session/team)
**Owner:** Michael (michaelv@agilebridge.co.za)

---

## 1. What you are receiving

A complete BMAD specification package for **Genesis v2** — a self-evolving 3D artificial-life simulator where a **purely local** LLM governor writes candidate world-plugins, tests them in sandboxed shadow simulations, promotes winners by measured ecological fitness, and remembers everything in a persistent lab notebook.

| Document | Role | Read order |
|---|---|---|
| `docs/brief.md` (v1.3) | Project Brief — vision, v1 lessons learned, goals/KPIs, MVP scope, risks | 1st |
| `docs/prd.md` (v1.3) | PRD — 24 FRs, 11 NFRs, UI goals, 4 epics / 22 stories with acceptance criteria | 2nd |
| `docs/architecture.md` (v1.3) | Architecture — components, plugin contract, safety layers, schemas, protocols, source tree, test strategy | 3rd |

**Repo naming note:** this spec package lives in the `TheDynSim` folder (docs only — no code yet, no `legacy/` here). The architecture's source tree calls the implementation repo `evosquared`; pick either name at scaffold time. The v1 prototype referenced by the brief (`backend/`, `meta_ai/`, `frontend/`) lives in the *original v1 repo*, not this folder — an implementing agent should not hunt for it here.

All three are self-contained; no other context from this session is required to implement. The PRD's "Architect Prompt" and the architecture's "Next Steps" sections tell an implementing agent where to start.

## 2. The product in one paragraph

A fully 3D layered world (underground / surface / sky strata over a heightmap, true `(x,y,z)` entity positions) runs deterministically at 60+ ticks/s for 10k+ entities. Every T ticks the governor observes the world, recalls its lab notebook, asks a **local** LLM (Ollama on the RTX 4080) for N candidate Python plugins, statically validates them, runs each in a parallel sandboxed shadow simulation forked from a live snapshot, scores survivors with an explicit fitness function, promotes at most one, and later measures whether reality matched the prediction. A React/Three.js "Observatory" renders the world in 3D plus the full evolutionary story (timeline, candidate cards, code diffs, plugin phylogeny).

## 3. Decisions that are LOCKED (do not re-litigate without the owner)

1. **Purely local AI.** Ollama on the operator's GPU is the *sole* production inference tier for MVP. No cloud SDKs, no API keys, no per-token cost, works offline. The `LLMProvider` interface is the seam for a possible post-MVP cloud tier — nothing downstream may know which provider ran. (Decided 2026-07-07, supersedes the earlier Claude-cloud-tier design; docs reflect this at v1.2.)
2. **Generate–Test–Select pipeline.** No AI-generated code ever executes in the live process without passing AST validation *and* shadow evaluation. This is the load-bearing design; everything else can flex.
3. **Layered 3D, not voxels.** Three discrete strata over a heightmap with continuous positions. Full-voxel space is a possible later evolution isolated to the engine's field layer.
4. **Python 3.12 engine, performance via NumPy vectorization + process parallelism.** No Rust/C# rewrite — the LLM writes Python plugins, keep the language surface uniform.
5. **One plugin contract** (`PLUGIN_META` + `setup(world)` + `on_tick(world)` against a capability-scoped `WorldAPI`), versioned. v1's dual contracts were a root cause of drift.
6. **SQLite for all history** (notebook, lineage, metrics); binary `.npz` snapshots for world state; one snapshot mechanism shared by shadow-forking and rollback.
7. **v1 code is reference only.** Freeze it in `legacy/` or a git tag; migrate lessons, not code.
8. **Determinism is non-negotiable.** Plugins may use only `world.rng` for randomness; direct module/global RNG APIs are validation failures.
9. **Performance claims require protocol evidence.** FR/NFR performance targets are valid only under the versioned benchmark protocol in architecture — including the fixed scalar-fauna/vectorized workload split and the ≥ 80% streaming-on gate.
10. **Worker isolation must be test-proven.** Shadow workers must fail outbound socket attempts and non-scratch writes with recorded disqualification reasons.
11. **Plugin state is snapshot-complete.** No module-level mutable state (validator-enforced); all plugin state lives in entity prop slots and the snapshot-included `world.store`. This is what makes shadow forks and rollback trustworthy.
12. **Writes are command-buffered, handles are generational.** Plugin mutations apply at tick end against tick-start reads; stale entity handles fail a generation check. (v1's most common generated-code bug class, closed structurally.)
13. **Determinism = seed + intervention log.** Every promotion/rollback/config change is recorded with tick + epoch; any run is exactly replayable. Rollback increments an epoch counter carried by all tick-keyed history.
14. **Every cycle runs a baseline control shadow.** Fitness is a delta vs "do nothing"; promotion requires beating the control. Control-run failures indict live plugins, not candidates.
15. **Binary position streaming from MVP.** Hot entity data ships as quantized binary WS frames (JSON only for cold metadata); the wire format is not a retrofit item.

## 4. Key context the docs assume

- **Reference machine:** Windows 11, RTX 4080 (16 GB VRAM), 64 GB RAM. Perf targets and worker counts are calibrated to it. Windows compatibility is an NFR — `multiprocessing` must use spawn-safe patterns, no Unix-only syscalls in core paths.
- **Single resident local model:** one Qwen-coder-class quantized model per run (~14B Q5 ≈ 10–11 GB, fully on-GPU at ~40+ tok/s), pinned warm via Ollama `keep_alive` for the run's lifetime. No mid-run model swapping — swap churn (VRAM eviction, reload, KV-cache loss) costs more than a bigger model buys, and the rejection pipeline compensates for model weakness. Escalation after failed cycles: widen N → more repair round-trips → richer generation context, same model.
- **Threat model is accidental damage** (buggy AI code), not adversarial code — documented in the architecture; the sandbox is not a security boundary against a malicious actor.
- **Weak-model compensation is a pipeline property:** schema-constrained decoding + repair retry + validation + shadow testing + fitness threshold mean a weaker model just gets more rejections; the live world never sees the difference.
- **Operator's available AI resources (noted 2026-07-07):** Ollama installed for local models (the MVP production tier); a GitHub Copilot account usable via the official CLI; a Claude account; a Google (Gemini) Pro account. The locked purely-local decision applies to the *in-loop generation tier only* — the cloud accounts are legitimate for (a) dev-time coding assistance while building Genesis itself, and (b) post-MVP `LLMProvider` cloud tiers behind the existing interface (Claude and Gemini both have official APIs; Copilot must only ever be used through official, documented surfaces — v1's unofficial-endpoint mistake stays dead).

## 5. Where v1 lessons live now

v1's giant prompt rules were converted into machinery — if you're wondering "why does the spec insist on X", the answer is usually a v1 failure:

| v1 failure | v2 mechanism |
|---|---|
| Animals starved on random walks; ecosystem collapsed | Shadow simulation + fitness function (stability, extinctions, trophic balance) gate promotion |
| Prompt rules 1–10 ("never register 'grass'", "guard properties"…) | Plugin contract + AST validator + capability-scoped `WorldAPI` |
| LLM free-text parsed by regex | JSON-schema-constrained decoding, Pydantic-validated everywhere |
| 3-iteration memory; repeated failed strategies | Persistent SQLite lab notebook + relevance recall + outcome feedback loop |
| Whole-codebase zip backups | Byte-exact world snapshots, < 5 s rollback |
| Unofficial Copilot endpoints | Official/documented APIs only (Ollama HTTP locally) |

## 6. Open questions for the implementation process

None block starting; all are flagged in the docs:

1. **Fitness weights** (Story 3.5) — the defaults are educated guesses; expect an iteration budget inside Epic 3 to tune them against observed runs.
2. **Exact local model choice** — validate early (Epic 3, Story 3.3) that a current Qwen-coder-class ~14B produces viable candidates under the schema + repair loop. The bet is that it does; if rejection rates prove unworkably high, the fallback is a larger model *as the single resident model for the run* (accepting slower cycles via partial offload) — never mid-run swapping.
3. **World defaults** — 256×256 world, 10 Hz stream rate, N=3 candidates, 2,000-tick shadow runs are starting values, all in run config.
4. **Delta protocol encoding** — resolved at v1.3: hybrid from MVP — binary frames (quantized typed arrays) for hot entity data, JSON for cold metadata. Remaining tunables are quantization precision and frame layout details.

## 7. Recommended execution order

1. **Story 1.0 spikes first** — three 1–2 day feasibility spikes (scalar-loop perf, local-model generation against the real candidate schema, Windows sandbox budgets). Each validates a load-bearing bet; each, if it fails, changes the architecture while changing it is still cheap.
2. **Epic 1, Stories 1.1–1.4** (scaffold → deterministic engine → world gen → snapshots). Everything else trusts determinism and snapshots; land the determinism + replay test suite before building on top.
3. Then 1.5–1.6 (streaming + first 3D view) for an early visible milestone — the day-night lighting and interpolated motion land here; this is the first "wow" checkpoint.
4. **Epic 2 entirely before Epic 3** — prove the safety pipeline with the two hand-written example plugins (grazer + predator), then the Story 2.5 baseline-ecology soak (≥ 4 h headless, no extinction) before any LLM writes code.
5. Epic 3 with `ReplayProvider` fixtures first, live Ollama second.
6. Epic 4 last (thin API-consuming layer; timeline/code-lab data is already being persisted by Epic 3).

If the new process uses BMAD's SM/dev-agent flow: shard `docs/prd.md` per epic/story and `docs/architecture.md` per section as usual (`docs/prd/`, `docs/architecture/`); the story ACs were written to be directly consumable.

## 8. Definition of "spec phase complete" (this handover's claim)

- Brief, PRD, and Architecture exist, are mutually consistent at v1.3, and reflect the late decisions: fully-3D layered world, purely-local AI, deterministic RNG policy, protocol-driven performance validation, snapshot-complete plugin state, command-buffered writes, replayable determinism (seed + intervention log), control-run fitness baselines, pipelined cycle budget, and binary streaming.
- Every FR/NFR maps to at least one story acceptance criterion.
- No code from this phase needs to carry over; the v1 repo (`backend/`, `meta_ai/`, `frontend/`) is reference material only.
