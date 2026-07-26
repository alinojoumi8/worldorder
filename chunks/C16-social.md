# C16 — Communication, social graph, social media, feed algorithms

**M4** · `polis/society/comms.py`, `graph.py`, `media/platform.py`, `media/feed.py` · **Depends on:** C02, C03, C04, C05, C06, C07, C08, C10, C11/C14 (`economy.ledger`) · **Blocks:** C17, C18, C19, C20, C23b, C24b · **Size:** L

## 1. Context

This chunk builds the channels through which one agent's state reaches another: speech at a
place, a direct message, a broadcast, a post, a repost, a follow — and the tie graph those
interactions deposit. **Its most important deliverable is the feed algorithm.** Research
question B1 ("does the ranking function change what a society believes") is answered by
swapping one object behind one interface and changing nothing else, so the four rankers must
be interchangeable at *identical call sites* with identical candidate pools. Everything else
here exists to make that comparison honest: reach is a consequence of exposure plus a
deliberate `REPOST`, never a transmission probability; ties form from logged contact, never
from a similarity prior; and no agent ever perceives a network statistic.

C16 owns **the single `InstitutionResolver` registered in `InstitutionSlot.COMMUNICATION`**.
C17 and C20 compose their slot-2 action handling into it (§5, §9.1).

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/07-SOCIETY-SPEC.md` | **§0–§3 in full (primary source)**, §5.4 (the update you call), §10.1/10.5 (metrics), §11 F1/F6/F7 (your failure modes), §12 (cadences) |
| `../docs/02-ARCHITECTURE.md` | §1.4 simultaneity, §3.2 kinds, §3.3 sampling, §4 determinism, §5 phases, §5.1 slot 2, §7.1, §8.1 MECHANISM |
| `../docs/03-DATA-MODEL.md` | §2.6 `relationships`, §8 `posts`/`follows`/`engagements`, §0 conventions, §12 rebuild |
| `../docs/04-AGENT-SPEC.md` | §5 perception caps (12/10/15/3), §7 salience `social` term, §8 reflex set |
| Chunks | **C10 (`InstitutionResolver`, `ValidatedAction`, `GateResult`, params models)**, C02 (`NewEvent`, `register_kind`), C03 (`Database`, `Projection`), C04 (`stable`, `det_id`, `RngRegistry`, `Cadence`), C06 (`World`, `PlaceView`), C07 (`Observation`, `PostBrief`, `MessageBrief`, `PerceptionSources`), C08 (`MemoryLookup` shape) |

## 3. Scope — in

1. `comms.py` — `SAY` / `DIRECT_MESSAGE` / `BROADCAST` resolution, the attention model (`07 §1.2`), and `ConversationTracker` (turn-based across ticks, `07 §1.4`).
2. `graph.py` — `SocialGraph`: formation from the ten logged sources, per-tick strength/valence/trust dynamics, decay, type transitions, and the weekly `NETWORK_SNAPSHOT`.
3. `media/platform.py` — `POST`, `REPOST`, `LIKE`, `COMMENT`, `FOLLOW`, `UNFOLLOW`; the `posts`/`follows`/`engagements` projections; cascade tracking and structural virality.
4. **`media/feed.py` — the candidate pool, the four rankers behind one `FeedRanker` protocol, and the online-fitted `EngagementModel`.** The B1 lever.
5. `CommunicationResolver` — the slot-2 facade, plus `compose()` for C17's and C20's slot-2 sub-resolvers.
6. `FeedService` — PHASE 1 feed construction for **every** agent, supplying `PerceptionSources.feed`.
7. Kinds 10000–10059 and 11000–11029 registered in `polis/events/kinds.py`.
8. The `BeliefChannel` protocol (C17 implements it) and the slot-2 call sites that fire the `social` channel.
9. `@mechanism("comms_attention", ...)` and `@mechanism("graph_homophily", ...)`.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `beliefs` table, `PROPOSITION_REGISTRY`, the §5.4 update maths, kinds 10060–10069 | **C17** |
| Outlets, articles, the claim checker, `posts.truthfulness` | **C17** |
| `PUBLISH_ARTICLE`, `RETRACT` semantics (you route them, C17 resolves them) | **C17** |
| `COURT`, `PROPOSE_UNION`, `DISSOLVE_UNION`, `HAVE_CHILD_INTENT` semantics | **C20** |
| `Observation` assembly, `PostBrief` type, perception caps | **C07** |
| Choosing to post; every media action is LLM-only | **C09** |
| Louvain/assortativity *reporting* pipeline into `metrics` | **C24b** (you compute, it stores/exports) |
| Any money except the `BROADCAST` venue fee | **C11/C14 ledger**, **C18** campaigns |

## 5. Interfaces you provide

```python
# polis/society/comms.py
from polis.agents.actions import (Action, ActionType, GateResult, GateFailure, InstitutionSlot,
                                  ResolutionContext, ValidatedAction, ValidationContext)

class CommunicationResolver:
    """THE InstitutionResolver for InstitutionSlot.COMMUNICATION (C10 §5). Exactly one
    resolver may occupy a slot, so C17 and C20 compose into this one."""
    slot:    Final[InstitutionSlot] = InstitutionSlot.COMMUNICATION
    handles: frozenset[ActionType]          # own set ∪ every composed sub-resolver's

    def __init__(self, *, log: EventLog, clock: Clock, rng: RngRegistry, world: World,
                 graph: "SocialGraph", platform: "Platform", conversations: "ConversationTracker",
                 beliefs: "BeliefChannel", ledger: LedgerApi, repo: "CommsRepository",
                 cfg: SocietySettings) -> None: ...

    def compose(self, sub: InstitutionResolver) -> None:
        """sub.slot must be COMMUNICATION and sub.handles disjoint from all others.
        Raises DuplicateHandler otherwise. Sub-resolvers are held in a tuple sorted by
        `type(sub).__name__` and consulted by ActionType, never by iteration order."""

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult: ...
    def check_locality(self, action: Action, ctx: ValidationContext)   -> GateResult: ...
    def check_resources(self, action: Action, ctx: ValidationContext)  -> GateResult: ...
    def resolve(self, actions: Sequence[ValidatedAction], tick: int,
                ctx: ResolutionContext) -> Sequence[Event]: ...
    def options_for(self, action_type: ActionType,
                    ctx: ValidationContext) -> tuple[Mapping[str, Any], ...]: ...

OWN_TYPES: Final[frozenset[ActionType]] = frozenset({
    ActionType.SAY, ActionType.DIRECT_MESSAGE, ActionType.BROADCAST,
    ActionType.POST, ActionType.REPOST, ActionType.LIKE, ActionType.COMMENT,
    ActionType.FOLLOW, ActionType.UNFOLLOW, ActionType.BEFRIEND})

@dataclass(frozen=True, slots=True)
class Listener:
    agent_id: str
    attention: float

@mechanism("comms_attention", entails="...")            # 07 §1.2 verbatim
def attention(speaker_id: str, listener_id: str, *, tie_strength: float, addressed: bool,
              occupancy: int, capacity: int, speech_id: str, tick: int,
              rng: RngRegistry, uniform: bool = False) -> float: ...

def heard_by(speaker_id: str, candidates: Sequence[AgentBrief], *, place: PlaceView,
             addressed_to: Sequence[str], graph: "SocialGraph", speech_id: str,
             tick: int, rng: RngRegistry, cfg: SocietySettings) -> tuple[Listener, ...]:
    """<= 12, sorted by agent_id, only those with attention >= hearing_threshold."""

@dataclass(frozen=True, slots=True)
class Conversation:
    conversation_id: str; place_id: str; participants: tuple[str, ...]
    topic: str | None; turn_index: int; last_turn_tick: int; opener_id: str

class ConversationTracker:
    """Projection object, no table. Rebuildable from 10010/10011/10012."""
    def open_or_join(self, speaker_id: str, place_id: str, addressed_to: Sequence[str],
                     topic: str | None, tick: int) -> tuple[Conversation, Event | None]: ...
    def record_turn(self, conversation_id: str, speaker_id: str, tick: int,
                    cause_seq: int | None) -> int: ...          # -> turn_index
    def close_idle(self, tick: int, world: World) -> Sequence[Event]:
        """PHASE 5 tail. reason ∈ idle|dispersed|closed|death|incarceration."""
    def active_for(self, agent_id: str) -> tuple[Conversation, ...]: ...
```

```python
# polis/society/graph.py
TieType = Literal["kin","partner","friend","colleague","rival","creditor","acquaintance"]

@dataclass(frozen=True, slots=True)
class Tie:
    a_id: str; b_id: str; type: TieType
    strength: float; valence: float; trust: float
    formed_tick: int; ended_tick: int | None; last_interaction_tick: int

@dataclass(frozen=True, slots=True)
class Interaction:
    a_id: str; b_id: str; kind: str          # key into the 07 §2.3 ι/ν/Δτ table
    weight: float = 1.0                      # attention, or 1.0

class SocialGraph:
    def __init__(self, *, log: EventLog, clock: Clock, rng: RngRegistry,
                 repo: "GraphRepository", cfg: SocietySettings) -> None: ...
    def tie(self, a_id: str, b_id: str, type: TieType | None = None) -> Tie | None:
        """Canonicalises to a_id < b_id for symmetric types; `creditor` keeps lender first."""
    def strength(self, a_id: str, b_id: str) -> float:      # 0.0 when no live tie
    def trust(self, a_id: str, b_id: str) -> float:         # 07 §5.3 social channel input
    def neighbours(self, agent_id: str, *, min_strength: float = 0.0
                   ) -> tuple[Tie, ...]:                    # sorted by (type, other_id)
    def stage_interaction(self, i: Interaction) -> None:     # buffered; no I/O
    def form(self, a_id: str, b_id: str, type: TieType, context: str,
             tick: int) -> Event | None: ...
    def apply_tick(self, tick: int, contacts: "ContactLedger") -> Sequence[Event]:
        """End of PHASE 5 slot 2. Formation, then dynamics, then transitions, then decay,
        in stable() order by (a_id, b_id, type). Emits 10040/10041/10042/10043."""
    def snapshot(self, tick: int) -> Event:                  # 10050, PHASE 7 weekly
    def end_all_for(self, agent_id: str, reason: str, tick: int) -> Sequence[Event]:
        """Called by C20 on death/emigration and by C19 on incarceration (decay only)."""

class ContactLedger:
    """Per-tick co-location counter, windowed 30 sim-days. Fed by C06's occupancy."""
    def record(self, place_id: str, occupants: Sequence[str], tick: int) -> None: ...
    def joint_place_ticks(self, a_id: str, b_id: str, tick: int) -> int: ...

@mechanism("graph_homophily", entails="...")                 # 07 §2.5 verbatim
def formation_multiplier(a: AgentState, b: AgentState, beta: float) -> float:
    """exp(beta * sim(a,b)); returns 1.0 exactly when beta == 0.0 (the default)."""
```

```python
# polis/society/media/platform.py
@dataclass(frozen=True, slots=True)
class Post:
    post_id: str; author_id: str; tick: int; text: str
    topic: str | None; stance_proposition: str | None; stance_value: float | None
    in_reply_to: str | None; repost_of: str | None; root_post_id: str
    claims: tuple[Mapping[str, Any], ...]; reach: int

class Platform:
    def __init__(self, *, log: EventLog, clock: Clock, repo: "PlatformRepository",
                 graph: SocialGraph, cfg: SocietySettings) -> None: ...
    def publish(self, author_id: str, params: PostParams, tick: int,
                cause_seq: int | None) -> tuple[Post, Sequence[Event]]: ...
    def repost(self, author_id: str, params: RepostParams, tick: int) -> tuple[Post, Sequence[Event]]: ...
    def engage(self, agent_id: str, post_id: str,
               type: Literal["view","like","repost","comment","report"], tick: int) -> Event | None: ...
    def follow(self, follower_id: str, followee_id: str, context: str, tick: int) -> Event | None: ...
    def unfollow(self, follower_id: str, followee_id: str, reason: str, tick: int) -> Event | None: ...
    def followees(self, agent_id: str) -> frozenset[str]: ...
    def follower_count(self, agent_id: str) -> int: ...
    def posts_in_window(self, tick: int, window_ticks: int) -> tuple[Post, ...]:
        """Sorted by post_id. The ONLY pool source the rankers may read."""
    def delete(self, post_id: str, reason: str, tick: int) -> Event: ...     # 11011

class CascadeTracker:
    def note(self, post: Post, tick: int) -> None: ...
    def close_due(self, tick: int) -> Sequence[Event]:      # 11022, PHASE 7 daily
    def structural_virality(self, root_post_id: str) -> float:
        """Wiener index / C(n,2) over the repost tree. 0.0 for n < 2."""
```

```python
# polis/society/media/feed.py
FeedAlgorithm = Literal["chronological", "engagement", "random", "adversarial"]

@dataclass(frozen=True, slots=True)
class Features:
    rec: float; pop: float; tie: float; aff: float; inf: float
    cong: float; ext: float; dis: float; agr: float; conf: float; repeat: float
    def vector(self) -> tuple[float, ...]:
        """[1, aff, tie, pop, rec, inf, ext, agr, dis, conf, repeat] — the fixed order
        the fitted beta indexes. Changing this order invalidates every stored beta."""

class FeedRanker(Protocol):
    name: FeedAlgorithm
    uses_out_of_network: bool
    def pool(self, agent_id: str, tick: int, ctx: "FeedContext") -> tuple[Post, ...]: ...
    def score(self, agent_id: str, post: Post, f: Features, ctx: "FeedContext") -> float: ...

class ChronologicalRanker: ...
class EngagementRanker:  ...   # holds a reference to EngagementModel
class RandomRanker:      ...
class AdversarialRanker: ...   # holds a reference to BeliefChannel.predict_delta

RANKERS: Final[Mapping[FeedAlgorithm, type[FeedRanker]]]

class EngagementModel:
    """Online-fitted P(engage | impression). 07 §3.3."""
    beta: tuple[float, ...]                                  # len 11, rounded to 6 dp
    n_observations: int
    def predict(self, f: Features) -> float: ...
    def refit(self, impressions: Sequence[tuple[str, str, Features, bool]], tick: int
              ) -> tuple[float, ...]:
        """PHASE 7 daily. `impressions` is sorted by (tick, agent_id, post_id) BEFORE entry.
        20 full-batch gradient passes at eta=0.05, then the n0 prior blend, then round6.
        Deterministic: no shuffling, no early stopping, no adaptive learning rate."""
    def dump(self) -> Mapping[str, Any]: ...                 # Checkpointable (C04)
    def load(self, state: Mapping[str, Any]) -> None: ...

class FeedService:
    def __init__(self, *, algorithm: FeedAlgorithm, platform: Platform, graph: SocialGraph,
                 beliefs: "BeliefChannel", model: EngagementModel, rng: RngRegistry,
                 clock: Clock, log: EventLog, cfg: SocietySettings) -> None: ...
    def build(self, agent_id: str, tick: int) -> tuple[tuple[Post, ...], tuple[float, ...]]: ...
    def build_all(self, agent_ids: Sequence[str], tick: int
                  ) -> Mapping[str, tuple[PostBrief, ...]]:
        """PHASE 1. Called for EVERY living agent including reflex agents. Writes
        `engagements` rows of type `view` for all; emits 11021 under the 02 §3.3 sampler."""
    def impressions_for_refit(self, sim_day: int
                              ) -> Sequence[tuple[str, str, Features, bool]]: ...

def reach(post_id: str) -> int: ...
def impressions(post_id: str) -> int: ...
```

```python
# polis/society/protocols.py
class BeliefChannel(Protocol):
    """Implemented by polis.society.beliefs (C17). C16 never computes a belief update."""
    def apply_social(self, agent_id: str, proposition: str, target: float,
                     source_id: str, tick: int) -> Event | None: ...
    def predict_delta(self, agent_id: str, proposition: str, target: float,
                      source_id: str, channel: Literal["social","media"]) -> float:
        """Pure forward model. AdversarialRanker's ONLY read into beliefs. No writes."""
    def value(self, agent_id: str, proposition: str) -> float: ...
    def confidence(self, agent_id: str, proposition: str) -> float: ...
    def population_mean(self, proposition: str) -> float: ...

class NullBeliefChannel:
    """Default until C17 lands. Returns 0.0 / 0.5, writes nothing, emits nothing."""
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C10 | `InstitutionResolver`, `ValidatedAction`, `ValidationContext`, `ResolutionContext`, `GateResult`, `GateFailure`, params models | the whole slot-2 contract |
| C02 | `NewEvent`, `EventLog.stage`, `register_kind`, `Persistence` | your 60 + 30 kinds |
| C03 | `Database`, `Projection`, `register_projection` | `posts`, `follows`, `engagements`, `relationships` |
| C04 | `stable`, `det_id`, `det_uuid`, `round6`, `RngRegistry`, `Clock`, `Cadence`, `@mechanism` | determinism, ids, cadences |
| C06 | `World.place_view`, occupancy, `places_of_type` | attention denominator, `BROADCAST` venue |
| C07 | `Observation`, `PostBrief`, `MessageBrief`, `PerceptionSources`, `AgentState` | perception hand-off |
| C11 | `Ledger.transfer(src, dst, amount_cents, reason) -> list[Leg]`, `Ledger.post_transaction(legs, *, tick, cause: Event) -> UUID`, `Leg(account_id, direction, amount_cents, reason)`, `account_id(code, owner_id, …)` | the `BROADCAST` venue fee only |

> **Coordination item 1 — one resolver per slot.** C10 §9.6 dispatches
> `registry.by_slot.get(slot)`, a single resolver. Slot 2 has three owners (C16, C17 news
> actions, C20 relational actions). C16 ships the facade and `compose()`; C17 and C20 ship
> `InstitutionResolver`s with `slot = COMMUNICATION` that are **composed, not registered**.
> Confirm with C10 that `ResolverRegistry.register` is called once for slot 2.

> **Coordination item 2 — the ledger.** `Leg` carries its own `reason`; `post_transaction`
> takes the **causing `Event`**, not a seq. Build legs with `Ledger.transfer`, never inline, and
> never format an `account_id` by hand — use `ledger.account_id(code, owner_id, …)`.

> **Coordination item 3 — `PostBrief`.** C07's `PostBrief` carries no `stance_*` and no
> `claims`. The feed's cross-cutting metric needs `stance_value`; ask C07 to widen it rather
> than shipping a parallel type.

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `posts` | **W** | one row per `POST`/`REPOST`/`COMMENT`; `truthfulness` left NULL — C17 fills it |
| `follows` | **W** | directed; `ended_tick` on unfollow/death/emigration |
| `engagements` | **W** | `view` for every feed impression; `like`/`repost`/`comment`/`report` from actions |
| `relationships` | **W** | the whole table; `a_id < b_id` for symmetric types |
| `agents` | R | stage, place, wealth percentile (assortativity), alive |
| `places` | R | capacity, occupancy, `rent_cents`, owner |
| `beliefs` | **R only** | via `BeliefChannel`. **C16 never writes `beliefs`** |
| `metrics` | W | via C24b's writer, not directly |

Partitioned by `run_id`: `posts`, `engagements` (C03 §9). Register three `Projection`s
(`posts_projection`, `follows_projection`, `relationships_projection`) with disjoint `tables`.

## 8. Event kinds owned

**Ranges: 10000–10059 and 11000–11029.** Owner `polis.society.comms` (10000–10039),
`polis.society.graph` (10040–10059), `polis.society.media` (11000–11029). **10060–10069 are
C17's** (`07 §0.5` D-1) — do not register them.

| Kind | Name | Payload |
|---|---|---|
| 10010 | `SPEECH_UTTERED` | `speaker_id, place_id, text, addressed_to[], heard_by[{agent_id, attention}], topic, stance_proposition, stance_value, conversation_id, turn_index, closing, claims[]` |
| 10011 | `CONVERSATION_OPENED` | `conversation_id, place_id, participants[], opener_id, topic` |
| 10012 | `CONVERSATION_CLOSED` | `conversation_id, turns, reason, duration_ticks, participants[]` |
| 10020 | `MESSAGE_SENT` | `message_id, sender_id, recipient_id, text, in_reply_to, topic, stance_proposition, stance_value, claims[]` |
| 10021 | `MESSAGE_READ` | `message_id, reader_id, latency_ticks, entered_memory` |
| 10030 | `BROADCAST_MADE` | `broadcaster_id, place_id, text, topic, audience_ids[], audience_size, venue_fee_cents, txn_id, stance_proposition, stance_value` |
| 10040 | `TIE_FORMED` | `a_id, b_id, type, context, strength, valence, trust` |
| 10041 | `TIE_UPDATED` | `a_id, b_id, type, d_strength, d_valence, d_trust, drivers[{kind, weight}]` |
| 10042 | `TIE_ENDED` | `a_id, b_id, type, reason, final_strength` |
| 10043 | `TIE_TYPE_CHANGED` | `a_id, b_id, from_type, to_type, trigger` |
| 10050 | `NETWORK_SNAPSHOT` | the 16 fields of `07 §2.4` |
| 11010 | `POST_PUBLISHED` | `post_id, author_id, text, topic, stance_proposition, stance_value, in_reply_to, repost_of, root_post_id, claims[], follower_count_at_post` |
| 11011 | `POST_DELETED` | `post_id, author_id, reason` |
| 11012 | `REPOST_MADE` | `post_id, repost_of, root_post_id, author_id, original_author_id, cascade_depth, comment` |
| 11020 | `POST_ENGAGED` | `post_id, agent_id, type, author_id` |
| 11021 | `FEED_SERVED` | `agent_id, algorithm, post_ids[≤15], scores[], candidate_pool_size, out_of_network_count, cross_cutting_count, mean_extremity` — **`Persistence.SAMPLED`** |
| 11022 | `CASCADE_CLOSED` | `root_post_id, size, depth, breadth, structural_virality, reach, impressions, unique_reposters, lifetime_ticks` |
| 11040 | `FOLLOW_CREATED` | `follower_id, followee_id, context` |
| 11041 | `FOLLOW_ENDED` | `follower_id, followee_id, reason` |

`10041` is emitted only when `|Δs| + |Δv| + |Δτ| ≥ tie_event_threshold` (0.02); sub-threshold
drift is folded into the projection silently or the graph emits ~10⁵ events/tick.

## 9. Implementation notes

### 9.1 Slot-2 composition and gate forwarding

`CommunicationResolver.handles` is `OWN_TYPES | union(sub.handles)`, computed at `compose()`
time and frozen. Every gate method dispatches on `action.type`: if the type belongs to a
sub-resolver, forward the call unchanged and return its `GateResult`. `resolve()` partitions
`actions` by owning sub-resolver, calls each with its `stable()`-sorted batch, and
**concatenates events in a fixed order: own actions first, then sub-resolvers sorted by class
name.** Never dict order. Empty batches still call `resolve()`.

### 9.2 Speech, hearing, and the tick-boundary rule

`SAY` reaches co-located agents; the candidate list comes from `ctx.observation.co_located`
(last tick's committed state, C10 §9.2), **not** live occupancy. Compute `attention` per
candidate, keep those ≥ `hearing_threshold`, cap 12, sort by `agent_id`, write into
`10010.heard_by` and into `subject_ids` so the GIN index answers "what did X hear".

**Speech is never heard in the tick it is spoken** (`07 §1.3`). C16 emits the event; C07's
PHASE 1 next tick turns it into `co_located[*].last_utterance` or `Observation.inbox`. If you
find yourself pushing an utterance into a listener's observation inside PHASE 5 you have
broken `02 §1.4` and made the tick order-dependent.

`BROADCAST` at a place the actor does not own charges `place.rent_cents` pro-rata through
`post_transaction([Leg(actor_cash, -1, fee), Leg(owner_cash, +1, fee)], reason="rent")`.
`check_resources` fails with `resources` if the actor cannot pay. The fee and `txn_id` go in
the `10030` payload.

`DIRECT_MESSAGE` gating: an existing `relationships` row of any type, **or** a `follows` edge
in either direction, **or** the recipient holds public office (ask C18's `OfficeRegister`
through a read-only protocol; until C18 lands, the office set is empty). Rate limit
`max_dms_per_tick` per (sender, recipient, sim-day) — keyed by sim-day, not tick.

### 9.3 Conversations are turn-based across ticks

Do **not** make a multi-turn LLM call. `07 §1.4` gives five independent reasons; C1
(determinism/cache) and C2 (simultaneity) are each individually fatal. One `SAY` action = one
turn = one event, with `cause_seq` pointing at the turn it answers. `turn_index` increments
per conversation. Close on `conversation_idle_ticks` (2) with no turn, on dispersal, on an
explicit `closing=true`, or on death/incarceration of a participant.

### 9.4 Tie dynamics

Order: **form → interact → transition → decay**, all inside `apply_tick`, in
`stable()` order by `(a_id, b_id, type)`.

```
δ_s(type) = ln 2 / (tie_halflife_sim_days[type] * clock.ticks_per_sim_day)   # 0 for kin/partner
s' = clip(s + Σ ι(kind)·w - δ_s·(tick - last_interaction_tick), 0, 1)
v' = clip(v + Σ ν(kind)·w, -1, 1);   τ' = clip(τ + Σ Δτ(kind)·w, 0, 1)
```

Never hard-code a per-tick decay constant: `chronicle` has `ticks_per_sim_day == 1` and a
constant tuned at 24 makes ties immortal or instantaneous. Transitions exactly as `07 §2.3`.
`rival → acquaintance` requires `v ≥ 0.10` **sustained** 30 sim-days — carry a
`v_positive_since_tick` counter on the tie; a single-tick check produces flapping.

**Homophily is off by default.** `formation_multiplier` returns exactly `1.0` when
`homophily_bias == 0.0` — assert it, because any nonzero default makes B1's assortativity
result circular.

### 9.5 The feed — the part that matters

```
W        = feed_window_sim_hours → ticks via clock
InNet_i  = posts in (t-W, t] whose author ∈ followees(i), or reposted by a followee
Out_i    = posts in (t-W, t] by living authors ∉ followees(i), author ≠ i
C_i      = InNet_i ∪ sample(Out_i)              # sample only for engagement / adversarial
```

1. Truncate `C_i` to `feed_candidate_cap` (300) by descending `(p.tick, sha256(post_id))`,
   **then sort by `post_id`** before scoring. Ranking must be order-independent.
2. Deduplicate by `root_post_id`, drop self-authored posts. `chronological` excludes
   already-engaged posts; the other three apply `repeat_penalty` (0.4) instead.
3. Out-of-network items may occupy at most `feed_out_of_network_quota` (0.30) of the 15 slots;
   draws use `rng.get("feed.pool", agent_id, tick)`.
4. Rank, take 15, ties broken by `rng.get("feed.tiebreak", agent_id, tick)` then `post_id`.
5. Write `engagements` type `view` for all 15. Emit `11021` under the `02 §3.3` sampler.

`chronological` uses `InNet_i` only and scores `p.tick` — nothing else. `random` draws its
pool from the whole city and scores `rng.get("feed.random", agent_id, tick).random()`.

**`EngagementRanker` must not contain a hand-written weight.** Its score is
`sigmoid(beta · x)` where `beta` came from `EngagementModel.refit`. If a coefficient appears
as a literal anywhere outside `beta_prior` in config, "the engagement feed polarises" becomes
a property of your code rather than of the society (T6).

`AdversarialRanker` scores `predict(f)**gamma * (d_after - d_before)` where `d_after` uses
`BeliefChannel.predict_delta` — a **pure forward model with no writes**. Content predicted to
move an agent toward the mean scores negative and is never shown. Its declared MECHANISM says
it is an upper bound on algorithmic harm, not a hypothesis; it is never cited as evidence that
feeds polarise.

### 9.6 Fitting the engagement model deterministically

Batch order is fixed `(tick, agent_id, post_id)`. 20 full-batch passes, `eta = 0.05`, no
shuffling, no minibatches, no adaptive optimiser, `round6` after every pass. Prior blend
`beta = (n0*beta_prior + n*beta_fitted) / (n0 + n)` with `n0 = 5000`. Write `beta` to
`metrics` daily — the coefficients are a research output. A cold-start prior with positive
weight on `dis`/`ext` is the declared `feed_engagement_prior` MECHANISM; B1 claims must be
measured after `n >> n0` and re-run at `beta_prior = 0`.

### 9.7 Reach, cascades, and the absence of contagion

`reach(p)` = unique agents who saw it; `impressions(p)` = agent-tick pairs. A repost exists
because an agent chose `REPOST`. **There is no transmission probability anywhere in this
chunk.** If you write one, delete it and the finding with it.

### 9.8 Network statistics are never perceived

Degree, clustering, centrality, community membership: computed in PHASE 7, written to
`10050` and `metrics`, and **absent from every `Observation`**. Louvain runs over nodes sorted
by `agent_id` with `seed = rng.get("metrics.louvain", "", tick).getrandbits(32)`. Report
`powerlaw_alpha` with `powerlaw_ks`; above 0.10 the distribution is not power-law and must
not be called scale-free.

## 10. Configuration keys

```yaml
society:
  feed_algorithm: engagement          # chronological | engagement | random | adversarial
  feed_slice: 15
  feed_candidate_cap: 300
  feed_window_sim_hours: 72
  feed_out_of_network_quota: 0.30
  repeat_penalty: 0.4
  hearing_threshold: 0.35
  max_dms_per_tick: 2
  conversation_idle_ticks: 2
  cascade_idle_ticks: 24
  colocation_threshold: 6
  befriend_window_sim_days: 14
  tie_event_threshold: 0.02
  tie_halflife_sim_days: {acquaintance: 30, friend: 90, colleague: 120, rival: 180,
                          kin: null, partner: null}
  homophily_bias: 0.0                 # MECHANISM graph_homophily
  feed:
    recency_halflife_sim_hours: 12
    pop_norm: 200
    follower_norm: 200
    adversarial_gamma: 0.5
    engagement: {eta: 0.05, passes: 20, n0: 5000,
                 beta_prior: [0,0,0,0,0,0,0,0,0,0,0]}   # 11 entries, feed_engagement_prior

mechanisms:
  comms_attention: tie_weighted       # tie_weighted | uniform
  graph_homophily: "off"

ablations:
  feed_off: false                     # empty feed slice; isolates the platform channel
```

## 11. Acceptance criteria

- [ ] `CommunicationResolver` is the only resolver registered in `InstitutionSlot.COMMUNICATION`; `compose()` raises on a duplicate `ActionType` or a sub-resolver whose `slot != COMMUNICATION`.
- [ ] An utterance spoken at tick *t* appears in no agent's `Observation` before tick *t+1*.
- [ ] `heard_by` is ≤ 12, sorted by `agent_id`, and contains only listeners with `attention ≥ hearing_threshold`; `comms_attention: uniform` sets every attention to exactly 1.0.
- [ ] A four-turn conversation occupies four ticks, produces four `10010`s with `turn_index` 0–3 and `cause_seq` chaining, one `10011` and one `10012`.
- [ ] Zero multi-turn LLM calls: no code path issues more than one router call per action.
- [ ] Tie decay over one sim-day is identical in `microscope` and `chronicle` for the same starting state.
- [ ] With `homophily_bias = 0.0`, `formation_multiplier` returns exactly `1.0` for every pair.
- [ ] **All four rankers accept the identical candidate pool and return exactly 15 items; swapping `society.feed_algorithm` changes no call site.**
- [ ] `chronological` output is strictly descending in `p.tick` with `post_id` ascending on ties, and contains zero out-of-network posts.
- [ ] `random` output is uncorrelated with `pop`, `tie` and `rec` over 10,000 impressions.
- [ ] The four arms on the same seed produce measurably different `exposure.crosscut`.
- [ ] `EngagementRanker` contains no hard-coded coefficient; an AST scan finds no float literal in its scoring path.
- [ ] `refit` is deterministic: same impression set twice → byte-identical `beta`.
- [ ] `AdversarialRanker` performs zero belief writes; `predict_delta` is pure.
- [ ] No `Observation` field anywhere contains a degree, clustering coefficient, centrality, community id, or `reach`.
- [ ] Every `BROADCAST` venue fee is a balanced `post_transaction`; `INV-MONEY` holds over 500 ticks of broadcasting.
- [ ] `polis rebuild` reproduces `posts`, `follows`, `engagements` and `relationships` exactly, including feeds recomputed from the seeded RNG for unsampled `11021`.
- [ ] `mypy --strict polis/society` and the `institutions-no-cognition` import-linter contract pass; no module here imports `polis.agents.cognition`, `.memory`, or `.state`.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/society/test_attention.py` | Formula against `07 §1.2` worked examples; cap 12; sort order; `uniform` ablation |
| `tests/unit/society/test_conversation_ticks.py` | Turn indices, `cause_seq` chain, idle close at 2 ticks, dispersal close, interleaved conversations |
| `tests/invariants/test_speech_not_same_tick.py` | **Merge gate.** No utterance appears in any `Observation` at its own tick, over 200 ticks and 200 agents |
| `tests/unit/society/test_tie_dynamics.py` | ι/ν/Δτ table applied correctly; decay halflife in both clock profiles; all five transitions; `rival→acquaintance` needs 30 sustained sim-days |
| `tests/unit/society/test_tie_formation.py` | All ten formation sources with their initial triples; `a_id < b_id` canonicalisation; `creditor` directionality |
| `tests/unit/society/test_homophily_off.py` | `formation_multiplier == 1.0` for 10,000 pairs at `beta = 0`; `> 1` for similar pairs at `beta > 0` |
| `tests/unit/society/test_feed_pool.py` | Cap 300, dedup by `root_post_id`, self-exclusion, out-of-network quota ≤ 0.30·15, pool sorted by `post_id` before ranking |
| `tests/unit/society/test_rankers_swappable.py` | **Merge gate.** All four rankers over one fixture pool return 15 items; call site identical; `chronological` strictly recency-ordered; `random` uncorrelated with `pop`/`tie` |
| `tests/unit/society/test_engagement_fit.py` | Determinism of `refit`; prior blend arithmetic; no literal coefficients (AST scan); `beta` length 11 and feature order stable |
| `tests/unit/society/test_adversarial_ranker.py` | Never shows content predicted to move the agent toward the mean; performs zero writes; `gamma` gating |
| `tests/unit/society/test_cascades.py` | Depth/breadth/size/structural virality on hand-built trees; `11022` on idle; virality 0.0 for singletons |
| `tests/invariants/test_no_network_stats_in_observation.py` | Reflective scan of `Observation` and every sub-view for banned fields |
| `tests/integration/test_feed_arms_diverge.py` | 200 agents, 500 ticks, four arms on one seed: `exposure.crosscut` differs by > 0.05 between `random` and `chronological` |
| `tests/integration/test_broadcast_ledger.py` | `INV-MONEY` across 500 ticks of venue-hired broadcasts; fee matches `place.rent_cents` pro-rata |
| `tests/determinism/test_society_comms_determinism.py` | Same seed twice → identical 10000–10059 and 11000–11029 sequence and identical feeds |
| `tests/integration/test_rebuild_social.py` | `polis rebuild` diff-clean on `posts`, `follows`, `engagements`, `relationships` after 500 ticks |

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. `polis/society/{comms,graph}.py` and `polis/society/media/{platform,feed}.py` export the §5 symbols with those exact signatures.
2. Kinds 10000–10059 and 11000–11029 registered with payload schemas; `11021` registered as `Persistence.SAMPLED`.
3. Three `Projection`s registered with disjoint `tables`, and a passing `polis rebuild --check`.
4. Both `@mechanism` declarations present with `entails` strings matching `07 §1.2` and `§2.5` verbatim, and both ablatable from config.
5. `CommunicationResolver.compose()` documented for C17 and C20 authors: what a sub-resolver must provide, and the fixed event-concatenation order.
6. Handback records: the three coordination items in §6; the measured `exposure.crosscut` spread across the four arms on a 500-tick calibration run; and the realised `beta` after one sim-year, which C24b needs for the B1 pre-registration.

## 14. Traps

1. **Everyone converging on one opinion (F1).** The most likely single outcome of this chunk plus C17. Its mechanical cause lives here: if `ALPHA[social]` is high *and* the follow graph is near-complete, every agent sees the same 15 posts and bounded confidence does the rest. Report genesis belief dispersion against steady-state dispersion in every run, and check `network.degree_gini` and cross-agent feed overlap before blaming the belief model.
2. **A transmission probability sneaking into reposting.** "Just a small p(share) to get cascades going" destroys the one property that makes a virality finding meaningful. Reach must be exposure × choice, always.
3. **Hand-tuning the engagement coefficients.** The moment `score += 0.3 * ext` appears, B1 is answered by your keyboard. Fit it or delete it.
4. **Feature-order drift in `Features.vector()`.** `beta` is a bare tuple indexed by position. Insert a feature in the middle and every checkpointed `beta`, every `metrics` row, and every replay silently reinterprets its weights.
5. **Ranking an unsorted pool.** Score ties are common (identical `p.tick` under `chronological`, identical scores under `random` at low precision). An unsorted pool makes the feed a function of dict iteration order and destroys replay.
6. **The adversarial ranker writing beliefs.** `predict_delta` looks like the update function because it *is* the update function minus the write. Call the shared kernel; never call the writer.
7. **Building feeds only for deliberate agents.** Reflex agents' impressions are the denominator of `exposure.crosscut` and the training set for `refit`. Build for everyone; sample only the *event*.
8. **A per-tick decay constant tuned at 24 ticks/day.** `chronicle` runs at 1. Derive from `clock.ticks_per_sim_day` or ties become immortal in one profile and vanish in the other.
9. **Emitting `10041` on every tie every tick.** ~10⁵ events/tick, a log 5× the size of the whole rest of the simulation, and a `TICK_COMMITTED` budget blown by one subsystem. Threshold it.
10. **Degenerate graph (F7).** `colocation_threshold` too low turns one popular park into a complete graph. Watch `mean_degree > 0.3·n` and `clustering > 0.8`; both are mechanical, not emergent.
11. **Letting an agent see its own `reach`, follower rank, or feed score.** It optimises the metric and every downstream measurement dies. `PostBrief` carries `likes` (visible on a real platform) and nothing else.
12. **Two resolvers claiming `LIKE`.** Comms and media both plausibly own it. `compose()` must assert disjointness or half the likes go to the wrong projection and nobody notices for a month.
13. **Treating `COMMENT` as distinct from `POST{in_reply_to}`.** It is sugar (`07 §3.1`). Two code paths means two `posts` row shapes and a broken cascade tree.
14. **Reading live occupancy in `check_locality`.** Movement resolves in slot 1; locality reads `ctx.observation` (C10 §9.2). Reading live position makes the reject rate a function of pathfinding.
15. **Assuming C17 exists.** At the start of M4 `BeliefChannel` is `NullBeliefChannel` and `PUBLISH_ARTICLE` has no sub-resolver. Every call site must tolerate that without a branch that later becomes permanent.
