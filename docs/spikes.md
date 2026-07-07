# Story 1.0 — Feasibility Spike Findings

**Date:** 2026-07-07 · **Machine:** reference machine (Windows 11, RTX 4080 16 GB, 64 GB RAM), Python 3.14.2, NumPy 2.4.2, Ollama with `qwen3-coder:30b`.

All three spikes **PASS**. Spike code was disposable (scratchpad) per AC4; these findings are the durable artifact.

## Spike A — Scalar plugin-loop performance: PASS (with design consequences)

Throwaway SoA store + uniform-grid spatial hash + per-entity Python loop (neighbor query → chase/wander → move), world 256², radius 8, 60 measured ticks:

| Variant | n | p50 tick | ~ticks/s |
|---|---|---|---|
| scalar, pure-Python dict grid | 2,000 | 5.6 ms | **177** |
| scalar, pure-Python dict grid | 5,000 | 24.0 ms | 41 |
| scalar, pure-Python dict grid | 10,000 | 81.3 ms | 12 |
| scalar, NumPy per-entity buckets | 2,000 | 43.0 ms | 23 |
| vectorized bulk (helper idiom) | 10,000 | 0.09 ms | **~11,000** |
| vectorized bulk (helper idiom) | 50,000 | 0.84 ms | ~1,200 |

**Decisions confirmed / taken:**
1. FR4's workload split (≥ 2k scalar fauna + ~8k vectorized) is **validated**: 2k scalar at 177 tps leaves ~2/3 of the 16.6 ms tick budget for fields, engine systems, and heavier plugin logic. 10k pure-scalar at 60 tps is confirmed fiction.
2. **SpatialHash needs two paths:** per-entity NumPy micro-queries are ~7× *slower* than a pure-Python dict grid (per-call overhead dominates tiny arrays). Scalar queries ⇒ pure-Python buckets; bulk queries ⇒ vectorized path.
3. The bulk-helper idiom has three orders of magnitude of headroom — worth teaching to the LLM prominently in the API reference.

## Spike B — Local-model candidate generation: PASS (first attempt, zero repairs)

`qwen3-coder:30b` (MoE, 18 GB, partially CPU-offloaded) via Ollama `/api/chat` with JSON-schema-constrained `format`, given a ~540-token mini API reference and asked for a grazer plugin:

- **Schema-valid JSON on attempt 1**; all required keys present; confidence 0.95.
- **Plugin passed the full validator battery** (AST parse, contract functions, PLUGIN_META, import allowlist, no banned calls/dunders, module-top-level rule) — zero repair round-trips.
- Code quality: genuinely correct ecology (graze, wander via `world.rng`, starvation death, energy-split reproduction) — not just syntactically valid.
- Throughput: **22.6 tok/s**, 839 output tokens, 41.8 s wall.

**Decisions confirmed / taken:**
1. The load-bearing bet (handover open question #2) holds — and with a *stronger* model class than assumed: the installed 30B MoE (3B active) beats the assumed dense 14B on quality while running acceptably despite partial offload.
2. Cycle arithmetic at 22.6 tok/s: ~40–75 s per candidate ⇒ 3 candidates ≈ 2–4 min generation ⇒ fits the ≤ 5 min **pipelined** envelope (NFR2). The 40 tok/s doc assumption is corrected to ~22 tok/s measured.
3. Default run-config model: `qwen3-coder:30b`, `keep_alive` pinned. A dense fully-resident 14B remains the fallback if sustained-load offload throughput disappoints.

## Spike C — Windows sandbox budget enforcement: PASS (all 4 scenarios)

`multiprocessing` spawn workers + parent-side psutil watchdog (250 ms poll, wall 5 s / RSS 500 MB limits) + bootstrap socket-block:

| Scenario | Result |
|---|---|
| Normal worker | Completed, reported result over IPC |
| Outbound socket attempt | Blocked by bootstrap (`RuntimeError`), worker reported it |
| Infinite loop | Hard-killed at wall limit (5.01 s) |
| Memory bomb (50 MB/step) | Hard-killed at 621 MB RSS, **0.75 s** after start |
| Parent process | Survived all of the above |

**Decisions confirmed:** the NFR7 mechanism (psutil parent watchdog + hard-kill + in-worker metering; no `setrlimit`) works as specified on Windows. Breach-detection latency at 250 ms polling is well inside tolerable disqualification lag.

## Net effect on the plan

No architecture changes required. Two doc-level corrections carried into implementation: measured generation throughput is ~22 tok/s (not 40) and the resident model is `qwen3-coder:30b` (MoE) rather than a dense 14B. Green light for Epic 1.
