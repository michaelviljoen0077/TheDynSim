# Genesis v2 — The Living World

A self-evolving 3D artificial-life simulator: a cube-sphere planet (surface & sky strata)
whose ecology is authored by an **AI governor that writes, shadow-tests, and promotes its own
plugins by measured ecological fitness** — running fully offline on a local GPU.

Spec package: [docs/brief.md](docs/brief.md) → [docs/prd.md](docs/prd.md) →
[docs/architecture.md](docs/architecture.md). Spike results: [docs/spikes.md](docs/spikes.md).

## Quickstart (Windows 11)

```powershell
python -m pip install -e .[dev,server]
python -m pytest                      # 155 tests: determinism, snapshots, energy conservation, governor
python scripts/bench_engine.py        # benchmark protocol (docs/architecture.md)
```

**Watch the world** (two commands, Story 1.1 AC2):

```powershell
cd web; npm install; npm run build; cd ..
python -m uvicorn server.app:app --port 8000
```

Open http://localhost:8000 — live cube-sphere planet with day-night cycle, weather, a
grazing herd and a bird flock, overlays, god-mode controls, and run controls. For
frontend dev with HMR: `cd web; npm run dev` (Vite proxies to :8000).

**To see it self-evolve** (the headline feature) you need a local **Ollama** with the
governor model — otherwise the world runs but never evolves:

```powershell
ollama serve            # in one terminal
ollama pull qwen3-coder:30b
```

Start the server with Ollama running and the governor comes online automatically
(watch the startup log). Everything else works without it.

> **Security:** `/api/plugins/install` compiles and `exec`s plugin source. The AST
> validator + restricted builtins are an *accidental-damage* gate, not a sandbox
> against a malicious actor, and the control endpoints are unauthenticated. Keep the
> server bound to `localhost` (uvicorn's default) — never run it with `--host 0.0.0.0`
> or otherwise expose it to an untrusted network.

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
