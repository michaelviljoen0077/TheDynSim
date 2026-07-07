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
- **No `set`/`frozenset`/`hash`/`id`** (including set literals `{a, b}` and set
  comprehensions): their behavior varies between processes, which breaks
  deterministic replay. Use lists, dicts, or sorted sequences instead.
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
- **Population quotas are soft.** Spawns beyond the per-tick or per-plugin caps
  are silently dropped and counted (`spawnDrops` in plugin status) — a booming
  population is an environmental limit, never an error. Capability violations
  (touching another plugin's species, unknown props) DO raise, and repeated
  errors quarantine the plugin.
- **Water:** SURFACE entities on open water drown (engine drains 0.8 energy/tick,
  death cause `drowning`) unless their species declared `swim_speed > 0` — and
  swimmers move at `swim_speed` (not land speed) while on water.

## WorldAPI

### Species & lifecycle
- `world.register_species(name, size=1.0, color="#rrggbb", speed=2.5, swim_speed=0.0, strata=(world.SURFACE,), props=("hunger",))`
  — only in `setup`, only names declared in `PLUGIN_META["species"]`. `props` are
  per-entity float slots (max 8). **`speed` is the engine-enforced maximum distance
  per tick** (clamped to 0.1–8.0): whatever you pass to `move()`, an entity's net
  displacement per tick never exceeds its species speed. **`swim_speed`** (0–8) makes
  the species aquatic-capable: on water it moves at `swim_speed` instead of `speed`,
  and it never drowns; `swim_speed=0` means water is lethal. Faster species should
  cost more energy per tick — that's your design responsibility, and the fitness
  function punishes free lunches.
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
- `world.size` — world edge length; positions span `0 .. world.size - 1`.
- Strata constants: `world.UNDERGROUND` (0), `world.SURFACE` (1), `world.SKY` (2).

## Reproduction (design guidance, not an engine feature)

The engine has NO built-in breeding — reproduction is behavior you author, which
means **litter size and gestation period are yours to set per species** and should
differ by animal. The reference plugins show the pattern: a `gestation` prop holds
a single countdown (one pregnancy at a time — a pregnant animal can't start
another), started only when the parent is mature (`age`) and has an energy surplus,
paid for with an up-front energy cost, and yielding a species-appropriate litter at
term. Grazers: ~160-tick pregnancy, litter 1–2. Wolves: ~60-tick, single cub. Make
small fast breeders cheap and prolific, large slow ones costly and single-young —
runaway breeders overcrowd and score badly on stability.

## Refining an existing system (lineage mutation)

You don't have to add something new — you can **rework a live plugin**. Set
`PLUGIN_META["lineage_parent"]` to the name of an installed plugin and declare
(some of) its species: on promotion, the parent plugin is **retired** and your
plugin **adopts its species and all living entities**. `register_species` for an
adopted species updates its size/color/speed but keeps its prop-slot layout
(live entities carry data in those slots). Your `setup` still runs — spawn extras
only if the population genuinely needs reinforcement. This is the right move when
the observation report shows an existing species is unstable, mis-tuned, or
wasting a niche.

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
