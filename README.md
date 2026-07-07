# Genesis v2 — The Living World

A self-evolving 3D artificial-life simulator: a layered world (underground / surface / sky)
whose ecology is authored by an **AI governor that writes, shadow-tests, and promotes its own
plugins by measured ecological fitness** — running fully offline on a local GPU.

Spec package: [docs/brief.md](docs/brief.md) → [docs/prd.md](docs/prd.md) →
[docs/architecture.md](docs/architecture.md). Spike results: [docs/spikes.md](docs/spikes.md).

## Quickstart (Windows 11)

```powershell
python -m pip install -e .[dev]
python -m pytest                      # engine test suite (determinism is first-class)
python scripts/bench_engine.py        # benchmark protocol (docs/architecture.md)
```

Backend server and web Observatory land in Epics 1.5–1.6 / 4 (see PRD).

## Layout

```
engine/            # deterministic world kernel (pure library: no I/O, no globals)
governor/          # evolution loop: observe → recall → generate → shadow-test → select
server/            # FastAPI: REST control + WebSocket world streaming
web/               # React + Three.js Observatory
plugins_examples/  # hand-written reference plugins (Epic 2)
tests/             # pytest suites; determinism + replay tests gate everything
scripts/           # benchmark protocol runner
data/              # run.db + snapshots (gitignored)
```

## Non-negotiables (from the spec)

- Determinism: *seed + intervention log ⇒ identical state hash*. Every random draw goes
  through the engine RNG or a per-plugin stream; all of it is snapshot-included.
- No AI-generated code executes live before AST validation **and** sandboxed shadow evaluation.
- Plugin state is snapshot-complete: entity props + `world.store` only; module-level mutable
  state is a validation failure.
- All plugin mutations are command-buffered (applied at tick end); entity handles are generational.
