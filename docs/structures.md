# Genesis v2 — Structures Feature Spec (BMAD)

**Version:** 0.1 (proposal)
**Date:** 2026-07-07
**Status:** Draft for operator review — NOT yet implemented
**Author:** Architect (Claude) with Michael
**Inputs:** `docs/brief.md`, `docs/prd.md`, `docs/architecture.md`, `docs/plugin_api.md`

---

## 1. Brief

### Problem / opportunity
Today plugins can create **mobile agents** (fauna) and influence the engine's **flora
field**, but there is no way for the AI to build *persistent, static, functional
things in the world* — nests, burrows, dams, hives, termite mounds, food caches,
territory markers. The brief lists a "civilizational layer (agents building
structures, economies, cultures)" as explicitly **out of MVP scope**, but a
*primitive* structure substrate is a natural, high-impact next step: it lets the
governor evolve **niche construction** — animals that reshape their environment —
which is one of the richest sources of emergent ecological complexity.

### What a "structure" is (and is not)
- **Is:** a placed, persistent, non-moving world object with a position, a type, an
  owner plugin, a small state bank, and a health/decay value. Other entities can
  sense it, use it, damage it, and be affected by it.
- **Is not (this version):** multi-tile buildings, pathfinding/among structures,
  economies, or inventories. Those stay in the post-MVP civilizational layer.

### Goals
- G1 — Plugins can build, sense, use, and destroy structures through `WorldAPI` only.
- G2 — Structures are **snapshot-complete and deterministic** (same guarantees as
  entities): identical seed + intervention log ⇒ identical structures at tick N.
- G3 — Structures are **first-class in the safety pipeline**: shadow-tested, quota-
  limited, no new I/O or escape surface.
- G4 — Structures render in the Observatory as a distinct visual layer.
- G5 — Fitness can reward niche construction (a new sub-score), so the governor has
  a reason to build.

### Non-goals (this version)
Multi-cell footprints, structure-to-structure connections, resource economies,
ownership transfer, structure "AI". All deferred.

---

## 2. Requirements

### Functional
- **SR1:** A dedicated **StructureStore** (SoA arrays, generational handles, freelist)
  holds ≥ 20,000 structures with `type_id, pos(x,y), stratum, owner_plugin, health,
  props[K]`. Structures do not move (no velocity, no per-tick position write).
- **SR2:** Plugins register **structure types** in `setup` (like species): name, color,
  size, max_health, decay_per_tick, blocks_movement flag, declared prop slots.
- **SR3:** `WorldAPI` gains a capability-scoped structure surface (build/remove/query/
  sense/damage/repair/prop get-set), owner-scoped for mutation exactly like species.
- **SR4:** Structures **decay**: the engine subtracts `decay_per_tick` each tick;
  at health ≤ 0 the structure is removed with cause `decay` in a structure death
  ledger. Plugins repair to keep them alive — an ongoing energy/behavior cost.
- **SR5:** Structures are queryable spatially (own per-stratum structure hash) so a
  bird can find "nearest nest within r" cheaply.
- **SR6:** Structure build/decay/destroy events feed the observation report and the
  death ledger, so the governor can reason about them.

### Non-functional
- **SNFR1:** Snapshot/restore includes all structure state and the structure type
  registry; state-hash determinism tests extended to cover structures.
- **SNFR2:** Structure updates are vectorized (decay is one array op); the structure
  spatial hash rebuilds only when the structure set changes (structures are static,
  so this is rare — a major perf win over the entity hash).
- **SNFR3:** Per-plugin structure quota (default 2,000) and per-tick build cap
  (default 50), enforced as **soft drops** (consistent with the spawn-quota fix).
- **SNFR4:** No new import/IO/escape surface; structure source passes the same AST
  validator; the wire protocol gains one binary frame kind (no schema-breaking change
  to existing frames).

---

## 3. Architecture

### Data model (engine-owned)
```
StructureStore (SoA):  id, generation, type_id, px, py, stratum,
                       owner_plugin, health, alive, props[K]
StructureType:         id, name, plugin, color, size, max_health,
                       decay_per_tick, blocks_movement, prop_slots{name:slot}
```
Mirrors `EntityStore` / `SpeciesRegistry` deliberately — same freelist, generational
handles, and snapshot mechanics, so it reuses proven code paths.

### Tick order (extends `World.step`)
```
weather -> flora -> spatial.rebuild -> structure_hash.rebuild_if_dirty
  -> plugin on_tick (build/use via command buffer)
  -> commands.apply (structure builds/removes/damage applied here, deterministically)
  -> structure_decay (vectorized: health -= decay; sweep health<=0)
  -> water/death sweeps -> age -> tick++
```
All structure mutations are **command-buffered** like entity mutations — reads see
tick-start state, order-independent within a tick.

### WorldAPI additions (capability-scoped)
```python
world.register_structure_type(name, color, size=1.0, max_health=100.0,
                              decay_per_tick=0.05, blocks_movement=False, props=())
world.build(type_name, x, y, stratum=SURFACE, health=None) -> None   # buffered, owner-only
world.remove_structure(handle)                                        # owner-only
world.structures(type_name) -> list[handle]        # any type (read)
world.nearest_structure(x, y, type_name=None, radius=r) -> handle | None
world.structure_pos(handle) -> (x, y, stratum)
world.structure_health(handle) -> float
world.damage_structure(handle, amount) -> float    # engine-mediated (any type)
world.repair_structure(handle, amount)             # owner-only
world.structure_prop_get/set(handle, name)         # owner-only for set
```
Quota violations are **soft drops** (`structure_drops` counter), never exceptions.

### Determinism & safety
- Generational handles + freelist (stale handle = recorded no-op).
- Per-plugin RNG unchanged; build positions from `world.rng` stay deterministic.
- Snapshot header gains `structure_types`; arrays gain the structure SoA — the
  existing `_dump_header` / `state_hash` machinery covers it for free.
- AST validator unchanged (no new imports/calls); new API methods are just facade
  methods on the object plugins already receive.

### Rendering (Observatory)
- New binary frame **kind 4 — structures**: `[kind, tick, epoch, n]` header then
  `id[], type[], x[], y[], stratum[], health_norm(u8)[]`. Sent on change + every
  Nth frame (structures are static, so low-rate).
- One `InstancedMesh` per structure type (box/cylinder glyph, color from type,
  scaled by health). A new "Structures" stratum-style toggle in the HUD.

### Fitness (governor)
- New sub-score **niche_construction**: rewards a candidate whose structures persist
  (are repaired, not just spam-built then decayed) AND correlate with a stability or
  diversity gain vs control. Weight defaults low (0.5) to avoid build-spam gaming;
  build-then-abandon nets ~0 because decayed structures don't count.

---

## 4. Epic & stories (implementation plan)

**Epic 5 — Structure Substrate** (fits after Epic 4; independent of governor changes)

- **Story 5.1 — StructureStore & type registry.** SoA store, generational handles,
  registry, snapshot round-trip + determinism test. *(engine only, no API yet)*
- **Story 5.2 — Structure command-buffer ops & decay.** Buffered build/remove/damage/
  repair; vectorized decay + sweep with `structure_deaths` ledger; unit tests.
- **Story 5.3 — WorldAPI structure surface + quotas.** Capability scoping, soft-drop
  quotas, structure spatial hash; validator battery unchanged (prove no new surface).
- **Story 5.4 — Example plugin: `beaver_dam`.** A surface builder that dams water
  cells (raises local flora), repairs its dams, and proves the whole path end-to-end
  through shadow testing.
- **Story 5.5 — Wire protocol kind-4 + Observatory structure layer.** Binary frame,
  instanced rendering, HUD toggle, inspector support.
- **Story 5.6 — Fitness niche_construction sub-score + prompt/API-doc update.** Teach
  the governor structures exist and when to use them; add the sub-score; extend
  `docs/plugin_api.md`.

### Estimated effort
~2–3 focused sessions. 5.1–5.3 are the load-bearing half (mirror the entity code);
5.4–5.6 are a builder plugin, a render layer, and a scoring term.

### Risks
| Risk | Mitigation |
|---|---|
| Build-spam gaming the fitness | Only *persisting* (repaired) structures score; decayed ones net ~0; per-tick build cap. |
| Perf: another spatial hash | Structures are static — rebuild only on change, far cheaper than the entity hash. |
| Scope creep toward "buildings" | This spec is deliberately single-cell, stateful-but-dumb; multi-tile is out. |
| `blocks_movement` + command buffer interaction | v1 ships with `blocks_movement=False` only; collision is a fast-follow once the substrate is proven. |

---

## 5. Decision needed from operator
1. Green-light Epic 5 as scoped (single-cell functional structures), or trim further
   for a first cut (e.g. ship 5.1–5.4 as "structures exist and one plugin uses them",
   defer rendering/fitness)?
2. Confirm `blocks_movement` deferred to a fast-follow (keeps v1 simple).
3. Priority vs the other open items (escalation policy, pipelining, soak test).
