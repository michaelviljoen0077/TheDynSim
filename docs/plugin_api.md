# Plugin Contract v1 & WorldAPI Reference

This page is the single source of truth for plugin authors — human or AI. It is
included verbatim in LLM generation context. The validator, the API, and this
document must stay in lockstep (coding standard #2).

## Contract

A plugin is one Python module containing, at top level, ONLY:
1. Optional imports from the allowlist: `math`, `typing`
2. `PLUGIN_META` — a **literal** dict
3. `def setup(world):` and `def on_tick(world):` (each takes exactly one argument)
4. Optional additional helper `def` functions

**Nothing else at module top level.** No module-level variables, lists, dicts,
counters, or classes — plugin state must live in entity props or `world.store`
so that snapshots, shadow forks, and rollback capture it completely.

```python
PLUGIN_META = {
    "name": "burrowing_vole",        # ^[a-z][a-z0-9_]{0,30}$
    "contract": 1,                    # exactly 1
    "species": ["vole"],              # species this plugin OWNS (writes require ownership)
    "lineage_parent": None,           # or the parent plugin's name if this mutates it
}

def setup(world):
    """Runs once at promotion. Register species, spawn the initial population."""

def on_tick(world):
    """Runs every tick, in plugin promotion order."""
```

## Hard rules (validation failures)

- No imports beyond `math`, `typing`. No `eval`/`exec`/`compile`/`open`/`getattr`/
  `setattr`/`print`/`input`. No dunder attribute access. No `global`/`nonlocal`.
- All randomness through `world.rng` (a seeded per-plugin stream). Never `random`
  or `numpy` — they don't exist in the sandbox.
- `while True` without `break` draws a warning; shadow budgets will kill it.

## Execution model (read carefully — this is unusual)

- **Reads see tick-start state.** `world.entities()`, `pos()`, `get()` etc. reflect
  the world as it was when the tick began.
- **Writes are deferred.** `spawn`/`remove`/`move`/`set`/`set_stratum`/`attack`
  queue commands that apply at tick end, in submission order. You can safely
  mutate while iterating. `spawn` returns nothing — you cannot touch an entity
  you spawned until the next tick.
- **Entities die automatically at energy <= 0** (engine sweep; cause recorded as
  starvation, or predation if drained by `attack`). You don't need to remove
  starving entities yourself.
- **Handles are generational.** A handle to a dead entity raises a `stale-handle`
  error if used; catch nothing — just re-query each tick.
- **Quotas** (machine-readable errors): max entities per plugin, max spawns per
  tick, max `world.store` keys. Exceeding one raises; the tick's remaining work
  is skipped and the error is recorded. Repeated errors quarantine the plugin.

## WorldAPI

### Species & lifecycle
- `world.register_species(name, size=1.0, color="#rrggbb", strata=(world.SURFACE,), props=("hunger",))`
  — only in `setup`, only names declared in `PLUGIN_META["species"]`. `props` are
  per-entity float slots (max 8).
- `world.spawn(species, x, y, stratum=world.SURFACE, energy=100.0, z=0.0)` — owned species only.
- `world.remove(handle)` — owned species only.

### Queries (any species)
- `world.entities(species) -> list[handle]` · `world.count(species) -> int`
- `world.pos(handle) -> (x, y, z)` · `world.get(handle, prop) -> float` (props: declared slots, `"energy"`, `"age"`)
- `world.nearest(handle, species=None, radius=10.0) -> handle | None` — same stratum only.
- `world.within(handle, radius, species=None) -> list[handle]` — same stratum only.

### Mutations (owned entities only)
- `world.move(handle, dx, dy, dz=0.0)` — positions clamp to world bounds.
- `world.set(handle, prop, value)` — props: declared slots or `"energy"`.
- `world.set_stratum(handle, stratum)` — must be in the species' declared strata.

### Interaction (any species)
- `world.attack(handle, amount) -> float` — engine-mediated predation: drains up
  to `amount` energy from the target (applies at tick end, after the target's own
  writes). Returns the expected gain — credit it to your own entity via `set`.

### Environment
- `world.flora_at(x, y) -> 0..1` · `world.eat_flora(x, y, amount) -> eaten` (immediate)
- `world.water_at(x, y) -> bool` · `world.height_at(x, y) -> 0..1` · `world.temperature_at(x, y)`
- `world.weather() -> {"temperature", "precipitation"}` (world means)
- `world.season() -> 0..1` (0 spring equinox) · `world.day_frac() -> 0..1` (0 midnight, 0.5 noon)
- `world.random_surface_point() -> (x, y)` — random non-water location.

### State & randomness
- `world.rng` — NumPy Generator, seeded per plugin: `world.rng.random()`,
  `world.rng.uniform(a, b)`, `world.rng.integers(a, b)`.
- `world.store` — plugin-scoped persistent key-value state (int/float/str,
  snapshot-included): `world.store.get(key, default)`, `world.store.set(key, value)`.
- Strata constants: `world.UNDERGROUND` (0), `world.SURFACE` (1), `world.SKY` (2).

## Reference plugins

`plugins_examples/grazer.py` (herbivore: graze, flee predators, disperse,
reproduce), `plugins_examples/predator.py` (hunt via `attack`, starve, reproduce),
`plugins_examples/birds.py` (flocking with `world.store` shared heading + per-entity
prop state). These are the canonical idioms — imitate their structure.

## What gets your candidate rejected

Static validation failure → one repair round-trip with machine-readable reasons →
dropped. Passing validation but crashing, stalling (per-tick > budget), exceeding
memory, or getting quarantined in the shadow simulation → disqualified. Surviving
the shadow run but scoring below the baseline control run on ecological fitness
(diversity, stability, no extinctions, trophic balance) → not promoted. Every
outcome is recorded in the lab notebook with reasons.
