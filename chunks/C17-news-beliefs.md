# C17 — News outlets, journalism, claim checking, belief dynamics

**M4** · `polis/society/media/news.py`, `media/checker.py`, `polis/society/beliefs.py` · **Depends on:** C02, C03, C04, C05, C08, C10, C16, C11/C14 (`economy.ledger`) · **Blocks:** C18, C19, C20, C24b · **Size:** L

## 1. Context

Two halves that only make sense together. The first is a **press**: outlets that are ordinary
firms, reporters who are ordinary employees, a newsworthiness function that decides what gets
covered, an `NEWS_WRITE` call that produces prose *and structured claims*, an editor who
spikes copy, and distribution that competes for three slots in every agent's perception. The
second is **belief dynamics**: a closed proposition vocabulary, four update channels with
different strengths, source-trust weighting, and an entrenchment branch. The join between them
is `checker.py`, which scores every claim against the event log. **That is what makes
misinformation a measurement rather than a label** — the answer to B2 — and it is why the
`NEWS_WRITE` output schema demands claims alongside the body. No agent is ever told its own
accuracy score, and no score enters any `Observation`; the moment it does, the model optimises
the metric and the measurement dies.

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/07-SOCIETY-SPEC.md` | **§4 and §5 in full (primary source)**, §0.3 ledger table, §0.5 D-1, §0.6 RNG namespaces, §10.2/10.3 metrics, §11 F4/F5 (your failure modes), §12 cadences |
| `../docs/02-ARCHITECTURE.md` | §3.2 kinds, §4 determinism, §5 PHASE 7, §7.1, §8 routing (`NEWS_WRITE`, `IMPORTANCE`), §8.1 MECHANISM, §10 LLM failure |
| `../docs/03-DATA-MODEL.md` | §2.4 `beliefs`, §8 `outlets`/`articles`/`posts`, §4 ledger, §12 rebuild |
| `../docs/04-AGENT-SPEC.md` | §5 perception rule 4 and the `news` cap of 3, §9.1 (never render a number as a number), §9.2 `belief_updates[]`, §12.1 birth priors |
| Chunks | **C10** (`InstitutionResolver`), **C16** (`BeliefChannel`, `CommunicationResolver.compose`, `Platform`, `Post`), C05 (`LLMRouter.call`, `Purpose`, `CallResult`), C08 (`BeliefWriter`, `BeliefUpdate`, `MemoryArchive`), C03 (`Projection`), C04 (`Cadence`, `stable`, `det_id`) |

## 3. Scope — in

1. `beliefs.py` — `PROPOSITION_REGISTRY` (policy / factual / trust classes), `BeliefEngine`, the four channels, source trust, the update rule with its entrenchment branch, the `07 §5.5` LLM-update gates, genesis and birth priors, and the `07 §5.7` polarisation metrics.
2. `news.py` — outlets, newsroom staffing, the reporter's availability rule, newsworthiness, the `NEWS_WRITE` call, the editor gate, distribution, retraction, and the weekly fiscal close.
3. `checker.py` — the six-step claim-checking procedure, the closed `RESOLVERS` registry, `articles.accuracy`, `posts.truthfulness`, and the omission audit.
4. `NewsResolver` — an `InstitutionResolver` for `PUBLISH_ARTICLE` and `RETRACT`, **composed into C16's slot-2 facade**, not registered directly.
5. `NewsCycle` — the PHASE 7 institution (daily selection/writing/editing/distribution, daily checking, weekly fiscal close, weekly trust-tracks-accuracy).
6. Kinds 11030–11069 and 10060–10069 registered in `polis/events/kinds.py`.
7. Outlet ad/subscription/campaign revenue as balanced `post_transaction` legs with named counterparties.
8. `@mechanism` declarations for `belief_social_influence`, `belief_backfire`.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `posts`, `follows`, `engagements`, the feed, cascades, the social graph | **C16** |
| The slot-2 resolver registration itself | **C16** (`compose()`) |
| Firm mechanics: wages, rent, distress, `9030 BANKRUPTCY_FILED`, `FOUND_COMPANY` | **C11/C15** |
| `POST_WRITE` (agents writing posts) — you consume the `claims[]` it produced | **C09** |
| Campaign spend *decisions* and `12012`; you only book the outlet's side | **C18** |
| `TESTIFY` claim checking *call site* and perjury flagging; you provide the checker | **C19** |
| Belief priors at birth *trigger*; you provide the formula and the writer | **C20** |
| Metric storage/export | **C24b** |

## 5. Interfaces you provide

```python
# polis/society/media/news.py
from polis.agents.actions import (Action, ActionType, GateResult, InstitutionSlot,
                                  ResolutionContext, ValidatedAction, ValidationContext)

class NewsResolver:
    """InstitutionResolver (C10 §5) for the two media actions the platform does not own.
    slot == COMMUNICATION, so it is COMPOSED into C16's CommunicationResolver via
    `compose(self)` and is NEVER passed to ResolverRegistry.register."""
    slot:    Final[InstitutionSlot] = InstitutionSlot.COMMUNICATION
    handles: Final[frozenset[ActionType]] = frozenset(
        {ActionType.PUBLISH_ARTICLE, ActionType.RETRACT})

    def __init__(self, *, log: EventLog, clock: Clock, rng: RngRegistry,
                 outlets: "OutletRegistry", articles: "ArticleStore",
                 checker: "ClaimChecker", ledger: LedgerApi, cfg: SocietySettings) -> None: ...

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        """PUBLISH_ARTICLE: actor is employed by an outlet firm as `reporter` or `editor`.
        RETRACT: actor is the outlet's editor for an article, or the author for a post."""
    def check_locality(self, action: Action, ctx: ValidationContext)  -> GateResult:
        """remote_ok. A reporter files from anywhere."""
    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        """Outlet not closed; article_id/post_id exists and is not already retracted."""
    def resolve(self, actions: Sequence[ValidatedAction], tick: int,
                ctx: ResolutionContext) -> Sequence[Event]: ...
    def options_for(self, action_type: ActionType,
                    ctx: ValidationContext) -> tuple[Mapping[str, Any], ...]: ...

@dataclass(frozen=True, slots=True)
class Outlet:
    outlet_id: str; name: str; firm_id: str | None
    slant: float; rigour: float; reach: int; closed_tick: int | None

@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str; text: str
    entity_id: str; predicate: str; value: Any; as_of_tick: int
    sourced_to_event_seqs: tuple[int, ...]

@dataclass(frozen=True, slots=True)
class Article:
    article_id: str; outlet_id: str; reporter_id: str | None; tick: int
    headline: str; body: str; source_event_seqs: tuple[int, ...]
    claims: tuple[Claim, ...]
    accuracy: float | None; slant_applied: float | None; reach: int
    retracted_tick: int | None

class OutletRegistry:
    def __init__(self, *, log: EventLog, repo: "NewsRepository", rng: RngRegistry,
                 cfg: SocietySettings) -> None: ...
    def seed_at_genesis(self, n: int, tick: int) -> Sequence[Event]: ...     # 11050
    def register_from_firm(self, firm_id: str, founder_id: str, place_id: str,
                           tick: int) -> tuple[Outlet, Event]:
        """Called when a `media`-sector firm posts its first `reporter` vacancy."""
    def get(self, outlet_id: str) -> Outlet | None: ...
    def live(self) -> tuple[Outlet, ...]: ...                # sorted by outlet_id
    def newsroom(self, outlet_id: str) -> tuple[str, tuple[str, ...]]:
        """(editor_id, reporter_ids). Editor = highest `writing` skill, ties by agent_id."""
    def close(self, outlet_id: str, reason: str, tick: int) -> Event: ...    # 11052

class Newsworthiness:
    def score(self, event: Event, outlet: Outlet, tick: int) -> float: ...   # 07 §4.3
    def story_list(self, outlet: Outlet, since_seq: int, tick: int,
                   n: int) -> tuple[Event, ...]:
        """Top n by N(e), ties by seq ascending. Only events available to the outlet's
        reporters under `availability`."""

class AvailabilityIndex:
    """07 §4.2. Reporters are NOT omniscient."""
    PUBLIC_KINDS: Final[frozenset[int]]
    def available_to(self, reporter_id: str, event: Event, tick: int) -> bool: ...
    def channel(self, reporter_id: str, event: Event, tick: int
                ) -> Literal["public","witness","source","document"] | None: ...
    def cultivate(self, reporter_id: str, source_id: str, message_id: str,
                  tick: int) -> Event | None: ...            # 11035

class NewsCycle:
    """PHASE 7 institution. Cadences: news_cycle (daily), claim_check (daily),
    outlet_close (weekly), trust_accuracy (weekly)."""
    def __init__(self, *, router: LLMRouter, outlets: OutletRegistry,
                 newsworthiness: Newsworthiness, availability: AvailabilityIndex,
                 checker: "ClaimChecker", beliefs: "BeliefEngine", memories: "MemoryLookup",
                 platform: Platform, ledger: LedgerApi, log: EventLog, clock: Clock,
                 rng: RngRegistry, cfg: SocietySettings) -> None: ...
    async def run_cycle(self, tick: int) -> Sequence[Event]:
        """Select → write (NEWS_WRITE) → edit → publish → distribute. Outlets processed in
        stable() order by outlet_id; all NEWS_WRITE calls issued through router.gather."""
    async def check_pending(self, tick: int) -> Sequence[Event]: ...         # 11034
    def distribute(self, articles: Sequence[Article], tick: int
                   ) -> tuple[Mapping[str, tuple[ArticleBrief, ...]], Sequence[Event]]:
        """Top 3 per agent by S(i,a); supplies C07's PerceptionSources.news. Emits 11032."""
    def close_books(self, tick: int) -> Sequence[Event]: ...                 # 11051
    def update_outlet_trust(self, tick: int) -> Sequence[Event]:             # 07 §5.6

class EditorGate:
    def review(self, draft: "Draft", outlet: Outlet, tick: int
               ) -> Literal["publish","rewrite","spike"]: ...
    def spike_reason(self, draft: "Draft", outlet: Outlet
                     ) -> Literal["thin_sourcing","slant_mismatch","legal_risk","budget"] | None: ...

def measured_slant(article: Article, checks: Sequence["CheckResult"], outlet: Outlet) -> float:
    """07 §4.6. Mean SIGNED deviation of claim values from ground truth on stance-relevant
    predicates, signed toward outlet.slant. Slant enters as narrative, leaves as a number."""
```

```python
# polis/society/media/checker.py
Verdict = Literal["supported", "imprecise", "contradicted", "unverifiable"]

@dataclass(frozen=True, slots=True)
class CheckResult:
    claim_id: str; predicate: str; entity_id: str
    claimed_value: Any; truth_value: Any
    verdict: Verdict; score: float | None       # 1.0 / 0.5 / 0.0 / None
    matched_event_seqs: tuple[int, ...]

class Resolver(Protocol):
    kind: Literal["categorical","boolean","numeric","existential"]
    def __call__(self, entity_id: str, as_of_tick: int, ctx: "CheckContext") -> Any:
        """PURE over the committed log and its projections AT as_of_tick. May not read any
        state after as_of_tick. A claim is judged against what was true when it was made."""

RESOLVERS: Final[Mapping[str, Resolver]]        # the closed 15 of 07 §4.5

class ClaimChecker:
    def __init__(self, *, ctx: "CheckContext", log: EventLog,
                 cfg: SocietySettings) -> None: ...
    def check(self, claim: Claim, subject_kind: Literal["article","post","speech"],
              subject_id: str, tick: int) -> tuple[CheckResult, Event]: ...
    def aggregate(self, results: Sequence[CheckResult]) -> float | None:
        """Σ score / |verifiable|. None when there are no verifiable claims."""
    def audit_unannotated(self, posts: Sequence[Post], tick: int) -> float:
        """07 §4.5 rule 3. Seeded misinfo_audit_rate sample through Purpose.IMPORTANCE with
        a claim-extraction template. Returns claim_annotation_coverage. NEVER mutates a post."""
```

```python
# polis/society/beliefs.py
PropClass = Literal["policy", "factual", "trust"]
Channel   = Literal["inherited", "experience", "social", "media", "reflection"]

@dataclass(frozen=True, slots=True)
class PropositionSpec:
    name: str; cls: PropClass
    lo: float; hi: float; default_value: float; default_confidence: float
    templated: bool = False                      # e.g. fact.firm.<fm_id>.solvent

PROPOSITION_REGISTRY: Final[Mapping[str, PropositionSpec]]
POLICY_PROPOSITIONS: Final[tuple[str, ...]]      # the 20 of 07 §5.1, declaration order
def resolve_proposition(raw: str, ctx: "EntityContext") -> str | None:
    """Expands a template and verifies the entity exists in-run. None => gate 1 failure."""

class BeliefEngine:
    """Implements C16's BeliefChannel AND C08's BeliefWriter. One engine, two protocols."""
    def __init__(self, *, log: EventLog, clock: Clock, rng: RngRegistry,
                 repo: "BeliefRepository", graph: SocialGraph, cfg: SocietySettings) -> None: ...

    # --- reads (BeliefChannel) ---
    def value(self, agent_id: str, proposition: str) -> float: ...
    def confidence(self, agent_id: str, proposition: str) -> float: ...
    def population_mean(self, proposition: str) -> float: ...
    def trust_in(self, agent_id: str, source_id: str, channel: Channel) -> float: ...   # 07 §5.3

    # --- the one update kernel ---
    def predict_delta(self, agent_id: str, proposition: str, target: float,
                      source_id: str, channel: Literal["social","media"]) -> float:
        """PURE. No writes, no events, no RNG. AdversarialRanker's forward model."""
    def apply(self, agent_id: str, proposition: str, target: float,
              channel: Channel, source_id: str, tick: int,
              llm_call_id: str | None = None) -> Event | None:
        """Calls the same kernel as predict_delta, then writes and emits 10060/10061."""
    def apply_social(self, agent_id: str, proposition: str, target: float,
                     source_id: str, tick: int) -> Event | None: ...
    def apply_media(self, agent_id: str, proposition: str, target: float,
                    outlet_id: str, tick: int) -> Event | None: ...
    def apply_experience(self, agent_id: str, trigger_kind: int,
                         payload: Mapping[str, Any], tick: int) -> Sequence[Event]:
        """07 §5.2 closed mapping. MAY ONLY WRITE FACTUAL CREDENCES AND TRUST."""

    # --- BeliefWriter (C08) ---
    def apply_llm_belief_updates(self, agent_id: str, tick: int,
                                 updates: Sequence[BeliefUpdate],
                                 llm_call_id: str | None) -> int:
        """The seven 07 §5.5 gates in order. Writes source='reflection',
        source_ref='llm_call:<id>'. Emits 10060 per applied, 10062 per rejected.
        Safe with an empty sequence. Returns the number applied."""

    # --- lifecycle ---
    def initialise_population(self, agent_ids: Sequence[str], tick: int) -> Sequence[Event]: ...
    def priors_at_birth(self, child_id: str, mother_id: str, father_id: str
                        ) -> tuple[tuple[str, float, float], ...]:
        """07 §9.6. Policy stances + trust.generalised ONLY. Uses rng.get('beliefs.noise',
        child_id) — entity-scoped, one-shot. C20 emits 15030 and 10063."""
    def priors_for_migrant(self, agent_id: str, offsets: Mapping[str, float]
                           ) -> tuple[tuple[str, float, float], ...]: ...

@mechanism("belief_social_influence", entails="...")     # 07 §5.4 verbatim
@mechanism("belief_backfire", entails="...")             # 07 §5.4 verbatim
def update_kernel(b: float, c: float, target: float, tau: float, alpha: float,
                  cfg: BeliefSettings) -> tuple[float, float, bool]:
    """Returns (new_value, new_confidence, entrenched). THE only place the maths lives."""

# polis/society/beliefs_metrics.py
def bimodality_coefficient(x: Sequence[float]) -> float: ...
def hartigan_dip(x: Sequence[float]) -> tuple[float, float]: ...      # (D, p)
def cross_cutting_exposure(agent_id: str, window: Sequence[tuple[Post, float]],
                           engine: BeliefEngine) -> float | None:
    """None when the agent has < 5 annotated impressions (excluded from the mean)."""
def affective_polarisation(graph: SocialGraph, partition: Mapping[str, int]) -> float: ...
def time_to_consensus(series: Sequence[tuple[int, float]], floor: float,
                      sustain_ticks: int) -> int | None: ...
```

```python
# polis/society/protocols.py  (extends C16's)
class MemoryLookup(Protocol):
    """society MUST NOT import polis.agents.memory (02 §7.1). C08's repository satisfies
    this structurally; the concrete object is injected at the composition root."""
    def holds_memory_of(self, agent_id: str, event_seq: int) -> bool: ...
    def holders_of(self, event_seq: int) -> frozenset[str]: ...
    def retrieve_recent_texts(self, agent_id: str, tick: int, n: int) -> tuple[str, ...]: ...

class OfficeLookup(Protocol):                    # C18 implements; empty until then
    def holds_office(self, agent_id: str, tick: int) -> str | None: ...
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C16 | `CommunicationResolver.compose`, `BeliefChannel`, `Platform`, `Post`, `SocialGraph.trust` | slot-2 registration; social-channel call sites; impressions |
| C05 | `LLMRouter.call/gather`, `Purpose.NEWS_WRITE`, `Purpose.IMPORTANCE`, `CallResult` | writing, rewriting, the omission audit |
| C08 | `BeliefWriter` (you implement), `BeliefUpdate`, `MemoryLookup` shape | LLM-authored updates; the witness/source channel |
| C10 | `InstitutionResolver`, `ValidatedAction`, `PublishArticleParams`, `RetractParams` | slot-2 sub-resolver |
| C02 | `register_kind`, `NewEvent`, `EventReader` (`by_kind`, `by_subject`, `by_seq`) | availability, `RESOLVERS`, evidence |
| C03 | `Projection`, `Database` | `outlets`, `articles`, `beliefs` |
| C04 | `stable`, `det_id`, `round6`, `Cadence`, `RngRegistry`, `@mechanism` | determinism, PHASE 7 cadences |
| C11/C14 | `ledger.post_transaction`, `Leg` | outlet revenue, subscriptions |
| C11/C18 | `polis.config.runtime.RuntimeOverlay` — `flag`/`bp`/`cents`/`as_of` (C11 ships the read side, C18 writes it), `OfficeLookup` | `regulation.media.disclosure_required`, the `policy.value` resolver |
| C11 | `Ledger.transfer(src, dst, cents, reason)`, `Ledger.post_transaction(legs, *, tick, cause: Event)`, `Leg(account_id, direction, amount_cents, reason)`, `account_id(code, owner, …)` | outlet revenue, subscriptions |

> **Coordination item 1.** C08's `BeliefWriter.apply_llm_belief_updates` returns `int`. Match
> it exactly or `NullBeliefWriter` and `BeliefEngine` are not substitutable at the composition
> root.
>
> **Coordination item 2.** `Purpose.POST_WRITE` is in C05's `DEFERRED_PURPOSES` and flushes in
> PHASE 7. The claim checker must run **after** `flush_deferred`, or deferred posts are checked
> a cycle late and `misinfo.half_life` is off by one news cycle.
>
> **Coordination item 3.** C16's `ArticleBrief` (from C07) carries `article_id, outlet_id,
> headline, tick` only. Confirm that is enough for the `media` belief channel — it is not:
> you also need the article's `stance_proposition`/`stance_value` to fire an update. Ask C07 to
> widen `ArticleBrief` rather than reading `articles` from inside perception.

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `outlets` | **W** | `slant`, `rigour`, `reach`; `firm_id` nullable until a firm exists |
| `articles` | **W** | `accuracy` and `slant_applied` written by the checker *after* publication |
| `posts` | **W (one column)** | `truthfulness` only. C16 owns every other column — use a targeted UPDATE, never a row rewrite |
| `beliefs` | **W** | the whole table; PK `(run_id, agent_id, proposition)` |
| `events` | R | ground truth for every resolver, and the availability index |
| `memories` | R | **only through `MemoryLookup`** |
| `metrics` | R | `macro.unemployment`, `macro.cpi` resolvers |
| `ohlcv`, `firms`, `employments`, `elections`, `court_cases`, `policies` | R | resolver ground truth |
| `ledger_*` | never directly | `post_transaction` only |

Register `Projection`s for `outlets`, `articles`, `beliefs`. `posts.truthfulness` is written
by the **articles/claims projection**, which must declare `posts` in its `tables` — coordinate
with C16, since `register_projection` asserts disjoint `tables`. Resolution: C16's posts
projection handles `11034` for `subject_kind == "post"` and writes `truthfulness`; C17 owns the
kind and the payload, C16 owns the column. Agree this before either merges.

## 8. Event kinds owned

**Ranges: 11030–11999** (owner `polis.society.media`) and **10060–10069** (owner
`polis.society.beliefs`, declared deviation D-1 in `07 §0.5`). 11000–11029 are C16's.

| Kind | Name | Payload |
|---|---|---|
| 11030 | `ARTICLE_PUBLISHED` | `article_id, outlet_id, reporter_id, headline, body_hash, source_event_seqs[], claims[], slant_at_write, rigour_at_write, llm_call_id` |
| 11031 | `ARTICLE_SPIKED` | `draft_id, outlet_id, reporter_id, editor_id, reason, rewrite_attempts` |
| 11032 | `ARTICLE_DISTRIBUTED` | `article_id, reach, impressions, district_shares{}, subscriber_share` |
| 11033 | `ARTICLE_RETRACTED` | `article_id\|post_id, outlet_id, author_id, reason, correction_text, original_reach, correction_reach` |
| 11034 | `CLAIM_CHECKED` | `subject_kind, subject_id, claim_id, predicate, entity_id, claimed_value, truth_value, verdict, matched_event_seqs[], score` |
| 11035 | `SOURCE_CULTIVATED` | `reporter_id, source_id, outlet_id, message_id, subject_event_seqs[]` |
| 11050 | `OUTLET_FOUNDED` | `outlet_id, firm_id, founder_id, slant, rigour, place_id` |
| 11051 | `OUTLET_REVENUE_BOOKED` | `outlet_id, period_start_tick, impressions, cpm_cents, ad_revenue_cents, subscription_cents, campaign_cents, advertisers[], txn_ids[]` |
| 11052 | `OUTLET_CLOSED` | `outlet_id, firm_id, reason, final_reach, staff_ids[]` |
| 10060 | `BELIEF_UPDATED` | `agent_id, proposition, old_value, new_value, old_confidence, new_confidence, channel, source_id, source_ref, entrenched, llm_call_id` |
| 10061 | `BELIEF_DRIFT_APPLIED` | `agent_id, channel, updates[{proposition, d_value, d_confidence}], n_sources` |
| 10062 | `BELIEF_UPDATE_REJECTED` | `agent_id, proposition, raw_value, gate, llm_call_id` |
| 10063 | `BELIEF_PRIORS_SET` | `agent_id, source (genesis\|birth\|migration), propositions[{proposition, value, confidence}]` |

`10060` is written in full for `experience` and `reflection`. High-volume `social` and `media`
nudges are aggregated per agent per tick into one `10061` — without this the belief layer
outweighs the rest of the log. Researcher-injected falsehoods get **no kind**: they arrive as
`99001` and cause an ordinary `11010`/`11030` whose `cause_seq` points at the injection.

## 9. Implementation notes

### 9.1 The news cycle, in order

```
PHASE 7, cadence society.news_cycle (daily), outlets in stable() order by outlet_id:
 1. since_seq = last cycle's high-water mark for this outlet
 2. candidates = events available to this outlet's reporters (AvailabilityIndex)
 3. stories   = Newsworthiness.story_list(outlet, since_seq, tick,
                    n = stories_per_reporter_per_cycle * len(reporters))
 4. assign stories to reporters in stable() order; build one CallRequest per story
 5. router.gather(requests)                      # results in REQUEST order (C05)
 6. EditorGate.review each draft -> publish | rewrite (one NEWS_WRITE retry) | spike (11031)
 7. publish -> 11030, articles row, claims stored unchecked
 8. distribute -> top 3 per agent by S(i,a) -> 11032, PerceptionSources.news
 9. (separate daily cadence, AFTER router.flush_deferred) ClaimChecker over all new claims
10. accuracy / truthfulness / measured_slant written back; 11034 per claim
```

Steps 1–8 are one cadence; step 9 is a second one so that deferred `POST_WRITE` results are
already flushed. Do not fuse them.

### 9.2 Reporters are not omniscient

`available_to` is four disjoint channels (`07 §4.2`) and it is the only read path into the
world for a reporter. The **source** channel requires a `friend`/`colleague` tie with
`trust ≥ 0.5`, a `DIRECT_MESSAGE` sent within `source_window` (14 sim-days), and an answer —
each of which costs an action slot. That is what makes a scoop work. If you shortcut it (any
"reporters may read any event with kind in X" fallback), investigative journalism becomes free
and every outlet publishes the same story, which is failure mode F5.

### 9.3 The `NEWS_WRITE` prompt and its output

Slant is rendered **as narrative editorial line, never as a number** (`04 §9.1`). Rigour is
rendered as a sourcing standard. The prompt carries the reporter's own retrieved memories and
beliefs (via `MemoryLookup` and `BeliefEngine`) — omitting them is the single most common
cause of F5. Output schema is `07 §4.4`: `{headline, body, claims[], confidence}` where each
claim carries `refers_to: {entity_id, predicate, value, as_of_tick}` and
`sourced_to_event_seqs[]`. Structured claims cost one extra field on a call you were making
anyway, and they are the entire reason B2 is answerable.

On `NEWS_WRITE` failure after the router's repairs: **spike the story**, `11031{reason:
budget}`, and continue. Do not synthesise an article from a template — a formatter-written
article has perfect accuracy and zero slant and it poisons both the F5 detector and the
`articles.accuracy` distribution.

### 9.4 Claim checking — the procedure that makes B2 real

The six steps of `07 §4.5`, exactly. Three properties are load-bearing:

- **Purity at `as_of_tick`.** Every resolver takes `as_of_tick` and may not read later state.
  A claim that `fm_acme` is insolvent, made when it was solvent and true a month later, is
  `contradicted`. Implement by passing a `CheckContext` that hard-caps `seq`/`tick` on every
  query, and test it by checking the same claim twice around a state change.
- **It never blocks and never penalises.** Nothing is deleted, demoted, down-ranked, or
  surfaced to any agent. The checker is an observer.
- **No agent is ever told its score.** `posts.truthfulness` and `articles.accuracy` appear in
  no `Observation`, no `PostBrief`, no `ArticleBrief`, no prompt variable.

Numeric comparison: `rel = |claimed − truth| / max(|truth|, floor)`; `≤ tol` supported,
`≤ 3·tol` imprecise, else contradicted, with `tol = claim_tolerance = 0.10`. Existential
claims are `contradicted` only when the log is *complete* for that kind — keep an explicit
`COMPLETE_KINDS` set and default to `unverifiable` when unsure.

The **omission audit** samples `misinfo_audit_rate` (0.05) of claim-free posts through
`Purpose.IMPORTANCE` with a claim-extraction template, reusing an existing purpose. It corrects
the denominator (`claim_annotation_coverage`); it never changes a post.

### 9.5 Belief updates — one kernel, four callers

```python
tau = trust_in(agent, source, channel)
alpha = ALPHA[channel]                       # experience .35, social .10, media .08
lam, d = 1 - c, abs(target - b)
entrenched = channel in ("social","media") and d > 0.60 and c > 0.60 and tau < 0.40
if entrenched:  db = -0.05*(1-tau)*sign(target-b)*min(d,1.0); dc = +0.03; trust -= 0.04
else:           db = alpha*tau*lam*(target-b);                 dc = +0.02*tau*(1-d)
```

`predict_delta` and `apply` **must call the same function**. The adversarial ranker's entire
premise is that the platform's forward model equals the real update rule; two implementations
that drift apart make the arm meaningless and the bug invisible.

**The normative rule, worth more than the rest of this section:** the direct-experience
channel may never update a policy stance. Being fired updates
`fact.economy.jobs_scarce`; it may **not** move `policy.welfare.generosity`. If it does, B4
("does precarity radicalise") is answered by your update table rather than by the society.
Enforce it in code: `apply_experience` asserts `spec.cls != "policy"` and raises.

`ALPHA[experience] > ALPHA[social] > ALPHA[media]` is a substantive assumption; the three
values are swept and must be config keys, not literals.

### 9.6 LLM-authored updates: the seven gates

Gates in order (`07 §5.5`), each failure incrementing a per-run counter and emitting `10062`:
unknown proposition → drop; value out of class range → clamp; confidence out of `[0,1]` →
clamp; more than `max_belief_updates_per_call` (5) → drop the excess in listed order;
`|Δvalue| > max_step` (0.35) → clamp the step keeping direction; a `trust.outlet.<X>` update
whose `source_ref` is an article from X → apply at half weight and flag `self_serving`;
then sort by `proposition` before application. Written with `source = 'reflection'` because
`03 §2.4`'s enum has no `deliberate` value — joining `llm_calls.purpose` recovers the
distinction.

### 9.7 Trust tracks accuracy, and that is a result not an assumption

Weekly, per agent, per outlet the agent was actually exposed to:
`Δ trust = 0.05 · (realised_accuracy − trust)`, over articles that entered *that agent's* news
slot and whose claims have since been checked. Restricting to seen-and-checked articles is
what makes convergence a measurable property: it will not converge if agents mostly see
outlets they already trust, which is exactly the feedback the feed controls.

### 9.8 Outlet economics — real counterparties or nothing

Weekly fiscal close:
`ad_revenue = round(impressions_week / 1000 · cpm_cents)`, **allocated from identified
advertisers** — firms allocate `ad_budget_share` of last period's revenue (C11/C15 owns the
firm decision) split across outlets in proportion to `outlet.reach`; government public notices
are a budget line (C18). Each is one balanced `post_transaction` with `reason="purchase"`.
There is no "advertising income" account. If total advertiser budget is zero, outlet ad revenue
is zero and outlets fail through `9030` → `11052`. Media concentration must be an economic
outcome, not a parameter.

Retraction distributes a correction at `correction_reach_multiplier` (0.6) of the original's
reach — deliberately less, because that asymmetry is what B2 quantifies.

## 10. Configuration keys

```yaml
society:
  outlets: 4
  outlet_slant_dispersion: 0.55
  cpm_cents: 40
  news_cycle: daily
  stories_per_reporter_per_cycle: 1
  claim_tolerance: 0.10
  misinfo_audit_rate: 0.05
  source_window_sim_days: 14
  line_threshold: 0.25
  correction_reach_multiplier: 0.6
  subscription_price_cents: 0
  reach_norm: 500
  newsworthiness_weights: {mag: 0.25, prom: 0.20, nov: 0.15, conf: 0.20, prox: 0.10, slant: 0.10}
  distribution_weights: {trust: 0.35, topic: 0.25, prox: 0.15, sub: 0.15, reach: 0.10}

beliefs:
  alpha: {experience: 0.35, social: 0.10, media: 0.08}
  theta_backfire: 0.60
  theta_entrench: 0.60
  theta_trust: 0.40
  beta_backfire: 0.05
  delta_entrench: 0.03
  eta_trust: 0.04
  gamma_c: 0.02
  max_belief_updates_per_call: 5
  max_step: 0.35
  genesis: {mixture_separation: 0.0, sd: 0.25}      # separation 0 => NOT pre-polarised
  heritability_beliefs: 0.40                        # B6 knob, swept 0..1
  confidence_dilution: 0.5
  sigma_belief: 0.08
  consensus_floor: 0.02

mechanisms:
  belief_social_influence: bounded_confidence
  belief_backfire: "on"

ablations:
  social_influence_off: false     # belief change becomes LLM-only
  backfire_off: false
```

## 11. Acceptance criteria

- [ ] `NewsResolver` is composed into C16's slot-2 facade and never passed to `ResolverRegistry.register`.
- [ ] A reporter cannot write about an event no channel makes available; a test with a private event and no witness produces zero articles citing it.
- [ ] `NEWS_WRITE` output validates against the `07 §4.4` schema; every published article has ≥ 1 claim with a resolvable predicate or is spiked for `thin_sourcing`.
- [ ] The `NEWS_WRITE` prompt contains no numeral for `slant` or `rigour` (template lint), and does contain the reporter's retrieved memories.
- [ ] On `NEWS_WRITE` failure the story is spiked; **no template-written article is ever published.**
- [ ] Every `RESOLVERS` entry is pure at `as_of_tick`: checking the same claim before and after the underlying fact changes yields the same verdict.
- [ ] The checker mutates no post, deletes nothing, and demotes nothing; `posts.truthfulness` and `articles.accuracy` appear in no `Observation`, `PostBrief`, `ArticleBrief`, or prompt variable.
- [ ] `truthfulness`/`accuracy` are `NULL` when there are no verifiable claims, and `claim_annotation_coverage` is reported per run.
- [ ] `slant_applied` is computed from published claims against ground truth, never copied from `outlets.slant`.
- [ ] `predict_delta` and `apply` produce identical `Δvalue` for identical inputs, over 10,000 randomised cases.
- [ ] **`apply_experience` raises if handed a `policy.*` proposition.**
- [ ] The entrenchment branch fires exactly on `d > 0.60 and c > 0.60 and tau < 0.40` for social/media, and never for `experience` or `inherited`.
- [ ] `--social-influence-off` produces zero `10061` events and belief change only via `reflection`.
- [ ] All seven `07 §5.5` gates fire with their own `10062` gate label; an unknown proposition is dropped, not clamped.
- [ ] `priors_at_birth` writes policy stances and `trust.generalised` only — no `fact.*` proposition is ever inherited.
- [ ] Every outlet cent has a named counterparty; `INV-MONEY` holds across a weekly close with four outlets, three advertisers and one campaign buy.
- [ ] An outlet with zero advertiser budget books zero ad revenue and can reach `9030`.
- [ ] `polis rebuild` reproduces `outlets`, `articles`, `beliefs` and `posts.truthfulness` exactly.
- [ ] `mypy --strict`, `import-linter`: no import of `polis.agents.cognition`, `.memory`, or `.state`; memory access only via `MemoryLookup`.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/society/test_availability.py` | Four channels; a private event with no witness is unavailable; the source channel requires tie + trust + DM + answer |
| `tests/unit/society/test_newsworthiness.py` | Each term of `N(e)`; tie-break by `seq`; different `slant` values select different story sets |
| `tests/unit/society/test_editor_gate.py` | All four spike reasons; one rewrite then spike; `rigour < 0.3` skips the legal gate |
| `tests/unit/society/test_checker_resolvers.py` | Every one of the 15 resolvers against a fixture log, including all four comparison kinds |
| `tests/invariants/test_checker_purity.py` | **Merge gate.** Same claim checked at `as_of_tick` and 5,000 ticks later after the fact flipped → identical verdict |
| `tests/invariants/test_no_score_leakage.py` | **Merge gate.** Reflective scan: `truthfulness`/`accuracy` absent from every perception type and every rendered prompt variable |
| `tests/unit/society/test_omission_audit.py` | Sampling rate is seeded and reproducible; no post is mutated; coverage arithmetic |
| `tests/unit/society/test_belief_kernel.py` | `predict_delta == apply` deltas over 10,000 cases; entrenchment boundary conditions; clip to class range |
| `tests/invariants/test_experience_never_moves_policy.py` | **Merge gate.** Every entry of the `07 §5.2` mapping targets a factual or trust proposition; `apply_experience` raises on a policy stance |
| `tests/unit/society/test_belief_gates.py` | All seven gates, each with its own `10062.gate` value; ordering by proposition; `self_serving` half-weight |
| `tests/unit/society/test_belief_priors.py` | Heritability 0 → population mean; 1 → midparent; no `fact.*` inherited; determinism from `rng.get('beliefs.noise', child_id)` |
| `tests/unit/society/test_polarisation_metrics.py` | BC against a known bimodal and unimodal sample; dip statistic; CCE excludes agents with < 5 annotated impressions |
| `tests/integration/test_outlet_ledger.py` | `INV-MONEY` across a weekly close with ads, subscriptions and a campaign buy; zero advertiser budget → zero revenue |
| `tests/integration/test_news_cycle.py` | 4 outlets, 60 sim-days, StubProvider: `news.editorial_divergence > 0.05`, `news.selection_divergence > 0`, accuracy variance > 0 |
| `tests/integration/test_misinfo_pipeline.py` | An injected `99001` falsehood produces a checkable `11030`, a `contradicted` claim, measurable adoption, and a correction with lower reach than the original |
| `tests/determinism/test_news_beliefs_determinism.py` | Same seed twice → identical 10060–10069 and 11030–11069 sequences |

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. `news.py`, `checker.py`, `beliefs.py` export the §5 symbols with those exact signatures; `BeliefEngine` satisfies both `BeliefChannel` (C16) and `BeliefWriter` (C08) with no adapter.
2. Kinds 11030–11069 and 10060–10069 registered with payload schemas, `10060–10069` owned by `polis.society.beliefs` per D-1.
3. `prompts/news_write/{system,user}.v1.jinja` + two paraphrase siblings + `prompts/schemas/news_write.schema.json`, all in `runs.prompt_manifest`, and a lint rule asserting no numeral renders `slant`/`rigour`.
4. `RESOLVERS` documented as a closed registry with its extension procedure (spec change only).
5. Both `@mechanism` declarations with `entails` matching `07 §5.4` verbatim, ablatable.
6. Handback records: the three coordination items in §6; the `posts.truthfulness` projection-ownership agreement with C16; the measured `misinfo.organic_share` and `claim_annotation_coverage` from a 60-sim-day calibration run; and the measured `news.editorial_divergence` against the F5 threshold.

## 14. Traps

1. **News that just restates the event log (F5).** The default outcome. Every outlet gets the same top story, transcribes it accurately, and the media channel carries no signal. Causes are always the same three: the reporter's memories and beliefs are missing from the prompt, `w_slant` is too small so all outlets select the same stories, and the editor's `line` gate never actually spikes anything. Check `11031` counts by reason before concluding the model "ignores slant".
2. **Rendering slant as a number.** `"your outlet's slant is -0.62"` makes the model perform slant theatrically or refuse it entirely. Narrative editorial line, always (`04 §9.1`).
3. **Falling back to a template article when `NEWS_WRITE` fails.** Perfect accuracy, zero slant, and a silent bimodal `articles.accuracy` distribution that looks like a finding. Spike instead.
4. **The model will not generate a falsehood (F4) — and trying to fix it by asking for one.** The system does not need agents to lie; it needs agents to be **wrong**. Belief error transmitted honestly is the primary channel: an agent whose `fact.firm.X.solvent` credence is 0.2 sincerely posts that X is failing and the checker scores it `contradicted`. No jailbreak, no prompt asking for deception. If you find yourself writing "generate a misleading claim" into a template, stop.
5. **A resolver reading state after `as_of_tick`.** The single subtlest bug in this chunk. Hindsight turns every prediction into a lie and every lucky guess into truth, and it will not show up until someone plots accuracy against article age.
6. **Letting the checker act.** Demoting a low-truthfulness post, flagging it in a feed, or telling the author. Any of these makes truthfulness endogenous to the ranker and B2 unanswerable.
7. **Two implementations of the update kernel.** `predict_delta` written "quickly" for the adversarial ranker and `apply` written properly. They drift within a week and the arm silently optimises the wrong objective.
8. **Experience moving a policy stance.** Even one entry — "unemployed 30 days → `policy.welfare.generosity` += 0.1" — deletes B4. It will look reasonable in review. Assert against it in code.
9. **`ALPHA[social]` too high.** Bounded confidence with a large α drives the whole population to consensus in a few sim-months and produces F1. Any B1 headline must survive `--social-influence-off`.
10. **Starting the population polarised.** `genesis.mixture_separation > 0` answers B1 by construction. Default separation is 0 and any nonzero value must be reported with the result.
11. **Inheriting `fact.*` propositions.** A newborn with a view on whether `fm_acme` is solvent corrupts every misinformation measurement, because adoption then has a birth channel.
12. **Aggregating `10060` for `experience` or `reflection`.** Those two channels are the ones analysis needs at full fidelity (`misinfo.adoption_reach` traces `source_ref`). Only `social` and `media` collapse into `10061`.
13. **Inventing an advertising revenue account.** "Outlets earn CPM" with no debited counterparty breaks `INV-MONEY` through the media layer, which is exactly the failure `07 §0.3` was written to prevent.
14. **Making outlet survival a parameter.** If outlets cannot fail, media concentration is an assumption. Let them go bankrupt through `9030` like any firm.
15. **Checking claims before `flush_deferred`.** Deferred `POST_WRITE` results land in PHASE 7; check after, or a whole class of posts is checked one cycle late and every half-life is biased.
16. **Writing the whole `posts` row to set `truthfulness`.** C16 owns that table. Targeted UPDATE, agreed projection ownership, or `polis rebuild` diverges.
