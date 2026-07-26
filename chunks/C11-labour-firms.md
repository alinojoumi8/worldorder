# C11 — Ledger, labour market, firms and production

**M2** · `polis/economy/{ledger,money,labour,firms,policy}.py`, `polis/config/runtime.py` · **Depends on:** C02 C03 C04 C06 C07 C10 C21 · **Blocks:** C12 C13 C14 C15 C24b — *every* economy chunk · **Size:** L

## 1. Context

This chunk delivers the double-entry ledger, and everything else in it is secondary. `06 §1` states
the rule the milestone rests on: no economic feature may exist that is not expressible as balanced
ledger legs, and `INV-MONEY` (validity gate **V2**) is the primary correctness gate for M2. On top
of the ledger it builds the labour market — vacancies, search, screening, offers, bargaining,
hiring, separation, payroll, the formal state-based unemployment definition — and firms:
production, capital, productivity, inventory, pricing, entry, exit. It also ships
`MechanicalPolicy`, the classical-ABM decision set that makes `--reflex-only` a usable null model
rather than a frozen economy (threat **T9**).

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/06-ECONOMY-SPEC.md` | **§1 the ledger (primary, read twice)**, §2 arithmetic and RNG, §3 labour, §4 firms, §11.1–11.3, §13, §14 F1–F3 F11 F14 F15, §15, §16 |
| `../docs/03-DATA-MODEL.md` | §0, **§4 ledger**, §5 firms/vacancies/applications/employments/inventory, §2.1–2.2 |
| `../docs/02-ARCHITECTURE.md` | §3.2 kinds, §4 determinism, §5 phases, §5.1 slots, §7.1 imports, §8.1 MECHANISM, §9 invariants, §10 errors |
| `chunks/C10-actions.md` | **§5 `InstitutionResolver` (frozen)**, §9.2 gates, §9.6 dispatch |
| Chunks | C02 `NewEvent`/`EventLog.stage`; C03 `LedgerRepository`; C04 `Clock` `Scheduler` `RngRegistry` `stable` `mint` `@mechanism` `WorldStateView`; C07 `AgentState.skill_bp` `Observation`; C21 curricula |

## 3. Scope — in

1. **`polis/economy/ledger.py` — build this first, alone, and prove it before writing a second
   module.** Chart of accounts (`06 §1.3`), `post_transaction(legs)` with the sum-to-zero
   assertion and P1–P7, `transfer()`, `issue_base_money()`, account open/close lifecycle,
   incremental balance maintenance, the tick buffer and PHASE 6 flush, and the `INV-MONEY`
   (M-1…M-6) / `INV-LEDGER` implementations.
   **Nothing else in M2 can be built until this is correct.** C12–C15 and C24b all post through it;
   accounting closure is **V2**, and V2 is the M2 exit gate (`06 §13.5`, `README §2`). A labour
   market on a leaky ledger is not partial progress, it is unpublishable data.
2. `polis/config/runtime.py` — the **read side** of the tick-keyed parameter overlay, plus a
   static-config default so M2 runs before the policy engine (C18) exists.
3. `money.py` — `bp`, `bp_ceil`, `allocate` (largest remainder), `mint`, the fixed `Decimal`
   context, `round_to_tick`.
4. **Labour**: vacancy posting and TTL, visibility slice, application, daily screening and
   `match_score_bp` over the 14-skill vector, shortlisting, offers, wage bargaining,
   accept/decline/expire, hiring, firing, redundancy under payroll shortfall, quitting,
   retirement, wage accrual, **payroll in PHASE 7 step 6** with withholding and employer payroll
   tax, skill decay and scarring, the `E/U/NILF` definitions of `06 §3.10`, benefit hooks.
5. **Firms**: Cobb–Douglas production with the micro-unit carry, capital and depreciation,
   productivity drift, inventory and weighted-average unit cost, spoilage, markup pricing, entry
   and exit, dividends, quarterly period close.
6. `MechanicalPolicy` (`06 §4.11`); `LabourResolver` (slot 3) and `FirmsResolver` (slot 7 — an
   **M2 stand-in** that C15 replaces at M3).
7. `INV-MONEY`, `INV-LEDGER`, `INV-EMPLOY`, `INV-LABSHARE`, `INV-PRODUCTION` registration and the
   `LedgerStateView` adapter onto C04's `WorldStateView`.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `BUY_GOOD`, the slot-4 resolver, SKUs, CPI, consumption, rent payment | **C12** — it registers slot 4 and calls your `apply_set_price/produce/restock` |
| Order book, `holdings`, IPO, commissions | **C13** |
| Loans, bank accounts, tax **collection**, transfers, treasury, deposit interest | **C14** — you compute withholding and hand it the legs |
| Startups, VC, M&A, bankruptcy, the full slot-7 resolver | **C15** |
| The **writer** side of `runtime.py`, policy enactment | **C18** |
| Rent levels, `places`, districts, housing match; curricula and enrolment (you read only "is this skill taught") | **C06**, **C21** |
| Salience, prompts, routing, choosing any action | **C09**, **C07** |

## 5. Interfaces you provide

```python
# polis/economy/money.py
MONEY_CTX: Final[Context] = Context(prec=28, rounding=ROUND_HALF_EVEN)     # 06 §2.1
def bp(amount_cents: int, rate_bp: int) -> int                             # floor
def bp_ceil(amount_cents: int, rate_bp: int) -> int
def allocate(pool_cents: int, weights: Sequence[tuple[str, int]]) -> dict[str, int]:
    """Largest remainder. Σ result == pool_cents exactly (06 §2.3). The ONLY splitter."""
def mint(prefix: str, tick: int, ordinal: int) -> str                      # "<p>_<tick:08d>_<o:04d>"
def round_to_tick(cents: int, tick_size_cents: int) -> int

# polis/economy/ledger.py — the only module permitted to touch ledger_accounts/ledger_entries
AccountCode = Literal["cash","dep","esc","res","lnr","txr","dpl","lnp","iss"]
POLARITY: Final[Mapping[AccountCode, Literal["asset","liability"]]]   # 06 §1.3; "eqy" unused

@dataclass(frozen=True, slots=True)
class Leg:
    account_id: str; direction: int; amount_cents: int; reason: str
    # direction +1 debit / -1 credit; amount strictly > 0; sign lives in direction, never here

def account_id(code: AccountCode, owner_id: str, *, bank_id: str | None = None,
               ref: str | None = None) -> str:
    """'<code>:<owner>[@<bank>][#<ref>]'. The only constructor; never format ids inline."""
def parse_account_id(aid: str) -> tuple[AccountCode, str, str | None, str | None]
def bank_of(aid: str) -> str | None

class Ledger:
    def __init__(self, run_id: UUID, repo: LedgerRepository, clock: Clock) -> None: ...
    def open_account(self, code: AccountCode, owner_id: str, owner_type: str, *,
                     bank_id: str | None = None, ref: str | None = None, tick: int) -> str: ...
    def close_account(self, aid: str, *, tick: int) -> None:
        """Asserts balance == 0. NOT called for a decedent with an open case (06 §10.7 A)."""
    def is_open(self, aid: str) -> bool; def balance(self, aid: str) -> int
    def net_worth(self, owner_id: str) -> int
    def liquid(self, owner_id: str) -> int               # Σ dep + cash, excludes esc
    def accounts_of(self, owner_id: str) -> tuple[str, ...]                # sorted
    def post_transaction(self, legs: Sequence[Leg], *, tick: int, cause: Event,
                         allow_negative: frozenset[str] = frozenset()) -> UUID:
        """Atomic, synchronous, in-memory. Asserts P1–P7 (06 §1.4). Raises LedgerError, which
        HALTs (02 §10): affordability is a PHASE 4 concern, so by here the money is known to be
        there. txn_id = uuid5(run_id, f'{tick}:{txn_ordinal}')."""
    def transfer(self, src: str, dst: str, amount_cents: int, reason: str) -> list[Leg]:
        """06 §1.4.2. Two legs same-bank, six cross-bank via res:*. Builds, does not post."""
    def issue_base_money(self, legs: Sequence[Leg], *, tick: int, cause: Event) -> UUID:
        """The ONLY caller permitted to name iss:bk_cb."""
    def commit_tick(self, tick: int) -> Sequence[Mapping[str, Any]]
    async def flush(self, tick: int) -> None             # PHASE 6, _caller='polis.economy.ledger'
    def dump(self) -> Mapping[str, Any]; def load(self, state: Mapping[str, Any]) -> None
                                                         # C04 Checkpointable

class CommitmentLedger:
    """Tick-scoped cumulative commitments. PHASE 4 debits THIS, never ledger.liquid() (F3)."""
    def available(self, owner_id: str, tick: int) -> int
    def commit(self, owner_id: str, cents: int, tick: int) -> bool
    def reset(self, tick: int) -> None

class LedgerError(PolisError): ...

# polis/economy/invariants.py
def check_money(l: Ledger, v: EconomyView) -> Result       # M-1..M-6 (06 §1.7) -> HALT
def check_ledger(l: Ledger) -> Result                      # M-1, M-3
def m0_cents(l: Ledger) -> int; def m1_cents(l: Ledger) -> int          # 06 §1.5
class LedgerStateView: ...                                 # C04 WorldStateView slice

# polis/config/runtime.py — READ side (C18 owns the writer)
class RuntimeOverlay(Protocol):
    def bp(self, key: str, tick: int) -> int; def cents(self, key: str, tick: int) -> int
    def flag(self, key: str, tick: int) -> bool
    def brackets(self, key: str, tick: int) -> tuple[tuple[int, int], ...]
    def as_of(self, tick: int) -> Mapping[str, Any]
class StaticOverlay(RuntimeOverlay): ...    # Settings-backed; the M2 default; constant in tick
class LayeredOverlay(RuntimeOverlay): ...   # static base + C18 enactments; the newest with
                                            # enacted_tick <= tick; never mutated retroactively

# polis/economy/labour.py
@dataclass(frozen=True, slots=True)
class Occupation:
    id: str; req: Mapping[Skill, int]; int_: Mapping[Skill, int]; w: Mapping[Skill, int]
    sectors: tuple[str, ...]; base_wage_cents: int
OCCUPATIONS: Final[Mapping[str, Occupation]]               # configs/occupations.yaml, hashed

def match_score_bp(a: AgentState, vac: Vacancy, occ: Occupation, *, ctx: LabourContext) -> int:
    """06 §3.5. Pure, integer, 0..10_000. ONE agent, ONE vacancy — never an aggregate."""
def skill_value_bp(a: AgentState, occ: Occupation) -> int
def anchor_wage_cents(occ: Occupation, a: AgentState | None) -> int             # 06 §3.9
def visible_vacancies(a: AgentState, open_vacs: Sequence[Vacancy], rng: RngRegistry,
                      tick: int, k: int) -> tuple[Vacancy, ...]                 # 06 §3.4
def employed(a: str, t: int, st: LabourState) -> bool                           # 06 §3.10
def searching(a: str, t: int, st: LabourState) -> bool
def labour_force(t: int, st: LabourState) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """(E, U, NILF). The single source for M4/M5/M6; C24b must not recompute it."""
def unemployment_rate_bp(t: int, st: LabourState, window_ticks: int) -> int
def vacancy_rate_bp(t: int, st: LabourState) -> int

class LabourResolver:                                      # implements InstitutionResolver
    slot:    Final = InstitutionSlot.LABOUR                # 3
    handles: Final = frozenset({ActionType.APPLY_FOR_JOB, ActionType.MAKE_OFFER,
        ActionType.ACCEPT_OFFER, ActionType.DECLINE_OFFER, ActionType.NEGOTIATE_WAGE,
        ActionType.POST_VACANCY, ActionType.FIRE_EMPLOYEE, ActionType.QUIT_JOB, ActionType.WORK})
    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult
    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult
    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult
    def resolve(self, actions: Sequence[ValidatedAction], tick: int,
                ctx: ResolutionContext) -> Sequence[Event]
    def options_for(self, t: ActionType, ctx: ValidationContext) -> tuple[Mapping[str, Any], ...]

def screen_applications(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]    # step 5
def run_payroll(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]            # step 6
def decay_skills(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]           # monthly
def open_benefit_claim(agent_id: str, tick: int, ctx: InstitutionContext) -> NewEvent:
    """Kind 5080. C14 calls this then posts the transfer; C14 never mints 5080."""

# polis/economy/firms.py — PHASE 7 steps 1-4 and the quarterly close
def produce(f: FirmState, tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]
def depreciate_and_spoil(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]
def review_prices(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]
def restock(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]
def close_period(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]
def found_firm(founder_id: str, p: FoundCompanyParams, tick: int,
               ctx: ResolutionContext) -> Sequence[NewEvent]         # 6001; C15 calls this at M3
def dissolve_firm(firm_id: str, reason: str, tick: int, ctx) -> Sequence[NewEvent]       # 6002
def declare_dividend(firm_id: str, total_cents: int, decided_by: str, tick: int,
                     ctx: ResolutionContext) -> Sequence[NewEvent]:
    """6030 + 6031 per holder via allocate(). C15's resolver calls this; 6030/6031 are C11's."""
def apply_set_price(va: ValidatedAction, tick: int, ctx) -> Sequence[NewEvent]
def apply_produce(va: ValidatedAction, tick: int, ctx) -> Sequence[NewEvent]
def apply_restock(va: ValidatedAction, tick: int, ctx) -> Sequence[NewEvent]
    # C12's slot-4 GoodsResolver delegates SET_PRICE / PRODUCE / RESTOCK to these three.

class FirmsResolver:                                       # implements InstitutionResolver
    slot:    Final = InstitutionSlot.VENTURES              # 7 — M2 stand-in only
    handles: Final = frozenset({ActionType.FOUND_COMPANY, ActionType.DECLARE_DIVIDEND})
    # same five protocol methods. C15 replaces this resolver wholesale at M3.

# polis/economy/policy.py
class MechanicalPolicy:
    """06 §4.11. Active iff ablations.reflex_only. Emits Actions with origin='scripted'."""
    def decide(self, obs: Observation, state: AgentState, tick: int) -> tuple[Action, ...]: ...
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C02 | `NewEvent`, `EventLog.stage() -> Event` | every emission; `seq` is bound at stage (§9.2) |
| C03 | `LedgerRepository.post/open_account/balance/reconcile_balances` | the only DB path |
| C04 | `Clock` (`sim_day/week/month/quarter/year`, `hour_of_day`, `starts_new`, `ticks_for`), `Scheduler.register`, `stable`, `RngRegistry.get`, `@mechanism`, `InvariantRunner` | cadence, order, draws, ablation, halts |
| C07 | `AgentState.skill_bp`, `education_level`, `reputation`, `criminal_record`, `age_years`, `apply_skill_growth/decay`; `Observation`, `OfferBrief`, `Obligation` | scoring, growth, decay; you populate the labour slices of `PerceptionSources` |
| C10 | `InstitutionResolver`, `ValidatedAction`, `GateResult`, `ValidationContext`, `ResolutionContext` | the boundary |
| C06 | `places` (firm sites, capacity), district distance | founding gate, visibility bands |
| C21 | curriculum weights per school | the "skill is used" predicate of `06 §3.8` |

`ResolutionContext` must carry `ledger`, `runtime`, `clock`, `rng`, `log` and read-only repo
handles. Agree the field set with C10 before coding; do **not** use a module-level `Ledger`
singleton — checkpoint/restore and the determinism test both break on it.

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `ledger_accounts`, `ledger_entries` | **W (exclusive)** | via `LedgerRepository`, `_caller='polis.economy.ledger'`; import-linter forbids all others |
| `firms`; `vacancies`, `job_applications`, `employments` | W | firm state and the full labour lifecycle |
| `inventory` | W | C11 writes production and spoilage; **C12 writes sale decrements**. Never both write `qty` |
| `agents`; `agent_skills` | W (narrow) | `employer_id`, `occupation`, `employment_status`, `wealth_cents` (denormalised each tick, M-6); `level`, `last_used_tick` via C07 |
| `metrics` | W | M4–M7, M12–M15, M24 |
| `places`, `districts`, `schools`, `skus`, `goods_transactions` | R | siting, distance, curricula, unit cost, revenue |

## 8. Event kinds owned

**Ranges 5000–5999 (labour) and 6000–6099 (firms).** Payloads exactly as `06 §3.1` and `§4.1`.

| Block | Kinds — names and payloads are `06 §3.1` and `§4.1` verbatim |
|---|---|
| Labour | 5001–5004 vacancy/application · 5005–5009 offer and bargaining · 5010–5013 hire and separation · 5020–5021 work · 5030–5032 payroll · 5040–5042 skills and spells · 5050 summary · 5060–5061 self-employment · 5070 retirement · 5080–5081 benefit |
| Firms | 6001–6004 lifecycle · 6010–6014 production, capital, inventory, productivity · 6022–6023 pricing and restock · 6030–6031 dividends · 6040 period close |

All other 5000–5999 and 6000–6099 values are reserved and unused.

> **Ratified renumbering — record it in handback.** `06 §5.1` prints the goods kinds inside
> 6000–6099 (6020, 6021, 6024, 6025, 6041–6043, 6050, 6051), colliding with the firm kinds above.
> The allocation moves **every goods kind to `old + 100`** into C12's 6100–6999 range; C11's
> numbers are unchanged. The illustrative `6020 GOODS_PURCHASED` row in `02 §3.2`'s *selected
> kinds* table is stale as a result — `polis/events/kinds.py` is the single source of truth and
> the doc needs a one-line edit. Never let two chunks register 6020.

C15 emits no 6000-range kind directly: `FOUND_COMPANY` and `DECLARE_DIVIDEND` go through
`firms.found_firm` / `firms.declare_dividend`, which return the events.

## 9. Implementation notes

**9.1 Order of work.** `money.py` → `ledger.py` → property tests green → `runtime.py` →
`firms.py` → `labour.py` → `policy.py` → resolvers. Do not invert this.

**9.2 `EventRef` is just `Event`.** `06 §1.4.1` works around seqs being assigned at PHASE 6, but
C02's `EventLog.stage()` returns a sealed `Event` with `seq` **already bound**, so
`post_transaction` takes the staged event as `cause` and writes `ledger_entries.event_seq`
immediately. Keep the rest of §1.4.1: balances mutate in memory at post time, rows buffer, the DB
write is one batched `flush()` in PHASE 6. Record the simplification.

**9.3 Polarity, P6 and INV-MONEY.** `POLARITY` is a table, not a heuristic: after applying a
transaction, assert no asset account is negative and no liability positive, except the ids in
`allow_negative` — exactly `res:<bank>` and `dep:gv_treasury@bk_cb`, nothing else, ever. P6 catches
the commonest ledger bug (`06 §1.6 E9`: crediting a liability *increases* the debt). `INV-MONEY` is
the six sub-checks of `06 §1.7`: M-2/M-4/M-5 every tick, M-3 as an incremental accumulator every
tick plus a full `reconcile_balances` at each checkpoint, M-6 comparing `agents.wealth_cents` and
`banks.capital_cents` to `net_worth`. On failure emit `1010` and HALT — no tolerance, and no
`--continue-on-violation` path produces publishable data. Ship `polis ledger explain --run <id>
--tick <n>` (legs grouped by `txn_id`, running sums); it is the only thing that makes an F1 halt
debuggable.

**9.4 F3, the intra-tick commitment ledger.** Two actions can each be affordable against the
committed balance and unaffordable together, so PHASE 4 debits `CommitmentLedger`, never
`ledger.liquid()`. It lives in `ledger.py` and is exported, or C12–C15 each write their own subtly
different one.

**9.5 Labour resolution order inside slot 3.** Actions arrive sorted `(actor_id, action_id)`.
Resolve by type: `WORK` → `POST_VACANCY` → `APPLY_FOR_JOB` → `MAKE_OFFER` → `NEGOTIATE_WAGE` →
`ACCEPT_OFFER` → `DECLINE_OFFER` → `QUIT_JOB` → `FIRE_EMPLOYEE`; within a type by
`(actor_id, action_id)`. Two agents accepting the last headcount: the earlier pair wins, the other
gets `OFFER_EXPIRED{reason_code:'position_filled'}`. That is the only order-dependence in the
slot; document it.

**9.6 Match scoring and the 14 skills.** `06 §3.5` verbatim, integer throughout, a function of one
agent and one vacancy. The vocabulary is C07's closed `Skill` enum; the fourteen implied by
`06 §3.3` are `manual, operations, writing, sales, persuasion, finance, research, engineering,
law, negotiation, medicine, teaching, design, management` — confirm against C07, never redefine.
Ties break on `rng.get("labour.screen", vacancy_id, tick)`, **never `agent_id`**, which would give
an alphabetical hiring advantage for the whole run.

**9.7 MECHANISM tags — all of these, `entails` copied verbatim from `06`:**
`labour.vacancy_autopost` (§3.2), `labour.vacancy_visibility` (§3.4), `labour_matching` (§3.5),
`labour_recency_penalty` (§3.5), `labour.redundancy_selection` (§3.7), `skill_decay` (§3.8),
`firms.production_cobb_douglas` (§4.2), `firms.productivity_drift` (§4.3), `price_setting` (§4.5).

**No Beveridge curve may be analytically implied.** There is no aggregate matching function:
`grep -n "def match" labour.py` must return exactly one hit, over one agent and one vacancy. Hires
at tick *t* are `Σ_a Σ_v 1[applied]·1[offered]·1[accepted]`, a sum over microdecisions never
computed as an aggregate. `mechanisms.labour_matching: aggregate_cobb_douglas` is an explicit
**comparison baseline only** (`06 §3.11`), never a default; any A1 Beveridge claim additionally
requires `labour_vacancy_autopost: off`.

**9.8 Payroll (step 6, biweekly, days 1 and 15, 17:00).** Wages accrue per tick worked in
`accrued_wage_cents[employment_id]`, rebuilt from 5020/5031; payroll pays the accrual and zeroes
it. Progressive income tax on the annualised gross (`06 §11.1`), rates from `RuntimeOverlay`,
withheld with the **E1** five-leg pattern; employer payroll tax is an extra leg. Separation of any
kind pays the accrual immediately — this closes **F15**. If the firm cannot pay: `5032`, the unpaid
amount becomes a class-2 claim for C15, and redundancy sheds headcount by ascending
`match_score_bp × sqrt(tenure_ticks)`.

**9.9 Production carry.** `units = (carry + Y_micro) // 1_000_000`, `carry' = … % 1_000_000`,
rebuilt from `PRODUCTION_RUN.output_micro`; without it a firm producing 0.7 units/day produces 0
forever. The Cobb–Douglas exponentiation is the module's only non-integer intermediate.

**9.10 Minimum wage (ratified runtime key).** `labour.minimum_wage_cents`, default 0, read in
`check_resources` and rejecting `MAKE_OFFER` below it. This does not contradict `06 §3.6`
("Agent's floor: None") — that forbids a *scripted reservation wage on the worker*, which would
manufacture the labour-supply elasticity being measured. An institutional floor set by policy is a
different object and is exactly what C18 must be able to move.

**9.11 `MechanicalPolicy` provenance.** Its actions carry `origin='scripted'`, never `'reflex'` —
C10 raises `ReflexActionViolation` for a reflex `APPLY_FOR_JOB` and that guard stays live.

## 10. Configuration keys

```yaml
economy: {currency: POL, capital_skus: [cap_machine, cap_fixture, cap_software]}
labour: {vacancy_ttl: 30d, vacancy_visibility_k: 8, max_open_vacancies_per_firm: 5,
  max_open_applications: 6, min_match_score_bp: 5500, shortlist_multiple: 3,
  max_bargaining_rounds: 2, offer_stale: 3d, offer_ttl: 5d, autopost_window: 5d,
  search_window: 4w, severance_periods_bp: 0, notice_ticks: 0, retirement_age: 65,
  payroll: {cadence: biweekly, days: [1, 15], hour: 17}}
firms: {beta_capital_bp: 3000, capital_ref_cents: 0, depreciation_bp_per_year: 1000,
  learning_bp_per_day: 3, productivity_sigma_bp: 40, productivity_bounds_bp: [2000, 40000],
  spoilage_bp_per_day: 2000, price_override_ttl: 30d, payout_ratio_bp: 3000,
  retained_floor_months: 1, min_founding_capital_cents: 0, max_firms_per_founder: 3,
  working_capital_months: 3, markup: {initial_bp: 2500, step_bp: 200, max_bp: 8000,
  target_low_bp: 70000, target_high_bp: 300000}}
mechanisms: {labour_matching: stochastic_skill_match,   # aggregate_cobb_douglas = BASELINE only
  labour_vacancy_autopost: on, labour_recency_penalty: on, skill_decay: on,
  price_setting: markup_over_cost}                      # | llm_owner | hybrid
runtime:                                      # StaticOverlay defaults; C18 overrides per tick
  tax: {income: {brackets: [[0,0],[2000000,1500],[6000000,2500],[15000000,3500]]},
        payroll_employer_bp: 500, corporate_bp: 2000}
  labour: {minimum_wage_cents: 0}
  spend: {benefit_replacement_bp: 4000, benefit_max: 26w}
```

## 11. Acceptance criteria

1. **INV-MONEY holds to the cent across N ticks of a stub run** — `N = 5_000` in CI, all six
   sub-checks, every tick, zero tolerance, on a schedule including a payroll shortfall, a firing,
   a firm dissolution and a death.
2. `post_transaction` raises `LedgerError` on any P1–P7 violation; Hypothesis over random balanced
   leg sets keeps `Σ balances == 0` after every sequence.
3. No balance is written outside `ledger.py` (import-linter + a foreign `_caller` raising
   `WriteForbidden`); `iss:bk_cb` reaches a leg only via `issue_base_money` and no `"iss:"` literal
   exists elsewhere.
4. `−balance(iss:bk_cb) == m0_cents` after genesis; M-4 holds every tick thereafter.
5. Assets `>= 0` and liabilities `<= 0` every tick, except the two `allow_negative` ids, restored
   to non-negative before PHASE 9.
6. `allocate()` sums exactly to the pool for every input (Hypothesis), all outputs `>= 0`; `txn_id`
   is `uuid5`-deterministic across a replay.
7. Two commitments in one tick, individually affordable and jointly not: the second is rejected
   with `reason='resources'` (F3).
8. `match_score_bp` matches hand arithmetic on ten fixture pairs; renaming every `agent_id` to a
   reverse-sorted alias leaves the set of hires unchanged.
9. `grep "def match" labour.py` returns one symbol; no function there takes an aggregate count of
   unemployed or vacancies.
10. Every §9.7 rule carries `@mechanism(id, entails=…)`, is in `MECHANISM_REGISTRY`, and changes
    behaviour under `--mechanism-off <id>`.
11. `E/U/NILF` partition the age-eligible alive population exactly; `u`, `u_marginal`, `u_broad`,
    `lfpr`, `v_rate` all come from one `labour_force()` call.
12. A sim-year of unemployment strictly lowers the score on the same vacancy via skill decay; the
    effect vanishes under `mechanisms.skill_decay: off`.
13. Minimum vacancy-post to first-day-of-work is exactly 3 ticks (`06 §3.4`).
14. Payroll pays exactly the accrual; a mid-period quitter is paid for ticks worked;
    `separations_by_days_to_payroll` shows no clustering (F15).
15. `INV-EMPLOY` holds; `INV-LABSHARE ≤ 12,000 bp` holds and a deliberate wage double-count trips it;
    production never exceeds the `INV-PRODUCTION` bound and carry lets a 0.7 units/day firm produce
    7 units in 10 days.
16. Under `--reflex-only`, `MechanicalPolicy` keeps the economy live over 2 sim-years: hires > 0,
    purchases > 0, prices move.
17. Both resolvers satisfy the C10 protocol, tolerate an empty batch, and are order-independent
    except where §9.5 documents otherwise.
18. Determinism: same seed twice → identical 5000/6000-range events and `ledger_entries`;
    `mypy --strict polis/economy` and `ruff` pass; `polis ledger explain` renders a tick.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/economy/test_ledger_post.py` | P1–P7 each; polarity table; `transfer` 2-leg and 6-leg forms; `txn_id` determinism |
| `tests/unit/economy/test_ledger_worked_examples.py` | **E1–E10 of `06 §1.6` posted verbatim**, each summing to zero with the stated M0/M1 effect; the *wrong* E9 raises |
| `tests/property/test_ledger_closes.py` | **Hypothesis, mandatory.** Random balanced transaction sequences keep `Σ balances == 0`, assets `>= 0`, liabilities `<= 0`; unbalanced sets raise |
| `tests/property/test_allocate_exact.py` | **Hypothesis, mandatory.** `Σ allocate(pool, w) == pool`; all `>= 0`; stable under equal weights |
| `tests/unit/economy/test_money_helpers.py` | `bp`/`bp_ceil` boundaries; `round_to_tick`; `mint` format; `StaticOverlay` constant in tick and `LayeredOverlay` returning the newest enactment ≤ tick, never a future one |
| `tests/unit/economy/test_match_score.py` | Ten hand-computed fixtures; monotone in skill; `rec_bp` capped at −1,500; clamped |
| `tests/unit/economy/test_labour_lifecycle.py` | post → apply → screen → offer → 2 negotiations → accept → hire → work → payroll → quit, exact event sequence, 3-tick floor |
| `tests/unit/economy/test_separations.py` | Redundancy ordering; severance and notice; accrual paid on quit, fire, death, firm exit |
| `tests/unit/economy/test_unemployment_definition.py` | Partition; `searching` window edges; `u_broad` part-time; students and retirees excluded; decay only when unused and `last_used_tick` set by work/study/self-employment; score falls with spell length |
| `tests/unit/economy/test_production.py` | Cobb–Douglas vs a `Decimal` reference; carry over 1,000 runs; multi-SKU labour split by `allocate` |
| `tests/unit/economy/test_pricing_inventory.py` | Markup steps at the inventory-days bands; weighted-average unit cost; spoilage writes off with **no** ledger transaction |
| `tests/unit/economy/test_payroll_tax.py` | Bracket arithmetic at every boundary cent; E1 legs; employer tax; zero-rate case |
| `tests/unit/economy/test_no_aggregate_matching.py` | AST scan: no `(u_count, v_count)` signature; one `def match*`; `MechanicalPolicy` keeps the economy unfrozen and implements every `06 §4.11` substitute |
| `tests/invariants/test_inv_money_stub_run.py` | **Merge gate.** 5,000 ticks, `StubProvider`, scripted shocks; six sub-checks every tick; an injected leak HALTs at the right tick; F3 double-commit rejected |
| `tests/determinism/test_economy_determinism.py` | Same seed twice → identical events and entries; agent renaming does not change hires |
| `tests/integration/test_firm_lifecycle.py` | 50 agents, 500 ticks: founded, hires, produces, prices, sells, pays a dividend, dissolves; INV-MONEY throughout |

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. `ledger.py` is the sole writer of both ledger tables, with the import-linter contract added and
   a green property suite; `Ledger` implements C04's `Checkpointable` and a checkpoint/restore
   round-trip reproduces every balance to the cent.
2. `INV-MONEY`, `INV-LEDGER`, `INV-EMPLOY`, `INV-LABSHARE`, `INV-PRODUCTION` registered with C04's
   `InvariantRunner` at the `06 §2.6` frequencies.
3. Kinds 5000–5999 and 6000–6099 registered with payload schemas, and the goods renumbering
   (`old + 100`) recorded in the handback with the `02 §3.2` doc edit it implies.
4. `polis/config/runtime.py` ships with the read API and `StaticOverlay`, plus a note to C18
   listing every key economy reads and the rule that economy never reads static config for them.
5. `polis ledger explain --run <id> --tick <n>` exists and is documented.
6. A one-page note for C12–C15: how to post a transaction, the `CommitmentLedger` contract, and
   the rule that a resolver never builds an `account_id` by string formatting.

## 14. Traps

1. **Building labour before the ledger closes.** Everything after sits on an unknown. The bug is
   not findable by inspection and is 40M rows deep by the time V2 catches it.
2. **A sign error on a liability account.** `lnp` and `dpl` are liabilities: crediting *increases*
   the obligation. `06 §1.6 E9` keeps the wrong version in the spec on purpose. P6 is the only
   thing that catches it; never weaken P6 to make a test pass.
3. **Putting a real asset on the ledger.** Inventory, capital, shares, land. The first spoilage
   write-down becomes a one-sided leg and V2 fails at an unpredictable tick.
4. **A payment with no named counterparty.** Corollary L1a: there is no outside. The bug always
   shows up as somebody inventing a sink account.
5. **`float` anywhere near money**, or **rounding a split by hand**. One `0.1 + 0.2` in a wage and
   INV-MONEY fails three sim-years later; `//` per claimant plus "remainder to the first" loses
   cents on some inputs and gains them on others. `Decimal` only inside `MONEY_CTX`; `allocate()`
   or nothing (F13).
6. **`balance_cents` maintained in two places.** The incremental balance and the sum of entries
   drift; M-3 exists because of it. Never let a projection handler write a balance.
7. **An aggregate matching function.** `M = A·U^α·V^(1−α)` anywhere — even as a sanity check, even
   in a metric — analytically implies the Beveridge curve and destroys A1 (`06 §3.11`).
8. **Screening ties broken by `agent_id`.** `ag_aaron` gets hired for the whole run: systematic,
   invisible, and it contaminates every distributional result.
9. **Forgetting wage accrual.** Pay only at payroll and agents learn to quit on day 13 while firms
   learn to fire on day 14 (F15).
10. **Firing mechanically outside the shortfall path.** `FIRE_EMPLOYEE` is LLM-only (`06 §4.6`); a
    mechanical "underperformer" rule makes the labour market a classical ABM and falsifies T9.
11. **A reservation wage on the worker.** `06 §3.6` forbids it: it manufactures the exact
    labour-supply elasticity the model exists to measure.
12. **Skipping the production carry.** Small firms produce zero forever and the firm-size
    distribution becomes an integer-division artefact.
13. **Markup with no cap**, or **an eternal `SET_PRICE` override**. `markup_bp` compounds weekly
    and CPI runs away; `max_markup_bp`/`step_bp` are the hyperinflation guard (F6) and
    `price_override_ttl_ticks` stops one deliberation freezing a price for five sim-years.
14. **A module-level `Ledger` singleton.** Checkpoint/restore and the determinism test break, and
    the failure presents as a money leak in the wrong module.
15. **Emitting a kind you do not own** (C15 minting 6030, C14 minting 5080) — call the owner's
    function; two registrations is silent last-write-wins. And **treating `PAYROLL_SHORTFALL` as an
    error**: it is a normal state driving redundancy and later insolvency, and halting on it hides
    the most interesting dynamics in M2.
16. **Reading tax rates from static config.** They arrive through `RuntimeOverlay`; a direct read
    means C18 enacts policy the economy ignores — "fiscal policy has no effect".
17. **Closing a decedent's accounts during an open bankruptcy case.** `06 §10.7 A`: the account
    outlives the owner. Closing it destroys the estate and breaks closure in the same tick.
