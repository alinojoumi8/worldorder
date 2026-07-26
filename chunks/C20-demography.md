# C20 — Households, partnering, fertility, migration, death settlement, inheritance

**M5** · `polis/agents/demography.py` · **Depends on:** C02, C03, C04, C06, C07, C08, C10, C16, C17, C18, C19, C11/C13/C14/C15 (via ports) · **Blocks:** C23b, C24b, C25 · **Size:** L

## 1. Context

M5 is what turns a city into generations. This chunk builds courtship that agents drive
themselves, households that form and dissolve, a fertility hazard that is a declared
`MECHANISM` rather than a birth rate, migration in and out, child-rearing as a real
consumption flow, and — the load-bearing deliverable — **the full death-settlement transaction
of `04-AGENT-SPEC.md §12.3`, all eight steps, atomically, with `INV-MONEY` holding across it.**
That transaction is the single most common place accounting closure breaks in simulations of
this kind, and it gets its own integration test. Inheritance runs in two currencies: wealth,
through balanced ledger legs, and **belief priors**, through the `heritability_beliefs`
channel that research question B6 measures by sweeping it from 0 to 1.

`04 §12.1`, `§12.2` and `§12.3` are the specification. **Do not restate them here — implement
them.** C07 shipped `mark_dead` as an M1 stub that emits `2002` and settles nothing; C20
replaces it wholesale.

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/04-AGENT-SPEC.md` | **§12 in full (primary source): §12.1 birth, §12.2 stages, §12.3 the eight settlement steps.** Also §2.1 trait inheritance, §3 skills, §5 perception |
| `../docs/07-SOCIETY-SPEC.md` | **§9 in full (primary source)**, §0.3 ledger table, §0.6 RNG, §10.4 mobility metrics, §11 F10, §12 cadences (PHASE 8 order) |
| `../docs/02-ARCHITECTURE.md` | §3.2 kinds, §4 determinism, §5 PHASE 8, §5.2 `demographic_acceleration`, §7.1 **dependency rules (read this twice — see §6)**, §8.1 MECHANISM, §9 `INV-MONEY`, `INV-POP` |
| `../docs/03-DATA-MODEL.md` | §2.1 `agents`, §2.5 `households`, §2.6 `relationships`, §4 the ledger and its rules, §8, §12 rebuild |
| Chunks | **C07** (`AgentState`, `inherit_traits`, `mark_dead`, `initialise_population`, `advance_age`), **C10** (`InstitutionResolver`), C08 (`MemoryArchive`), C16 (`CommunicationResolver.compose`, `SocialGraph`), C17 (`BeliefEngine.priors_at_birth`), C18 (`RuntimeOverlay`), C19 (`Incarceration`) |

## 3. Scope — in

1. `RelationalResolver` — an `InstitutionResolver` for `COURT`, `PROPOSE_UNION`, `DISSOLVE_UNION`, `HAVE_CHILD_INTENT`, **composed into C16's slot-2 facade** (not registered directly).
2. Courtship: compatibility as *narrative*, mutual-courtship windows, union formation.
3. Households: formation, leaving home, dissolution with a 50/50 split of jointly-acquired wealth, dependant reassignment, state households.
4. The fertility hazard (`MECHANISM fertility_hazard`), conception, gestation, `PREGNANCY_ENDED`, and the hand-off to `04 §12.1`'s `AGENT_BORN`.
5. Child-rearing costs as real SKU consumption, arrears, and state care.
6. Migration in (cohorts, origin profile, no ties) and out (`MECHANISM emigration_hazard`), with the emigration settlement mirroring death.
7. **The death settlement**: the mortality hazard (`MECHANISM mortality_hazard`), the eight-step atomic transaction, the intestacy order, inheritance tax, escheat, and bereavement.
8. **Inheritance of belief priors** via C17's `priors_at_birth`, and the `15030` record.
9. The PHASE 8 institution that runs all of the above in the fixed order of `07 §9`.
10. Kinds 15000–15999, plus a small allocation inside the 2000 block (§8 — read the conflict note).

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `AgentState`, traits, needs, skills, `inherit_traits`, stages, ageing | **C07** |
| `2001 AGENT_BORN` and `2002 AGENT_DIED` kind *ownership* (you trigger and extend the payload) | **C07** |
| Memory archival mechanics | **C08** (`MemoryArchive.archive_agent`) |
| Belief prior *formula* and the `beliefs` table | **C17** (`BeliefEngine.priors_at_birth`) |
| Tie ending and bereavement *mechanics* | **C16** (`SocialGraph.end_all_for`) |
| Order cancellation, share liquidation, market prices | **C13** |
| Loan schedules, write-off accounting entries | **C14** |
| `post_transaction` itself | **C11/C14** |
| Housing allocation, `home`/`shelter` places | **C06** |
| `tax.inheritance.rate`, `welfare.child_benefit_cents`, `migration.quota_per_sim_year` values | **C18** (read via `runtime.get`) |

## 5. Interfaces you provide

```python
# polis/agents/demography.py
from polis.agents.actions import (Action, ActionType, GateResult, InstitutionSlot,
                                  ResolutionContext, ValidatedAction, ValidationContext)

class RelationalResolver:
    """InstitutionResolver (C10 §5) for the four 'social' actions. slot == COMMUNICATION,
    so it is COMPOSED into C16's CommunicationResolver and never registered directly.
    It records intents; the effects land in PHASE 8."""
    slot:    Final[InstitutionSlot] = InstitutionSlot.COMMUNICATION
    handles: Final[frozenset[ActionType]] = frozenset({
        ActionType.COURT, ActionType.PROPOSE_UNION,
        ActionType.DISSOLVE_UNION, ActionType.HAVE_CHILD_INTENT})

    def __init__(self, *, log: EventLog, clock: Clock, rng: RngRegistry, world: World,
                 households: "HouseholdRegistry", courtships: "CourtshipRegistry",
                 graph: "SocialGraphPort", cfg: DemographySettings) -> None: ...

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        """COURT / PROPOSE_UNION: both age >= 18, alive, neither holds a live `partner` tie.
        DISSOLVE_UNION: actor holds a live `partner` tie with the target (unilateral).
        HAVE_CHILD_INTENT: age in the fertile band, alive, not incarcerated."""
    def check_locality(self, action: Action, ctx: ValidationContext)  -> GateResult:
        """COURT: co-located this tick (from ctx.observation) OR an existing relationship of
        any type. The other three are remote_ok."""
    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult: ...
    def resolve(self, actions: Sequence[ValidatedAction], tick: int,
                ctx: ResolutionContext) -> Sequence[Event]: ...
    def options_for(self, action_type: ActionType,
                    ctx: ValidationContext) -> tuple[Mapping[str, Any], ...]: ...

class DemographyInstitution:
    """PHASE 8. Runs in the FIXED order of 07 §9: partnering -> household formation/
    dissolution -> conception -> gestation advance -> birth -> child costs ->
    migration in -> migration out -> mortality -> death settlement."""
    phase: Final[int] = 8
    def __init__(self, *, log: EventLog, clock: Clock, rng: RngRegistry, world: World,
                 agents: "AgentRegistry", households: "HouseholdRegistry",
                 courtships: "CourtshipRegistry", estate: "EstateSettler",
                 ledger: "LedgerPort", graph: "SocialGraphPort", beliefs: "BeliefPriorPort",
                 memories: "MemoryArchivePort", runtime: RuntimeOverlay,
                 cfg: DemographySettings) -> None: ...
    async def run(self, tick: int) -> Sequence[Event]: ...

@dataclass(frozen=True, slots=True)
class Household:
    household_id: str; formed_at_tick: int; dissolved_at_tick: int | None
    home_place_id: str; member_ids: tuple[str, ...]; head_agent_id: str | None
    tenure: Literal["own", "rent", "shelter"]; rent_cents: int

class HouseholdRegistry:
    def form(self, member_ids: Sequence[str], tick: int, *, reason: str
             ) -> tuple[Household, Sequence[Event]]: ...          # 15010
    def join(self, agent_id: str, household_id: str, reason: str, tick: int) -> Event: ...   # 15011
    def leave(self, agent_id: str, reason: str, tick: int) -> Event: ...                     # 15012
    def dissolve(self, household_id: str, reason: str, tick: int) -> Sequence[Event]: ...    # 15013
    def of(self, agent_id: str) -> Household | None: ...
    def head_of(self, household_id: str) -> str | None: ...
    def income_cents(self, household_id: str, tick: int) -> int: ...
    def spare_capacity(self, household_id: str) -> bool: ...
    def state_household(self, tick: int) -> Household:
        """The `shelter`-tenure household children and the destitute are placed into."""

class CourtshipRegistry:
    def court(self, initiator_id: str, target_id: str, tick: int) -> Sequence[Event]: ...    # 15001
    def mutual(self, a_id: str, b_id: str, tick: int) -> bool:
        """Both courted within courtship_window (60 sim-days)."""
    def compatibility(self, a: AgentState, b: AgentState) -> float:
        """07 §9.1. NOT a matching rule. Surfaced to perception as NARRATIVE only."""
    def compatibility_narrative(self, score: float) -> str:
        """'you have a lot in common with…' — never a number (04 §9.1)."""
    def propose_union(self, a_id: str, b_id: str, tick: int) -> Sequence[Event]: ...
    def confirm(self, a_id: str, b_id: str, tick: int) -> Sequence[Event]: ...               # 15003
    def expire(self, tick: int) -> Sequence[Event]: ...                                      # 15002

class Fertility:
    @mechanism("fertility_hazard", entails="...")            # 07 §9.3 verbatim
    def hazard(self, mother: AgentState, tick: int) -> float: ...
    def draw(self, mother: AgentState, tick: int) -> bool:
        """rng.get('demog.conception', mother_id, tick).random() < h * delta_sim_days"""
    def conceive(self, mother_id: str, father_id: str, tick: int) -> Sequence[Event]: ...    # 15020
    def advance(self, tick: int) -> Sequence[Event]:
        """Gestation. On due_tick: 15021, then the 04 §12.1 birth path (2001)."""

class ChildCosts:
    def charge(self, tick: int) -> Sequence[Event]:
        """Daily. Debit household head cash, credit supplying firms (`purchase`).
        Net of runtime.get('welfare.child_benefit_cents', tick). Emits 15022."""
    def arrears(self, household_id: str) -> int: ...
    def state_intervention(self, child_id: str, tick: int) -> Sequence[Event]: ...           # 15023

class Migration:
    def arrive(self, tick: int) -> Sequence[Event]:
        """Monthly, up to runtime.get('migration.quota_per_sim_year', t)/12. Traits from the
        population distribution shifted by origin_profile; ZERO social ties; home by
        affordability. Emits 15040."""
    @mechanism("emigration_hazard", entails="...")           # 07 §9.7 verbatim
    def emigration_hazard(self, a: AgentState, tick: int) -> float: ...
    def depart(self, agent_id: str, tick: int) -> Sequence[Event]:
        """Mirrors the death settlement (§9.4) but distributes nothing: the emigrant takes
        their residual with them. Emits 15041, then records
        died_at_tick = tick, death_cause = 'emigrated' (07 §9.7 schema encoding)."""

@dataclass(frozen=True, slots=True)
class Estate:
    decedent_id: str; escrow_account_id: str
    gross_cents: int; debts_cents: int; written_off_cents: int
    tax_cents: int; distributable_cents: int
    heirs: tuple[tuple[str, int], ...]          # (heir_id, cents), sorted by heir_id
    escheated_cents: int

class EstateSettler:
    @mechanism("mortality_hazard", entails="...")            # 04 §12.3 / gompertz_makeham
    def mortality_hazard(self, a: AgentState, tick: int) -> float: ...

    def settle(self, decedent_id: str, cause: str, tick: int) -> tuple[Estate, Sequence[Event]]:
        """THE eight steps of 04 §12.3, in ONE atomic unit. Steps 1–5 (orders, employment,
        positions, debts, tax, distribution) are DELEGATED to C15's
        `bankruptcy.settle_death` through `EstatePort`, which owns the 06 §10.7 cases A–D;
        C20 owns the trigger, the ordering, the heir list, and steps 6–8. INV-MONEY must
        hold across the whole unit to the cent. See §9.4."""
    def intestacy_shares(self, decedent_id: str, distributable_cents: int
                         ) -> tuple[tuple[str, int], ...]:
        """07 §9.6: partner 50%; children split the remainder equally; else parents; else
        siblings; else escheat. Uses C11's `money.allocate` (largest remainder, ties on
        ascending agent_id). Σ shares == distributable_cents, exactly. Passed INTO
        EstatePort.settle_death; C20 decides WHO inherits, C15 moves the money."""
    def bereave(self, decedent_id: str, tick: int) -> Sequence[Event]:
        """Health and `social` need hit plus elevated salience for strong ties, via C07."""
```

```python
# polis/agents/ports.py — protocols C20 defines and the composition root satisfies
class EstatePort(Protocol):
    """Satisfied by polis.economy.ventures.bankruptcy (C15). C15 owns 06 §10.7 cases A–D,
    the seven-step ordering inside the death tick, the simplified insolvency waterfall, the
    E7a/E7b/E8b leg patterns, and the estate tax. C20 supplies the heirs and the trigger."""
    def settle_death(self, agent_id: str, tick: int, *,
                     heirs: Sequence[tuple[str, int]] | None,
                     ctx: Any) -> Sequence[Event]: ...
    def case_for(self, agent_id: str, tick: int) -> Literal["A", "B", "C", "D"]:
        """A open bankruptcy case | B insolvent, no case | C solvent | D owns a firm."""

class LedgerReadPort(Protocol):
    """Satisfied by C11's `Ledger`. C20 READS balances and posts only the two flows it owns:
    the dissolution split and the child-cost purchase. Legs are built with
    `Ledger.transfer(src, dst, amount_cents, reason)` and posted with
    `Ledger.post_transaction(legs, tick=..., cause=<the NewEvent>)`."""
    def balance(self, account_id: str) -> int: ...
    def liquid(self, owner_id: str) -> int: ...
    def accounts_of(self, owner_id: str) -> tuple[str, ...]: ...
    def transfer(self, src: str, dst: str, amount_cents: int, reason: str) -> list[Any]: ...
    def post_transaction(self, legs: Sequence[Any], *, tick: int, cause: Any) -> UUID: ...
    def allocate(self, pool_cents: int,
                 weights: Sequence[tuple[str, int]]) -> dict[str, int]: ...

class HousingPort(Protocol):
    def vacate(self, agent_id: str, tick: int) -> None: ...
    def find_affordable_home(self, income_cents: int, tick: int) -> str | None: ...

class SocialGraphPort(Protocol):
    def end_all_for(self, agent_id: str, reason: str, tick: int) -> Sequence[Event]: ...
    def strong_ties(self, agent_id: str, threshold: float) -> tuple[str, ...]: ...
    def strength(self, a_id: str, b_id: str) -> float: ...

class BeliefPriorPort(Protocol):
    def priors_at_birth(self, child_id: str, mother_id: str, father_id: str
                        ) -> tuple[tuple[str, float, float], ...]: ...
    def priors_for_migrant(self, agent_id: str, offsets: Mapping[str, float]
                           ) -> tuple[tuple[str, float, float], ...]: ...

class MemoryArchivePort(Protocol):
    def archive_agent(self, agent_id: str, tick: int) -> int: ...
```

## 6. Interfaces you consume

> **Read this before writing a line of code.** `02 §7.1` allows
> `agents → kernel, events, world, llm, store, config` and **nothing else**. C20 lives in
> `polis/agents/demography.py`, so it **may not import `polis.economy` or `polis.society`** —
> not the ledger, not the exchange, not the social graph, not `BeliefEngine`. Every one of
> those arrives as a **Protocol defined in `polis/agents/ports.py` and satisfied by a concrete
> object injected at the composition root** (`polis.research` / `polis.cli`), which is exactly
> the pattern C08 already uses for `BeliefWriter`. If you find yourself typing
> `from polis.economy.ledger import post_transaction`, stop: `import-linter` will fail the
> build and the fix is a port, not an exemption.

| From | Symbol | Use |
|---|---|---|
| C07 | `AgentState`, `inherit_traits`, `advance_age`, `stage_for_age`, `population_mean_traits`, `derive_reflex_profile`, `mark_dead` (**which you replace**) | birth construction; ageing; the M1 stub you supersede |
| C08 | `MemoryArchive.archive_agent` via `MemoryArchivePort` | step 7 |
| C10 | `InstitutionResolver`, `ValidatedAction`, params models for the four social types | slot-2 sub-resolver |
| C16 | `CommunicationResolver.compose`, `SocialGraph` via `SocialGraphPort` | registration; tie ending and bereavement |
| C17 | `BeliefEngine.priors_at_birth` via `BeliefPriorPort` | B6's channel |
| C11 | `Ledger.transfer/post_transaction/balance/liquid/accounts_of`, `money.allocate`, `RuntimeOverlay` (`cents`, `bp`, `flag`) via ports | the two flows you own; policy values |
| C13/C14 | reached only through C15's `settle_death` — **you never call the exchange or the banks directly** | order cancellation, liquidation, `write_off_loan` |
| **C15** | **`bankruptcy.settle_death(agent_id, tick, ctx)` via `EstatePort`** | steps 1–5 of the settlement and the `06 §10.7` cases |
| C18 | `RuntimeOverlay` — `tax.inheritance*`, `welfare.child_benefit_cents`, `welfare.pension_cents`, `migration.quota_per_sim_year` | policy values, per tick |
| C19 | `Incarceration.is_incarcerated` via a small port | courtship/fertility/franchise gating |
| C04 | `RngRegistry`, `Clock` (`demographic_acceleration`), `Cadence`, `stable`, `det_id`, `@mechanism` | hazards, PHASE 8 cadences |

**Overlay accessors.** C11's `RuntimeOverlay` exposes `bp`, `cents`, `flag`, `brackets`,
`as_of` — there is no untyped `get`. Where this brief writes `runtime.get('x', t)`, use the
accessor matching the key's type.

> **Coordination item 1 — the settlement is split, and the split is the risk.** C15's brief
> already declares `bankruptcy.settle_death(agent_id, tick, ctx)` — "called by PHASE 8 (C20)" —
> and owns `06 §10.7` cases A–D plus the seven-step intra-tick ordering. C20 owns the mortality
> trigger, `intestacy_shares`, and steps 6–8. **Agree in writing who emits what** before either
> merges: C15 emits the 9xxx estate/waterfall kinds and the ledger legs, C20 emits 2006–2009,
> 2051, the household/tie/memory effects and the widened `2002`. Two owners and no agreement is
> exactly how a settlement half-runs.
>
> **Coordination item 2 — `2002 AGENT_DIED` payload.** C07 owns the kind and emits a minimal
> M1 payload. C20 widens it with `estate_value_cents`, `debts_cents`, `written_off_cents`,
> `tax_cents`, `heirs[]`, `escheated_cents`, `txn_ids[]`, `case (A|B|C|D)`. One kind, one
> widened schema — never a second kind.
>
> **Coordination item 3 — `mark_dead`.** C07's stub must be deleted or made to raise at M5.
> Two live death paths is exactly how a settlement gets skipped for one cause of death.
>
> **Coordination item 4 — case A keeps accounts open.** C11's `Ledger.close_account` asserts a
> zero balance and `06 §10.7 A` says it must **not** be called for a decedent with an open
> bankruptcy case. C20's step 8 must therefore ask `EstatePort.case_for` before closing
> anything.
>
> **Coordination item 5 — inheritance rate units.** `07 §7.2` writes `tax.inheritance.rate` as
> a float; C11's `RuntimeOverlay` exposes rates as **basis points** (`bp(key, tick) -> int`) and
> C15 calls it `tax.estate_bp`. Integer bp wins (`02 §4.6`). Settle the key name with C18 and
> C15 and use one string.

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `households` | **W** | the whole table; `tenure ∈ {own, rent, shelter}` (ratified addition) |
| `agents` | **W** | `household_id`, `mother_id`, `father_id`, `generation`, `died_at_tick`, `death_cause`, `home_place_id`. Trait/skill/need columns stay C07's |
| `relationships` | W via `SocialGraphPort` | `kin` and `partner` creation; `ended_tick` on death/emigration |
| `beliefs` | W via `BeliefPriorPort` | priors only |
| `ledger_accounts` | W via `LedgerPort` | one `escrow` account per estate, closed at zero |
| `ledger_entries` | W via `LedgerPort` | `escrow`, `write_off`, `tax`, `inheritance`, `purchase`, `transfer` |
| `places` | R/W via `HousingPort` | vacate, allocate |
| `memories` | W via `MemoryArchivePort` | `archived = TRUE` |
| `loans`, `orders`, `holdings`, `employments` | R/W via ports | the settlement |

Register `Projection`s for `households` and for the `agents` columns you own — coordinate with
C07, since `register_projection` asserts disjoint `tables`. Resolution: **one** agents
projection owned by C07, which handles C20's kinds for the columns above.

## 8. Event kinds owned

**Range: 15000–15999**, owner `polis.agents.demography`.

| Kind | Name | Payload |
|---|---|---|
| 15001 | `COURTSHIP_STARTED` | `a_id, b_id, initiator_id, compatibility, place_id` |
| 15002 | `COURTSHIP_ENDED` | `a_id, b_id, outcome, duration_ticks` |
| 15003 | `UNION_FORMED` | `partner_ids[], household_id, courtship_ticks` |
| 15004 | `UNION_DISSOLVED` | `partner_ids[], initiator_id, reason, split_txn_id, dependants[], custody{}` |
| 15010 | `HOUSEHOLD_FORMED` | `household_id, member_ids[], home_place_id, tenure, rent_cents, head_agent_id` |
| 15011 | `HOUSEHOLD_JOINED` | `agent_id, household_id, reason` |
| 15012 | `HOUSEHOLD_LEFT` | `agent_id, household_id, reason` |
| 15013 | `HOUSEHOLD_DISSOLVED` | `household_id, reason, members_reassigned[]` |
| 15020 | `CONCEPTION` | `mother_id, father_id, due_tick, hazard, draw` |
| 15021 | `PREGNANCY_ENDED` | `mother_id, outcome, child_id, gestation_ticks` |
| 15022 | `CHILD_COST_CHARGED` | `household_id, child_ids[], amount_cents, benefit_offset_cents, txn_id, arrears_cents` |
| 15023 | `STATE_CARE_STARTED` | `child_id, from_household_id, to_household_id, reason, cost_cents` |
| 15030 | `BELIEF_PRIORS_INHERITED` | `child_id, mother_id, father_id, heritability_beliefs, propositions[{proposition, value, confidence}]` |
| 15040 | `MIGRATION_IN` | `agent_id, cohort_id, origin_profile, arrival_wealth_cents, skills{}, belief_priors[], home_place_id` |
| 15041 | `MIGRATION_OUT` | `agent_id, hazard_components{}, exit_wealth_cents, ties_severed, debts_settled_cents, debts_defaulted_cents` |

### The 2000-block allocation — a conflict to resolve before registering

The assignment sheet gives C20 **2003–2059** inside `polis.agents`. That range is not free:
C07 has already registered `2001, 2002, 2003, 2004, 2010, 2011, 2020, 2030, 2040`, `2050` is
reserved by C21, and `2060–2079` belong to C10. **Register only in the genuinely free
sub-ranges and record the discrepancy in the handback** (`chunks/README §5` item 9 — do not
silently patch a spec conflict).

| Kind | Name | Payload |
|---|---|---|
| 2005 | `MORTALITY_HAZARD_DRAWN` | `agent_id, hazard, draw, components{age, health, wealth_pct, district_crime}` — **sampled** at `cognition_sample_rate`; the *death* is always in 2002 |
| 2006 | `ESTATE_OPENED` | `decedent_id, escrow_account_id, gross_cents, open_orders, open_loans, dependants[]` |
| 2007 | `ESTATE_DEBTS_SETTLED` | `decedent_id, paid_cents, written_off_cents, creditors[{creditor_id, loan_id, paid, written_off}], txn_ids[]` |
| 2008 | `ESTATE_DISTRIBUTED` | `decedent_id, tax_cents, distributable_cents, heirs[{heir_id, cents}], escheated_cents, txn_ids[]` |
| 2009 | `ESTATE_CLOSED` | `decedent_id, escrow_account_id, residual_cents (MUST be 0), steps_completed, total_txn_ids[]` |
| 2051 | `BEREAVEMENT_APPLIED` | `decedent_id, bereaved_ids[], health_delta, social_need_delta, salience_boost_ticks` |

2052–2059 reserved, unused. `2002 AGENT_DIED` remains C07's kind with a widened payload
(coordination item 1).

## 9. Implementation notes

### 9.1 PHASE 8 ordering is fixed

`partnering → household formation/dissolution → conception → gestation advance → birth →
child costs → migration in → migration out → mortality → death settlement`
(`07 §9`, with mortality and settlement last so a death this tick does not have to be undone
by a birth or a household change later in the same phase). Every sub-step iterates in
`stable()` order by the relevant id. Hazards are drawn per entity with tick-scoped streams so
that adding or removing a subsystem does not shift anyone else's draws.

### 9.2 Courtship is agent-driven

`compatibility` is a **feature, not a matching rule**. It is rendered as narrative into the
courting agent's perception and conditions the reflex `SAY` template choice; the decision to
court and the decision to accept are LLM actions. Never auto-pair on a compatibility
threshold — the moment you do, partnering becomes an algorithm and every assortative-mating
result is your code.

Courtship is LLM-only, so it competes for the same budget as everything else. Give
courtship-eligible agents a `scheduled` salience term of 0.3 when a mutual courtship is live —
that force-boosts without pinning them. In tight-budget runs partnering slows and the birth
rate falls; that is failure mode F10 and it must be reported alongside any demographic result
(threat T8).

### 9.3 Fertility is a hazard, not a decision

`h_fert` exactly as `07 §9.3`, with all eight multipliers, `base(age)` scaled by
`demographic_acceleration`, and the draw from `rng.get("demog.conception", mother_id, tick)`.
`HAVE_CHILD_INTENT` **multiplies** the hazard (`ι = 2.0` within 90 sim-days); it does not
create a child. The declared `entails` is unusually consequential: κ_inc and κ_policy make
"redistribution raises fertility" and "wealth correlates with family size" *entailed*, not
findings. Ship the `fertility_hazard: uniform` ablation and make it work.

Child costs are real consumption: debit the household head, credit the supplying firms,
`reason='purchase'`, net of `runtime.get("welfare.child_benefit_cents", tick)`. Arrears beyond
`arrears_tolerance_days` (30) degrade child health; below `child_welfare_threshold` the child
moves to the state household (`tenure='shelter'`) and government pays. That is a real fiscal
cost of child poverty and it is what gives `welfare.child_benefit_cents` a budget consequence.

### 9.4 The death settlement — the eight steps, atomically

`04 §12.3` gives eight steps; `06 §10.7` gives the money ordering and cases A–D. **C20
orchestrates; C15 moves the money.** The whole thing is one unit of work: on any exception,
roll back everything, emit no partial events, and HALT (`02 §10` — never swallow).

```python
def settle(decedent_id, cause, tick):
    # 0. FREEZE. died_at_tick set; the agent submits no further actions, this tick or ever.
    case  = estate.case_for(decedent_id, tick)          # A | B | C | D  (06 §10.7)
    heirs = self.intestacy_shares(decedent_id, ...)     # WHO inherits — C20's decision
    emit(2006 ESTATE_OPENED, case=case, dependants=..., open_orders=..., open_loans=...)

    # 1-5. DELEGATED to C15. It performs, in this fixed order:
    #        1 cancel resting orders, release escrow/reserved shares      (C13)
    #        2 terminate employment, pay accrued wages if the employer can (C11)
    #        3 determine case A/B/C/D
    #        4 run that settlement as ONE atomic transaction: debts in class order,
    #          shortfall via C14's write_off_loan (E8b — no cash moves, lender capital falls)
    #        5 estate tax (bp), then distribute to `heirs` by largest remainder,
    #          escheat to gv_treasury when there are none
    events += estate.settle_death(decedent_id, tick, heirs=heirs, ctx=ctx)
    emit(2007 ESTATE_DEBTS_SETTLED); emit(2008 ESTATE_DISTRIBUTED)

    # 6-8. C20's own work.
    housing.vacate(decedent_id, tick)
    households.dissolve_or_restructure(...)      # dependants reassigned; 15012/15013
    memories.archive_agent(decedent_id, tick)    # C08
    events += graph.end_all_for(decedent_id, "death", tick)     # 10042
    events += self.bereave(decedent_id, tick)                   # 2051
    #   Case A ONLY: do NOT close the decedent's ledger accounts (06 §10.7 A).
    emit(2009 ESTATE_CLOSED, residual_cents=0)
    emit(2002 AGENT_DIED, ...)                   # widened payload; obituary-eligible
```

Six properties that must hold and that naive implementations break:

1. **Orders before liquidation.** Reserved shares released after liquidation are counted twice; `INV-SHARES` catches it, but only after the estate has already distributed. C15 owns the order; C20 must not re-order it.
2. **Case A defers.** An open bankruptcy case means steps 4–5 do not run, heirs get only the class-5 residual at discharge, and **the accounts stay open**. Calling `close_account` here asserts a zero balance and halts the run.
3. **Escrow closes at exactly zero.** Assert `2009.residual_cents == 0`. A non-zero residual is money the run has lost track of, and it surfaces a thousand ticks later as an `INV-MONEY` halt with no cause.
4. **Integer cents throughout.** Estate tax is basis points: `bp(cents, rate_bp)`, never `cents * 0.10`. `02 §4.6`.
5. **Shares sum exactly.** `Σ intestacy_shares == distributable_cents`, via C11's `money.allocate` (largest remainder, ties on ascending `agent_id`). Do not write your own splitter.
6. **A write-off moves no cash.** It is a loss on the lender's balance sheet — precisely how death propagates into the credit system — and it is C14's `write_off_loan`, never a cash payment from nowhere.

Emigration reuses the same path with no distribution: the emigrant takes their residual. Then
`died_at_tick = tick, death_cause = 'emigrated'` (`07 §9.7`), and **every mortality metric
filters `death_cause <> 'emigrated'`** — a normative rule, not a convention.

### 9.5 Inheritance of belief priors — B6's channel

At birth, `BeliefPriorPort.priors_at_birth(child, mother, father)` returns policy stances and
`trust.generalised` only, blended at `η_b = heritability_beliefs` (default 0.4) against the
population mean with `N(0, σ_belief)` noise from `rng.get("beliefs.noise", child_id)` — an
entity-scoped, one-shot stream. Confidence is the parental mean × `confidence_dilution` (0.5).
Emit `15030`, and let C17 emit `10063`. **No `fact.*` proposition is ever inherited**: a newborn
with a view on whether `fm_acme` is solvent corrupts every misinformation measurement.

The result is that wealth and worldview descend through two separable channels, and sweeping
`heritability_beliefs` from 0 to 1 against `mobility.rank_rank` for the same cohort is exactly
what B6 asks. Make the sweep cheap: nothing else may depend on `η_b`.

### 9.6 Households and dissolution

Formation on `15003 UNION_FORMED` if either partner is not already a head; home chosen by
`min(rent) s.t. rent ≤ housing_burden × combined income`. Leaving home at
`leave_home_age` (18) with income ≥ `independence_threshold`.

`DISSOLVE_UNION` is unilateral. **Jointly-acquired wealth** — value accumulated since
`formed_tick` — splits 50/50 through balanced legs; separately-held prior wealth is untouched.
That requires tracking each partner's balance at `formed_tick`; store it on the household row
or recompute from the ledger, but do it deterministically. Dependants follow the higher-income
parent by default (`MECHANISM custody_default`), ablatable to `coin_flip`.

### 9.7 Migration

**In**: monthly cohorts up to `runtime.get("migration.quota_per_sim_year", t)/12`, traits from
the population distribution shifted by `origin_profile`, belief priors via
`priors_for_migrant`, **zero social ties**, home by affordability. Because arrivals have no
ties, assimilation is directly observable — time to first tie, first job, degree trajectory,
belief convergence — **without any assimilation mechanism being coded**. Do not add one.

**Out**: the `emigration_hazard` MECHANISM, whose `entails` warns that selective out-migration
of the poor and weakly-tied mechanically improves the resident wealth distribution and raises
mean tie density. Any A2 or network-density result must be re-run at `base_emig = 0`.

## 10. Configuration keys

```yaml
demography:
  courtship_window_sim_days: 60
  courtship_salience_boost: 0.3
  leave_home_age: 18
  independence_threshold_cents: 180000
  housing_burden: 0.35
  compatibility_weights: {age: 0.20, traits: 0.25, beliefs: 0.20, tie: 0.20, econ: 0.15}
  age_norm_years: 20
  fertility:
    peak_age: 28
    band: [16, 45]
    kappa_income: {a: 0.6, b: 0.8}
    kappa_parity: [1.0, 0.85, 0.6, 0.35, 0.15, 0.05]
    phi_single: 0.15
    iota_intent: 2.0
    intent_window_sim_days: 90
    psi_child_benefit: 0.4
    kappa_housing_penalty: 0.4
    gestation_sim_days: 270
    loss_base: 0.03
  child:
    base_cost_cents_per_sim_day: 3500
    age_multiplier: {infant: 1.0, child: 1.2, adolescent: 1.6}
    arrears_tolerance_sim_days: 30
    welfare_threshold_health: 0.35
  migration:
    cadence: monthly
    origin_profile: {skill_premium: 0.0, wealth_offset_cents: 0, belief_offsets: {}}
    base_emig_per_sim_day: 0.00015
  estate:
    liquidate_on_intestacy: true
    creditor_priority: [secured, tax, unsecured]
  bereavement:
    strong_tie_threshold: 0.45
    health_delta: -0.04
    social_need_delta: -0.25
    salience_boost_ticks: 72

mechanisms:
  fertility_hazard: income_conditional     # income_conditional | uniform
  mortality_hazard: gompertz_makeham
  emigration_hazard: precarity_conditional
  custody_default: higher_income           # higher_income | coin_flip

beliefs:
  heritability_beliefs: 0.40               # B6 sweep knob, 0..1
```

Estate tax, `welfare.child_benefit_cents`, `welfare.pension_cents` and
`migration.quota_per_sim_year` are **not here** — they are C18 policy parameters read per tick
through C11's `RuntimeOverlay` (`bp` / `cents` / `flag`).

## 11. Acceptance criteria

- [ ] **`INV-MONEY` holds to the cent across a death settlement, including a death with open exchange orders, an outstanding loan, and no heirs.**
- [ ] The estate escrow account closes with `balance_cents == 0` on every settlement; `2009.residual_cents` is always 0.
- [ ] `INV-LEDGER`, `INV-SHARES` and `INV-ORDERS` all hold immediately after every settlement.
- [ ] The eight steps are atomic: an injected failure inside `EstatePort.settle_death` leaves no `ledger_entries`, no `2006`–`2009` events, and no mutated `agents` row — and halts the run.
- [ ] Steps 1–5 are delegated to C15's `settle_death`; C20 contains no order-cancellation, liquidation, debt-priority or write-off code of its own.
- [ ] The four `06 §10.7` cases are exercised: **case A leaves the decedent's ledger accounts open and calls no `close_account`.**
- [ ] A creditor write-off moves no cash: money supply before == after, and the lender's capital falls.
- [ ] `Σ intestacy_shares == distributable_cents` exactly, for 10,000 randomised estates including odd-cent residuals, using C11's `money.allocate`.
- [ ] Escheat fires when there is no partner, no child, no parent and no sibling, and `gv_treasury` receives the full residual.
- [ ] Estate tax is applied **before** distribution, in basis points via `RuntimeOverlay.bp`, never float arithmetic.
- [ ] `04 §12` is implemented, not duplicated: `04 §12.1`'s birth path emits `2001` once, and C07's `mark_dead` is unreachable at M5.
- [ ] Belief priors at birth cover policy stances and `trust.generalised` only; **no `fact.*` proposition is ever inherited**.
- [ ] `heritability_beliefs = 0.0` reproduces the population mean; `1.0` reproduces the parental blend up to `σ_belief`.
- [ ] `compatibility` never auto-pairs: no union forms without two agent actions.
- [ ] `HAVE_CHILD_INTENT` multiplies the hazard and never creates a child.
- [ ] `fertility_hazard: uniform` sets κ_inc = κ_policy = 1 and runs.
- [ ] Child costs are balanced `purchase` legs to real firms, net of the child benefit; arrears and state care both fire.
- [ ] Emigration mirrors the settlement, records `death_cause = 'emigrated'`, and every mortality metric filters it out.
- [ ] Migrants arrive with zero ties and no assimilation mechanism exists in the codebase.
- [ ] PHASE 8 sub-steps run in the `07 §9` order and every iteration is `stable()`-ordered.
- [ ] `polis rebuild` reproduces `households` and the C20-owned `agents` columns exactly.
- [ ] `import-linter` passes: `polis/agents/demography.py` imports nothing from `polis.economy` or `polis.society`; every cross-boundary call goes through a port.
- [ ] Kinds registered only in 15000–15999, 2005–2009 and 2051, with the 2000-block discrepancy recorded in the handback.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/invariants/test_death_settlement.py` | **The merge gate for this chunk.** `INV-MONEY` to the cent across: (a) a plain death; (b) a death with two resting buy orders and one resting sell; (c) a death with an outstanding loan larger than the estate; (d) a death with no heirs; (e) all three at once. Also `INV-LEDGER`, `INV-SHARES`, `INV-ORDERS` after each |
| `tests/unit/agents/test_estate_atomicity.py` | Injected failure at each of steps 1–8 leaves zero events, zero ledger entries, zero mutated rows, and halts |
| `tests/unit/agents/test_death_cases.py` | The four `06 §10.7` cases end to end; case A defers distribution and leaves accounts open; case D transfers firm shares or escheats them |
| `tests/unit/agents/test_intestacy.py` | Partner 50%; children equal split; parents; siblings; escheat; odd-cent remainder deterministic; shares sum exactly over 10,000 randomised estates via `money.allocate` |
| `tests/unit/agents/test_settlement_delegation.py` | AST scan: C20 contains no order-cancel, liquidation, loan-priority or write-off logic; `EstatePort.settle_death` is called exactly once per death |
| `tests/unit/agents/test_belief_priors.py` | η=0 → population mean; η=1 → parental blend; no `fact.*`; determinism from `rng.get('beliefs.noise', child_id)`; `15030` payload |
| `tests/unit/agents/test_fertility_hazard.py` | All eight multipliers; band edges; `demographic_acceleration` scaling; `HAVE_CHILD_INTENT` multiplies only; `uniform` ablation |
| `tests/unit/agents/test_courtship.py` | No union without two actions; compatibility surfaced as narrative with no numeral; window expiry → `15002` |
| `tests/unit/agents/test_households.py` | Formation, leaving home, unilateral dissolution, 50/50 split of jointly-acquired wealth only, custody default and its ablation |
| `tests/unit/agents/test_child_costs.py` | Balanced legs to real firms; benefit offset via `runtime.get`; arrears accumulation; state care transition to a `shelter` household |
| `tests/unit/agents/test_migration.py` | Quota respected via `runtime.get`; arrivals have zero ties; emigration settlement mirrors death; `death_cause='emigrated'` filtered from mortality metrics |
| `tests/invariants/test_no_economy_import.py` | AST/import scan of `polis/agents/demography.py` and `ports.py`: no `polis.economy`, no `polis.society` |
| `tests/integration/test_three_generations.py` | 3 sim-years at `demographic_acceleration: 8`, 300 agents: ≥ 2 generations, `INV-POP` in range, `INV-MONEY` every tick, `mobility.rank_rank` computable |
| `tests/integration/test_inheritance_channels.py` | Two arms at `heritability_beliefs` 0.0 and 1.0, same seed: `mobility.belief_ige` differs; `mobility.rank_rank` does not |
| `tests/determinism/test_demography_determinism.py` | Same seed twice → identical 15000–15999 and 2005–2051 sequences, identical settlements |
| `tests/integration/test_rebuild_demography.py` | `polis rebuild` diff-clean on `households` and the C20-owned `agents` columns |

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. `polis/agents/demography.py` and `polis/agents/ports.py` export the §5 symbols with those exact signatures; every cross-boundary dependency is a Protocol satisfied at the composition root.
2. Kinds 15000–15999, 2005–2009 and 2051 registered with payload schemas; the `2002` payload widened once, in agreement with C07.
3. C07's `mark_dead` deleted or made to raise, with a test asserting there is exactly one death path at M5.
4. Four `@mechanism` declarations (`fertility_hazard`, `mortality_hazard`, `emigration_hazard`, `custody_default`) with `entails` matching `07 §9` and `04 §12.3` verbatim, all ablatable.
5. `tests/invariants/test_death_settlement.py` passing, and named in the handback as the gate it is.
6. A signed-off split with C15: which chunk emits which kind and which ledger leg across `06 §10.7` cases A–D, plus the exact `EstatePort` signature both sides build against.
7. Handback records: the five coordination items in §6; **the 2000-block kind-range conflict and the sub-ranges actually used**; the estate-tax key name and units agreed with C15/C18; the measured birth rate, emigration rate and deliberate-share correlation from a 3-sim-year calibration run against F10's thresholds; and the `heritability_beliefs` sweep cost for B6.

## 14. Traps

1. **Estate settlement leaking money.** The defining failure of this chunk. It leaks in five specific places: reserved order funds released twice, a cent lost to float arithmetic on the estate tax, an intestacy remainder dropped, an escrow account closed with a non-zero balance, and a write-off implemented as a cash payment. `INV-MONEY` will halt the run — a thousand ticks later, with no traceable cause. Assert residual-is-zero inside `settle`, not only in a test.
2. **A partial settlement.** An exception mid-`settle_death` that leaves employment terminated, orders cancelled, and cash swept into an escrow account nobody will ever close. All eight steps are one unit of work: commit everything or nothing, then halt (`02 §10`). Never `try/except: log.warning`.
2b. **Reimplementing steps 1–5 because C15 "isn't ready".** Two waterfalls, two write-off paths, two orderings, and a settlement that closes under one and leaks under the other. Build against `EstatePort` with a stub and wait.
3. **Importing `polis.economy` from `polis/agents/`.** The most likely way this chunk fails CI on day one. `02 §7.1` does not allow it. Ports, injected at the composition root — the same pattern C08 already established.
4. **Duplicating `04 §12`.** Rewriting the birth or death spec here creates two sources of truth that drift. Implement §12.1/§12.3; do not restate them.
5. **Two live death paths.** C07's M1 `mark_dead` stub still wired for one cause of death (starvation, say) means that death skips the settlement entirely and the money never comes back. Delete it.
6. **Float money.** `residual * 0.10` then `int()`. `02 §4.6` forbids it, C11 exposes rates as integer basis points, and this is the exact place it bites, because estates are large and deaths are frequent.
7. **Estate tax after distribution.** Then heirs are taxed individually, the arithmetic no longer closes against the estate, and the rate stops meaning what `07 §9.6` says it means.
7b. **Closing a case-A decedent's accounts.** `Ledger.close_account` asserts a zero balance; a decedent inside an open bankruptcy has one and it is not zero. `06 §10.7 A` says do not call it. This halts the run on the first coincidence of death and bankruptcy — which, in a city with real firms, will happen.
8. **Auto-pairing on compatibility.** The fastest way to make assortative mating a property of your code. Compatibility is narrative; the decisions are actions.
9. **Making `HAVE_CHILD_INTENT` create a child.** Reproduction becomes a deterministic consequence of one decision, the hazard becomes decorative, and the fertility MECHANISM's `entails` becomes false.
10. **Budget-induced demographic collapse (F10) reported as a demographic finding.** Courtship, partnering and voting are LLM-only, so a tight budget suppresses family formation and the population falls. Correlate births per sim-year with the deliberate-call share across every sweep cell; a strong positive correlation is a budget artefact (T8).
11. **Inheriting `fact.*` propositions.** A newborn with an opinion about a specific firm gives misinformation adoption a birth channel and corrupts B2 as well as B6.
12. **Coding an assimilation mechanism for migrants.** Zero ties on arrival is the whole design: assimilation must be *observed*, not implemented. A "migrants gradually gain ties" rule deletes the measurement.
13. **Forgetting the `death_cause = 'emigrated'` filter.** Every mortality metric silently doubles, the wealth–mortality gradient inverts (the poor "die" more because they leave), and it looks like a finding.
14. **Splitting all wealth 50/50 on dissolution.** Only *jointly-acquired* wealth splits. Splitting prior wealth makes union formation a wealth-transfer strategy and agents will find it.
15. **A per-sim-day cost constant applied per tick.** `chronicle` has one tick per sim-day, `microscope` has 24. Child costs charged per tick at a per-day rate bankrupt every household in one profile.
16. **Registering kinds in 2003–2059 as assigned.** Seven of those are already C07's and one is C21's. Register in 2005–2009 and 2051–2059, and say so in the handback.
