# C18 — Parties, elections, voting, offices, the policy engine

**M4** · `polis/society/polity.py`, `policy.py` (+ **the write side of `polis/config/runtime.py`**; C11 ships its read side) · **Depends on:** C02, C03, C04, C05, C10, **C11 (`Ledger`, `RuntimeOverlay`)**, C16, C17 · **Blocks:** C19, C20, C24b, C25 · **Size:** L

## 1. Context

An election that does not change a simulation parameter is theatre, and a society whose
politics cannot touch its economy is not worth simulating. This chunk builds parties that
agents found for themselves (no party exists at genesis — seeding one answers research
question B3 by fiat), candidacies with real deposits, campaigns where money buys reach through
balanced ledger legs, a vote model that mixes belief congruence, self-interest, social
influence and media exposure, and offices with terms and succession. **Its critical deliverable
is the policy engine**: a closed `POLICY_REGISTRY` of controllable parameters, an admissibility
stage that no proposal may bypass, and an enactment that writes into
`polis/config/runtime.py`'s tick-keyed overlay so that economy and society *actually read the
new value next tick*. If any institution caches a policy parameter, everything above it is
decoration (failure mode F3).

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/07-SOCIETY-SPEC.md` | **§6 and §7 in full (primary source)**, §0.2 resolution order, §0.3 ledger table, §0.5 D-2/D-4, §0.6 RNG, §10.1/10.6, §11 F3/F8, §12 cadences |
| `../docs/02-ARCHITECTURE.md` | §3.2 kinds, §4 determinism, §5 PHASE 5 slot 8 and PHASE 7, §5.2 sim-time cadences, §7.1, §8.1 MECHANISM, §9 invariants |
| `../docs/03-DATA-MODEL.md` | §8 `parties`/`elections`/`candidacies`/`votes`/`policies`, §4 ledger, §12 rebuild |
| `../docs/04-AGENT-SPEC.md` | §7 salience and `mandatory` obligations, §9.2 output schema, §12.2 stages (age ≥ 18) |
| Chunks | **C10** (`InstitutionResolver`, `FoundPartyParams`, `ValidatedAction`), **C01** (`RuntimeConfig`, `Enactment`, `Settings`), C05 (`LLMRouter`, `Purpose.DELIBERATE`), C16 (`SocialGraph`, `Platform`), C17 (`BeliefEngine`, `OutletRegistry`), C04 (`Cadence`, `Scheduler`, `stable`, `det_id`) |

## 3. Scope — in

1. `PolityResolver` — the `InstitutionResolver` registered in `InstitutionSlot.POLITY` (slot 8), handling all seven polity action types.
2. Parties: `FOUND_PARTY` (ratified addition, `07 §0.5` D-2), `JOIN_PARTY`, membership, leadership, quarterly platform drift, dissolution.
3. Candidacy, deposits and refunds, the record bar.
4. Campaigning: `ads` / `rally` / `canvass`, each with its ledger consequence and its reach computation, and exposure decay.
5. The vote model: deliberate voters via LLM, reflex voters via a conditional logit **fitted to the deliberate voters in the same election**, abstention, and the full per-vote utility decomposition in `12020`.
6. Election mechanics: `plurality`, `approval`, `irv`, `proportional`; turnout; calling and campaign windows.
7. Offices: president, council, judge, police chief, cb governor — appointment, confirmation, terms, succession, salaries as government payroll.
8. **The policy engine**: `POLICY_REGISTRY`, the five admissibility predicates plus `P-SCOPE`/`P-SEPARATION`, static fiscal scoring, council voting, veto and override, enactment into `RuntimeConfig`, and the `policies` projection.
9. `BUDGET_SET` — the government budget allocation that C19 (police, courts, prisons) and C17 (public notices) read.
10. Kinds 12000–12999 registered in `polis/events/kinds.py`.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `RuntimeConfig`'s class definition (C01 already ships it) — you are its **only writer** | **C01** ships, **C18** writes |
| Reading policy parameters and acting on them (payroll, rates, benefits, sentencing) | every consuming institution |
| Government tax collection, transfers, payroll execution | **C11/C14** |
| Crime, courts, incarceration, the franchise consequence of a sentence | **C19** |
| The judge's `JUDGE` call; you only appoint and confirm judges | **C19** |
| Belief propositions and the `policy.*` vocabulary | **C17** |
| Campaign *ad delivery* into perception; you compute reach, C17 books the outlet revenue | **C17** |
| Metric storage/export | **C24b** |

## 5. Interfaces you provide

```python
# polis/society/polity.py
from polis.agents.actions import (Action, ActionType, GateResult, GateFailure, InstitutionSlot,
                                  ResolutionContext, ValidatedAction, ValidationContext)

class PolityResolver:
    """THE InstitutionResolver for InstitutionSlot.POLITY. Registered directly with
    ResolverRegistry (C10 §5). Resolves 8th, after every economic institution."""
    slot:    Final[InstitutionSlot] = InstitutionSlot.POLITY
    handles: Final[frozenset[ActionType]] = frozenset({
        ActionType.FOUND_PARTY, ActionType.JOIN_PARTY, ActionType.ANNOUNCE_CANDIDACY,
        ActionType.CAMPAIGN, ActionType.VOTE, ActionType.PROPOSE_POLICY, ActionType.LOBBY})

    def __init__(self, *, log: EventLog, clock: Clock, rng: RngRegistry,
                 parties: "PartyRegistry", elections: "ElectionOffice",
                 offices: "OfficeRegister", policy: "PolicyEngine",
                 exposure: "ExposureLedger", graph: SocialGraph, beliefs: BeliefEngine,
                 outlets: OutletRegistry, world: World, ledger: LedgerApi,
                 runtime: "Overlay", cfg: PolitySettings) -> None: ...

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        """FOUND_PARTY: age>=18, alive, not incarcerated, >=3 founding_member_ids.
        ANNOUNCE_CANDIDACY: age>=18, record bar, election in candidacy window.
        CAMPAIGN: actor holds the named candidacy, or is a member of its party.
        VOTE: eligible for this election (07 §6.5), has not already voted.
        PROPOSE_POLICY: council seat or presidency, OR >= initiative_signatures cosigners,
                        AND parameter in POLICY_REGISTRY. A parameter outside the registry
                        fails HERE, at capability — never at admissibility."""
    def check_locality(self, action: Action, ctx: ValidationContext)  -> GateResult:
        """VOTE and rally CAMPAIGN require a place; everything else is remote_ok."""
    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        """Deposits, fees, campaign funds, and polity.campaign_cap_cents read through
        runtime.get(..., ctx.tick) — never from the static Settings object."""
    def resolve(self, actions: Sequence[ValidatedAction], tick: int,
                ctx: ResolutionContext) -> Sequence[Event]: ...
    def options_for(self, action_type: ActionType,
                    ctx: ValidationContext) -> tuple[Mapping[str, Any], ...]:
        """Live parties for JOIN_PARTY; open candidacies for VOTE; POLICY_REGISTRY keys with
        their admissible ranges for PROPOSE_POLICY. () for FOUND_PARTY and LOBBY."""

@dataclass(frozen=True, slots=True)
class Party:
    party_id: str; name: str; platform: Mapping[str, float]
    founded_tick: int; dissolved_tick: int | None
    member_ids: tuple[str, ...]; leader_id: str | None

class PartyRegistry:
    def found(self, founder_id: str, params: FoundPartyParams, tick: int
              ) -> tuple[Party, Sequence[Event]]: ...                    # 12001
    def join(self, agent_id: str, party_id: str, tick: int) -> Sequence[Event]: ...  # 12002/12003
    def leave(self, agent_id: str, reason: str, tick: int) -> Event | None: ...
    def membership(self, agent_id: str) -> str | None: ...
    def live(self) -> tuple[Party, ...]: ...                             # sorted by party_id
    @mechanism("party_platform_drift", entails="...")                    # 07 §6.1 verbatim
    def drift_platforms(self, tick: int) -> Sequence[Event]:
        """PHASE 7 quarterly. platform[p] <- 0.75*platform[p] + 0.25*trimmed_mean_10%(members)."""
    def dissolve_stale(self, tick: int) -> Sequence[Event]: ...          # 12005

@dataclass(frozen=True, slots=True)
class Candidacy:
    candidacy_id: str; election_id: str; agent_id: str; party_id: str | None
    platform: Mapping[str, float]; spend_cents: int; votes: int

@dataclass(frozen=True, slots=True)
class Ballot:
    voter_id: str
    choice: str | None
    ranking: tuple[str, ...] = ()
    approvals: tuple[str, ...] = ()
    origin: Literal["deliberate", "reflex"] = "reflex"
    utility: Mapping[str, float] = field(default_factory=dict)

class ElectionOffice:
    """PHASE 7. Cadence per office from polity.offices[*].term_sim_years."""
    def call(self, office: str, tick: int) -> tuple[str, Event]: ...     # 12010
    def announce(self, agent_id: str, params: AnnounceCandidacyParams, tick: int
                 ) -> tuple[Candidacy, Sequence[Event]]: ...             # 12011 + deposit txn
    def eligible(self, agent_id: str, election_id: str, tick: int) -> bool:
        """alive, age>=18, not incarcerated unless runtime.get('polity.felon_franchise'),
        resident >= 90 sim-days."""
    async def hold(self, election_id: str, tick: int) -> Sequence[Event]:
        """Force-route eligible agents DELIBERATE (mandatory obligation, 04 §7), gather VOTEs,
        fit the reflex model on the deliberate ballots, cast reflex ballots, tally, resolve."""
    def tally(self, election_id: str, ballots: Sequence[Ballot], method: str
              ) -> "Tally": ...
    def turnout(self, election_id: str) -> float: ...

class VoteModel:
    """MECHANISM vote_model: fitted_from_deliberate. 07 §6.5."""
    def features(self, voter_id: str, c: Candidacy, election_id: str, tick: int
                 ) -> Mapping[str, float]:
        """{congruence, self_interest, social, media, party_id, incumbency}. Deterministic
        and auditable; stored verbatim in the 12020 payload."""
    def self_interest(self, voter_id: str, platform: Mapping[str, float], tick: int) -> float:
        """First-order Δ in the voter's own annual disposable income under the platform's
        parameter values, applied to their CURRENT income statement. Static, no behaviour."""
    def fit(self, deliberate: Sequence[tuple[str, str, Mapping[str, float]]], election_id: str
            ) -> "FitResult":
        """Multinomial logit, deterministic (fixed order, fixed passes, round6). 20% holdout."""
    def choose(self, voter_id: str, candidacies: Sequence[Candidacy], omega: Mapping[str, float],
               election_id: str, tick: int) -> Ballot:
        """argmax U with Gumbel noise from rng.get('polity.vote', voter_id, tick);
        ABSTAIN when max U < abstain_threshold_i."""

@dataclass(frozen=True, slots=True)
class FitResult:
    omega: Mapping[str, float]; log_likelihood: float
    holdout_accuracy: float; n_deliberate: int; usable: bool

class OfficeRegister:
    """Implements C17's OfficeLookup."""
    def holds_office(self, agent_id: str, tick: int) -> str | None: ...
    def holder(self, office: str, tick: int) -> str | tuple[str, ...] | None: ...
    def assume(self, office: str, agent_id: str, tick: int, *, via: str,
               salary_cents: int) -> Sequence[Event]: ...                # 12023
    def vacate(self, office: str, agent_id: str, reason: str, tick: int) -> Sequence[Event]:
        """reason ∈ term_end|death|resignation|removal|incarceration|emigration. Runs
        succession: council member with most votes at the last election takes the presidency."""
    def appoint(self, office: str, appointee_id: str, by_id: str, tick: int
                ) -> Sequence[Event]: ...                                # 12040 (+ confirmation)
    def remove(self, office: str, agent_id: str, by: str, margin: float,
               tick: int) -> Sequence[Event]: ...                        # 12041
```

```python
# polis/society/policy.py  (imported by polity.py; kept separate for testability)
Authority = Literal["council_majority", "council_and_president", "cb_governor_only"]

@dataclass(frozen=True, slots=True)
class PolicySpec:
    parameter: str
    py_type: type | str                     # 'int' | 'float' | 'bool' | 'enum' | 'brackets'
    lo: Any | None; hi: Any | None
    authority: Authority
    lag: str                                # SimDuration spec: '1M', '1Q', '1W', 'immediate'
    effect_site: str                        # documentation; also the F3 audit's expectation
    enabled_when: str | None = None         # e.g. 'polity.can_regulate_feed'

POLICY_REGISTRY: Final[Mapping[str, PolicySpec]]      # the 31 rows of 07 §7.2. CLOSED.
def registry_for(settings: Settings) -> Mapping[str, PolicySpec]:
    """Applies `enabled_when`; society.feed_algorithm is ABSENT unless can_regulate_feed."""

# UNITS. 07 §7.2 writes rates as floats; C11's RuntimeOverlay exposes them as INTEGER BASIS
# POINTS (`bp(key, tick) -> int`) and money as `cents(key, tick) -> int`, because 02 §4.6
# forbids float money. Integer bp wins. Every `*.rate` row in 07 §7.2 becomes a `*_bp` key
# with range [0, 7500] etc., and the registry stores `py_type='bp'`. Reconcile the exact key
# strings with C11 and C14 (`policy.rate_bp`, `tax.*_bp`, `spend.*_cents`) — one string, one
# owner — and record the mapping in the handback.

Predicate = Literal["P-RANGE","P-MONEY","P-SOLVENCY","P-NONNEGATIVE",
                    "P-MONOTONE","P-SCOPE","P-SEPARATION"]

@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: str; proposer_id: str; parameter: str
    old_value: Any; proposed_value: Any; rationale: str
    cosigners: tuple[str, ...]; proposed_tick: int

@dataclass(frozen=True, slots=True)
class Admissibility:
    admissible: bool
    failed: Predicate | None
    detail: str = ""

class Overlay(Protocol):
    """The single object C18 writes and everyone else reads. It satisfies C11's read-side
    `RuntimeOverlay` Protocol (`bp`, `cents`, `flag`, `brackets`, `as_of`) AND the write API
    of `07 §7.1` / C01's `RuntimeConfig`. C11 ships `StaticOverlay` (M2 default) and
    `LayeredOverlay`; C18 supplies the enactments the layered one reads."""
    def bp(self, key: str, tick: int) -> int: ...
    def cents(self, key: str, tick: int) -> int: ...
    def flag(self, key: str, tick: int) -> bool: ...
    def brackets(self, key: str, tick: int) -> tuple[tuple[int, int], ...]: ...
    def as_of(self, tick: int) -> Mapping[str, Any]: ...
    def enact(self, parameter: str, value: Any, effective_tick: int,
              policy_id: str, event_seq: int, *, enacted_tick: int = 0) -> None:
        """Append-only. Never mutates history. Called ONLY from PolicyEngine.enact."""
    def history(self, parameter: str) -> tuple[Any, ...]: ...

class PolicyEngine:
    def __init__(self, *, runtime: Overlay, log: EventLog, clock: Clock,
                 offices: OfficeRegister, fiscal: "FiscalProjector",
                 repo: "PolicyRepository", cfg: PolitySettings) -> None: ...
    def propose(self, p: Proposal) -> Event: ...                         # 12025
    def admissible(self, p: Proposal, tick: int) -> Admissibility:
        """The seven predicates in the order P-SCOPE, P-RANGE, P-NONNEGATIVE, P-MONOTONE,
        P-SEPARATION, P-MONEY, P-SOLVENCY. First failure stops."""
    async def council_session(self, tick: int) -> Sequence[Event]:
        """PHASE 7 weekly. Admissibility (12033 on failure), then the vote (12027),
        then veto/override (12028), then enact or reject."""
    def enact(self, p: Proposal, margin: float, enacted_by: str, tick: int
              ) -> Sequence[Event]:
        """Emits 12030, inserts `policies`, closes the prior live row, THEN calls
        runtime.enact(param, value, effective_tick, policy_id, event_seq).
        effective_tick = tick + lag_ticks(spec.lag) and is ALWAYS > tick."""
    def effective_tick_for(self, parameter: str, enacted_tick: int) -> int: ...
    def set_budget(self, tick: int) -> Event: ...                        # 12034

class FiscalProjector:
    @mechanism("fiscal_scoring", entails="...")                          # 07 §7.5 verbatim
    def projected_balance(self, overlay: Mapping[str, Any], horizon_ticks: int,
                          tick: int) -> int:
        """Static scoring: proposed values applied to the CURRENT population and firm
        distribution, held fixed. Cents. No behavioural response, deliberately."""

class ExposureLedger:
    def record(self, candidacy_id: str, agent_ids: Sequence[str], channel: str,
               tick: int) -> None: ...
    def exposure(self, agent_id: str, candidacy_id: str, tick: int) -> float:
        """Decayed by exposure_halflife_sim_days (14). Feeds VoteModel.features['media']."""
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| **C11** | **`polis.config.runtime.RuntimeOverlay` / `LayeredOverlay`** (read side), `Enactment` | the overlay you are the sole writer of |
| **C11** | `Ledger.transfer/post_transaction`, `Leg(account_id, direction, amount_cents, reason)`, `account_id(code, owner, …)`, `money.allocate`, `money.bp` | deposits, ad buys, venue hire, salaries, D'Hondt seat allocation |
| C10 | `InstitutionResolver`, `FoundPartyParams`, `ValidatedAction`, `GateResult` | slot 8 |
| C05 | `LLMRouter.gather`, `Purpose.DELIBERATE` | election-day deliberate ballots (via C09's path, not directly) |
| C16 | `SocialGraph.neighbours/strength/trust`, `Platform.posts_in_window` | the `social` utility term (observable stances only) |
| C17 | `BeliefEngine.value/confidence`, `OutletRegistry.get/live` | `congruence`; `ads` targeting by `S(i, outlet)` |
| C04 | `Scheduler.register/fires`, `Cadence`, `stable`, `det_id`, `RngRegistry` | election/council/policy cadences |
| C02/C03 | `register_kind`, `Projection` | kinds and the `parties`/`elections`/`candidacies`/`votes`/`policies` projections |

`Ledger.post_transaction(legs, *, tick, cause: Event)` takes the **causing event**, not a seq,
and `reason` lives on each `Leg`. Build legs with `Ledger.transfer(src, dst, cents, reason)`
rather than constructing them inline.

> **Coordination item 1 — election-day budget.** `07 §6.5` force-routes eligible voters to
> DELIBERATE with `llm_election_multiplier: 6.0`, which is still far short of 1,000 voters at
> 90 calls/tick. C09 owns force-routing (`mandatory` obligations, `04 §7`). Agree with C09 how
> a mandatory obligation that exceeds the budget degrades: the answer must be "the top-salience
> subset deliberates, the rest vote reflex", and `turnout.deliberate` must be reported.
>
> **Coordination item 2 — three shapes of the overlay, pick one.** `07 §7.1` specifies
> `RuntimeConfig.get(parameter, tick)`; C01 ships that class in `polis/config/runtime.py`;
> C11 declares the read side as `RuntimeOverlay` with typed accessors `bp/cents/flag/brackets/
> as_of` plus `StaticOverlay` and `LayeredOverlay`, and lists `polis/config/runtime.py` in its
> own owner module list. **This must be reconciled before C18 writes a line.** The recommended
> resolution is the §5 `Overlay` Protocol: C11's typed read accessors (they respect `02 §4.6`)
> plus C01's `enact/history/dump/load`, one class, `LayeredOverlay`. Record what was agreed.
>
> **Coordination item 3 — the overlay is checkpointed.** C01 gives it `dump`/`load` and
> `name = "runtime_config"`. Confirm C04's `CheckpointManager` registers it, or a resume loses
> every enacted policy and the run silently reverts to the static YAML.
>
> **Coordination item 4 — the government ledger account.** C11 owns it (`gv_treasury`). You
> need its `account_id` for deposits, fines routing (C19), and salaries. Get it from
> `ledger.account_id(...)`, never by formatting a string.

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `parties` | **W** | `platform JSONB` over the closed `policy.*` vocabulary, ≤ `max_platform_planks` |
| `elections` | **W** | `turnout`, `winner_id`, `method` |
| `candidacies` | **W** | `spend_cents` accumulates; `votes` written at tally |
| `votes` | **W** | PK `(run_id, election_id, voter_id)` — one vote per voter, enforced by the PK not by a check |
| `policies` | **W** | append a row per enactment; set `repealed_tick` on the prior live row for that parameter |
| `agents` | R | age, alive, incarcerated, wealth, `criminal_record`, district |
| `beliefs` | R | via `BeliefEngine`, never by SQL |
| `relationships` | R | via `SocialGraph` |
| `ledger_*` | never directly | `post_transaction` only |

**`polis/config/runtime.py` is not a table.** It is an in-memory projection of `12030` events,
so `polis rebuild` reconstructs it exactly and every accessor is a pure function of
`(key, tick)`.

## 8. Event kinds owned

**Range: 12000–12999**, owner `polis.society.polity`.

| Kind | Name | Payload |
|---|---|---|
| 12001 | `PARTY_FOUNDED` | `party_id, founder_id, name, platform{}, founding_member_ids[], fee_cents, txn_id` |
| 12002 | `PARTY_JOINED` | `agent_id, party_id, alignment_score, prior_party_id` |
| 12003 | `PARTY_LEFT` | `agent_id, party_id, reason` |
| 12004 | `PARTY_PLATFORM_CHANGED` | `party_id, changes[{proposition, old, new}], driver` |
| 12005 | `PARTY_DISSOLVED` | `party_id, reason, final_membership, merged_into` |
| 12010 | `ELECTION_CALLED` | `election_id, office, seats, method, called_tick, voting_tick, campaign_ends_tick, electorate_size` |
| 12011 | `CANDIDACY_ANNOUNCED` | `candidacy_id, agent_id, election_id, party_id, platform{}, deposit_cents, txn_id` |
| 12012 | `CAMPAIGN_SPEND` | `candidacy_id, agent_id, amount_cents, channel, target_id, reached_agent_ids[], reach, txn_id` |
| 12020 | `VOTE_CAST` | `election_id, voter_id, candidacy_id\|ranking[]\|approvals[], origin, utility{congruence, self_interest, social, media, party_id, incumbency, epsilon}` |
| 12021 | `ABSTAINED` | `election_id, agent_id, reason, max_utility` |
| 12022 | `ELECTION_RESOLVED` | `election_id, method, tallies{}, winner_id(s), turnout, margin, rounds[], n_deliberate, n_reflex, fitted_omega{}, holdout_accuracy` |
| 12023 | `OFFICE_ASSUMED` | `office, agent_id, election_id\|appointment, term_start_tick, term_end_tick, salary_cents` |
| 12024 | `OFFICE_VACATED` | `office, agent_id, reason, successor_id` |
| 12025 | `POLICY_PROPOSED` | `proposal_id, proposer_id, parameter, old_value, proposed_value, rationale, cosigners[]` |
| 12026 | `POLICY_REJECTED` | `proposal_id, yeas, nays, abstentions` |
| 12027 | `POLICY_VOTED` | `proposal_id, chamber, votes[{agent_id, choice, origin}], yeas, nays, abstentions, passed, margin` |
| 12028 | `POLICY_VETOED` | `proposal_id, president_id, overridden, override_margin` |
| 12030 | `POLICY_ENACTED` | `policy_id, parameter, old_value, new_value, effective_tick, enacted_by, vote_margin, proposal_seq` |
| 12032 | `POLICY_REPEALED` | `policy_id, parameter, restored_value, repealed_policy_id` |
| 12033 | `POLICY_BLOCKED` | `proposal_id, predicate, detail` |
| 12034 | `BUDGET_SET` | `period_start_tick, revenue_projection_cents, outlay_projection_cents, allocations{police, courts, education, welfare, prisons, public_notices}, debt_cents` |
| 12040 | `APPOINTMENT_MADE` | `office, appointee_id, appointed_by, confirmed, confirm_margin` |
| 12041 | `OFFICER_REMOVED` | `office, agent_id, removed_by, margin, reason` |

`12030` is the **only** event `RuntimeConfig` projects from. Nothing else may mutate the overlay.

## 9. Implementation notes

### 9.1 The runtime overlay — the one rule that makes the loop closed

```python
runtime.enact(parameter, new_value, effective_tick, policy_id, event_seq)   # C18 only
# and everywhere else, in every institution, every tick:
vat_bp = runtime.bp("tax.vat_bp", tick)          # int basis points
benefit = runtime.cents("welfare.unemployment_benefit_cents", tick)
allowed = runtime.flag("regulation.finance.short_selling_allowed", tick)
```

Four normative rules from `07 §7.1`:

1. **Every institution reads policy-controllable parameters through the overlay at the current
   tick, never from the static config object, and never cached across ticks.** This is the whole
   loop. Ship a CI audit (`§11 F3`) that runs a 500-tick smoke run and asserts every
   `POLICY_REGISTRY` key was read through the overlay at least once. A parameter nobody reads
   cannot have an effect no matter how often it is enacted.
2. The overlay is a projection of `12030`; `polis rebuild` reconstructs it.
3. `effective_tick > enacted_tick`, always. Lag per parameter, from `PolicySpec.lag` converted
   through `SimDuration` — never a tick literal.
4. `society` writes it; `economy` and `world` read it. Both may import `config` under
   `02 §7.1`, so there is no illegal `society → economy` edge. Do not "simplify" this by
   calling into `polis.economy` from `polity.py`; `import-linter` will not catch it as a cycle
   but it violates the dependency rules.

### 9.2 The seven admissibility predicates

Evaluated in the order `P-SCOPE, P-RANGE, P-NONNEGATIVE, P-MONOTONE, P-SEPARATION, P-MONEY,
P-SOLVENCY`; first failure stops and emits `12033`. Cheap structural checks first so the
expensive fiscal projection runs only on well-formed proposals.

- `P-SCOPE` — the parameter is not under `run:`, `llm:`, `clock:`, `mechanisms:`, `ablations:`,
  `population:`, or `world:`. **Agents cannot legislate the simulation's own machinery.**
  In practice this is implied by `POLICY_REGISTRY` being closed, but implement it explicitly:
  a future registry edit that adds `clock.demographic_acceleration` must fail loudly.
- `P-SEPARATION` — `money.policy_rate` set by anyone other than `cb_governor` is blocked. The
  council's only monetary lever is removing the governor, which makes central bank independence
  a variable rather than an assumption.
- `P-MONEY` — no policy may credit an account without a matching debit. `INV-MONEY` restated
  at the legislative level.
- `P-SOLVENCY` — the static projection must stay above `−government.debt_ceiling_cents`.

A blocked proposal is still visible next tick, so agents learn what is constitutionally
impossible. Watch `policy.blocked_rate`: above 0.7 the usual cause is a debt ceiling set too
low, and the polity is being strangled by `P-SOLVENCY` rather than by politics.

### 9.3 Parties are founded, never seeded

There are **no parties at genesis**. `FOUND_PARTY` requires ≥ 3 founding members who each
submit `JOIN_PARTY` in the same or the next tick; if they do not, the party is dissolved at
the next dissolution sweep. Founding fee is a `transfer` to government. If `FOUND_PARTY`
attempts are zero across a run, B3 is answered trivially and the fee or the capability gate is
too strict (failure mode F8) — report the attempt count regardless of the success count.

Platform drift is a declared MECHANISM and it means **party polarisation is not independent
evidence of mass polarisation**. Ship the `party_platform_drift: fixed` ablation.

### 9.4 Campaigning buys reach, with a real transaction

| Channel | Ledger legs | Reach |
|---|---|---|
| `ads` | debit candidate cash, credit target outlet's **firm** cash, `purchase` | `min(outlet.reach, round(amount_cents / cpm_cents * 1000 * outlet_efficiency))` agents taken top-down by `S(i, outlet)` from `07 §4.7` |
| `rally` | debit candidate cash, credit venue owner cash, `rent` | all agents at the place this tick; emit a `BROADCAST` as the content |
| `canvass` | none | candidate and party members spend action slots on `DIRECT_MESSAGE`; reach = messages sent |

Exposure goes in the `12012` payload as `reached_agent_ids` (capped, sorted) — **not** one
event per exposure, or a competitive election emits 10⁵ events. `polity.campaign_cap_cents` is
itself a policy parameter, so the society can vote on how much money is allowed in its own
politics; read it through `runtime.get` in `check_resources`.

### 9.5 The vote model, and its honest limitation

Deliberate voters: as many as the boosted budget allows, ranked by salience, choosing via the
LLM. Reflex voters: conditional logit whose `ω` is **fitted to the deliberate voters in the
same election**, on the six utility terms with the deliberate choices as labels, with a 20%
holdout. Report `n_deliberate`, `n_reflex`, `ω`, log-likelihood and `holdout_accuracy` in
`12022`. **If holdout accuracy is not at least 0.5 above chance, the reflex vote is unusable
and the election must be re-run with a larger LLM share** — implement that as an explicit
branch, not as a warning. The first election of a run uses the config prior for `ω` and is
excluded from B-track analysis; mark it in the payload.

`social_i(c)` is derived **only from other agents' observable posts and speech**, never from
their actual votes. Reading `votes` to compute social influence makes turnout self-fulfilling
and is the most tempting shortcut in this chunk.

`self_interest_i(c)` is a deterministic first-order income delta computed by applying the
candidate's proposed parameter values to the voter's current income statement. Store the
components; "why did this agent vote this way" must be a query, not a study.

### 9.6 Election mechanics

`plurality` ties break on `rng.get("polity.vote", election_id, tick)` and the coin-flip is
logged. `irv` records every round in `12022.rounds`. `proportional` is D'Hondt over party vote
shares with seats to each party's candidates in vote order. Cadence in sim-time via
`Scheduler`; the election is *called* `campaign_length` (30 sim-days) before `voting_tick` and
candidacies close 7 sim-days before.

Offices are ordinary employment: salary paid by government through payroll (`wage`), which
makes office capture economically motivated and the government payroll a real fiscal line.
Succession on death/incarceration/emigration of the president: the council member with the
most votes at the last election, `12024` then `12023`, in that order.

### 9.7 Determinism specifics

Every collection processed in `stable()` order: parties by `party_id`, candidacies by
`candidacy_id`, voters by `agent_id`, proposals by `proposal_id`. The logit fit is a fixed
number of passes over a fixed ordering with `round6` — no shuffling, no adaptive optimiser, no
convergence tolerance. RNG namespaces: `polity.vote`, `polity.turnout`,
`polity.platform_drift`, and nothing else.

## 10. Configuration keys

```yaml
polity:
  election_method: plurality        # plurality | approval | irv | proportional
  offices:
    president: {seats: 1, term_sim_years: 4, term_limit: 2, salary_cents: 900000}
    council:   {seats: 7, term_sim_years: 2, method: proportional, salary_cents: 450000}
    judge:     {seats: 2, term_sim_years: 6, salary_cents: 700000, min_skill_law: 0.6}
    police_chief: {seats: 1, salary_cents: 600000}
    cb_governor:  {seats: 1, term_sim_years: 6, salary_cents: 800000}
  council_session: weekly
  policy_review: weekly
  court_session: twice_weekly       # consumed by C19
  campaign_length_sim_days: 30
  candidacy_close_sim_days: 7
  candidacy_deposit_cents: 250000
  deposit_refund_share: 0.05
  candidacy_record_bar: [fraud, embezzlement, perjury]
  party_founding_fee_cents: 100000
  max_platform_planks: 8
  initiative_signatures: 50
  exposure_halflife_sim_days: 14
  outlet_efficiency: 0.6
  abstain: {theta_0: 0.35, theta_conscientiousness: 0.15, theta_civic: 0.10}
  vote_model: fitted_from_deliberate      # MECHANISM
  vote_holdout_share: 0.20
  vote_min_holdout_lift: 0.5
  omega_prior: {congruence: 1.0, self_interest: 0.6, social: 0.4,
                media: 0.3, party_id: 0.8, incumbency: 0.2}
  llm_election_multiplier: 6.0
  can_regulate_feed: false          # gates society.feed_algorithm out of POLICY_REGISTRY

  policy:                           # INITIAL values of the POLICY_REGISTRY parameters
    tax: {income: {brackets: [[0, 0.0], [3000000, 0.20], [9000000, 0.35]]},
          corporate: {rate: 0.20}, capital_gains: {rate: 0.15},
          inheritance: {rate: 0.10}, vat: {rate: 0.10}}
    welfare: {unemployment_benefit_cents: 120000, benefit_duration_ticks: 8640,
              pension_cents: 150000, child_benefit_cents: 40000}
    police: {budget_cents: 5000000}
    courts: {budget_cents: 3000000, loser_pays: false}
    prison: {capacity: 40}
    sentencing: {multiplier: 1.0}
    labour: {minimum_wage_cents: 1200, max_hours_per_sim_week: 48}
    regulation: {finance: {margin_allowed: true, short_selling_allowed: true,
                           insider_trading_enforced: true},
                 labour: {at_will_dismissal: true}, media: {disclosure_required: false},
                 housing: {rent_cap_pct: null}}
    migration: {quota_per_sim_year: 60}
    government: {debt_ceiling_cents: 500000000}

mechanisms:
  party_platform_drift: member_mean   # member_mean | fixed
  fiscal_scoring: static
```

## 11. Acceptance criteria

- [ ] `PolityResolver` is registered in `InstitutionSlot.POLITY` and its `handles` is exactly the seven polity types; it resolves after every economic institution.
- [ ] **No party exists at tick 0.** A run with zero `FOUND_PARTY` actions has zero parties and reports the attempt count.
- [ ] `FOUND_PARTY` without 3 confirming `JOIN_PARTY`s within one tick leaves a party that is dissolved at the next sweep.
- [ ] `PROPOSE_POLICY` naming a parameter outside `registry_for(settings)` fails at the **capability** gate, not at admissibility.
- [ ] With `can_regulate_feed: false`, `society.feed_algorithm` is absent from `registry_for()` and a proposal naming it is rejected.
- [ ] The seven predicates run in the documented order and the first failure is the one reported in `12033.predicate`.
- [ ] `P-SEPARATION` blocks any `money.policy_rate` proposal from a non-`cb_governor`, including the president.
- [ ] `effective_tick > enacted_tick` for every enactment, and `lag` is derived from `SimDuration`, identically in `microscope` and `chronicle`.
- [ ] **After enactment, `runtime.get(param, effective_tick)` returns the new value and `runtime.get(param, effective_tick - 1)` returns the old one.**
- [ ] **CI audit: every parameter in `POLICY_REGISTRY` is read through `runtime.get` at least once during a 500-tick smoke run.** (F3's decisive detector.)
- [ ] `RuntimeConfig` survives a checkpoint/resume cycle with every enactment intact.
- [ ] `polis rebuild` reconstructs the overlay from `12030` alone and `snapshot(tick)` matches the live run at every tick.
- [ ] Every candidacy deposit, ad buy, venue hire and refund is a balanced `post_transaction`; `INV-MONEY` holds across a full contested election.
- [ ] `polity.campaign_cap_cents` is enforced through `runtime.get` in `check_resources`, so enacting a cap changes behaviour next tick.
- [ ] Exactly one `votes` row per (election, voter); a second `VOTE` is rejected at capability.
- [ ] `12020.utility` contains all six components plus `epsilon` for every ballot, deliberate and reflex.
- [ ] `social_i(c)` reads only posts and speech; an AST/import scan shows no read of the `votes` table from `VoteModel`.
- [ ] `fit` is deterministic and reports `holdout_accuracy`; an election with `holdout_accuracy < 0.5 + chance` is flagged `usable: false` and re-run.
- [ ] All four election methods produce a winner on a fixture ballot set; `irv` records every round.
- [ ] `mypy --strict polis/society/polity.py polis/society/policy.py`; no import of `polis.economy` or `polis.agents.cognition`.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/society/test_policy_registry.py` | 31 rows, closed; ranges and authorities match `07 §7.2`; `enabled_when` gating for the feed row |
| `tests/unit/society/test_admissibility.py` | Each of the seven predicates in isolation and in order; first failure reported; a blocked proposal never reaches a vote |
| `tests/invariants/test_policy_closes_the_loop.py` | **Merge gate.** Enact `tax.vat.rate`; assert `runtime.get` returns old at `effective_tick-1` and new at `effective_tick`, and that a goods purchase at `effective_tick` uses the new rate |
| `tests/invariants/test_runtime_read_audit.py` | **Merge gate.** 500-tick smoke run instrumenting `RuntimeConfig.get`; every `POLICY_REGISTRY` key was read at least once |
| `tests/unit/society/test_runtime_overlay.py` | Append-only; history never mutated; `effective_tick > enacted_tick`; rebuild from `12030` alone; checkpoint round-trip |
| `tests/unit/society/test_parties.py` | No parties at genesis; 3-member founding rule; dissolution at < 3 for 30 sim-days; leader selection ties by `agent_id`; drift and its `fixed` ablation |
| `tests/unit/society/test_candidacy.py` | Age, record bar, deposit posted and refunded at ≥ 5% of vote; independent candidacy legal |
| `tests/unit/society/test_campaign_reach.py` | Ads reach formula and cap; rally audience; canvass slot cost; `reached_agent_ids` sorted and capped; cap enforced through `runtime.get` |
| `tests/unit/society/test_vote_model.py` | Each utility component; abstention threshold; Gumbel from the right namespace; `social` reads posts not votes |
| `tests/unit/society/test_vote_fit.py` | Deterministic `ω` from one deliberate ballot set; holdout split reproducible; `usable: false` below the lift threshold |
| `tests/unit/society/test_election_methods.py` | Plurality tie coin-flip logged; approval; IRV rounds; D'Hondt seat allocation against a worked example |
| `tests/unit/society/test_offices.py` | Term end, succession on death/incarceration, appointment + confirmation, `cb_governor` removal at 5/7 |
| `tests/integration/test_election_ledger.py` | `INV-MONEY` across a contested election: deposits, refunds, three ad buys, one rally, salaries |
| `tests/integration/test_elections_change_policy.py` | 3 sim-years, two elections: `policy.enactments_per_sim_year >= 1` and `policy.parameter_drift > 0` between administrations |
| `tests/determinism/test_polity_determinism.py` | Same seed twice → identical 12000–12999 sequence including the fitted `ω` |
| `tests/integration/test_rebuild_polity.py` | `polis rebuild` diff-clean on `parties`, `elections`, `candidacies`, `votes`, `policies` |

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. `polis/society/polity.py` and `policy.py` export the §5 symbols with those exact signatures.
2. Kinds 12000–12999 registered with payload schemas; `12030`'s schema is the contract `RuntimeConfig` projects from.
3. `POLICY_REGISTRY` complete (31 rows), closed, and documented as extendable only by spec change.
4. The **F3 runtime-read CI audit** wired into the test suite, not a manual check.
5. `RuntimeConfig` registered with C04's `CheckpointManager`; resume verified.
6. `@mechanism("party_platform_drift", ...)`, `@mechanism("vote_model", ...)` and `@mechanism("fiscal_scoring", ...)` declared with `entails` matching `07 §6.1`, `§6.5` and `§7.5` verbatim, all ablatable.
7. A one-page note for C11/C12/C14/C19/C21 authors: **which parameters they must read through `runtime.get`, with the exact key strings and effect sites.** Without this, F3 is guaranteed.
8. Handback records: the three coordination items in §6; measured `policy.enactments_per_sim_year`, `policy.blocked_rate` and `policy.platform_delivery` from a 3-sim-year calibration run; and the first-election `ω` prior sensitivity.

## 14. Traps

1. **Elections that change nothing (F3).** The headline failure of this chunk, and its usual cause is not political — it is an institution that read the static config once at startup. `runtime.get(param, tick)` on every read, every tick, no caching, and the CI audit to prove it. If `policy.parameter_drift` across an administration is ≈ 0, look for a cached parameter before looking at the politics.
2. **Caching a policy parameter "for performance".** PHASE 7 runs once per tick and reads a handful of keys. `runtime.get` is a bisect over a short list. There is nothing to optimise and everything to lose.
3. **Making `effective_tick == enacted_tick`.** Policy applied retroactively within the tick makes the phase order meaningless and lets an institution that already resolved this tick disagree with one that has not.
4. **A tick literal for a lag.** `effective_tick = tick + 720` is a sim-month in `microscope` and two sim-years in `chronicle`. Convert through `SimDuration`, always.
5. **Seeding parties at genesis "so elections have candidates".** It answers B3 by fiat. An election with no candidates is a legitimate, reportable outcome; a seeded party system is not.
6. **Computing `social_i(c)` from other agents' votes.** Votes are secret and same-tick; reading them makes the model self-fulfilling and violates `02 §1.4` into the bargain. Observable posts and speech only.
7. **Hand-setting `ω` instead of fitting it.** The reflex electorate then encodes your theory of voting, and every turnout and coalition result is a restatement of the config. Fit it, report the holdout, and refuse to use an unusable fit.
8. **Ignoring `turnout.deliberate`.** A turnout difference across arms that is entirely a difference in how many voters got LLM cognition is a budget artefact (T8), not a political finding. Report it with every turnout number.
9. **One event per campaign exposure.** A contested election with 1,000 exposed agents × 30 sim-days of campaigning emits 10⁵+ events and blows the per-tick budget. Batch into `12012.reached_agent_ids`.
10. **Letting the council set `money.policy_rate`.** `P-SEPARATION` exists so that central bank independence is a *variable*: the council can remove the governor, which is a different and far more interesting lever. Losing the predicate collapses two institutions into one.
11. **Blocking too much.** `policy.blocked_rate > 0.7` usually means `government.debt_ceiling_cents` is too low and `P-SOLVENCY` is eating every proposal. That looks like political dysfunction and is actually a config bug.
12. **Dynamic fiscal scoring.** It embeds a macro theory into the legislature, and then "policy transmission" measures your Laffer curve rather than the society's. Static scoring is declared, deliberately mis-calibrated, and correct for this purpose.
13. **Allowing a `POLICY_REGISTRY` entry under `mechanisms:` or `clock:`.** Agents legislating `demographic_acceleration` or `fertility_hazard` makes every ablation meaningless. `P-SCOPE` must be a real check, not a comment.
14. **Two `votes` rows for one voter.** The PK prevents it in the database; make the capability gate prevent it in the engine, or the tally silently depends on insert order.
15. **Forgetting that C19 and C11 read your budget.** `12034 BUDGET_SET` drives police detection, court throughput, prison capacity and welfare transfers. If it never fires, C19's detection probability is whatever the static config says and the deterrence sweep measures nothing.
