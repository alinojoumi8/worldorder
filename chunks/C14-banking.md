# C14 — Banks, credit, central bank, treasury, monetary policy

**M2** · `polis/economy/banking.py` (+ `credit.py`, `central.py`, `fiscal.py` under the same package) · **Depends on:** C04, C05, C07, C10, **C11 (ledger)**, C12 (CPI for the Taylor rule) · **Blocks:** C13 (escrow accounts, policy rate), C15 (write-offs, bank failure), C18 (fiscal/monetary levers), C24b · **Size:** L

## 1. Context

Banks are where money is *created*. `06 §1.5` allows exactly two channels — central-bank
issuance of base money and commercial-bank credit — and this chunk owns both. Loan origination
(**E4**) simultaneously creates a deposit and a receivable: the bank does not need the money
first, which is what makes an endogenous credit cycle possible rather than decorative. It also
owns the entities that make cross-bank settlement real (reserves, the interbank market, the
discount window), the capital constraint that is the actual brake on credit expansion, and the
treasury as fiscal agent, because kinds 8060–8074 are money-movement events with ledger
transactions. **07 decides the number; 06 moves the money** — every policy parameter here
arrives through `RuntimeOverlay`, never from static config.

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/06-ECONOMY-SPEC.md` | **§7 banking and credit (primary source)**, **§11 government finance**, §1.3–1.6 (E1, E4, E5, E6, E8, E9, E10 — the leg patterns you will write most often), §1.5 money creation, §1.7 M-4/M-5, §2.1–2.3, §2.6, §12 M20–M25, §13.1–13.2 genesis, §14 F1/F2/F10/F12, §16.2 steps 8–12 and 16–19 |
| `../docs/03-DATA-MODEL.md` | §0, **§4 ledger**, §7 `banks`, `loans`, `loan_payments`, §6 `securities` (bonds) |
| `../docs/02-ARCHITECTURE.md` | §3.2 kinds, §4 determinism, §5.1 slot 6, §8 routing table, §8.1 MECHANISM, §9 invariants |
| `chunks/C10-actions.md` | §5 `InstitutionResolver`, the six banking params models, §9.2 gates |
| `chunks/C11-labour-firms.md` | `Ledger`, `Leg`, `account_id`, `transfer`, `issue_base_money`, `money.*`, `RuntimeOverlay`, `CommitmentLedger` |
| Chunks | C05 (`LlmRouter`, structured output, `StubProvider`) for the `CREDIT_EVAL` purpose; C12 (`cpi_bp`, `inflation_yoy_bp`); C13 (bond auction venue) |

## 3. Scope — in

1. **Bank balance sheets**: `banks` rows, the asset/liability layout of `06 §7.2`,
   `capital(B) = net_worth(B) + mark-to-market of holdings`, and the M-6 reconciliation.
2. Accounts and deposits: `OPEN_ACCOUNT`, `DEPOSIT`, `WITHDRAW`, vault cash vs reserves, the
   liquidity limit, the seeded withdrawal service order, `WITHDRAWAL_REFUSED`, and
   `BANK_RUN_DETECTED` as an **observation**, never as a scripted run.
3. **Credit creation as inside money**: origination (E4), the reserve requirement, and the
   capital constraint that actually binds.
4. **Underwriting and credit scoring** from simulation state (`06 §7.4` scorecard), plus the
   optional `CREDIT_EVAL` LLM path behind `banking.underwriting: llm`.
5. Interest accrual (daily, memo-only), level-payment amortisation (monthly), `INV-INTEREST`.
6. Delinquency → default → collateral seizure → write-off (E8b), with `DEFAULT` available as a
   first-class strategic action.
7. Bank capital ratios, RWA, undercapitalisation, failure and resolution (`assume` /
   `liquidate`), deposit insurance and the bail-in haircut (E10).
8. Interbank lending and refusal; the discount window and the reserve overdraft conversion.
9. The **central bank**: `bk_cb`, `iss:bk_cb`, genesis issuance, open-market operations, the
   policy rate (`taylor` / `fixed` / `political`), the reserve requirement.
10. **Treasury as fiscal agent**: tax assessment and collection, arrears-as-loans, transfers and
    spending, bond issuance and the auction hand-off to C13, coupons, maturity, budget close.
11. `BankingResolver` (slot 6) and PHASE 7 steps 8–12 and 16–19.
12. `INV-INTEREST` registration and the banking half of M-4/M-5.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `post_transaction`, account ids, `allocate` | **C11** — but you own `issue_base_money`'s only legitimate call site |
| Payroll withholding arithmetic, the E1 legs | **C11** — you own the treasury's side and the arrears machinery |
| Sales tax at point of sale, CPI construction | **C12** — you consume `cpi_bp`/`inflation_yoy_bp` |
| The order book, the bond auction *venue*, mark-to-market prices | **C13** |
| Bankruptcy cases, the priority waterfall, the automatic stay | **C15** — you expose `write_off_loan` and `register_claims_for` |
| Deciding to borrow, to default, or to run on a bank | **C09** / **C07** / C11's `MechanicalPolicy` |
| Setting policy *values* (tax rates, the rate mandate) | **C18** via `POLICY_ENACTED` (12030) into `RuntimeOverlay` |

## 5. Interfaces you provide

```python
# polis/economy/banking.py
@dataclass(slots=True)
class Bank:
    bank_id: str; name: str; place_id: str; ledger_account_id: str; reserve_account_id: str
    capital_cents: int; reserve_ratio_bp: int; is_central: bool; status: str
    founded_tick: int; failed_tick: int | None

def capital_cents(bank_id: str, ctx: BankContext) -> int:
    """net_worth(B) + Σ mark-to-market of holdings[B, *]. The mark is a real-asset valuation and
    posts NO ledger leg (Rule L1), so a mark-down cuts capital without touching M0 or M1."""
def deposits_cents(bank_id: str, ctx) -> int    # −balance(dpl:B)
def reserves_cents(bank_id: str, ctx) -> int    # balance(res:B)
def rwa_cents(bank_id: str, ctx) -> int         # 06 §7.7 risk weights
def capital_ratio_bp(bank_id: str, ctx) -> int
def ldr_bp(bank_id: str, ctx) -> int; def npl_bp(bank_id: str, ctx) -> int
def open_customer_account(owner_id: str, owner_type: str, bank_id: str, tick: int,
                          ctx: ResolutionContext) -> Sequence[NewEvent]:
    """Creates dep:<owner>@<bank> (and esc: on first order, called by C13). Emits 8002."""
def deposit(owner_id: str, bank_id: str, cents: int, tick: int, ctx) -> Sequence[NewEvent]
def withdraw(owner_id: str, bank_id: str, cents: int, tick: int, ctx) -> Sequence[NewEvent]:
    """Serves only up to cash:B + res:B. Requests ordered by
    rng.get('banking.queue', bank_id, tick) — never by agent_id. Unserved -> 8006."""

# polis/economy/credit.py
@dataclass(frozen=True, slots=True)
class LoanRequest:
    borrower_id: str; lender_id: str; principal_cents: int
    purpose: Literal["consumer","mortgage","corporate","interbank","sovereign","tax_arrears"]
    term_ticks: int; collateral: Mapping[str, Any]; collateral_value_cents: int
    stated_purpose_text: str | None          # free text; NEVER parsed, only shown to CREDIT_EVAL

@dataclass(frozen=True, slots=True)
class BorrowerState:
    employed_ticks_last_year: int; annual_income_cents: int
    annual_debt_service_cents: int; on_time_payments: int; total_payments: int
    total_debt_cents: int; total_assets_cents: int
    bankruptcy_flag: bool; delinquency_flag: bool

def credit_score_bp(b: BorrowerState, req: LoanRequest,
                    mkt: MarketState) -> tuple[int, Mapping[str, int]]:
    """06 §7.4 verbatim. Integer. Returns (score, components) — components go into 8012."""
def decide(req: LoanRequest, b: BorrowerState, bank: Bank, tick: int,
           ctx: CreditContext) -> LoanDecision:
    """Scorecard by default. banking.underwriting == 'llm' routes an LLM CREDIT_EVAL call
    with the same state plus stated_purpose_text and the scorecard output as a reference."""

@dataclass(frozen=True, slots=True)
class LoanDecision:
    approved: bool; score_bp: int; components: Mapping[str, int]
    offered_cents: int; annual_rate_bp: int; term_ticks: int
    reason_codes: tuple[str, ...]; llm_call_id: UUID | None

def originate(req: LoanRequest, d: LoanDecision, tick: int,
              ctx: ResolutionContext) -> Sequence[NewEvent]:
    """E4 legs. lnr:<lender>#<loan_id> +, dpl:<lender> −, dep:<borrower>@<lender> +,
    lnp:<borrower>#<loan_id> −. M1 rises by the principal; M0 unchanged. Emits 8010."""
def schedule(principal_cents: int, annual_rate_bp: int, term_ticks: int,
             interval_ticks: int) -> tuple[Payment, ...]:
    """Level-payment annuity, MONEY_CTX, ceil on payment so it amortises to exactly zero."""
def accrue_interest(tick: int, ctx) -> Sequence[NewEvent]                # step 8, daily
def amortise(tick: int, ctx) -> Sequence[NewEvent]                       # step 9, monthly
def pay_deposit_interest(tick: int, ctx) -> Sequence[NewEvent]           # step 10
def mark_delinquent(loan_id: str, tick: int, ctx) -> Sequence[NewEvent]  # E9, 8015
def mark_default(loan_id: str, trigger: str, tick: int, ctx) -> Sequence[NewEvent]   # 8016
def seize_collateral(loan_id: str, tick: int, ctx) -> Sequence[NewEvent]            # 8019
def write_off_loan(loan_id: str, amount_cents: int, recovery_cents: int, tick: int,
                   ctx: ResolutionContext) -> Sequence[NewEvent]:
    """E8b. No money moves; the asset and the liability both vanish; bank capital falls.
    C15's discharge path calls THIS — it must not mint 8017 itself."""

# polis/economy/central.py
def issue_genesis_money(tick: int, ctx) -> Sequence[NewEvent]                # 8032, §13.2
def open_market_operation(direction: Literal["inject","drain"], amount_cents: int,
                          bank_id: str, tick: int, ctx) -> Sequence[NewEvent]        # 8034
def set_policy_rate(tick: int, ctx) -> Sequence[NewEvent]:
    """PHASE 7 step 19, LAST. taylor | fixed | political. New rate applies from tick+1."""
def discount_window(bank_id: str, shortfall_cents: int, tick: int, ctx) -> Sequence[NewEvent]
def settle_banks(tick: int, ctx) -> Sequence[NewEvent]:
    """PHASE 7 step 12: reserve requirement -> interbank -> discount window -> ratios ->
    failure. Runs after ALL customer money has moved."""
def resolve_failure(bank_id: str, tick: int, ctx) -> Sequence[NewEvent]     # 8053, §7.11

# polis/economy/fiscal.py
def assess_taxes(tick: int, ctx) -> Sequence[NewEvent]                      # 8070, step 16
def collect_taxes(tick: int, ctx) -> Sequence[NewEvent]                     # 8071
def convert_arrears(tick: int, ctx) -> Sequence[NewEvent]:
    """Unpaid assessment -> a loan with lender gv_treasury on txr/lnp. 8072."""
def pay_transfers(tick: int, ctx) -> Sequence[NewEvent]                     # 8073, step 17
def issue_bond(tick: int, ctx) -> Sequence[NewEvent]                        # 8060, step 18
def pay_coupons(tick: int, ctx) -> Sequence[NewEvent]; def mature_bonds(tick, ctx) -> ...
def close_budget(tick: int, ctx) -> Sequence[NewEvent]                      # 8074

# polis/economy/banking_resolver.py
class BankingResolver:                                     # implements InstitutionResolver
    slot:    Final = InstitutionSlot.BANKING               # 6
    handles: Final = frozenset({ActionType.OPEN_ACCOUNT, ActionType.DEPOSIT,
        ActionType.WITHDRAW, ActionType.APPLY_FOR_LOAN, ActionType.REPAY_LOAN,
        ActionType.DEFAULT})
    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        """Adult or firm; borrower is not under an automatic stay; the bank is solvent and,
        for APPLY_FOR_LOAN, not lending-frozen (8051)."""
    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult:
        """OPEN_ACCOUNT/DEPOSIT/WITHDRAW require a `bank` place; loans are remote_ok."""
    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        """DEPOSIT <= cash; WITHDRAW <= dep; REPAY_LOAN <= liquid via CommitmentLedger."""
    def resolve(self, actions: Sequence[ValidatedAction], tick: int,
                ctx: ResolutionContext) -> Sequence[Event]: ...
    def options_for(self, t: ActionType, ctx: ValidationContext
                    ) -> tuple[Mapping[str, Any], ...]: ...     # banks in the district

def check_interest(loan_id: str, ctx) -> Result                 # INV-INTEREST at loan close
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C11 | `Ledger.post_transaction`, `transfer`, `issue_base_money`, `account_id`, `close_account`, `CommitmentLedger` | every leg |
| C11 | `money.bp`, `bp_ceil`, `allocate`, `MONEY_CTX` | rates, annuities, haircuts, coupon splits |
| C11 | `RuntimeOverlay` — `tax.*`, `policy.rate_bp`, `spend.*`, `banking.reserve_ratio_bp`, `banking.capital_ratio_min_bp` | **all** policy values |
| C11 | `labour.open_benefit_claim` | you post the transfer; C11 mints 5080/5081 |
| C12 | `cpi_bp`, `inflation_yoy_bp` | the Taylor rule's inflation term |
| C13 | bond auction venue, `last_price_cents` for mark-to-market | issuance, OMO, capital |
| C05 | `LlmRouter.complete(purpose="CREDIT_EVAL", …)`, structured output, `StubProvider` | the optional underwriting path |
| C04 | `Clock`, `Scheduler`, `RngRegistry.get("banking.queue"/"banking.interbank", …)`, `stable`, `@mechanism` | cadence, ordering, ablation |
| C10 | `InstitutionResolver`, the six banking params models | the boundary |

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `banks` | W | `capital_cents` (denormalised, M-6), `reserve_ratio`, `status`, `failed_tick` |
| `loans` | W | `outstanding_cents`, `status`, `credit_score_at_origination`, `collateral` |
| `loan_payments` | W | one row per instalment incl. `missed` |
| `securities` | W (bonds) | `class='bond'`, issuer `gv_treasury` or a firm; C13 owns the venue |
| `holdings` | R/W (bank book) | bond and equity holdings; mark-to-market only, never a ledger leg |
| `ledger_*` | via C11 only | you are the only legitimate caller of `issue_base_money` |
| `agents`, `firms` | R | income, employment, assets for the scorecard |
| `metrics` | W | M20–M25 (`credit_growth_yoy`, `credit_to_gdp`, `default_rate`, `bank_capital_ratio`, `m0`/`m1`/`velocity`, rates) |

## 8. Event kinds owned

**Range: 8000–8999.** Payloads exactly as `06 §7.1`.

| Block | Kinds |
|---|---|
| Banks and accounts | `8001 BANK_FOUNDED`, `8002 ACCOUNT_OPENED`, `8003 ACCOUNT_CLOSED`, `8004 DEPOSIT_MADE`, `8005 WITHDRAWAL_MADE`, `8006 WITHDRAWAL_REFUSED` |
| Credit | `8010 LOAN_ORIGINATED`, `8011 LOAN_APPLICATION_SUBMITTED`, `8012 LOAN_APPLICATION_DECIDED`, `8013 LOAN_PAYMENT_MADE`, `8014 LOAN_PAYMENT_MISSED`, `8015 LOAN_DELINQUENT`, `8016 LOAN_DEFAULTED`, `8017 LOAN_WRITTEN_OFF`, `8018 LOAN_REPAID`, `8019 COLLATERAL_SEIZED`, `8020 INTEREST_ACCRUED`, `8021 DEPOSIT_INTEREST_PAID` |
| Monetary | `8030 POLICY_RATE_SET`, `8031 RESERVE_REQUIREMENT_SET`, `8032 MONEY_ISSUED`, `8033 MONEY_WITHDRAWN`, `8034 OPEN_MARKET_OPERATION` |
| Interbank | `8040 INTERBANK_LOAN`, `8041 DISCOUNT_WINDOW_BORROWED`, `8042 INTERBANK_REFUSED` |
| Soundness | `8050 BANK_RATIOS_COMPUTED`, `8051 BANK_UNDERCAPITALISED`, `8052 BANK_RUN_DETECTED`, `8053 BANK_FAILED`, `8054 DEPOSIT_INSURANCE_PAID`, `8055 DEPOSIT_HAIRCUT` |
| Treasury finance | `8060 BOND_ISSUED`, `8061 BOND_AUCTION_CLEARED`, `8062 BOND_AUCTION_FAILED`, `8063 COUPON_PAID`, `8064 BOND_MATURED`, `8070 TAX_ASSESSED`, `8071 TAX_COLLECTED`, `8072 TAX_ARREARS`, `8073 TRANSFER_PAID`, `8074 GOV_BUDGET_CLOSED` |

**Boundary.** Policy *parameter changes* are `POLICY_ENACTED` (12030), owned by C18. C14 never
emits a 12000-range kind and never writes `policies`. Benefit-claim kinds 5080/5081 are C11's:
call `labour.open_benefit_claim` and post the transfer yourself.

## 9. Implementation notes

**9.1 The two money channels, and no third.** `iss:bk_cb` may appear on a leg only from
`Ledger.issue_base_money`, and this chunk is its only caller: genesis (`06 §13.2`), open-market
operations, discount-window lending. Everything else that "creates money" is E4 — inside money, a
deposit and a receivable created together, M0 unchanged. Ship a test that greps the repo for the
literal `"iss:"` and asserts it appears only in `ledger.py` and `central.py`.

**9.2 The identities you must keep true every tick.**

```
M0 = Σ balance(cash:*) + Σ balance(res:*) + balance(dep:gv_treasury@bk_cb) = −balance(iss:bk_cb)
∀ commercial B:  Σ balance(dep:*@B) + Σ balance(esc:*@B) + balance(dpl:B) == 0      (M-5)
```

M-5 localises a bug: a break means a deposit moved without its bank leg. Every cross-bank
`transfer` settles in reserves; every payment *to* the government is the four-leg E6 shape,
because the central bank has no `dpl`.

**9.3 Interest accrual is a memo.** Daily accrual (`floor(outstanding × rate_bp / 10_000 / 360)`)
emits `8020` and touches **no** ledger account, becoming a ledger event only when paid (E5) or
capitalised on delinquency (E9). Keeping accrual off-ledger is what makes closure trivially true
for the most frequent operation in the system.

**9.4 Amortisation.** Level-payment annuity in `MONEY_CTX`; `ceil` on the payment so the loan
amortises to exactly zero (a `floor` leaves a residual cent that never clears and eventually trips
`INV-INTEREST`). Final payment is balloon-adjusted (`principal = outstanding`); interest is
`floor(outstanding × r)`. `INV-INTEREST` at close: `Σ interest legs == Σ scheduled − forgiven`; a
mismatch is **F12** and HALTs.

**9.5 E9 is the sign-error trap, kept in the spec on purpose.** Capitalising interest on a
delinquent loan debits `lnr` **and credits `lnp`** — `lnp` is a liability, so crediting it
increases the debt. The plausible-looking version (two debits) sums to `+2×amount` and drives
`lnp` positive, which P6 catches. Do not silence P6.

**9.6 Underwriting.** `credit_score_bp` is `06 §7.4` verbatim, integer, with Laplace smoothing in
`history` so a first-time borrower scores a neutral 5,000 rather than 0 — without that, credit
never starts (`06 §13.3`). Approval also requires the bank's capital and reserve constraints and
single-borrower concentration ≤ 2,500 bp of capital; the rate is
`policy_rate + base_spread + risk_spread + term_premium` with
`risk_spread_bp = k × (10_000 − score)² // 10_000²`.
`@mechanism("credit_scoring", entails=…)` carries the `06 §7.4` string verbatim, which says
plainly that **any cross-sectional finding that the unemployed, indebted or previously bankrupt
are denied credit or charged more is implied and is not a result**; credit volume, its clustering,
and shock amplification through the capital constraint are outcomes.

**9.7 `CREDIT_EVAL`.** The ratified LLM purpose. Structured output
`{approve: bool, rate_view_bp: int, amount_view_cents: int, concerns: [str]}`; prompt inputs are
the same simulation state as the scorecard plus the borrower's free-text stated purpose, with the
scorecard's score and components supplied as a reference. `llm_call_id` goes into `8012`. It is
**off by default** — M2 must not depend on it — and exists so `banking.underwriting: llm` is
sweepable against the scorecard. The prompt never says "you are an AI" and never names a
provider (`04 §13`). All tests use `StubProvider`.

**9.8 PHASE 7 step 12, in this exact internal order.** Reserve requirement → interbank market →
discount window → ratio computation → failure resolution. It runs at 23:00, **after all customer
money has moved**, and must restore every `res:B` to non-negative before PHASE 9 runs `INV-MONEY`
— `res:<bank>` is one of only two accounts ever permitted in `allow_negative`, and the overdraft
becomes a discount-window loan in the same step. Interbank ordering: `shorts` by
`(−shortfall, bank_id)`, `longs` by `(−excess, bank_id)`, ties broken by
`rng.get("banking.interbank", bank_id, tick)`. `@mechanism("banking.interbank_refusal", entails=…)`:
a contagion channel is **assumed**, not discovered; whether contagion propagates, how far and
whether it clusters are outcomes.

**9.9 Bank runs are not scripted.** No rule says depositors run when a withdrawal is refused.
`WITHDRAWAL_REFUSED` (8006) is a visible event entering the refused agent's perception and, through
C16/C17, other agents'; whether that produces a run is an LLM outcome. `BANK_RUN_DETECTED` (8052)
is a *detector emitted after the fact*, never a trigger. Scripting the run would convert the single
most interesting emergent phenomenon available into a mechanism.

**9.10 Failure and resolution.** Triggered at `capital(B) < 0` in step 12. `assume` (default):
insurance covers each depositor to `insurance_cap_cents` from the treasury; the uninsured excess is
haircut by `allocate()` in proportion (E10); performing loans transfer one balanced transaction per
loan with the borrower's `lnp` **untouched** (the obligor does not change, the creditor does);
non-performing loans are written off first. `liquidate`: loans sold at `fire_sale_bp`, deposits
paid to the extent of realised assets plus insurance, residual haircut; if no solvent bank exists
the central bank assumes the book at the fire-sale price — a **logged, reportable intervention**,
not a silent bailout.

**9.11 The policy rate is set LAST (step 19).** A new rate applies from the following tick and never
mid-tick, or the same tick's loans price at two rates depending on resolution order.
`@mechanism("banking.policy_rate_rule", entails=…)`: under `taylor`, a correlation between the
policy rate and inflation or output **is not a finding**; A4 should prefer `fixed` plus an injected
rate shock (`SHOCK_INJECTED`, 99001).

**9.12 Taxes are cash-basis.** `TAX_ASSESSED` (8070) writes a projection only; only `TAX_COLLECTED`
(8071) posts, so an assessment can never create money. Unpaid at `due_tick` → convert to a loan on
`txr`/`lnp` at `tax.arrears_penalty_bp`, giving tax debt the same delinquency, default and class-3
bankruptcy priority as any other claim at no extra machinery. Every spending programme is a
`transfer` with an in-world recipient; government spending with no recipient is forbidden and
`post_transaction` cannot express it. `@mechanism("spend.unemployment_benefit", entails=…)`:
unemployed agents receive positive income, so aggregate consumption cannot fall to zero with
unemployment — this **damps any deflationary spiral by construction**, and the
`benefit_replacement_bp: 0` ablation is part of any downturn-depth claim.

**9.13 Deficit financing.** `dep:gv_treasury@bk_cb` is the second `allow_negative` account; the
intra-period overdraft is converted at step 18 into a bond issue. A failed auction is a real,
observable state: either the CB backstops (monetary financing, logged, **off by default**) or the
treasury cuts discretionary spending proportionally. Bank holdings of government bonds carry
`risk_weight_bp = 0`, so the sovereign–bank loop is available without being scripted.

## 10. Configuration keys

```yaml
banking:                                # reserve_ratio_bp and capital_ratio_min_bp are
  banks: 3                              # runtime-overridable by C18. + bk_cb; 06 §13.1
  reserve_ratio_bp: 1000 ; capital_ratio_min_bp: 800 ; capital_buffer_bp: 1050
  stress_score_bump_bp: 500 ; min_score_bp: 4500 ; concentration_bp: 2500
  interbank_min_ratio_bp: 900 ; interbank_spread_bp: 50 ; interbank_concentration_bp: 2500
  discount_penalty_bp: 200 ; deposit_rate_bp: 50 ; insurance_premium_bp: 5
  insurance_cap_months: 6 ; fire_sale_bp: 7000 ; policy_review_interval: 6w
  cb_backstop: false                    # true == monetary financing, logged as such
  resolution: assume                    # assume | liquidate
  underwriting: scorecard               # scorecard | llm  (CREDIT_EVAL)
  policy_rate_rule: taylor              # taylor | fixed | political
  taylor: {neutral_bp: 250, target_bp: 200, phi_pi: 15000, phi_y: 5000, bounds_bp: [0, 4000]}
credit:
  risk_spread_k: 6000 ; base_spread_bp: 150 ; term_premium_bp_per_year: 25
  max_loan_income_multiple_bp: 40000 ; payment_interval: 1mo ; grace: 14d
  delinquency_days: 30 ; default_days: 90 ; delinquency_penalty_bp: 300 ; writeoff_after: 180d
  max_term: {consumer: 3y, mortgage: 25y, corporate: 7y}
  risk_weight_bp: {sovereign: 0, mortgage: 5000, corporate_secured: 7500,
                   corporate_unsecured: 10000, consumer: 10000, interbank: 2000}
treasury: {floor_cents: 0, bond_denomination_cents: 100000, bond_terms: [1y, 5y, 10y],
           sovereign_spread_bp_table: {}, arrears_penalty_bp: 800}
mechanisms: {credit_scoring: linear_scorecard, banking_interbank_refusal: on,
  banking_policy_rate_rule: taylor, spend_unemployment_benefit: on,
  credit_supply: endogenous}            # | exogenous (A5 falsification ablation)
```

## 11. Acceptance criteria

1. `−balance(iss:bk_cb) == m0_cents` after genesis; M-4 holds every tick of a 5,000-tick stub run.
   M-5 holds for every commercial bank every tick, and a deliberately omitted `dpl` leg breaks it
   and HALTs at the right tick.
2. `"iss:"` appears in exactly two modules (`ledger.py`, `central.py`), by AST/grep test.
3. **E4 origination**: M1 rises by exactly the principal, M0 is unchanged, bank assets and
   liabilities both rise, bank capital and borrower net worth are both unchanged.
4. **E5 repayment**: M1 falls by the payment; bank capital rises by exactly the interest; the
   borrower's net worth falls by exactly the interest. No equity account is posted.
5. **E8b write-off**: no money moves, M0 and M1 are both unchanged, bank capital falls by the
   written-off amount, the debtor's net worth rises by the same amount.
6. **E9 capitalisation**: the two-debit version raises `LedgerError` via P6; the correct version
   sums to zero and increases the debt. **E10 haircut**: M1 falls by the haircut, the depositor's
   net worth falls, the failed bank's negative capital moves toward zero.
7. An amortisation schedule for random `(principal, rate, term)` sums principal payments to exactly
   the principal (property test), and `INV-INTEREST` holds at every loan close.
8. A first-time borrower with no history scores 5,000 bp on the `history` component (Laplace), so
   credit can start from a zero-loan genesis.
9. A loan is refused when the bank is below `capital_ratio_min_bp` (`8051`, lending frozen), when
   concentration would exceed the cap, and when the score is below `min_score_bp` — each with a
   distinct reason code in `8012`.
10. `WITHDRAW` beyond `cash:B + res:B` emits `8006` for the unserved requests in a seeded order;
    renaming agents does not change who is served.
11. `BANK_RUN_DETECTED` is emitted only as a consequence of refusals, and no code path makes an
    agent withdraw. A grep for scripted run behaviour returns nothing.
12. Step 12 restores every `res:B` to `>= 0` before PHASE 9; an intraday overdraft becomes a
    discount-window loan in the same step.
13. Interbank refusal fires on the capital-ratio and concentration conditions with the right reason
    codes; ties broken by RNG, not `bank_id` alone.
14. Bank failure under `assume` transfers performing loans with the borrower's `lnp` untouched,
    writes off NPLs first, pays insurance from the treasury, and haircuts the uninsured excess by
    `allocate()` — with `INV-MONEY` holding at every intermediate tick.
15. A write-off cascade driving a second bank's capital negative resolves without leaking a cent
    (the M2 V2 gate case).
16. The policy rate is set in step 19 and takes effect at tick+1; no loan originated in the same
    tick uses the new rate.
17. Taxes: assessment posts nothing; collection posts a balanced transaction; an unpaid assessment
    converts to a `txr`/`lnp` loan and appears as a class-3 claim to C15. Every transfer programme
    has a named in-world recipient and a transfer with no recipient is inexpressible.
18. A failed bond auction takes the documented path and does not silently monetise unless
    `cb_backstop: true`, which logs `8032` with `purpose='omo'`.
19. `banking.underwriting: llm` runs end to end against `StubProvider` with zero network calls and
    records `llm_call_id` in `8012`; the scorecard path is unchanged.
20. Determinism: same seed twice → identical 8000-range events, `loans` and `loan_payments`;
    `mypy --strict`, `ruff`, import-linter pass.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/banking/test_money_creation.py` | E4/E5 leg-for-leg; M0 and M1 deltas; capital as a residual with no equity account |
| `tests/unit/banking/test_writeoff_and_haircut.py` | E8b and E10 leg-for-leg; capital and net-worth effects; M0/M1 unchanged on write-off |
| `tests/unit/banking/test_interest_capitalisation.py` | E9 — the wrong version raises via P6, the right version balances and increases the debt |
| `tests/property/test_amortisation_closes.py` | **Hypothesis.** For any `(principal, rate_bp, term, interval)`, `Σ principal payments == principal`, no negative payment, final balance exactly 0 |
| `tests/unit/banking/test_credit_score.py` | `06 §7.4` component-by-component against hand arithmetic; Laplace neutral first-timer; flags halve/quarter correctly; clamped |
| `tests/unit/banking/test_underwriting_decision.py` | Approval gates (score, capital, reserve, concentration); rate composition; amount caps; reason codes |
| `tests/unit/banking/test_credit_eval_llm.py` | `StubProvider` only; structured-output parse and repair; `llm_call_id` recorded; scorecard supplied as reference; no network |
| `tests/unit/banking/test_delinquency_default.py` | 30/90-day transitions; penalty rate; strategic `DEFAULT` action path; collateral seizure realised at the sale price, not the appraisal |
| `tests/unit/banking/test_withdrawal_queue.py` | Liquidity limit; seeded service order; agent renaming invariance; 8006 and 8052 emission |
| `tests/unit/banking/test_settlement_step12.py` | Internal order; reserve overdraft → discount window; `res:B >= 0` before PHASE 9; ratio computation |
| `tests/unit/banking/test_interbank.py` | Sorting; refusal conditions and reason codes; concentration cap; RNG tie-break |
| `tests/unit/banking/test_bank_failure.py` | `assume` and `liquidate`; NPLs written off first; `lnp` untouched on transfer; insurance and haircut arithmetic; employees fired with `firm_exit` |
| `tests/unit/banking/test_policy_rate.py` | Taylor arithmetic and clamps; `fixed` and `political` paths; effective at tick+1; emitted in step 19 |
| `tests/unit/banking/test_fiscal.py` | Cash-basis assessment vs collection; progressive brackets at boundary cents; arrears→loan conversion; every transfer has a recipient |
| `tests/unit/banking/test_bonds.py` | Issuance trigger at the treasury floor; auction hand-off; coupon `allocate` split; maturity; failed-auction path with and without `cb_backstop` |
| `tests/invariants/test_banking_invariants.py` | M-4, M-5 and `INV-INTEREST` over a 5,000-tick stub run with a scripted failure and a cascade |
| `tests/determinism/test_banking_determinism.py` | Same seed twice → identical events, loans, payments |
| `tests/integration/test_credit_cycle.py` | 50 agents, 800 ticks: loans originate, amortise, one defaults, capital falls, lending freezes, interbank refuses; INV-MONEY holds every tick |

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. Kinds 8000–8999 registered with payload schemas; the 12030 boundary documented so C18 knows
   it owns policy enactment and C14 owns the money.
2. `INV-INTEREST` registered with C04's `InvariantRunner`; M-4 and M-5 wired into C11's
   `INV-MONEY`.
3. Five `@mechanism` declarations (`credit_scoring`, `banking.interbank_refusal`,
   `banking.policy_rate_rule`, `spend.unemployment_benefit`, `credit_supply`) with the `06`
   `entails` strings verbatim; `credit_supply: exogenous` implemented as the A5 falsification
   ablation.
4. `CREDIT_EVAL` added to the model-routing table and the prompt manifest, off by default.
5. `write_off_loan` and a claim-registration hook exported for C15, with a note that C15 must
   not mint 8017.
6. Genesis issuance (`06 §13.2`) implemented as one transaction per recipient class and
   verified against `m0_cents`.
7. A handback note listing every `RuntimeOverlay` key this chunk reads, for C18.

## 14. Traps

1. **Requiring the bank to "have" the money before lending.** Debiting reserves at origination
   makes credit creation impossible, M1 stops responding to lending, and the credit cycle
   flatlines. E4 creates the deposit and the receivable together; that is the point.
2. **A sign error on `lnp` or `dpl`.** Both are liabilities. Crediting increases them. E9 is in
   the spec as a worked *wrong answer* because this is the most common ledger bug in the system.
3. **Posting accrued interest to the ledger daily.** It is a memo until paid or capitalised.
   Posting it creates an asset with no counterparty and V2 fails within a sim-week.
4. **`floor` on the annuity payment.** A residual cent survives forever, the loan never reaches
   `repaid`, and `INV-INTEREST` trips at close (F12). `ceil`, with a balloon-adjusted final.
5. **Scripting a bank run.** "If a withdrawal is refused, other depositors withdraw" turns the
   most interesting available emergent result into a mechanism and makes it unpublishable.
6. **Serving withdrawals in `agent_id` order.** Under a liquidity shortfall this decides who
   loses their money by alphabet. Seeded permutation, per `(bank, tick)`.
7. **Letting `res:B` stay negative into PHASE 9.** `INV-MONEY` HALTs. Step 12 must convert the
   overdraft to a discount-window loan in the same step, before metrics run.
8. **Adding a third money channel.** A "subsidy from nowhere", a "central bank grant", a
   rounding sink. `06 §1.5` allows exactly two; anything else is a leak wearing a policy name.
9. **Marking bank bond holdings with a ledger leg.** Marks are real-asset valuations (Rule L1); a
   mark-down must cut capital without touching the money supply.
10. **Transferring loans on failure by rewriting `lnp`.** The obligor does not change — only the
    creditor does. Rewriting the borrower's side either forgives or duplicates the debt.
11. **Paying deposit insurance from nowhere.** It comes from the treasury, funded by the premium.
    An insurance payment with no source account is the classic government-spending leak.
12. **Setting the policy rate anywhere but step 19.** A mid-tick change makes a loan's price depend
    on resolution order and corrupts every A4 impulse response.
13. **Reading tax rates or the policy rate from static config.** They arrive via `RuntimeOverlay`.
    Read them statically and C18 enacts policy the economy ignores — which presents as "fiscal and
    monetary policy have no effect", the most expensive possible false finding.
14. **Assessing a tax and treating it as collected.** Assessment posts nothing; conflating the two
    creates receipts that never arrived and money that never existed.
15. **A transfer with no recipient.** `post_transaction` cannot express it, so the bug always takes
    the form of somebody inventing a sink account. There is no outside (Corollary L1a).
16. **Emitting 5080/8017 from the wrong chunk.** 5080 is C11's; C15 must call your
    `write_off_loan` rather than minting 8017. Two owners of a kind is silent corruption.
17. **Making `CREDIT_EVAL` the default.** M2 must clear V2 with the mechanical scorecard; an
    LLM in the underwriting path makes the credit series irreproducible before the ledger has
    been proven, and it costs money on every loan application in a 1,000-agent city.
18. **Treating `BANK_UNDERCAPITALISED` as an error.** It is the credit brake working. Halting
    or auto-recapitalising deletes the contraction half of the credit cycle (F10, A5).
