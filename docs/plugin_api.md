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
    "name": "example_plugin",         # ^[a-z][a-z0-9_]{0,30}$ — INVENT your own name
    "contract": 1,                    # exactly 1
    "species": ["example_species"],   # species this plugin OWNS (writes require ownership)
    "lineage_parent": None,           # or the parent plugin's name if this mutates it
}

def setup(world):
    """Runs once at promotion. Register species, spawn the initial population."""

def on_tick(world):
    """Runs every tick, in plugin promotion order."""
```

> **`example_plugin` / `example_species` are PLACEHOLDERS.** Never ship them —
> and don't fall back on a "vole": choose a fresh name and animal that fits the
> niche your strategy is targeting (a sky forager, a swimmer, an omnivore, a
> hider…). Reusing the placeholder or defaulting to the same creature every time
> is a failure to read the world.

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
- `world.register_species(name, size=1.0, color="#rrggbb", speed=2.5, swim_speed=0.0, lifespan=0, strata=(world.SURFACE,), props=("hunger",))`
  — only in `setup`, only names declared in `PLUGIN_META["species"]`. `props` are
  per-entity float slots (max 8). **`speed` is the engine-enforced maximum distance
  per tick** (clamped to 0.1–8.0): whatever you pass to `move()`, an entity's net
  displacement per tick never exceeds its species speed. **`swim_speed`** (0–8) makes
  the species aquatic-capable: on water it moves at `swim_speed` instead of `speed`,
  and it never drowns; `swim_speed=0` means water is lethal. **`lifespan`** (ticks,
  0 = immortal) gives old-age death: the engine removes entities older than it
  (death cause `old_age`). Faster species should cost more energy per tick — that's
  your design responsibility, and the fitness function punishes free lunches.

### Writing a STABLE predator (read this before adding one)
The most common failure is a predator that hunts so well it exterminates its prey
and then starves — wiping out two species. A sustainable predator:
- **Only hunts when hungry** (e.g. `if energy < threshold`), so well-fed predators
  leave prey alone and the prey population recovers.
- **Reproduces slowly and capped**, ideally tied to prey abundance (fewer prey ⇒
  fewer predators), not on a fixed timer.
- **Gives prey escape**: modest speed/attack, a real chance to flee.
- Expect the fitness function to reject candidates that crash any existing species
  toward extinction in the shadow run — a predator that halves its prey scores badly.

### Reviving an extinct species
An extinct species name (0 living members) is FREE to reclaim: just
`register_species` it again (same name is fine) — you don't need lineage_parent for
a dead species. Reusing a live species' name still fails; mutate a LIVE plugin via
`PLUGIN_META['lineage_parent']` instead. Give a brand-new plugin a unique `name`.

### Extinction is remembered
When a species that once had a population dies out completely, it is moved to the
world's **extinction ledger** (surfaced in the observation report as
`extinct_species`) and its plugin is retired. Don't blindly recreate a species the
ledger shows already failed under the same conditions — mutate the approach instead
(fix its food source, its stratum, its reproduction) or fill a genuinely different
niche. Predators especially: make sure your prey is reachable (same stratum, or
sense-then-surface) or the species starves and joins the ledger.

### Design for MANY species, not big herds
The world's goal is a rich web of *many* species, and total entity count — not
species count — is what limits performance. So:
- **Keep populations modest.** A stable species of 100–400 is worth far more than a
  herd of 1000; the fitness function penalizes piling on biomass and rewards
  diversity gained per entity added.
- **Prefer small founder counts and slow, gated reproduction** (see below) so a new
  species settles at a low equilibrium instead of sprinting to the cap.
- **Vectorize decorative/ambient species.** If a species just drifts (plankton,
  insects, background flora-eaters), update it in bulk over its whole population
  rather than heavy per-entity logic, so it costs almost nothing.

### Engine-enforced population limits (you get these for free)
- **Per-species hard cap** (default 1000): spawns beyond it are dropped (counted in
  `spawnDrops`), never an error. Design for a stable population well under the cap.
- **Crowding stress** (death cause `crowding`): an entity with too many same-species
  neighbours nearby loses energy proportional to the local overcrowding — a built-in,
  non-catastrophic overpopulation brake (competition/disease/stress). You don't code
  it, but you benefit: spreading out and not over-breeding avoids it.
- **Starvation** (`energy <= 0`) and **drowning** are also engine-mediated. Reproduction
  should be gated on food/energy so scarcity naturally curbs growth.
- `world.spawn(species, x, y, stratum=world.SURFACE, energy=100.0, z=0.0)` — owned species only.
- `world.remove(handle)` — owned species only.

### Queries (any species)
- `world.entities(species) -> list[handle]` · `world.count(species) -> int`
- `world.pos(handle) -> (x, y, z)` · `world.get(handle, prop) -> float` (props: declared slots, `"energy"`, `"age"`)
- `world.nearest(handle, species=None, radius=10.0, stratum=None) -> handle | None` — searches
  the caller's own stratum by default. Pass `stratum=world.SURFACE` (etc.) to **sense the other
  layer** — e.g. a flyer spotting prey on the ground. To *interact* (attack) across layers it
  must still `set_stratum` onto that layer first; sensing alone doesn't move you. Hidden
  creatures are never returned.
- `world.within(handle, radius, species=None, stratum=None) -> list[handle]` — same rules.

### Mutations (owned entities only)
- `world.move(handle, dx, dy, dz=0.0)` — positions clamp to world bounds.
- `world.set(handle, prop, value)` — props: declared slots or `"energy"`.
- `world.set_stratum(handle, stratum)` — must be in the species' declared strata.
- `world.hide(handle, hidden=True)` / `world.is_hidden(handle)` — **ability/state**:
  a hidden (burrowed/camouflaged) creature is invisible to every other creature's
  `nearest`/`within`. It can still sense the world. Use it to escape predators or
  ambush prey. Make it cost something (a hidden creature usually can't forage).

### Interaction (any species)
- `world.attack(handle, amount) -> float` — engine-mediated predation: drains up
  to `amount` energy from the target (applies at tick end, after the target's own
  writes). Returns the expected gain — credit it to your own entity via `set`.

**Where predation code belongs.** You may only *mutate* species you own, but
`attack` works on any target — do not abuse that. The hunter drives predation:
put `nearest`+`attack` in the PREDATOR's `on_tick`, not in the prey's. Writing a
prey plugin that makes its own predator eat it (or that attacks the predator to
"defend" itself) is backwards and will be scored down. To give an existing prey a
new predator, MUTATE the predator (lineage) so it hunts that prey.

### Give EXISTING creatures new abilities — don't just add species
Prefer *mutating a live plugin* (`PLUGIN_META['lineage_parent']`) to grant its
species a new behaviour over inventing yet another species. Abilities you can
express with the existing API: **hiding/burrowing** (`hide`), **camouflage**
(hide when a predator nears), **ambush** (predator hides, then strikes when prey
is adjacent), **nocturnality** (act on `daylight`), **fleeing/herding**
(`direction_to`), **caching** (`world.store`). A richer web often comes from
smarter existing species, not more of them.

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
- Strata constants: `world.SURFACE` (the ground) and `world.SKY` (the air). There
  are only these two — there is no underground layer. A creature "goes to ground"
  via `hide()`, not a separate stratum.

## Diet is emergent, not a declared type

There is no "herbivore/carnivore" flag — an animal's diet is simply which intake
calls its `on_tick` makes. Grazing = `world.eat_flora(x, y, amount)`. Predation =
`world.nearest(me, species=..., radius=r)` then `world.attack(prey, amount)`. An
**omnivore** does both: hunt when prey is near, graze otherwise. A **scavenger**
could gain energy near recently-dead entities. Mix freely — generalists stabilize
an ecosystem that specialists make fragile.

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
