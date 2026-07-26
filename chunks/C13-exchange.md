# C13 — Limit order book, matching, market data

**M3** · `polis/economy/exchange/` (`book.py`, `matching.py`, `orders.py`, `sessions.py`, `marketdata.py`, `shorts.py`, `ipo.py`, `resolver.py`) · **Depends on:** C04, C07, C10, **C11 (ledger)**, **C14 (banks, deposits, escrow, policy rate)** · **Blocks:** C15 (IPO exits, liquidation, mark-to-market), C24b · **Size:** L

## 1. Context

The exchange is the one institution in POLIS where resolution is explicitly **not**
order-independent: it is price–time priority, and time is defined by
`orders.submitted_seq`. That single decision (`03 §6`) makes matching deterministic and
replayable, and it creates two problems this chunk exists to solve — the sequence number does
not exist during PHASE 5, and PHASE 3 hands actions over sorted by `actor_id`, which would give
every alphabetically-early agent a permanent queue advantage and hand threat **T10** a
free exploit. The book is also where money and shares are most easily created or destroyed by
accident: escrow, partial fills, cancellations and short selling each have a release path, and
each release path is a place where cents or shares leak. Twelve property tests (`06 §6.12`) are
the acceptance bar, not a nice-to-have.

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/06-ECONOMY-SPEC.md` | **§6 exchange (primary source, all of it)**, §1.6 E3a/E3b/E3c (the trade legs), §2.1–2.3 arithmetic, §2.5 RNG namespaces, §2.6 invariants, §4.9 fair value, §10.4 automatic stay, §11.5 bond auctions, §12 M18/M19, §14 F5/F8/F9/F19/F20, §16.1 slot 5, §16.2 step 11 |
| `../docs/03-DATA-MODEL.md` | §0, **§6 `securities`, `holdings`, `orders`, `trades`, `ohlcv`**, §7 `cap_table` |
| `../docs/02-ARCHITECTURE.md` | §3.2 kinds, §4.1–4.3 determinism, §5.1 slot order, §6.3 action budget, §9 invariants, §11 performance (matching is the PHASE 5 hot spot) |
| `chunks/C10-actions.md` | §5 `InstitutionResolver`, `SubmitOrderParams`, §9.2 gates, §9.6 dispatch |
| `chunks/C11-labour-firms.md` | `Ledger`, `Leg`, `account_id`, `money.allocate/bp/bp_ceil/round_to_tick`, `CommitmentLedger`, `RuntimeOverlay` |
| `chunks/C14-banking.md` | `esc:` account creation, `bank_of`, policy rate for the discount/ERP inputs |

## 3. Scope — in

1. `Book` — two price-ordered sides with `(−price, submitted_seq)` bids and
   `(price, submitted_seq)` asks, O(log n) insert/pop, deterministic iteration.
2. **Arrival ordering**: the seeded per-`(symbol, tick)` permutation that assigns
   `arrival_ordinal`, the emission-order guarantee, and the `submitted_seq` binding.
3. Order admission (the seven checks of `06 §6.3`), limit and market semantics, day-order TTL,
   partial fills, and the `open → partial → {filled, cancelled, expired}` state machine.
4. **Reservation**: `dep → esc` on buy, `holdings.reserved_qty` on sell, exact release on
   cancel/expiry/partial residual, and the `INV-ORDERS` implementation.
5. `CANCEL_ORDER` semantics — **prior-tick orders only**.
6. Self-trade prevention, price bands and circuit breakers, halts and reopen auctions.
7. Continuous matching (`microscope`) and call auctions (open, close, reopen, and the sole
   mechanism under `chronicle`), including the uncrossing algorithm.
8. Execution and settlement: E3b legs, commissions to `fm_broker`, capital-gains tax,
   `holdings` and `cap_table` updates, `trades` rows.
9. Market data: OHLCV, VWAP, the divisor-adjusted capitalisation-weighted index,
   `BOOK_SNAPSHOT` and its ephemeral twin 90700.
10. **Short selling** as negative `holdings.qty`: aggregate cap, initial and maintenance margin,
    margin calls as MANDATORY obligations, forced liquidation, borrow fees, recall.
11. **IPO**: `IPO_LIST`, book-building, pricing, allocation, settlement, lockup.
12. Bond listing and the uniform-price treasury auction *mechanics* (C14 owns the issuance
    decision and the coupon/maturity money).
13. `ExchangeResolver` (slot 5) and the PHASE 7 step 11 market close.
14. `INV-SHARES`, `INV-ORDERS`, `INV-BOOK`, `INV-SHORT`, `INV-CAPTABLE` registration.
15. The twelve property tests P1–P12 of `06 §6.12`.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `post_transaction`, account ids, `allocate` | **C11** |
| Opening `esc:` accounts, bank solvency, the policy rate, bond *issuance* decisions | **C14** |
| Who issues shares, cap tables at formation, dividends, M&A, the bankruptcy waterfall | **C15** (you provide the venue and the mark) |
| Deciding to trade, order sizing, price beliefs | **C09** / **C07** / C11's `MechanicalPolicy` (zero-intelligence trader) |
| Insider-trading classification | **C19**'s `LegalityOracle` — an insider order **validates and executes** (C10 §9.3) |
| Metric registration and export | **C24b** — you expose `last_price`, `index_bp`, `ohlcv` |

## 5. Interfaces you provide

```python
# polis/economy/exchange/orders.py
Side = Literal["buy", "sell"]
OrderType = Literal["limit", "market"]
OrderStatus = Literal["open", "partial", "filled", "cancelled", "expired"]

@dataclass(slots=True)
class Order:
    order_id: str; symbol: str; trader_id: str; side: Side; order_type: OrderType
    limit_price_cents: int | None; qty: int; filled_qty: int; status: OrderStatus
    submitted_tick: int
    arrival_ordinal: int                  # assigned in PHASE 5 by the seeded permutation
    submitted_seq: int                    # the seq of this order's 7010; monotone in ordinal
    reserved_cents: int                   # buy side
    reserved_qty: int                     # sell side
    flags: frozenset[str]                 # {"opens_short","ipo","auction"}
    @property
    def remaining(self) -> int: ...

# polis/economy/exchange/book.py
class Book:
    symbol: str
    def insert(self, o: Order) -> None; def pop(self, side: Side) -> Order
    def remove(self, order_id: str) -> Order
    def peek(self, side: Side) -> Order | None:
        """Best price, then lowest submitted_seq. A heap keyed (−p, seq) / (p, seq)."""
    def best_bid(self) -> int | None; def best_ask(self) -> int | None
    def depth(self, side: Side, levels: int = 5) -> tuple[tuple[int, int], ...]
    def resting(self) -> tuple[Order, ...]:
        """Deterministic: bids by (−price, seq), then asks by (price, seq)."""

# polis/economy/exchange/matching.py
@dataclass(frozen=True, slots=True)
class Fill:
    buy_order_id: str; sell_order_id: str; price_cents: int; qty: int; aggressor: Side

def on_aggressor(book: Book, o: Order, tick: int, ctx: ExchangeContext) -> Sequence[Fill]:
    """06 §6.5. Resting order sets the price; price improvement accrues to the aggressor.
    Self-trade -> cancel the resting order with initiator='stp'. Market orders never rest."""
def crosses(o: Order, best: Order) -> bool: ...
def uncross(orders: Sequence[Order], prev_close_cents: int) -> tuple[int, int]:
    """(clearing_price, executable_volume). Tie-break: volume, |imbalance|, |p − prev|, low p."""
def allocate_auction(orders: Sequence[Order], price: int, volume: int) -> Mapping[str, int]:
    """Price-time priority; the marginal level split by C11's allocate() on remaining qty,
    ordered by submitted_seq."""

# polis/economy/exchange/reservation.py
def reserve_buy(o: Order, ctx: ExchangeContext) -> list[Leg]:
    """qty × limit + ceil(commission) : dep -> esc  (E3a). Market buy uses band_upper."""
def reserve_sell(o: Order, ctx: ExchangeContext) -> None:
    """holdings.reserved_qty += qty. Asserts reserved_qty <= max(0, qty) afterwards."""
def release(o: Order, qty: int, ctx: ExchangeContext) -> list[Leg]:
    """Exactly qty × limit + commission(qty), never more (P12). esc -> dep (E3c)."""
def check_inv_orders(ctx: ExchangeContext) -> Result:      # 06 §6.6, HALT

# polis/economy/exchange/settlement.py
def execute(f: Fill, tick: int, ctx: ExchangeContext) -> Sequence[NewEvent]:
    """Atomic, six steps in the order of 06 §6.7: E3b legs (escrow -> seller, commissions to
    fm_broker, inter-bank via res:); holdings both sides incl. reserved_qty; cap_table mirror;
    capital-gains tax on the seller's realised gain; buyer avg_cost; trades row + 7020."""
def commission_cents(px: int, qty: int, ctx: ExchangeContext) -> int:
    """max(commission_floor_cents, bp_ceil(px × qty, commission_bp))."""

# polis/economy/exchange/sessions.py
class SessionManager:
    def is_open(self, tick: int) -> bool
    def phase(self, tick: int) -> Literal["closed","open_auction","continuous","close_auction"]
    def open_session(self, tick: int, ctx: ExchangeContext) -> Sequence[NewEvent]      # 7003
    def close_session(self, tick: int, ctx: ExchangeContext) -> Sequence[NewEvent]:
        """7004 + expire every unfilled remainder (day orders) + OHLCV + index. PHASE 7 step 11."""

# polis/economy/exchange/marketdata.py
def ohlcv_for(symbol: str, session_tick: int, ctx) -> Mapping[str, int | None]
def vwap_cents(trades: Sequence[TradeRow]) -> int | None           # Σ p×q // Σ q, integer floor
def last_price_cents(symbol: str) -> int | None
def index_bp(tick: int, ctx) -> int                                # divisor-adjusted, base 10_000
def rebase_divisor(mcap_before: int, mcap_after: int, ctx) -> None

# polis/economy/exchange/shorts.py
def open_short(a: ValidatedAction, tick: int, ctx) -> Sequence[NewEvent]              # 7060
def mark_and_call(tick: int, ctx) -> Sequence[NewEvent]:
    """Maintenance margin check -> 7063 MARGIN_CALL as a MANDATORY Obligation (06 §2.4)."""
def force_liquidate(trader_id: str, symbol: str, tick: int, ctx) -> Sequence[NewEvent] # 7064
def charge_borrow_fees(tick: int, ctx) -> Sequence[NewEvent]                           # 7062
def short_interest(symbol: str, ctx) -> int                        # Σ max(0, −qty)

# polis/economy/exchange/ipo.py
def announce(a: ValidatedAction, tick: int, ctx) -> Sequence[NewEvent]                 # 7070
def price_book(firm_id: str, tick: int, ctx) -> Sequence[NewEvent]                     # 7072
def settle_ipo(firm_id: str, tick: int, ctx) -> Sequence[NewEvent]                     # 7073
def list_security(symbol: str, issuer_firm_id: str, cls: str, shares: int,
                  tick: int, ctx) -> Sequence[NewEvent]                                # 7001
def delist(symbol: str, reason: str, tick: int, ctx) -> Sequence[NewEvent]             # 7002

# polis/economy/exchange/resolver.py
class ExchangeResolver:                                   # implements InstitutionResolver
    slot:    Final = InstitutionSlot.EXCHANGE             # 5
    handles: Final = frozenset({ActionType.SUBMIT_ORDER, ActionType.CANCEL_ORDER,
                                ActionType.SHORT, ActionType.IPO_LIST})
    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        """IPO_LIST eligibility (age, revenue, net worth, mandated underwriter); SHORT only
        when regulation.short_selling_allowed; no action under an automatic stay."""
    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult:
        """remote_ok — a brokerage account, not physical presence at mk_exchange."""
    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        """Fundable reservation against CommitmentLedger; holdable shares net of
        reserved_qty and lockup; short cap; per-tick order rate limit."""
    def resolve(self, actions: Sequence[ValidatedAction], tick: int,
                ctx: ResolutionContext) -> Sequence[Event]:
        """§9.2: cancels first, then the seeded arrival permutation, then matching."""
    def options_for(self, t: ActionType, ctx: ValidationContext
                    ) -> tuple[Mapping[str, Any], ...]:
        """Listed symbols with a quote. () for IPO_LIST."""

# polis/economy/exchange/invariants.py
def check_shares(ctx) -> Result; def check_orders(ctx) -> Result
def check_book(ctx) -> Result;   def check_short(ctx) -> Result
def check_captable(ctx) -> Result
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C11 | `Ledger.post_transaction`, `transfer`, `account_id`, `CommitmentLedger` | E3a/E3b/E3c |
| C11 | `money.allocate`, `bp`, `bp_ceil`, `round_to_tick`, `MONEY_CTX` | commissions, splits, the index divisor |
| C11 | `RuntimeOverlay.bp("tax.capgains_bp")`, `flag("regulation.short_selling_allowed")`, `bp("policy.rate_bp")` | tax, regulation flags, ERP inputs — never static config |
| C14 | `esc:` account provisioning, `bank_of`, `bk_cb` policy rate | reservations, discount rate |
| C15 | share issuance, cap tables, stay notifications | listings, forced cancels |
| C10 | `InstitutionResolver`, `SubmitOrderParams`, `ValidatedAction` | the boundary |
| C04 | `RngRegistry.get("exchange.arrival", symbol, tick)`, `.get("exchange.liquidation", case_id, tick)`, `Clock.hour_of_day`, `Scheduler`, `stable` | permutation, slicing, sessions |
| C07 | `Observation.market` (`MarketView`), `Obligation` | quotes into perception, margin calls |

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `securities` | W | listing, delisting, `shares_outstanding` |
| `holdings` | W | `qty` (may be **negative** — that is a short), `avg_cost_cents`, `reserved_qty` |
| `orders` | W | full lifecycle; `submitted_seq` written from the 7010 event's `seq` |
| `trades` | W | one row per fill, partitioned by run |
| `ohlcv` | W | keyed on the closing-auction tick |
| `cap_table` | W (mirror) | issuer `common` class only, to keep `INV-CAPTABLE` true |
| `ledger_*` | via C11 only | never directly |
| `metrics` | W | M18 `market_index`, M19 `price_fair_value_gap_bp` |

## 8. Event kinds owned

**Range: 7000–7999.** Payloads exactly as `06 §6.1`.

`7001 SECURITY_LISTED`, `7002 SECURITY_DELISTED`, `7003 SESSION_OPENED`, `7004 SESSION_CLOSED`,
`7010 ORDER_SUBMITTED`, `7011 ORDER_REJECTED`, `7012 ORDER_CANCELLED`, `7013 ORDER_EXPIRED`,
`7020 TRADE_EXECUTED`, `7021 ORDER_FILLED`, `7022 ORDER_PARTIALLY_FILLED`,
`7030 BOOK_SNAPSHOT` (ephemeral twin **90700**, never persisted), `7040 OHLCV_COMPUTED`,
`7041 INDEX_COMPUTED`, `7050 CIRCUIT_BREAKER_TRIGGERED`, `7051 TRADING_RESUMED`,
`7060 SHORT_OPENED`, `7061 SHORT_COVERED`, `7062 BORROW_FEE_CHARGED`, `7063 MARGIN_CALL`,
`7064 FORCED_LIQUIDATION`, `7070 IPO_ANNOUNCED`, `7071 IPO_INDICATION`, `7072 IPO_PRICED`,
`7073 IPO_COMPLETED`, `7080 BOND_LISTED`.

`7010` is emitted in **arrival order** and its `seq` becomes `orders.submitted_seq`. That is a
hard constraint on emission order, not a convention — see §9.1.

## 9. Implementation notes

**9.1 Arrival ordering — the reason this chunk exists.**

```
At the start of PHASE 5 slot 5, per symbol, in ascending symbol order:
    cancels     = [CANCEL_ORDER actions]                     # processed FIRST, §9.3
    new_orders  = [SUBMIT_ORDER / SHORT / IPO_INDICATION actions for this symbol this tick]
    perm        = rng.get("exchange.arrival", symbol, tick).permutation(len(new_orders))
    for ordinal, o in enumerate(apply(perm, new_orders)):
        o.arrival_ordinal = ordinal
        ev = log.stage(NewEvent(7010, …))                    # emission order == arrival order
        o.submitted_seq = ev.seq                             # monotone in arrival_ordinal
        admit_and_match(o)
```

Three things must hold or priority is broken: the permutation is seeded and symbol-and-tick scoped;
`7010` is emitted **before** any fill event for that order; and nothing else emits between two
`7010`s for the same symbol. Priority on `arrival_ordinal` is then identical to priority on
`submitted_seq`, which is what `03 §6` requires. `06 §1.4.1`'s worry about seqs not existing yet
does not apply — C02's `EventLog.stage()` returns a sealed `Event` with `seq` already bound
(C11 §9.2). **Never sort new orders by `actor_id`:** PHASE 3 hands them over in that order, and
using it gives `ag_aaron` permanent queue priority for the whole run — a systematic, exploitable,
invisible artefact and the cleanest possible instance of **T10**.

**9.2 Resolution order inside slot 5.** (1) every `CANCEL_ORDER` in `(actor_id, action_id)` order,
prior-tick orders only; (2) per symbol ascending, the arrival permutation of new orders; (3) per
order: admission checks → reservation → `on_aggressor` → rest or cancel; (4) `IPO_LIST` and
`IPO_INDICATION` after ordinary orders for that symbol.

**9.3 Cancellation semantics.** `CANCEL_ORDER` applies **only to orders resting from a previous
tick**; cancelling one submitted this tick is `ORDER_REJECTED{reason:'rate_limit'}` — not an error,
a rule. That removes same-tick quote-stuffing and priority-gaming as a family without a
minimum-resting-time rule (F5). Release is exact — `remaining × limit + commission(remaining)`, not
a cent more (**P12**) — and a cancel of an already-filled order is a no-op, never a double release.

**9.4 Reservation and INV-ORDERS.**

| Event | Cash | Shares |
|---|---|---|
| Buy accepted | `dep → esc`, `qty × limit + ceil(comm)` (E3a) | — |
| Sell accepted | — | `reserved_qty += qty` |
| Fill (buy) | `esc → counterparties` (E3b) | `qty += fill` |
| Fill (sell) | `→ dep` net of commission | `qty -= fill`, `reserved_qty -= fill` |
| Cancel / expiry / residual | `esc → dep`, exact remainder (E3c) | `reserved_qty -= remaining` |
| Death · automatic stay | cancel all, release all | as above |

`INV-ORDERS` (every tick, HALT) is the four clauses of `06 §6.6`. The cash side is `≤` because a
partial fill can leave rounding residue; the gap is bounded by the number of open orders **in
cents**, and a gap larger than that is **F19** — an orphaned escrow — and must HALT. Release
must be in the same transaction as the state change that caused it.

**9.5 Matching.** `06 §6.5` verbatim. Load-bearing: the **resting** order sets the price, so price
improvement accrues to the aggressor; self-trade prevention cancels the *resting* order
(`initiator='stp'`) and continues — it never cancels the aggressor and never prints; a print
outside the band triggers the breaker and **breaks** the loop rather than clamping the price;
market orders never rest and their remainder is cancelled with `market_unfilled`. Call auctions run
at open, at close and after a halt, all printing at one clearing price with the marginal level split
by `allocate()` on remaining quantity ordered by `submitted_seq`. Under `chronicle` there is **one
call auction per tick and no continuous phase**; OHLCV is degenerate (`o = h = l = c = p*`) and must
be flagged as such in the payload, not emitted as if it were a real range.

**9.6 Short selling keeps INV-SHARES exact.** A short is a **negative `holdings.qty`**: the
lender's holding is unchanged, the shorter's is negative, the buyer's is positive, so
`Σ qty == shares_outstanding` still holds with no synthetic shares anywhere. Do not model a
share-borrow inventory; the aggregate cap (`INV-SHORT`, 1,000 bp of shares outstanding)
substitutes. Initial margin 15,000 bp in `esc:trader@bank#margin`, maintenance 3,000 bp; a breach
emits `7063` as a **MANDATORY** force-routed obligation (`06 §2.4`) with a one-session deadline, and
a miss is a market buy for the full position at the next session with any shortfall becoming an
unsecured claim (C15 §10). Borrow fees are distributed across long holders by `allocate()`; recall
order is `(−|qty|, trader_id)`. Shorting is what makes a bubble contestable — without it price can
only be bid up and A3 degenerates into a study of buying pressure.

**9.7 Circuit breakers, and what they cost.** Per-symbol price band (`band_bp` 2,000) plus a short
halt (`halt_bp` 3,000, `halt_ticks` 2, `max_halts_per_session` 2, after which the band widens to
5,000). An order that would print outside the band is **rejected**; the market is not halted for a
rejection. `@mechanism("exchange.circuit_breakers", entails=…)` with the `06 §6.9` string verbatim:
single-session returns are bounded, therefore **any claim about tail risk, crash magnitude or
extreme return quantiles requires `enabled: false` or an explicit conditioning statement**. Bubble
duration and price-vs-fair-value divergence are unaffected because the band binds within a session.
Also declare `@mechanism("exchange.ipo_underpricing", entails=…)`: a positive first-day return is
implied by `underwriter_discount_bp` and is **not** an emergent finding.

**9.8 Market data.** OHLCV exactly as `06 §6.8`, with `open`/`close` taken by `min`/`max` on
`(tick, trade_id)` — never by insertion order into a list. Empty session: carry the previous close,
volume 0, `vwap = NULL` (not 0 — a zero VWAP silently poisons every downstream average). The index
is capitalisation-weighted with a continuity divisor rebased on any change to the constituent set or
shares outstanding, so listings and delistings do not create artificial jumps; analogue named
separately (T11) as an S&P-500-type float-adjusted cap-weighted index. `fair_value_cents`
(`06 §4.9`) is reported next to `last_price` for M19 and is **never** read by agent-facing code.

**9.9 IPO.** Seven steps, `06 §6.11`. Eligibility is a capability gate (age ≥ 2 sim-years, trailing
revenue, positive net worth, a solvent underwriter that accepted the mandate, fees paid).
Indications are limit orders flagged `ipo` with funds reserved as usual, **visible only to the
underwriter** — leaking the book into any other agent's observation is an information-model
violation (`04 §5` rule 4). Allocation at `offer` by price–time priority, marginal level by
`allocate()`. Settlement is **one transaction**: primary proceeds to the firm, secondary to selling
holders, underwriting fee to the bank, listing fee to the treasury. Lockup holds insiders' shares
as `reserved_qty` for `lockup_ticks`; orders against them are rejected with `reason='lockup'`.
Bonds list through the same table with `class='bond'` and trade on the same book; the treasury
auction is uniform-price (bids sorted descending, clearing at the lowest accepted, all winners
paying it, marginal level by `allocate()`). C14 decides *whether* to issue; C13 runs the auction.

**9.10 Performance.** `02 §11` budgets PHASE 5 at < 100 ms with matching the hot spot: heaps keyed
`(−price, seq)`, an `order_id → node` index for O(log n) cancels, no re-sorting of the book per
aggressor. Do not micro-optimise beyond that; the LLM call dominates the tick.

## 10. Configuration keys

```yaml
exchange:
  tick_size_cents: 1 ; commission_bp: 20 ; commission_floor_cents: 1
  max_order_qty_bp: 1000              # of shares outstanding
  session: {open_hour: 9, close_hour: 16, holidays: []}
  band_bp: 2000 ; halt_bp: 3000 ; halt_ticks: 2 ; max_halts_per_session: 2
  widened_band_bp: 5000 ; circuit_breakers: {enabled: true} ; book_snapshot_levels: 5
  shorts: {max_short_bp: 1000, initial_margin_bp: 15000, maintenance_margin_bp: 3000,
           borrow_fee_bp_per_year: 200}
  ipo: {min_age: 2y, min_revenue_cents: 0, book_ticks: 3d, underwriter_discount_bp: 500,
        uw_fee_bp: 300, lockup: 180d}
  index: {name: POLIS100, base_bp: 10000}
mechanisms: {exchange_circuit_breakers: on, exchange_ipo_underpricing: on}
```

## 11. Acceptance criteria

1. **P1–P12 of `06 §6.12` all pass as Hypothesis property tests over random order streams.**
   They are the acceptance bar for this chunk.
2. `best_bid < best_ask` after every match cycle (`INV-BOOK`);
   `Σ holdings.qty == securities.shares_outstanding` per symbol after every trade with shorts
   present (`INV-SHARES`); `reserved_qty ∈ [0, max(0, qty)]` and `balance(esc:*) >= 0` always.
3. Renaming every `actor_id` to a reverse-sorted alias produces an identical `trades` sequence:
   priority is a function of the seeded permutation, not of the name.
4. Two orders at the same price on the same side fill strictly in `submitted_seq` order.
5. `CANCEL_ORDER` on a same-tick order is rejected; on a prior-tick order it releases exactly
   `remaining × limit + commission(remaining)`.
6. A partially filled order later cancelled releases the residual once and only once; total
   released over the order's life equals total reserved minus total spent, to the cent.
7. No `trades` row has `buyer_id == seller_id`; a self-crossing stream produces STP cancels and
   zero prints.
8. With `commission_bp = 0` and `capgains_bp = 0`, `Σ_T (dep + esc)` is invariant across a session;
   every trade's transaction sums to zero and
   `Σ buyer paid == Σ seller received + Σ commission (+ capgains tax)`.
9. No print lies outside the band around its session reference while breakers are enabled — the
   band is checked on the **print**, not only on the order.
10. Market orders never rest; the unfilled remainder is cancelled with `market_unfilled`. Day
    orders: every remainder expires at the closing auction with its reservation released, so the
    book is empty at session open.
11. `Σ max(0, −qty) ≤ bp(shares_outstanding, max_short_bp)` at every tick, including through
    partial fills (`INV-SHORT`).
12. A margin breach emits `7063` as a MANDATORY obligation; a missed deadline force-liquidates at
    the next session and any shortfall is recorded as an unsecured claim.
13. IPO: allocation sums exactly to `shares_offered`; unallocated reservations released in full;
    settlement is one transaction; locked shares cannot be sold before `lockup_ticks`.
14. Under `chronicle`, exactly one call auction per tick, OHLCV flagged degenerate, VWAP equal to
    the auction price. An empty session carries the previous close, volume 0, `vwap = NULL`.
15. The index divisor is rebased on listing, delisting and share-count change; no jump appears in
    `INDEX` at any of the three.
16. An automatic stay (C15) cancels all of the entity's resting orders and releases everything;
    new orders are rejected with `reason='stay'`.
17. Replaying a recorded `(book, ordered aggressors)` stream produces a byte-identical `trades`
    sequence (**P7**).
18. `mypy --strict polis/economy/exchange`, `ruff`, import-linter pass; PHASE 5 slot 5 p50 under
    100 ms with 10 symbols and 200 orders/tick.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/property/test_exchange_properties.py` | **Hypothesis, mandatory. P1–P12 of `06 §6.12`, one test function each, named `test_p1_no_crossed_book` … `test_p12_exact_release`.** |
| `tests/unit/exchange/test_priority.py` | `(−price, seq)` / `(price, seq)` heap order; equal-price FIFO; agent renaming leaves fills unchanged |
| `tests/unit/exchange/test_arrival_permutation.py` | Permutation is seeded, symbol-and-tick scoped, stable across `actor_id` renames; 7010 emission order == arrival order; `submitted_seq` monotone in `arrival_ordinal` |
| `tests/unit/exchange/test_cancel_semantics.py` | Same-tick cancel rejected; prior-tick cancel releases exactly; cancel of a filled order is a no-op; double cancel does not double release |
| `tests/unit/exchange/test_reservation.py` | E3a/E3c legs; partial-fill residual release; `esc` never negative; orphaned-escrow detection (F19) |
| `tests/unit/exchange/test_matching_limit_market.py` | Crossing rules; resting order sets the price; price improvement to the aggressor; market never rests; partial fill state machine |
| `tests/unit/exchange/test_stp.py` | Self-cross cancels the resting order, continues matching, prints nothing; `self_cross_attempts` metric increments |
| `tests/unit/exchange/test_auction_uncross.py` | Clearing price and volume on hand-built books; the four tie-breakers in order; marginal-level split by `allocate` |
| `tests/unit/exchange/test_bands_breakers.py` | Band rejection vs halt; cumulative move; reopen auction sets a new reference; band widening after `max_halts_per_session` |
| `tests/unit/exchange/test_settlement_legs.py` | **E3b leg-for-leg**, same-bank and cross-bank; commission floor and bp; capgains on the seller only; `avg_cost` weighted average |
| `tests/unit/exchange/test_shorts.py` | Negative holdings keep `INV-SHARES`; aggregate cap on partial fills; margin call and forced liquidation; borrow-fee distribution sums exactly; recall order |
| `tests/unit/exchange/test_ipo.py` | Eligibility gates; book visible only to the underwriter; pricing and oversubscription; allocation sums to `shares_offered`; one settlement transaction; lockup rejection |
| `tests/unit/exchange/test_marketdata.py` | OHLCV from a synthetic trade stream; empty session; VWAP integer floor; index divisor continuity across list/delist/share change |
| `tests/unit/exchange/test_bond_auction.py` | Uniform price; lowest accepted clears; all winners pay it; marginal split by `allocate`; failed auction path |
| `tests/invariants/test_exchange_invariants.py` | `INV-SHARES`, `INV-ORDERS`, `INV-BOOK`, `INV-SHORT`, `INV-CAPTABLE` over a 2,000-tick stub session run |
| `tests/determinism/test_exchange_determinism.py` | Same seed twice → identical `trades`, `orders`, `ohlcv` and 7000-range events; **P7** replay equality |
| `tests/integration/test_ipo_to_trading.py` | Firm IPOs, shares list, agents trade, a short is opened and margin-called, session closes, index updates; INV-MONEY holds every tick |

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. Kinds 7000–7999 registered with payload schemas; `7030`'s ephemeral twin `90700` registered
   as non-persisted (`02 §3.2`).
2. `INV-SHARES`, `INV-ORDERS`, `INV-BOOK`, `INV-SHORT`, `INV-CAPTABLE` registered with C04's
   `InvariantRunner` at the frequencies of `06 §2.6`.
3. The twelve property tests exist under the names in §12 and are green in CI.
4. Two `@mechanism` declarations (`exchange.circuit_breakers`, `exchange.ipo_underpricing`)
   with the `06` `entails` strings verbatim, both ablatable.
5. `Book`, `SessionManager` and the reservation state implement C04's `Checkpointable`; a
   checkpoint/restore round-trip reproduces the book order for order.
6. A handback note for C15: how to force-cancel under a stay, how liquidation slicing consumes
   `rng.get("exchange.liquidation", case_id, tick)`, and the mark-to-market API.
7. The `commission_bp` / `tick_size_cents` calibration question (`06 §18` item 4) is recorded
   with whatever the smoke run showed.

## 14. Traps

1. **Priority by `actor_id`.** The most consequential single bug available in this chunk.
   PHASE 3 hands orders over sorted by actor, the naive loop preserves it, and every
   alphabetically-early agent front-runs the market for five sim-years. Seeded permutation,
   always, and a test that renames agents.
2. **Emitting `7010` after the fill events.** `submitted_seq` then does not match
   `arrival_ordinal`, priority silently diverges from the book, and replay disagrees with live.
3. **Clamping a print into the band instead of rejecting it.** You get a trade at a price nobody
   submitted, `last_price` becomes fiction, and it propagates into the index, collateral
   valuations, margin calls and bankruptcy estates.
4. **Releasing more than was reserved.** A partial-fill release computed from the original
   quantity instead of the remaining quantity mints money out of escrow. **P12** exists for
   exactly this and `INV-MONEY` will catch it a thousand ticks later, in a different module.
5. **Double release on cancel-after-fill.** The order is terminal; the reservation is already
   spent. Guard the state machine: `open → partial → {filled, cancelled, expired}` and nothing
   re-enters a terminal state (**P5**).
6. **Modelling a share-borrow inventory.** It creates synthetic shares, `INV-SHARES` breaks, and
   the fix is invasive. A short is a negative `holdings.qty` and nothing else.
7. **Self-trade prevention that cancels the aggressor.** The aggressor then loses its slot for
   the tick and an agent can grief a competitor by resting an order against them. Cancel the
   resting order, continue matching.
8. **Allowing same-tick cancels.** Reintroduces quote stuffing and priority gaming (F5), and
   because it looks like generous semantics nobody flags it in review.
9. **`vwap = 0` on an empty session**, or **forgetting the index divisor**. VWAP is `NULL`; a zero
   propagates into averages and turns a quiet market into a crash in every chart. Without a
   divisor rebase every listing and delisting is a step change in `INDEX`, and A3's "price
   diverges from fundamentals" becomes unmeasurable noise.
10. **Seeding a market maker to fix a quiet book.** `06 §13.3` forbids it: a scripted liquidity
    provider manufactures the microstructure being studied. If F8 (zero-trade equilibrium)
    appears, check `commission_bp`, `tick_size_cents`, whether any firm pays a dividend, and
    whether traders are being routed to DELIBERATE at all.
11. **Rejecting an insider order.** Legality flags, it never rejects (C10 §9.3). An insider
    `SUBMIT_ORDER` validates, executes, and is classified by C19 downstream; rejecting it deletes
    research question B5.
12. **Letting the IPO book leak.** Indications are visible only to the underwriter. Putting them
    in any other agent's observation is hidden-information leakage and invalidates the pricing.
13. **Iterating a `dict` of resting orders.** Insertion order looks stable until a cancel removes
    a key; then fill order changes and determinism dies without any test noticing.
14. **Skipping the `cap_table` mirror.** `INV-CAPTABLE` compares `Σ cap_table.shares(common)` with
    `shares_outstanding`; forget the mirror on a trade and it fails at the next tick, in C15's
    module, for no visible reason.
15. **Treating a halt as a market-wide event.** `06 §6.9` rejected market-wide halts precisely
    because they suppress the cascade dynamics A3 and A5 exist to study. Per symbol only.
16. **Marking to a stale `last_price` after a delisting.** Bankruptcy delists at 0; a holder still
    marked at the pre-failure price shows phantom net worth, which breaks M-6 and reads as a
    ledger bug.
