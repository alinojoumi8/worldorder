# C06 — Grid generation, places, pathfinding, movement, housing

**M1** · `polis/world/` · **Depends on** C01 C02 C03 C04 · **Blocks** C07 C10 C11 C12 C16 C19 C20 C21 C23a · **Size L**

---

## 1. Context

The grid is a research instrument, not a renderer. It exists to produce three primitives and nothing else: the `district_id` of an agent's home, the `place_id` two agents share at a tick, and `travel_ticks(a, b)`. Those three are what turn homophily from a parameter you set into a quantity you measure, and they are the whole causal chain behind research questions A2, B3, and B4. This chunk builds the world once at tick 0 into a hash-sealed, immutable topology, then runs the four things that move over it every tick: movement, co-location, district property dynamics, and the housing market. Five of this module's fourteen mechanisms produce district-level correlations that look exactly like the findings the project is hunting for, so every one of them is tagged and ablatable.

## 2. Required reading

| Document | Sections | Why |
|---|---|---|
| `docs/02-ARCHITECTURE.md` | all | Binding. §4 determinism, §5 tick phases, §5.1 resolution order, §7.1 imports, §8.1 MECHANISM |
| `docs/03-DATA-MODEL.md` | §0, §2.1, §2.5, §3, §12 | Binding. `districts`, `places`, `tiles`, `place_paths`, movement columns on `agents`, `households` |
| `docs/05-WORLD-SPEC.md` | **all, in full** | This chunk implements it. §2 generation, §3 mechanisms, §4 places, §5 movement, §6 co-location, §7 housing, §8 clock, §9 API, §10 zone mode, §11 rendering, §13 kinds, §14 config, §15 register |
| `docs/04-AGENT-SPEC.md` | §5, §7, §8, §11 | You supply `PlaceView` and `co_located`; you own the Locality gate |
| Chunk interfaces consumed | C01 config, C02 `Event`/kind registry, C03 repositories, C04 `RngRegistry`/`Clock`/`stable`/`@mechanism` | |

If this brief conflicts with `02` or `05`, those win. Stop and flag it.

## 3. Scope — in

1. **Generation** — `05 §2.1` steps 1–13 verbatim, producing `tiles`, `districts`, `places`, `place_paths` and a `world_hash`. Both `grid` and `zone` modes (`05 §10`).
2. **The tile array** — one `numpy.ndarray[int8]`, loaded once, `writeable = False`, private to `polis/world/grid.py`.
3. **All-pairs routing** — Dijkstra sweep in a process pool, turn-point polylines only, `travel_ticks` per `05 §5.2`, symmetry asserted.
4. **Movement** — `path_cursor` multi-tick journeys, retarget, abandon, bounce, strand; resolved as PHASE 5 **step 1**, before every other institution.
5. **Occupancy and co-location** — `freeze_occupancy(tick)`, the ranked cap-12 slice with the 3 reserved novel slots, `FIRST_ENCOUNTER`, truncation accounting.
6. **District dynamics** — the five PHASE 7 mechanisms of `05 §3.2`–`§3.6`, integer/basis-point arithmetic throughout.
7. **Housing** — asking vs contracted rent, lease stickiness, deferred-acceptance allocation, `RENT_DUE` emission, arrears intake, eviction, homelessness, shelter tenure.
8. **`RENT_HOME`** — the ratified new ActionType. This chunk owns its pydantic params model, its validator, and its resolution into the housing step's bid pool.
9. **Time-of-day** — `PLACE_SCHEDULE`, `is_open`, weekday and term gating; `microscope` vs `chronicle` movement semantics (`05 §8.4`).
10. **Affordance** — `affords(place_id, action, tick)`, the Locality gate of `04 §11`.
11. **Rendering producers** — the static payload and the six ephemeral payloads of `05 §11`.
12. Event kinds **3000–3999** and **90000–90099**; the world invariants `INV-WORLD-POSITION`, `INV-WORLD-PATHS`, `INV-WORLD-REACHABLE`, `INV-WORLD-RENT`.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `Observation`, `SelfView`, `AgentBrief`, the perception builder | C07 |
| The reflex destination policy (`world.reflex_destination`) — you **declare** the mechanism id and `entails`, C07 **implements** it | C07 (`world` may not import `Observation`) |
| Relationship strength, kinship, household membership, prominence — you rank with them but never compute them | C16 / C20; injected as `ColocationContext` |
| Any ledger write. You emit `RENT_DUE` (3083); the economy posts the legs | C11/C14 |
| `schools.quality`. You own `districts.school_quality` and emit 3063; education applies `quality_offset` | C21 |
| Household formation/dissolution. You expose `find_home` and nothing more | C20 |
| Crime generation. `crime_rate` is an environmental scalar that never causes a crime | C19 |
| Congestion, weather, line of sight, vehicles, per-tile rendering | nobody — `05 §5.6`, `§8.5` |
| The HTTP/WS endpoints themselves | C23 (you supply the payloads) |

## 5. Interfaces you provide

```python
# polis/world/types.py — no imports from polis.agents, polis.economy, polis.society. Ever.
from __future__ import annotations
from dataclasses import dataclass
from typing import Final, Literal, Mapping, Protocol, Sequence

PlaceType = Literal["home","office","factory","shop","school","university","bank","exchange",
                    "town_hall","courthouse","police","hospital","park","bar","newsroom",
                    "studio","shelter","prison"]
Archetype = Literal["core","uptown","midtown","industrial","suburb","periphery"]
WorldMode = Literal["grid","zone"]
GOV: Final[str] = "gov"

@dataclass(frozen=True, slots=True)
class Place:
    place_id: str; district_id: str; type: PlaceType; name: str
    x: int; y: int; capacity: int; owner_id: str
    rent_cents: int; base_rent_cents: int
    open_from_hour: int; open_to_hour: int          # half-open, evaluate to_hour % 24
    dwelling_class: Literal["detached","row","block"] | None

@dataclass(frozen=True, slots=True)
class District:
    district_id: str; name: str; archetype: Archetype
    x0: int; x1: int; y0: int; y1: int               # half-open, from bbox INT4RANGE[]
    land_value_cents: int; rent_index_bp: int
    school_quality: float; crime_rate: float; amenity_score: float
    accessibility_bp: int; buildable_tiles: int

@dataclass(frozen=True, slots=True)
class Location:
    place_id: str | None            # None <=> in transit
    dest_place_id: str | None
    path_cursor: int | None         # ticks already travelled, 0-based
    travel_ticks: int | None
    district_id: str                # defined in transit too
    x: int; y: int                  # RENDER ONLY. Reading these outside polis/world/ is a bug.

@dataclass(frozen=True, slots=True)
class PlaceView:                    # consumed by C07's Observation.place
    place_id: str | None; place_type: PlaceType | None; name: str
    district_id: str; district_name: str
    in_transit: bool; eta_ticks: int | None
    occupancy: int; capacity: int; is_open: bool
    safety: float                   # 1 - district.crime_rate
    amenity: float; rent_cents: int | None
    legal_action_types: frozenset[str]   # ActionType.value, filtered by affords()

@dataclass(frozen=True, slots=True)
class ColocationContext:             # supplied by the caller; world computes none of it
    tie_strength: Mapping[str, float]        # other_id -> 0..1, absent == 0
    kin_or_partner: frozenset[str]
    household_members: frozenset[str]
    colleagues: frozenset[str]
    addressed_me_last_tick: frozenset[str]
    ever_co_located: frozenset[str]
    prominence: Mapping[str, float]          # 0..1

@dataclass(frozen=True, slots=True)
class DistrictInputs:                # district-keyed, one map per field; PHASE 7
    residents: Mapping[str, int]
    poverty_share_bp: Mapping[str, int]
    pupils: Mapping[str, int]
    detected_crimes_7d: Mapping[str, int]

@dataclass(frozen=True, slots=True)
class HouseholdFinancials:
    household_id: str; income_cents: int; liquid_cents: int
    size: int; has_children: bool
    anchor_place_ids: tuple[str, ...]        # workplaces and schools of members
```

```python
# polis/world/generate.py
def generate_world(cfg: WorldConfig, rng: RngRegistry) -> GeneratedWorld: ...
def world_hash(g: GeneratedWorld) -> str: ...
@dataclass(frozen=True, slots=True)
class GeneratedWorld:
    mode: WorldMode; width: int; height: int
    terrain: "np.ndarray"                                    # int8, C-order, writeable=False
    districts: tuple[District, ...]; places: tuple[Place, ...]
    travel_ticks: "np.ndarray"                               # int16 [n_places, n_places]
    distance_tiles: "np.ndarray"                             # int32 [n_places, n_places]
    polylines: Mapping[tuple[int, int], tuple[int, ...]]     # turn points only
    place_index: Mapping[str, int]; world_hash: str
```

```python
# polis/world/api.py — the ONLY module other packages import
class World(Protocol):
    frozen_at_tick: int
    # topology (immutable after generation)
    def place(self, place_id: str) -> Place: ...
    def places_of_type(self, type: PlaceType, district_id: str | None = None) -> tuple[Place, ...]: ...
    def district(self, district_id: str) -> District: ...
    def district_of(self, place_id: str) -> str: ...
    # routing — O(1) matrix lookup, never A*
    def travel_ticks(self, a: str, b: str) -> int: ...
    def distance_tiles(self, a: str, b: str) -> int: ...
    def reachable(self, a: str, b: str) -> bool: ...
    def nearest_place(self, x: int, y: int, type: PlaceType | None = None) -> str: ...
    # live state — the most recently frozen snapshot (see §9.3)
    def occupants(self, place_id: str) -> tuple[str, ...]: ...
    def occupancy(self, place_id: str) -> int: ...
    def free_capacity(self, place_id: str) -> int: ...
    def is_open(self, place_id: str, tick: int) -> bool: ...
    def location_of(self, agent_id: str) -> Location: ...
    def place_view(self, agent_id: str, tick: int) -> PlaceView: ...
    def co_located(self, agent_id: str, tick: int, ctx: ColocationContext,
                   cap: int = 12) -> tuple[str, ...]: ...
    # affordance — the Locality gate of 04 §11
    def affords(self, place_id: str | None, action_type: str, tick: int,
                *, in_transit: bool = False, counterparty_present: bool = False) -> bool: ...
    # housing
    def rent_cents(self, place_id: str) -> int: ...
    def vacant_homes_for(self, household_id: str, size: int) -> tuple[Place, ...]: ...
    def find_home(self, members: Sequence[str], budget_cents: int,
                  anchors: Sequence[str]) -> str | None: ...
    def home_of(self, household_id: str) -> str | None: ...
    # mutation — engine only
    def create_place(self, type: PlaceType, district_id: str, owner_id: str,
                     capacity: int, tick: int) -> Place: ...
    def close_place(self, place_id: str, reason: str, tick: int) -> None: ...
```

```python
# polis/world/movement.py — PHASE 5, step 1. Called before every other institution.
def resolve_movement(actions: Sequence[Action], w: WorldState, tick: int,
                     rng: RngRegistry) -> list[Event]: ...
def freeze_occupancy(w: WorldState, tick: int) -> None: ...

# polis/world/steps.py — PHASE 7, in this order, called by the scheduler
def rent_due_step(w: WorldState, tick: int) -> list[Event]: ...          # W1
def housing_step(w: WorldState, fin: Mapping[str, HouseholdFinancials],
                 tick: int, rng: RngRegistry) -> list[Event]: ...        # W3a, sim-weekly
def eviction_step(w: WorldState, arrears_cents: Mapping[str, int],
                  tick: int) -> list[Event]: ...                          # W3b
def district_step(w: WorldState, inputs: DistrictInputs, tick: int,
                  rt: RuntimeConfig) -> list[Event]: ...                  # W3c

# polis/world/actions.py — this chunk owns these validators (C10 dispatches to them)
class RentHomeParams(BaseModel):
    place_id: str
    offered_rent_cents: int = Field(ge=0)
def validate_rent_home(a: Action, w: WorldState, hh: HouseholdFinancials,
                       tick: int) -> RejectReason | None: ...
def validate_move_to(a: Action, w: WorldState, tick: int) -> RejectReason | None: ...

# polis/world/render.py — data producers for C23; this chunk serves no HTTP
def static_payload(w: WorldState) -> Mapping[str, object]: ...
def ephemeral_payloads(w: WorldState, tick: int,
                       tracked: Sequence[str]) -> list[tuple[int, Mapping[str, object]]]: ...

# polis/world/invariants.py
def inv_world_position(w: WorldState) -> Ok | Violation: ...   # every tick, HALT
def inv_world_paths(w: WorldState) -> Ok | Violation: ...      # every checkpoint, HALT
def inv_world_reachable(w: WorldState) -> Ok | Violation: ...  # every checkpoint, HALT
def inv_world_rent(w: WorldState, median_income_cents: int) -> Ok | Violation: ...  # daily, WARN
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C01 | `polis.config.settings.WorldConfig`, `ClockConfig`, `MechanismsConfig` | the `world:` block of `05 §14` |
| C01 | `polis.config.runtime.RuntimeConfig.get(param, tick)` | `education.spend_cents_per_student`, `police.budget_cents`, `regulation.housing.rent_cap_pct` — **read per call, never cached across ticks** |
| C02 | `Event`, `EventRef`, `KIND_REGISTRY`, `register_kind` | your kinds go in `polis/events/kinds.py`, range 3000–3999 / 90000–90099 |
| C03 | repositories for `districts`, `places`, `tiles`, `place_paths`, `households`, and the movement columns of `agents` | batched writes only |
| C04 | `RngRegistry.get(ns, entity_id, tick)`, `Clock` (`sim_day`, `hour`, `weekday`, `ticks_per_sim_day`, `profile`), `polis.kernel.det.stable`, `@mechanism(id, entails=)`, `InvariantRunner` | |

You import **nothing** from `polis.agents`, `polis.economy`, or `polis.society`. `import-linter` enforces it.

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `districts` | write at gen, update PHASE 7 | `land_value_cents`, `school_quality`, `crime_rate`, `amenity_score`. `rent_index_bp` is in-memory only (not a column) and is rebuilt from 3061 |
| `places` | write at gen, `rent_cents` weekly, `owner_id` on transfer, insert on `create_place` | |
| `tiles` | write at gen only; empty in `zone` mode | Never read row-wise in a tick |
| `place_paths` | write at gen, one row+column on `create_place` | 129,600 rows at defaults. `path` = turn points only |
| `agents` | read/write `current_place_id`, `dest_place_id`, `path_cursor`, `pos_x`, `pos_y` | You touch no other column |
| `households` | read/write `home_place_id`, `tenure`, `rent_cents` | `tenure` gains `shelter` (ratified) |
| `schools` | **read `place_id` only** | Quality is C21's write |

Additive indexes (`05 §5.3`): `ag_transit`, `pl_home_vacant`. Two additive vocabulary values, no DDL: `places.type += shelter, prison`; `households.tenure += shelter`.

## 8. Event kinds owned

Range **3000–3999** persisted, **90000–90099** ephemeral. Register every one in `polis/events/kinds.py` with a payload JSON Schema. The full table is `05 §13` — implement it exactly. Highlights and the three kinds this chunk **adds** inside its range:

| Kind | Name | Payload |
|---|---|---|
| 3001 | `AGENT_MOVED` | `agent_id, from_place_id, to_place_id, from_district_id, to_district_id, distance_tiles, travel_ticks, origin` |
| 3002 / 3004 / 3005 / 3006 | `JOURNEY_STARTED` / `MOVE_BLOCKED` / `JOURNEY_ABANDONED` / `AGENT_STRANDED` | `05 §13` |
| 3010 / 3020 / 3021 | `PLACE_OCCUPANCY` / `FIRST_ENCOUNTER` / `COLOCATION_SLICE_TRUNCATED` | 3021 sampled at `cognition_sample_rate` |
| 3040–3045 | place lifecycle and leases | |
| **3046** | **`HOME_BID_PLACED`** *(added)* | `household_id, place_id, offered_rent_cents, expires_tick, origin` — the `RENT_HOME` resolution |
| **3047** | **`HOME_BID_EXPIRED`** *(added)* | `household_id, place_id, offered_rent_cents, rounds_unmatched` |
| **3048** | **`HOME_BID_LOST`** *(added)* | `household_id, place_id, offered_rent_cents, winning_rent_cents` |
| 3060–3065 | district metrics, one per mechanism, each carrying `mechanism` id, inputs and outputs | |
| 3080–3090 | housing: listing, allocation, search failure, `RENT_DUE`, arrears, eviction, displacement, relocation, lease renewal, homelessness in/out | |
| 3100–3103 | `WORLD_GENERATED`, `PATHS_PRECOMPUTED`, `INFRASTRUCTURE_BUILT`, `WORLD_PARAMS_CHANGED` | |
| 3900 | `WORLD_INVARIANT_WARNING` | |
| 90010, 90050–90054 | ephemerals of `05 §11.2` | never persisted; published to Redis in PHASE 6 and dropped |

## 9. Implementation notes

**9.1 Generation ordering.** Every stage in `05 §2.1` iterates a sorted sequence. District BSP splits the largest leaf next, ties broken by `(-area, x0, y0)`. Place siting scores candidates by `road_adjacency * 2 + centrality` and breaks ties on `(y, x)`. Dijkstra breaks ties on `(cost, y, x)`. There is no `set` iteration and no `dict` iteration anywhere in the generator. `world_hash` is `sha256` over the canonical serialisation of `tiles ‖ districts ‖ places ‖ travel_ticks` — not over the polylines, which are render-only and must not gate reproducibility.

**9.2 Pathfinding.** 360 Dijkstra sources over ~40,000 nodes. Run in a `ProcessPoolExecutor`; each worker returns `(source_index, dist_array, parent_array)` and mutates nothing. Reduce in source order. Compute each unordered pair once and mirror it into both matrix cells — do **not** run Dijkstra in both directions and hope symmetry holds. Assert `travel_ticks[i,j] == travel_ticks[j,i]` and that `place_paths` has exactly `n²` rows before sealing.

**9.3 The two ages of occupancy.** `freeze_occupancy(tick)` runs at the end of PHASE 5 step 1 and sets `frozen_at_tick = tick`. Everything later in PHASE 5 of tick *T* reads the snapshot frozen at *T*. PHASE 1 of tick *T+1* runs before movement, so the most recent snapshot is still *T*'s — which is exactly the "last tick's committed state" that `04 §5` rule 1 demands. One method, `occupants()`, serves both; correctness comes from phase ordering, not from a tick argument. Assert `frozen_at_tick == tick` at the top of PHASE 5 institutions and `frozen_at_tick == tick - 1` at the top of PHASE 1.

**9.4 Movement resolution.** Implement `05 §5.4` literally and in that order: (1) auto-advance every in-transit agent, collecting arrivals; (2) interpret `MOVE_TO`, which may abandon a journey and restart from `nearest_place(x, y)`; (3) admit per place, over the *complete* claimant set, sorted by `(-priority_class, lottery, agent_id)`. `free = capacity - occupancy_excluding(place_id, claim)` — the exclusion is what stops an agent already resident there from being counted twice when it re-arrives. Blocked travellers hold at `path_cursor = travel_ticks - 1`, never at `travel_ticks`, or `INV-WORLD-POSITION` fires next tick.

**9.5 Co-location ranking.** `05 §6.2`'s score, with three slots reserved for agents in `place_occupants - ctx.ever_co_located`, filled before the general ranking; unused reserved slots fall back. `ε ~ U(0, colocation_epsilon)` from `rng.get("world.colocate", self_id, tick)` — a single `Random` per observer-tick, drawn in sorted occupant order. Emit `FIRST_ENCOUNTER` at most once per unordered pair per run; keep the seen-pairs set as a `frozenset` of `f"{a}|{b}"` with `a < b`, checkpointed. In M1, `ColocationContext` arrives with empty maps and the score degrades to prominence + novelty + ε; that is expected and must not crash.

**9.6 District mechanisms.** All five in basis points and cents, all `//`, no float touching money. Order within `district_step` is fixed: crime (daily) → rent (weekly) → amenity (weekly) → land value (monthly) → school funding (per term). Each is a separate `@mechanism`-decorated function that reads only committed state and returns `(new_value, event_payload)`. `--mechanism-off <id>` is honoured by the decorator, which short-circuits to the ablation behaviour in `05 §15`.

**9.7 `RENT_HOME`.** Resolves in PHASE 5 institution slot 10 (misc/world) into a standing bid: `bids[place_id] += (household_id, offered_rent_cents, expires_tick)`, `expires_tick = tick + housing.bid_ttl_ticks` (default 168). `housing_step` resolves bids **before** the mechanical matcher; the landlord takes `max(offered_rent_cents, income_cents, household_id)`, losers get 3048 and keep their bid until TTL. Households with a live bid are excluded from the mechanical pool. This is what makes `world.housing_match` ablatable *towards agent choice* rather than towards noise, and it is the answer to `05 §13.1`.

**9.8 Money.** The world emits `RENT_DUE` (3083) in PHASE 7 step W1 and reads an arrears map in step W3. It never constructs a `Leg` and never imports `polis.economy`. The same pattern applies to `chronicle`'s commute fare: emit `RENT_DUE`-shaped obligations, let the economy post them.

**9.9 `chronicle`.** `travel_ticks` is identically 0, `dest_place_id` and `path_cursor` are always `NULL`, the in-transit branch is unreachable but `INV-WORLD-POSITION` still checks it. `capacity` becomes `capacity * chronicle.turnover`, `open_hours` collapses to "open on this weekday", `colocation_epsilon` rises to 0.15, and `world.chronicle_nightly_return` returns housed agents home at end of tick. Cost target: the $12/sim-year figure holds only here; `microscope` is $250–400/sim-year.

**9.10 `zone` mode.** A config switch, not a fork. Skip generation steps 1–3, 6, 7, 10, 11; synthesise district rectangles and a seeded ring-plus-chord adjacency graph; `travel_ticks` = 0 same district, 1 adjacent, 2 otherwise. Everything from §3 onward is byte-identical code. If the tile layer leaks into any other module, this mode silently breaks — hence the CI grep gate on `pos_x`/`pos_y`.

## 10. Configuration keys

The complete `world:` block of `05 §14` (mode, generator_version, grid, tile_metres, districts, places_per_district, water_fraction, blocked_fraction, road_density, min_place_separation, housing_slack, `movement.*`, `colocation.*`, `housing.*`, `chronicle.*`, `render.*`), plus:

| Key | Default | Note |
|---|---|---|
| `world.housing.bid_ttl_ticks` | `168` | **added** — `RENT_HOME` bid lifetime |
| `world.colocation.cap` | `12` | must equal `04 §5`'s cap; validated at config load |
| `mechanisms.world.*` | the 14 rows of `05 §15` | every one ablatable via `--mechanism-off` |

`world.colocation.cap != 12` is a config error unless `04 §5` is amended.

## 11. Acceptance criteria

- [ ] `generate_world` produces one distinct `world_hash` across 20 regenerations from the same seed, and different hashes for different seeds.
- [ ] `|places| == districts * places_per_district`; `|place_paths| == |places|²`; `travel_ticks` is symmetric; every place is reachable from every other.
- [ ] `Σ home.capacity >= housing_slack * initial_agents`, and place *counts* were not adjusted to achieve it.
- [ ] The tile array is `writeable = False`; an attempted write raises.
- [ ] `INV-WORLD-POSITION` holds for every living agent on every tick of a 500-tick run.
- [ ] A `MOVE_TO` across the city takes ≥ 2 ticks in `microscope` and 0 ticks in `chronicle`, from identical config otherwise.
- [ ] A journey retargeted mid-flight emits `JOURNEY_ABANDONED` and restarts from `nearest_place`, never from the original origin.
- [ ] Admission to a full place is independent of the order claimants were collected: shuffling the action list produces an identical admitted set.
- [ ] A blocked traveller emits `AGENT_STRANDED` after exactly `max_bounce_ticks` and lands in a park or shelter. No agent is ever teleported.
- [ ] `co_located` returns ≤ `cap` ids, contains at most 3 never-before-seen agents when the general pool is non-empty, and is not a constant sequence across 100 ticks in a 40-occupant place.
- [ ] `FIRST_ENCOUNTER` fires at most once per unordered pair per run.
- [ ] Each of the five district mechanisms emits its own kind with its `mechanism` id; `--mechanism-off` for each produces the ablation behaviour in `05 §15`.
- [ ] No float appears in any rent, land-value, or arrears computation.
- [ ] `RENT_HOME` with `offered_rent_cents` above a rival's wins the dwelling; with a household that cannot fund one period it is rejected at the resources gate.
- [ ] An evicted household reaches `tenure = 'shelter'` with `home_place_id NOT NULL` after the grace period, and exits via `HOMELESSNESS_EXITED` when re-housed.
- [ ] `zone` mode runs the full tick loop with `tiles` empty and produces the same event *kinds* (different values) as `grid`.
- [ ] Ephemeral payloads total ≤ 8 KB/tick at 1,000 agents; 90050 and 90051 are not computed when no client is subscribed.
- [ ] `polis rebuild` reproduces `districts`, `places`, `households` byte-identically from the log.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/determinism/test_world_generation.py` | 20 regenerations → 1 hash; 2 seeds → 2 hashes; hash excludes polylines; `PYTHONHASHSEED` insensitivity |
| `tests/unit/world/test_generation_invariants.py` | place counts, `n²` paths, symmetry, connectivity, housing slack, capacity totals per `05 §4.2` |
| `tests/unit/world/test_pathfinding.py` | Dijkstra vs brute-force BFS on a 30×30 fixture; `offroad_cost` respected; `travel_ticks` clamp and `travel_cap_binding_share` |
| `tests/unit/world/test_movement_resolution.py` | multi-tick journey advances one tick at a time; retarget; abandon; capacity admission is permutation-invariant over the action list; bounce → strand → park fallback |
| `tests/unit/world/test_occupancy_ages.py` | `frozen_at_tick == tick-1` in PHASE 1 and `== tick` in PHASE 5; a same-tick arrival is invisible to that tick's perception |
| `tests/unit/world/test_colocation_ranking.py` | cap respected; 3 novel slots; ε prevents a frozen slice over 100 ticks; ablation gives a uniform sample; `COLOCATION_SLICE_TRUNCATED` counts reconcile |
| `tests/unit/world/test_district_mechanisms.py` | each of the five: monotonicity in its driver, step caps bind, `--mechanism-off` behaviour, integer-only arithmetic (property test: no `float` in the payload) |
| `tests/unit/world/test_housing.py` | deferred acceptance is order-independent; lease stickiness holds contracted rent for `lease_ticks`; `RENT_HOME` bid beats the mechanical matcher; search failure → shelter |
| `tests/unit/world/test_eviction.py` | arrears threshold, grace period, `LEASE_ENDED` → `HOUSEHOLD_DISPLACED` → `HOMELESSNESS_ENTERED`; re-housing emits `HOMELESSNESS_EXITED` with correct `ticks_homeless` |
| `tests/unit/world/test_affordances.py` | the full `05 §4.4` table, including in-transit `SAY` refusal and the closed-place exception for home/employer |
| `tests/unit/world/test_opening_hours.py` | `[18,26)` overnight ranges; weekday gating; term gating for `school`/`university` |
| `tests/unit/world/test_zone_mode.py` | generation skips tile stages; `travel_ticks` ∈ {0,1,2}; identical kind set to `grid` over 50 ticks |
| `tests/unit/world/test_rent_home_validator.py` | schema, capability (household member), locality (not in transit), resources (one period funded), non-`home` place rejected |
| `tests/invariants/test_world_invariants.py` | `INV-WORLD-POSITION` catches a dropped agent; `INV-WORLD-PATHS` catches `create_place` without a matrix update; `INV-WORLD-RENT` warns outside `[0.10, 0.80]` |
| `tests/integration/test_world_tick.py` | 500 ticks, 200 agents, StubProvider: occupancy entropy above floor, stranded rate < 0.01, no HALT |

## 13. Definition of done

All of `chunks/README.md §5`. Specifically: acceptance criteria met; `pytest` green including every file in §12; `mypy --strict polis/world` clean; `ruff check` and `ruff format --check` clean; `import-linter` shows `polis.world` importing only `kernel`, `events`, `config`; the same-seed determinism test passes twice; the `world:` block is in the pydantic schema with the defaults above; all 3000-range and 90000-range kinds are registered with payload schemas; the 14 mechanisms are decorated and ablatable. Write down: the `quality_offset` handoff to C21, the `ColocationContext` injection decision, the `HOME_BID_*` kinds you added, and anything in `05` you found unimplementable as written.

## 14. Traps

1. **Unsorted iteration in generation.** One `for p in places_set` in the siting or Dijkstra reduce and `world_hash` differs across runs on the same seed. It will pass locally and fail in CI on a different `PYTHONHASHSEED`. Sort everything; the determinism test regenerates 20×, which is the only reason you will catch it.
2. **Asymmetric `travel_ticks`.** Running Dijkstra from both endpoints gives different tie-break paths and occasionally different costs. `05 §5.2` asserts symmetry and housing/commute arithmetic assumes it. Compute the unordered pair once and mirror.
3. **Storing full tile sequences in `place_paths.path`.** ~96 MB versus ~6 MB for turn points, a `COPY` that takes minutes, and a checkpoint that no longer fits in memory. Nothing but the renderer reads `path`.
4. **Advancing in-transit agents after interpreting `MOVE_TO`.** An agent arriving this tick that also submits a `MOVE_TO` will be double-counted or will lose its arrival. Auto-advance is step 1 for a reason.
5. **Occupancy age confusion.** Building `co_located` from the current tick's occupancy makes agents perceive same-tick movement, violates simultaneous submission, and produces a subtle "agents anticipate each other" artefact that looks like coordination. Assert `frozen_at_tick` at both phase boundaries.
6. **`occupancy_excluding` omitted.** Residents already in a place who re-arrive get counted against their own capacity, places appear full at half occupancy, and the stranded rate climbs until W4 fires. The bug looks like a capacity-tuning problem and is not.
7. **Bouncing at `path_cursor = travel_ticks`.** The invariant requires `0 <= path_cursor < travel_ticks`. Off-by-one here HALTs the run on the first busy place.
8. **Floats in rent.** `rent * 1.05` instead of `rent * rent_index_bp // 10_000` drifts by cents, the drift compounds weekly through `world.rent_response`, and eventually `INV-MONEY` fails in M2 with the world as the last place anyone looks.
9. **Rent runaway.** `rent_elasticity_bp` × a low-vacancy district compounds; `rent_step_cap_bp` bounds the weekly move but not the level. Watch `INV-WORLD-RENT` from day one; a rent/income ratio above 0.80 makes every housing finding an artefact of the feedback loop, not of agent behaviour.
10. **Writing `schools.quality`.** `05 §3.4`'s pseudocode says `for s in schools(d): s.quality = ...`. Doing that from `polis/world/` requires importing education state and creates a double-writer with C21. Emit 3063 and stop.
11. **Constructing a `Leg`.** `world → economy` is forbidden by `02 §7.1` and ledger writes are confined to `ledger.py` by `03 §4.2` rule 4. `RENT_DUE` is the entire protocol.
12. **Leaking `pos_x`/`pos_y`.** The moment any module outside `polis/world/` and `polis/observatory/` reads a coordinate, `zone` mode is dead and the M1 cut option is gone. Add the grep gate to CI in this chunk, not later.
13. **Home capacity as per-household.** A `home` is a *building* shared by up to 14 households. The vacancy test is `Σ members of all resident households + incoming size <= capacity`. Getting this wrong either homes everyone instantly or homes nobody.
14. **Homeless households with `home_place_id = NULL`.** The column is `NOT NULL`. Point it at the district `shelter` and set `tenure = 'shelter'`; do not invent a nullable variant.
15. **`create_place` without a matrix update.** One Dijkstra, one new row and column, or `INV-WORLD-PATHS` HALTs at the next checkpoint — possibly 500 ticks after the actual bug.
16. **Overnight hours.** `[17,26)` for a bar is `17:00–02:00`. A naive `open_from <= hour < open_to` closes every bar permanently and silently removes a whole place type from the simulation.
17. **Eviction arithmetic in ticks.** `eviction_grace_ticks: 720` is one sim-month at 24 ticks/day and one sim-year at 1 tick/day. Every cadence must go through the scheduler's sim-time conversion, never a raw tick constant.
18. **Mechanism laundering (W10/T6).** The single most likely thing to reach a paper. Five mechanisms here manufacture district-level correlations that are indistinguishable from A2's and B4's target findings. Every one needs its `entails` string written before the code, not after, and every world-derived result needs its `--mechanism-off` baseline.
