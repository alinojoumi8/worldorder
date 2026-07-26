# C15 — Startups, VC, funding rounds, M&A, bankruptcy

**M3** · `polis/economy/ventures.py` (+ `funds.py`, `rounds.py`, `ma.py`, `bankruptcy.py` in the same package) · **Depends on:** C04, C05, C07, C10, **C11 (ledger, firms)**, **C13 (exchange)**, **C14 (banking)**, C20 (death, when it lands) · **Blocks:** C24b, C19 (creditor petitions) · **Size:** L

## 1. Context

This chunk owns the capital-allocation layer and, at the other end of it, failure. Startups
raise from VC funds through pitches, term sheets and priced rounds with real cap-table
arithmetic — option pools, dilution, anti-dilution, down rounds — and exit by acquisition or
IPO, or die. Bankruptcy is here too, because it is the same machinery seen from the liability
side: a trigger, a filing, an automatic stay, liquidation into real buyers, a five-class
priority waterfall, and a discharge that writes off the deficiency without moving a cent
(**E8b**). The four ordered cases where bankruptcy meets agent death (`06 §10.7`) are where
accounting closure is most likely to break in the whole system, and they are normative here.
This chunk resolves slot 7 and **replaces** C11's M2 stand-in `FirmsResolver`.

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/06-ECONOMY-SPEC.md` | **§8 ventures, §9 M&A, §10 bankruptcy (primary sources)**, §1.6 E7/E8 (inheritance and write-off legs), §2.3 `allocate`, §2.4 force-routed obligations, §2.5 RNG, §4.7–4.9 firm entry/exit/dividends, §6.11 IPO, §7.6 default, §7.11 bank failure, §12 M28, §14 F4/F10/F13, §16.1 slot 7, §16.2 steps 13–15 |
| `../docs/03-DATA-MODEL.md` | §0, §7 `startups`, `vc_funds`, `funding_rounds`, `cap_table`, `bankruptcies`, `loans`; §5 `firms`, `employments`; §6 `securities`, `holdings` |
| `../docs/02-ARCHITECTURE.md` | §3.2 kinds, §4 determinism, §5.1 slot order, §8 routing (`VC_EVAL`), §8.1 MECHANISM, §9 invariants |
| `chunks/C10-actions.md` | §5 `InstitutionResolver`, `DeclareDividendParams`, the polymorphic `ACCEPT_OFFER`/`DECLINE_OFFER`, §9.3 legality never rejects |
| `chunks/C11-labour-firms.md` | `Ledger`, `money.allocate`, `firms.found_firm`, `firms.declare_dividend`, `firms.dissolve_firm`, `RuntimeOverlay` |
| `chunks/C13-exchange.md` | stay-cancel API, liquidation slicing, `last_price_cents`, IPO listing |
| `chunks/C14-banking.md` | `write_off_loan`, claim registration, `capital_cents` |

## 3. Scope — in

1. **Startups**: `startups` rows, the free-text thesis (never parsed), stage advance, burn rate
   and runway, the fundraise obligation, pivots and death.
2. **VC funds as firms**: LP commitments as `cap_table` rows with `share_class='lp'`, capital
   calls, LP default, management fees, the distribution waterfall, dry powder.
3. **Pitch and evaluation**: `PITCH`, the traction block computed from state, and the LLM
   `VC_EVAL` call with its structured output and verdict gating.
4. **Valuation**: the comparables blend (`06 §8.6`) and the M&A DCF/comps/market anchor.
5. **Term sheets and rounds**: the full term set, the polymorphic accept/decline/counter, round
   close arithmetic (pre-money option pool, price per share, new shares, dilution), settlement,
   follow-ons, **down rounds** and broad-weighted / full-ratchet anti-dilution, pro-rata rights.
6. **Exits**: acquisition, IPO (converting preferred to common 1:1 at listing), shutdown, and
   `EXIT_COMPLETED` with per-investor `multiple_bp` and holding period.
7. **M&A**: valuation, `ACQUIRE`, consideration (cash / stock / mixed), tender and approval
   thresholds, drag-along and squeeze-out, the antitrust hook, and the three integration modes.
8. **`DECLARE_DIVIDEND`** resolution (delegating the money to C11's `firms.declare_dividend`).
9. **Bankruptcy**: the five triggers, filing, the automatic stay, claim registration,
   liquidation into real buyers, the five-class priority waterfall, recovery rates, discharge,
   reorganisation and dismissal, and the credit flag.
10. **The four death-interaction cases** of `06 §10.7` and the fixed ordering within the death
    tick.
11. `VenturesResolver` (slot 7), replacing C11's stand-in; PHASE 7 steps 13–15.
12. `INV-CAPTABLE` (jointly with C13) and the waterfall's exactness assertion.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `post_transaction`, `allocate`, account ids | **C11** |
| `firms` row creation, dividends' money, production, dissolution mechanics | **C11** — you call `found_firm`, `declare_dividend`, `dissolve_firm` |
| Loan write-off legs and 8017 | **C14** — you call `write_off_loan` |
| Order cancellation, forced liquidation on the book, IPO pricing and settlement | **C13** — you request, it executes |
| Bank failure (`06 §7.11`) | **C14** — banks are resolved there, **not** through this waterfall (trigger B5) |
| Creditor petitions as lawsuits, judgments | **C19** — a judgment for the creditor triggers B3 here |
| PHASE 8 death itself (vitals, heirs, household) | **C20** — you own only the estate/case interaction |
| Deciding to found, pitch, invest, acquire or file | **C09** |

## 5. Interfaces you provide

```python
# polis/economy/ventures.py
Stage = Literal["idea", "preseed", "seed", "a", "b", "exited", "dead"]

@dataclass(slots=True)
class Startup:
    startup_id: str; firm_id: str; thesis: str       # free text, NEVER parsed by code
    stage: Stage; burn_rate_cents: int; runway_ticks: int
    founded_tick: int; died_tick: int | None; exit_tick: int | None; exit_type: str | None

def found_startup(founder_id: str, p: FoundCompanyParams, tick: int,
                  ctx: ResolutionContext) -> Sequence[NewEvent]:
    """Calls C11's firms.found_firm, then adds the startups row. 6001 + 9001."""
def update_runway(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]:
    """PHASE 7 step 13, monthly. burn = max(0, monthly_costs − monthly_revenue);
    runway = INT32_MAX if burn == 0 else liquid × ticks_per_month // burn. Emits 9003 and,
    below fundraise_trigger_ticks, a MANDATORY Obligation for the founder (06 §2.4)."""
def revise_thesis(startup_id: str, new_thesis: str, trigger: str, tick: int,
                  ctx) -> Sequence[NewEvent]: ...                                  # 9002
def kill_startup(startup_id: str, cause: str, tick: int, ctx) -> Sequence[NewEvent]: # 9004

# polis/economy/funds.py
def form_fund(gp_agent_id: str, p: FoundCompanyParams, tick: int, ctx) -> Sequence[NewEvent]:
    """A fund IS a firm (sector='finance'). LP interests are cap_table rows with
    share_class='lp', one unit == lp_unit_cents of commitment. Commitments stay OFF-ledger —
    a promise is not money. 9005."""
def call_capital(fund_id: str, amount_cents: int, tick: int, ctx) -> Sequence[NewEvent]:
    """Pro rata across LP units via allocate(). LP deposit -> fund deposit. 9006."""
def handle_lp_default(fund_id: str, lp_id: str, tick: int, ctx) -> Sequence[NewEvent]: # 9007
def charge_management_fee(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]:   # 9009
def distribute(fund_id: str, exit_id: str, gross_cents: int, tick: int,
               ctx) -> Sequence[NewEvent]:
    """Return called capital -> hurdle (a MULTIPLE of called capital, not an IRR) ->
    carry split -> remainder to LPs by units. Every step via allocate(). 9008."""
def dry_powder_cents(fund_id: str, ctx) -> int: ...           # committed − deployed

# polis/economy/rounds.py
@dataclass(frozen=True, slots=True)
class TermSheet:
    term_sheet_id: str; startup_id: str; investor_id: str
    pre_money_cents: int; amount_cents: int
    security: Literal["preferred", "common"]
    liq_pref_bp: int; participating: bool; pro_rata: bool; board_seat: bool
    option_pool_bp: int; anti_dilution: Literal["broad_weighted", "full_ratchet", "none"]
    expires_tick: int

def traction(startup_id: str, tick: int, ctx) -> Mapping[str, int]:
    """revenue_ttm, revenue_growth_bp, headcount, burn, runway_ticks, months_since_founding,
    prior_rounds, founder track record — ALL computed from the event log and state."""
def evaluate_pitch(pitch_id: str, investor_id: str, tick: int,
                   ctx: VentureContext) -> PitchVerdict:
    """LLM purpose VC_EVAL (02 §8, temperature 0.4). Structured output:
    {conviction, thesis_fit, valuation_view_cents, check_size_cents, verdict, concerns[]}.
    verdict ∈ {pass, explore, term_sheet}. Emits 9012 with llm_call_id."""
def pre_money_cents(startup_id: str, view_cents: int, tick: int, ctx) -> int:
    """06 §8.6 comparables blend, w_llm_bp default 5,000."""
def close_round(ts: TermSheet, participants: Sequence[tuple[str, int]], tick: int,
                ctx: ResolutionContext) -> Sequence[NewEvent]:
    """Round arithmetic of 06 §8.7, all integer:
        pool_shares    = ceil(shares_pre × pool_bp / (10_000 − pool_bp))     # PRE-money
        price_per_share= max(1, pre_money_cents // (shares_pre + pool_shares))
        new_shares     = amount_cents // price_per_share
        dilution_bp    = 10_000 × new_shares // (shares_pre_pool + new_shares)
    Residual cents stay with the company as premium; the FULL amount moves on the ledger.
    One transaction. 9010 + 9018 (+ 9017 and anti-dilution if a down round)."""
def apply_anti_dilution(round_id: str, tick: int, ctx) -> Sequence[NewEvent]: ...   # 9017
def waterfall(proceeds_cents: int, rounds: Sequence[Round],
              common: Sequence[tuple[str, int]]) -> dict[str, int]:
    """06 §8.10. Preferences senior-first (reverse chronological), then residual to common and
    participating preferred, then the 'greater of' conversion test as a deterministic
    fixed point in <= len(rounds) passes. asserts sum(out.values()) == proceeds_cents."""

# polis/economy/ma.py
def valuation_anchor_cents(target_id: str, tick: int, ctx) -> int:
    """max(mkt, median(dcf, comps, book)). Decimal via MONEY_CTX, floored to cents."""
def propose_acquisition(a: ValidatedAction, tick: int, ctx) -> Sequence[NewEvent]: ...  # 9020
def tally_approval(deal_id: str, tick: int, ctx) -> Sequence[NewEvent]: ...      # 9021 / 9022
def integrate(deal_id: str, mode: Literal["absorb","standalone","asset_sale"], tick: int,
              ctx) -> Sequence[NewEvent]: ...                                    # 9023/9024/9025
def hhi_bp(sector: str, ctx) -> int: ...        # for the antitrust hook, 9026

# polis/economy/bankruptcy.py
PriorityClass = Literal[1, 2, 3, 4, 5]

def check_triggers(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]:
    """PHASE 7 step 14, daily 22:00, after all money has moved. B1 cash-flow, B2 balance-sheet
    (firms and funds ONLY — never agents), B3 creditor petition (from C19), B4 voluntary.
    B5 (bank insolvency) is C14's and never enters this waterfall."""
def file_case(entity_id: str, entity_type: str, trigger: str, filed_by: str, tick: int,
              ctx: ResolutionContext) -> Sequence[NewEvent]: ...                   # 9030
def impose_stay(case_id: str, tick: int, ctx) -> Sequence[NewEvent]:
    """9031. Cancels every resting order via C13 and releases escrow and reserved shares;
    blocks all ActionTypes except FILE_BANKRUPTCY, TESTIFY, SAY, DIRECT_MESSAGE, WORK, EAT,
    SLEEP, MOVE_TO, NULL_ACTION; stops interest accrual; expires open offers."""
def register_claims(case_id: str, tick: int, ctx) -> Sequence[NewEvent]: ...       # 9032
def liquidate(case_id: str, tick: int, ctx) -> Sequence[NewEvent]: ...             # 9033
def distribute_estate(case_id: str, tick: int, ctx) -> Sequence[NewEvent]: ...     # 9034
def discharge(case_id: str, tick: int, ctx) -> Sequence[NewEvent]: ...             # 9035 (+ 9036)
def settle_death(agent_id: str, tick: int, ctx) -> Sequence[NewEvent]:
    """06 §10.7 cases A–D and the seven-step ordering. Called by PHASE 8 (C20)."""

# polis/economy/ventures_resolver.py
class VenturesResolver:                                    # implements InstitutionResolver
    slot:    Final = InstitutionSlot.VENTURES              # 7 — REPLACES C11's stand-in at M3
    handles: Final = frozenset({ActionType.FOUND_COMPANY, ActionType.PITCH,
        ActionType.ISSUE_TERM_SHEET, ActionType.INVEST, ActionType.ACQUIRE,
        ActionType.SELL_STAKE, ActionType.FILE_BANKRUPTCY, ActionType.DECLARE_DIVIDEND,
        ActionType.ACCEPT_OFFER, ActionType.DECLINE_OFFER})   # see §9.1 on the last two
    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult: ...
    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult: ...
    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult: ...
    def resolve(self, actions: Sequence[ValidatedAction], tick: int,
                ctx: ResolutionContext) -> Sequence[Event]: ...
    def options_for(self, t: ActionType, ctx: ValidationContext
                    ) -> tuple[Mapping[str, Any], ...]: ...
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C11 | `Ledger.post_transaction`, `transfer`, `money.allocate`, `MONEY_CTX`, `CommitmentLedger` | every settlement, every split |
| C11 | `firms.found_firm`, `firms.declare_dividend`, `firms.dissolve_firm`, `firms.capital/inventory` read API | you never mint a 6000-range kind yourself |
| C13 | stay-cancel, `rng.get("exchange.liquidation", case_id, tick)` slicing, `last_price_cents`, `list_security`, `delist`, IPO settlement | exits, liquidation, marks |
| C14 | `write_off_loan`, loan and claim state, `capital_cents` | discharge, class-4 deficiency, cascade into bank failure |
| C05 | `LlmRouter.complete(purpose="VC_EVAL", …)`, structured output, `StubProvider` | pitch evaluation |
| C10 | `InstitutionResolver`, polymorphic `ACCEPT_OFFER`/`DECLINE_OFFER`, `DeclareDividendParams` | the boundary |
| C04 | `rng.get("ventures.comparables"/"ventures.outcome"/"bankruptcy.impact", …)`, `Clock`, `Scheduler`, `stable`, `@mechanism` | draws, cadence, ablation |
| C20 | PHASE 8 death hook | `settle_death` is called from there, not here |

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `startups`, `vc_funds`, `funding_rounds` | W | stage, burn, runway, commitments, deployment |
| `cap_table` | W | `common`, `preferred`, `lp` classes; `INV-CAPTABLE` covers `common` on listed firms |
| `bankruptcies` | W | `assets_cents`/`liabilities_cents` snapshotted at filing; `recovery_rate` at discharge |
| `firms` | W (status) | `bankrupt`, `acquired`, `dissolved`, `dissolved_tick`; row creation is C11's |
| `employments` | W (via C11) | transfer on `absorb`, `FIRED{firm_exit}` on discharge |
| `holdings`, `securities` | W (via C13) | delisting at 0, tendered shares, preferred→common at IPO |
| `loans` | R + write-off via C14 | claims, deficiency, discharge |
| `ledger_*` | via C11 only | — |
| `metrics` | W | M28 `venture_moic_bp`, M15 `firm_exit_rate` inputs, M17 `hhi_sector` |

## 8. Event kinds owned

**Range: 9000–9999.** Payloads exactly as `06 §8.1`, `§9.1`, `§10.1`.

| Block | Kinds |
|---|---|
| Startups | `9001 STARTUP_FOUNDED`, `9002 THESIS_REVISED`, `9003 RUNWAY_UPDATED`, `9004 STARTUP_DIED` |
| Funds | `9005 VC_FUND_FORMED`, `9006 CAPITAL_CALLED`, `9007 LP_DEFAULTED`, `9008 FUND_DISTRIBUTION`, `9009 MANAGEMENT_FEE_CHARGED` |
| Rounds | `9010 ROUND_CLOSED`, `9011 PITCH_MADE`, `9012 PITCH_EVALUATED`, `9013 TERM_SHEET_ISSUED`, `9014 TERM_SHEET_ACCEPTED`, `9015 TERM_SHEET_DECLINED`, `9016 TERM_SHEET_EXPIRED`, `9017 DOWN_ROUND`, `9018 CAP_TABLE_UPDATED`, `9019 OPTION_POOL_SET` |
| M&A | `9020 ACQUISITION_PROPOSED`, `9021 ACQUISITION_APPROVED`, `9022 ACQUISITION_REJECTED`, `9023 ACQUISITION_COMPLETED`, `9024 ASSET_SALE`, `9025 INTEGRATION_COMPLETED`, `9026 ACQUISITION_BLOCKED` |
| Bankruptcy | `9030 BANKRUPTCY_FILED`, `9031 AUTOMATIC_STAY_IMPOSED`, `9032 CLAIM_REGISTERED`, `9033 ASSETS_LIQUIDATED`, `9034 DISTRIBUTION_MADE`, `9035 BANKRUPTCY_DISCHARGED`, `9036 CREDIT_FLAG_SET`, `9037 EXEMPTION_APPLIED`, `9038 ESTATE_DEFERRED_TO_CASE` |
| Exits | `9040 EXIT_COMPLETED`, `9041 WATERFALL_APPLIED` |

**You emit nothing outside 9000–9999.** Dividends (6030/6031), firm founding (6001), dissolution
(6002), ownership transfer (6004), loan write-offs (8017), delisting (7002) and order
cancellation (7012) all belong to other chunks; call their functions and pass their events
through.

## 9. Implementation notes

**9.1 The polymorphic offer types.** `06 §0.2` clarifies that `ACCEPT_OFFER`/`DECLINE_OFFER`
operate on any `Offer` with `offer_kind ∈ {job, term_sheet, acquisition, settlement,
loan_terms}`. C10 requires `handles` to be **disjoint** across resolvers, so both C11 (jobs) and
C15 (term sheets, tenders) cannot claim them. Resolve it one way and record it: a single
`OfferRegistry` owned by C10 or C11 maps `offer_id → owning slot`, the slot-3 resolver keeps
`handles` on the two types, and it dispatches non-job offers to a callback C15 registers. Agree
this with C11 and C10 **before** either merges; two resolvers claiming `ACCEPT_OFFER` raises
`DuplicateHandler` at registration, which is the good failure, but discovering it at integration
costs a week.

**9.2 A fund is a firm and a commitment is not money.** LP commitments stay off the ledger.
Posting a commitment would create money that does not exist and V2 would fail the first time an
LP under-delivered. Capital calls move real deposits; an LP that cannot fund a call within
`call_grace_ticks` forfeits its units, reallocated pro rata to performing LPs — a genuine
contagion channel from household distress into the venture layer.

**9.3 The thesis and the deck are free text and are never parsed.** Same discipline as
`Action.reasoning` (`02 §6.1`): stored verbatim, shown to other LLMs and to researchers, never
branched on. Grep for `thesis` and `deck_text` in CI and assert no comparison operator touches
them.

**9.4 `VC_EVAL`.** Prompt inputs are the thesis, the state-derived traction block, the cap table,
the ask, comparable recent rounds, the fund's dry powder and thesis, and the investor's
retrieved memories — **no hidden information** (`04 §5` rule 4). `verdict == "term_sheet"` makes
`ISSUE_TERM_SHEET` legal for that investor for `term_sheet_window_ticks`; `"explore"` schedules
a second pitch obligation after a diligence delay; `"pass"` closes the pitch. Founders are
capped at 1 pitch per tick (action slot) and `max_open_pitches` open. All tests use
`StubProvider`.

**9.5 Round arithmetic — the three things people get wrong.** The option pool is created
**pre-money**, so it dilutes founders and not the incoming investor:
`pool_shares = ceil(shares_pre × pool_bp / (10_000 − pool_bp))`. `price_per_share` uses
`shares_pre + pool_shares`. The residual cents (`amount − new_shares × price`) stay with the
company as premium, but the **full `amount_cents` moves on the ledger** regardless — otherwise
closure breaks by the residual on every round. Multiple participants split `new_shares` by
`allocate()` on check sizes.

**9.6 Down rounds and anti-dilution.** A round is a down round iff
`price_per_share < previous price_per_share`. Broad-weighted:
`new_conversion_price = old_price × (A + B) // (A + C)` where `A` = fully-diluted shares before,
`B = amount // old_price`, `C = new_shares`; full ratchet sets it to the new price. Extra shares
`old_shares × old_price // new_conversion_price − old_shares` are issued to protected holders,
diluting common further. This is the mechanism that transfers ownership from founders to earlier
investors and one of the clearest places to look for **A6**.

**9.7 The waterfalls, both of them, and `allocate()` everywhere.** The venture waterfall
(`06 §8.10`) applies to acquisition consideration and to class 5 of a bankruptcy. The bankruptcy
priority waterfall (`06 §10.5`) has five classes paid in full in order, pro rata within a class:

| Rank | Class | Contents |
|---|---|---|
| 1 | Secured | Each secured claim up to the **realised** value of its specific collateral; deficiency → class 4 |
| 2 | Administration and wages | `bp(estate, admin_fee_bp)` to `gv_treasury`, then unpaid wages up to `wage_priority_cap_cents` per employee; excess → class 4 |
| 3 | Tax | `txr` claims and assessed-unpaid amounts |
| 4 | Unsecured | Remaining loans, trade payables, judgment debts, class-1 deficiencies, class-2 wage excess |
| 5 | Equity | Only if 1–4 are paid in full; distributed through the §8.10 venture waterfall |

Both end in `assert sum(out.values()) == pool`. That assertion is not decorative — the waterfall
is the most intricate money split in the system and the natural home of **F13**.

**9.8 Liquidation needs real buyers.** Listed securities are sold with market orders sliced over
`liquidation_ticks` sessions via `rng.get("exchange.liquidation", case_id, tick)`, so **price
impact is realised on the book, not assumed**. Unlisted stakes are offered to existing holders at
a haircut; inventory and capital to solvent firms in the same sector at their haircuts. If no
buyer exists the asset is **scrapped** — a real write-off with no ledger transaction (Rule L1) —
and recovery is simply lower. That is the correct outcome and it leaks nothing.

**9.9 Discharge moves no money.** Residual class-4 and class-5 claims are written off with the
E8b pattern via C14's `write_off_loan`: the creditor's asset and the debtor's liability both
vanish, **M0 and M1 are unchanged**, and the creditor's capital falls. If that drives a bank's
capital negative, C14's §7.11 fires and the cascade is real. The agent exemption
(`EXEMPTION_APPLIED`, default 1 sim-month of median wage plus necessary housing tenure) is
load-bearing: without it bankruptcy is functionally identical to death and both research
objects are destroyed.

**9.10 The four death cases — normative, `06 §10.7`.**

| Case | Condition | Behaviour |
|---|---|---|
| **A** | Dies with an **open bankruptcy case** | PHASE 8 settlement **defers**. Emit `9038`; skip `04 §12.3` steps 4–5. The case continues with `gv_treasury` as administrator. Heirs get only the class-5 residual at discharge. **The decedent's ledger accounts stay open — `ledger.close_account` must NOT be called.** Steps 1, 2, 6, 7, 8 run normally. |
| **B** | Dies **insolvent, no open case** | No case, no stay, no `bankruptcies` row. PHASE 8 step 4 runs a **simplified waterfall in one atomic transaction** using the §10.5 class ordering over whatever the estate realises; the shortfall is written off (E8b). Step 5 distributes zero. |
| **C** | Dies **solvent** | `04 §12.3` unmodified: E7a settles debts, estate tax if `tax.estate_bp > 0`, E7b distributes to heirs by largest remainder with ties on ascending `agent_id`, escheat to `gv_treasury` if intestate with no heirs. |
| **D** | Deceased **owns a firm** | Shares pass to heirs via `6004 OWNERSHIP_TRANSFERRED{cause:'inheritance'}`. No heirs → escheat to `gv_treasury`, which sells the stake or dissolves the firm after `orphan_firm_ticks`. If the firm is itself insolvent, B2 fires on its own schedule: **a firm bankruptcy and its owner's death are separate proceedings against separate estates.** |

Ordering within the death tick is fixed and must be implemented literally:
`1` cancel resting orders and release escrow/reserved shares → `2` terminate employment and pay
accrued wages if the employer can → `3` determine case A/B/C → `4` run that settlement as **one
transaction** → `5` vacate housing and restructure the household → `6` archive memories and end
relationships → `7` close ledger accounts asserting zero balance, **skipped in case A**.

**9.11 The automatic stay.** Immediate, until `resolved_tick` or `stay_max_ticks`. It cancels
every resting order through C13 and releases everything, blocks all action types except the nine
listed in `06 §10.4`, stops interest accrual and penalty rates, keeps employees working with
wages accruing as a **class-2 claim**, and rejects collateral seizure, new suits and set-off. The
stay is what makes bankruptcy orderly rather than a race, and it is what stops the run
deadlocking on an entity that keeps transacting while insolvent.

**9.12 MECHANISM tags, `entails` verbatim from `06`.**

| id | Says plainly |
|---|---|
| `venture_valuation` (`comparables_blend`) | Valuation momentum, and hence a partial private-market bubble, is **implied** by the anchor; report both `w_llm_bp: 5,000` and `10,000` for any A3/A6 claim |
| `ma.valuation_anchor` | A **positive acquisition premium is implied**; premium levels, cyclicality and overpayment are not |
| `ventures.integration_synergy` | `integration_synergy_bp` defaults to **0** — assuming positive synergies would make "acquisitions improve productivity" a mechanism, and A6 asks whether they do |

**9.13 Antitrust is a policy, not a rule.** `HHI_after > hhi_block_threshold` and `ΔHHI > 200`
makes a block *possible*; whether the government blocks is C18's decision through the enacted
competition policy, read from `RuntimeOverlay`. Degenerate monopoly (**F4**) then becomes an
observable consequence of a policy regime — a finding — rather than an artefact of a threshold
in market code.

## 10. Configuration keys

```yaml
ventures:
  lp_unit_cents: 10000
  mgmt_fee_bp: 200
  carry_bp: 2000
  hurdle_bp: 800
  call_grace: 14d
  fundraise_trigger: 180d
  max_open_pitches: 5
  term_sheet_window: 7d
  term_sheet_ttl: 14d
  comparable_window: 8
  w_llm_bp: 5000
  seed_default_pre_money_cents: 0
  sector_multiple_bp: {}
  growth_cap_bp: 20000
  option_pool_bp: 1000
  liq_pref_bp: 10000
  board_seat_threshold_bp: 1500
  anti_dilution: broad_weighted
  orphan_firm: 90d
ma:
  premium_bp: 2500
  dcf_horizon: 10y
  equity_risk_premium_bp: 500
  min_tender_bp: 5000
  drag_along_bp: 7500
  squeeze_out_bp: 9000
  redundancy_bp: 3000
  integration_synergy_bp: 0
  hhi_block_threshold: 2500
bankruptcy:
  grace: 14d
  insolvency_persist: 30d
  petition_min_cents: 0
  stay_max: 60d
  liquidation_ticks: 5d
  admin_fee_bp: 300
  wage_priority_cap: 90d
  unlisted_haircut_bp: 5000
  inventory_haircut_bp: 5000
  capital_haircut_bp: 4000
  exempt_months: 1
  credit_flag: 7y
mechanisms:
  venture_valuation: comparables_blend
  ma_valuation_anchor: on
  ventures_integration_synergy: on
```

## 11. Acceptance criteria

1. Every round, exit, distribution and estate settlement posts balanced transactions; a
   2,000-tick stub run exercising all of them keeps `INV-MONEY` closed to the cent.
2. LP commitments never appear on the ledger; `vc_funds.committed_cents` moves without any leg,
   and only capital calls post.
3. `waterfall()` asserts `sum(out) == proceeds` and passes a Hypothesis test over random cap
   tables, preference stacks and proceeds — including proceeds of 0 and proceeds below class 1.
4. The bankruptcy waterfall pays classes strictly in order; a class is never partially paid
   while a junior class receives anything; `Σ distributions + Σ write-offs == Σ claims`.
5. `class_recovery_bp` and `blended_recovery_bp` match hand arithmetic on a fixture estate.
6. Option-pool arithmetic dilutes founders and not the new investor; `dilution_bp` matches hand
   arithmetic; residual cents stay with the company while the full amount moves on the ledger.
7. A down round emits `9017`, applies broad-weighted anti-dilution with the exact formula, and
   the extra shares reconcile against `cap_table` and `INV-CAPTABLE`.
8. `INV-CAPTABLE` holds for listed firms after every round, acquisition and IPO.
9. `VC_EVAL` runs end to end against `StubProvider` with no network call, records `llm_call_id`
   in `9012`, and gates `ISSUE_TERM_SHEET` on `verdict == "term_sheet"`.
10. Thesis and deck text never affect control flow: a run with both replaced by random strings
    produces identical events (mirroring C10's `reasoning` test).
11. An acquisition completes only above the tender/approval threshold; drag-along and
    squeeze-out apply at their thresholds; a blocked deal emits `9026` and moves no money.
12. `absorb` transfers employments at unchanged wages, fires `redundancy_bp` of overlap with
    severance, moves loans with the acquirer as obligor in one balanced transaction per loan,
    and delists the target's symbol.
13. `asset_sale` leaves the shell holding the liabilities and it satisfies B2 within
    `insolvency_persist_ticks` — the case the waterfall exists to police.
14. Agents are **never** subject to trigger B2; a mortgaged household with negative net worth
    and a servicing wage is not forced into a filing.
15. The automatic stay cancels every resting order and releases every reservation in the same
    tick, blocks exactly the action types of `06 §10.4`, and stops interest accrual.
16. Discharge moves no money: M0 and M1 unchanged, creditor capital falls, debtor net worth
    rises by the same amount.
17. A discharge that drives a bank's capital negative triggers C14's failure path, and
    `INV-MONEY` holds at every tick of the cascade.
18. **All four death cases pass**, plus death holding a partially-filled order, death as a
    creditor in another entity's open case, death of a fund's GP, and death of a bank's sole
    owner. In case A no ledger account is closed.
19. `EXEMPTION_APPLIED` preserves the exempt amount and housing tenure; a discharged agent is
    materially different from a dead one.
20. `DECLARE_DIVIDEND` resolves in slot 7, is rejected by the resources gate when it would
    breach `retained_floor_cents`, and emits 6030/6031 **through C11**.
21. `VenturesResolver` replaces C11's stand-in cleanly: exactly one slot-7 resolver is
    registered per run and `DuplicateHandler` proves it.
22. Determinism: same seed twice → identical 9000-range events, `cap_table`, `funding_rounds`
    and `bankruptcies`.
23. `mypy --strict`, `ruff`, import-linter pass.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/property/test_waterfall_exact.py` | **Hypothesis.** Venture waterfall over random rounds/preferences/proceeds: sums exactly, no negative allocation, "greater of" converges in ≤ len(rounds) passes |
| `tests/property/test_priority_waterfall_exact.py` | **Hypothesis.** Five-class waterfall: strict class ordering, pro rata within class, `Σ paid + Σ written off == Σ claimed` |
| `tests/unit/ventures/test_round_math.py` | Pre-money option pool; price per share; new shares; dilution; residual premium; multi-participant `allocate` split |
| `tests/unit/ventures/test_down_round.py` | Down-round detection; broad-weighted and full-ratchet formulas; extra shares reconcile with `cap_table`; founders diluted |
| `tests/unit/ventures/test_fund_mechanics.py` | Commitments off-ledger; capital call pro rata; LP default and reallocation; management fee; hurdle-as-multiple then carry then LPs |
| `tests/unit/ventures/test_valuation.py` | Comparables blend at `w_llm_bp` 0/5,000/10,000; revenue-multiple path; DCF against a `Decimal` reference; anchor = max(mkt, median(...)) |
| `tests/unit/ventures/test_vc_eval.py` | `StubProvider` only; structured output parse and repair; verdict gating of `ISSUE_TERM_SHEET`; no hidden information in the prompt inputs |
| `tests/unit/ventures/test_thesis_not_parsed.py` | Randomised thesis/deck text → identical events; AST scan finds no comparison on those fields |
| `tests/unit/ventures/test_ma_approval.py` | Tender threshold; drag-along; squeeze-out; antitrust block emits 9026 and moves no money; cash/stock/mixed consideration legs |
| `tests/unit/ventures/test_integration.py` | `absorb` employment and loan transfer; redundancy selection and severance; productivity blend; `standalone` and `asset_sale` |
| `tests/unit/ventures/test_bankruptcy_triggers.py` | B1/B2/B3/B4; agents exempt from B2; B5 routed to C14, not here; evaluated at step 14 after money moves |
| `tests/unit/ventures/test_automatic_stay.py` | Order cancellation and release; blocked action set exactly as `06 §10.4`; accrual stopped; wages continue as class-2 |
| `tests/unit/ventures/test_liquidation.py` | Slicing via the seeded RNG; realised price impact on the book; haircuts; unsold assets scrapped with **no** ledger transaction |
| `tests/unit/ventures/test_discharge.py` | E8b via C14; M0/M1 unchanged; capital and net-worth effects; exemption; credit flag duration and its scorecard effect |
| `tests/invariants/test_death_settlement.py` | **Merge gate.** Cases A–D plus: partial fill at death, deceased as creditor in an open case, GP death, bank-sole-owner death. Case A leaves accounts open. INV-MONEY every tick |
| `tests/invariants/test_bankruptcy_cascade.py` | Discharge → bank capital negative → C14 failure → deposit haircut, with INV-MONEY holding throughout |
| `tests/determinism/test_ventures_determinism.py` | Same seed twice → identical events, cap tables, rounds, cases |
| `tests/integration/test_startup_lifecycle.py` | Founded → pitches → term sheet → seed → follow-on → down round → runs out of cash → files → liquidates → discharges, over 800 ticks |

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. Kinds 9000–9999 registered with payload schemas; a test asserting this chunk emits nothing
   outside the range.
2. `INV-CAPTABLE` registered (jointly with C13); both waterfall assertions live in production
   code, not only in tests.
3. Three `@mechanism` declarations (`venture_valuation`, `ma.valuation_anchor`,
   `ventures.integration_synergy`) with the `06` `entails` strings verbatim and ablatable.
4. `VC_EVAL` present in the model-routing table and prompt manifest.
5. The `ACCEPT_OFFER`/`DECLINE_OFFER` ownership question of §9.1 **resolved in writing with C10
   and C11 before merge**, with the chosen mechanism documented in the handback.
6. `VenturesResolver` registered in slot 7 and C11's stand-in removed from the M3 composition
   root; exactly one slot-7 resolver per run.
7. A handback note for C20 giving the exact seven-step death-tick ordering and the case A rule
   that ledger accounts stay open.

## 14. Traps

1. **Closing a decedent's ledger accounts in case A.** The single most likely place for V2 to
   break. The account outlives the owner until discharge; `close_account` asserts a zero balance
   and destroys the estate in the same tick it breaks closure.
2. **Running both a death settlement and a bankruptcy distribution over the same estate.** Case
   A defers, case B does not open a case. Doing both double-pays creditors and the second
   payment has no source.
3. **Posting LP commitments to the ledger.** A commitment is a promise. Post it and you have
   created money that does not exist; the first LP default then destroys money that was never
   there, and V2 fails in a module nobody suspects.
4. **A rounding leak in a waterfall.** Five classes, pro rata within each, preferences, the
   "greater of" test, and a fund distribution on top. Every split is `allocate()`; the closing
   assertion stays in production code (F13).
5. **Paying a junior class while a senior class is short.** Absolute priority is the whole point
   of the waterfall; violating it makes recovery rates meaningless and hides the `asset_sale`
   abuse the classes exist to police.
6. **Creating the option pool post-money.** It then dilutes the new investor instead of the
   founders, every cap table is wrong by the pool, and A6 measures nothing.
7. **Moving only `new_shares × price` on the ledger.** The residual cents vanish and closure
   breaks by a few cents per round — small enough to survive review, fatal to V2.
8. **Assuming positive synergies.** `integration_synergy_bp` defaults to 0. A positive default
   makes "acquisitions improve productivity" a mechanism and deletes the question.
9. **Hard-coding an antitrust block.** It is a policy read from `RuntimeOverlay`. A hard rule
   turns degenerate monopoly (F4) from an interpretable outcome into a threshold artefact.
10. **Subjecting agents to balance-sheet insolvency (B2).** Every mortgaged household files,
    household leverage becomes impossible, and the bankruptcy rate is an artefact of a rule that
    `06 §10.2` explicitly excludes.
11. **A stay that does not cancel orders.** A stayed entity with live resting orders keeps
    trading, escrow keeps moving, and the estate changes value during liquidation.
12. **Liquidating without a buyer.** Every sale needs an in-world counterparty (Corollary L1a).
    "Sell to the market at book value" is a money leak; scrapping is correct and lowers recovery.
13. **Assuming a liquidation price instead of realising it.** The whole point of slicing market
    orders over sessions is that price impact is produced by the book. An assumed haircut makes
    fire-sale dynamics a parameter.
14. **Discharge that moves money.** A write-off moves nothing: the asset and the liability both
    disappear. Paying the creditor "the written-off amount from somewhere" is the classic leak.
15. **Removing the agent exemption.** Bankruptcy becomes indistinguishable from death, the
    post-bankruptcy trajectory (the interesting object) does not exist, and the credit flag has
    nothing to attach to.
16. **Parsing the thesis or the pitch deck.** `if "AI" in thesis` is the same class of error as
    branching on `Action.reasoning` and destroys the determinism boundary.
17. **Minting another chunk's kind.** 6030 dividends, 6001 founding, 8017 write-offs, 7002
    delisting, 7012 cancels. Call the owner's function; two registrations is silent corruption.
18. **Two resolvers claiming `ACCEPT_OFFER`.** §9.1. `DuplicateHandler` fires at registration,
    which is the good outcome — but only if you resolve the ownership question before merge.
19. **Letting a bankruptcy cascade halt the run.** > 3,000 bp of firms bankrupt in a quarter is
    a WARN (F10) and is genuinely interesting for A5 — but rule out a payroll-shortfall or
    interest-arithmetic bug before reporting it as a finding.
