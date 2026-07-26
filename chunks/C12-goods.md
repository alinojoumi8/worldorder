# C12 — Goods market, consumption, CPI

**M2** · `polis/economy/goods.py` · **Depends on:** C04, C06, C07, C10, **C11 (ledger + firms)** · **Blocks:** C24b, C13, C15 (price level, CPI, real wages) · **Size:** M

## 1. Context

This is where money meets need. Firms post prices, buyers see a capped slice of sellers, and a
purchase is a posted-price transaction with sales tax, an inventory decrement and a need
restoration — no clearing, no auction, no Walrasian tâtonnement. Deliberately: `06 §5.3` lags
price adjustment by a full period so stockouts, queues, rationing and inventory dynamics are
real events rather than assumptions. The chunk also owns the **CPI**, which is the deflator
under real GDP, real wages and the Taylor rule, so its construction is normative and fixed at
genesis. It is small in code and large in consequence: the price level is what makes every
other economic number interpretable.

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/06-ECONOMY-SPEC.md` | **§5 goods market (primary source)**, §1.6 E2 (the purchase legs), §2.1–2.3 arithmetic, §4.4–4.5 inventory and pricing, §11.1 sales tax, §11.3 health subsidy, §12 M1/M3/M8/M9, §13.3 the deflation guard, §14 F6/F7/F16, §16.1 slot 4, §16.2 steps 3/4/7 |
| `../docs/03-DATA-MODEL.md` | §0 conventions, §5 `skus`, `inventory`, `goods_transactions`, §3.1 `places` (rent, landlord) |
| `../docs/02-ARCHITECTURE.md` | §3.2 kind ranges, §4 determinism, §5.1 slot order, §8.1 MECHANISM, §9 invariants |
| `chunks/C10-actions.md` | §5 `InstitutionResolver`, `BuyGoodParams`, §9.2 gate order |
| `chunks/C11-labour-firms.md` | `Ledger`, `money.py`, `RuntimeOverlay`, `firms.apply_set_price/apply_produce/apply_restock`, the goods kind renumbering |
| Chunks | C06 (`places.rent_cents`, `owner_id`, district distance), C07 (`AgentState.needs`, `Observation`) |

## 3. Scope — in

1. The SKU catalogue: `skus` rows from `configs/skus.yaml`, categories, `is_necessity`,
   `base_utility`, perishability, durability, and the `capital_skus` set.
2. **Posted-price search**: `visible_sellers(a, sku, t)` — the distance-then-price slice with a
   seeded shuffle inside each distance band.
3. `BUY_GOOD` resolution in PHASE 5 slot 4: grouping by `(seller_firm_id, sku)`, seeded
   service order, sales tax, health subsidy, the **E2** leg set, inventory decrement,
   `goods_transactions` row, and rationing when demand exceeds stock.
4. **The slot-4 `GoodsResolver`**, which also handles `SET_PRICE`, `PRODUCE` and `RESTOCK` by
   delegating to C11's `firms.apply_*` (one resolver per slot — C10 §9.6).
5. Consumption: the purchase→need-restoration mapping of `06 §5.4`, food-on-hand,
   durables and their expiry, the esteem relative-status term.
6. **Household budget allocation**: the Stone–Geary linear expenditure system, monthly in
   PHASE 7, consumed by the reflex policy and by `MechanicalPolicy`.
7. Rent payment (PHASE 7 step 7) and arrears — the *payment*, not the level.
8. **CPI**: genesis basket construction, the fixed-base Laspeyres index, category and core
   sub-indices, the chained Fisher secondary series, and `inflation_yoy` / `inflation_mom`.
9. `INV-PRICE` registration; the `PURCHASE_FAILED` taxonomy that feeds agent perception.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `post_transaction`, account ids, `allocate`, `bp` | **C11** — you build legs, C11 posts them |
| Production, unit cost, markup arithmetic, spoilage *draws* | **C11** — you read `inventory.price_cents` and call `firms.apply_*` |
| Rent *levels*, `places.rent_cents`, land value, housing match, `RENT_HOME` | **C06** |
| Tax *rates* and the treasury's side of the money | **C14** — you emit the tax leg into the same transaction; C14 owns collection accounting |
| Capital purchases incrementing `firms.capital_cents` | **C11**, called from your `BUY_GOOD` path when `sku ∈ capital_skus` |
| Metric registration and Parquet export | **C24b** — you expose `cpi_bp()` and it reads |
| Choosing what to buy | **C09** (deliberate), **C07** (reflex), **C11**'s `MechanicalPolicy` |

## 5. Interfaces you provide

```python
# polis/economy/goods.py
Category = Literal["food", "housing", "goods", "services", "luxury", "health"]

@dataclass(frozen=True, slots=True)
class Sku:
    sku: str; category: Category; is_necessity: bool
    base_utility_bp: int
    perishable_bp_per_day: int          # 0 == not perishable
    durable_life_ticks: int | None      # None == consumable/service
    is_service: bool
    is_capital: bool
    need_restore_bp: Mapping[Need, int]
    gamma_units_per_year: int           # LES subsistence quantity; 0 for non-necessities
    beta_bp: int                        # LES marginal budget share
SKUS: Final[Mapping[str, Sku]]          # loaded from configs/skus.yaml, hashed into the manifest

@dataclass(frozen=True, slots=True)
class SellerQuote:
    firm_id: str; sku: str; price_cents: int; qty_available: int
    district_id: str; distance_bands: int

@dataclass(frozen=True, slots=True)
class PurchaseBreakdown:
    gross_cents: int; sales_tax_cents: int; subsidy_cents: int; paid_cents: int

def visible_sellers(a: AgentState, sku: str, tick: int, *, ctx: GoodsContext
                    ) -> tuple[SellerQuote, ...]:
    """06 §5.3. Sorted by (district_distance, price_cents, firm_id) after a seeded shuffle
    inside each distance band via rng.get('goods.search', agent_id, tick). Capped at
    goods_search_k. A firm outside goods_search_radius_districts is invisible unless it is the
    only seller."""

def purchase_legs(buyer_id: str, seller_firm_id: str, sku: str, qty: int,
                  unit_price_cents: int, *, tick: int, ctx: GoodsContext
                  ) -> tuple[list[Leg], PurchaseBreakdown]:
    """Builds the E2 leg set: buyer dep -> seller dep (cross-bank via res), sales tax to
    dep:gv_treasury@bk_cb, subsidy from the treasury for hl_* when spend.health_subsidy_bp > 0.
    Returns legs plus the breakdown for the 6120 payload. Does NOT post."""

class GoodsResolver:                                          # implements InstitutionResolver
    slot:    Final = InstitutionSlot.GOODS                    # 4
    handles: Final = frozenset({ActionType.BUY_GOOD, ActionType.SET_PRICE,
                                ActionType.PRODUCE, ActionType.RESTOCK})
    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        """BUY_GOOD of a capital SKU by an agent -> capability failure (06 §5.2).
        SET_PRICE/PRODUCE/RESTOCK -> firm owner or self-employed only."""
    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult:
        """Seller must be in the buyer's visible slice, built from ctx.observation's place."""
    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        """qty * price + tax <= CommitmentLedger.available(buyer); posted price
        <= max_unit_price_cents; inventory.qty > 0."""
    def resolve(self, actions: Sequence[ValidatedAction], tick: int,
                ctx: ResolutionContext) -> Sequence[Event]:
        """SET_PRICE, PRODUCE, RESTOCK delegate to C11's firms.apply_*; BUY_GOOD is §9.2."""
    def options_for(self, t: ActionType, ctx: ValidationContext
                    ) -> tuple[Mapping[str, Any], ...]:
        """For BUY_GOOD: the visible slice, so legal_actions() can carry concrete targets."""

# consumption
def consume(a: AgentState, sku: str, qty: int, tick: int, ctx) -> Sequence[NewEvent]:
    """Need restoration; emits 6124. Called by C06 for EAT/SLEEP and here for services."""
def food_on_hand(agent_id: str) -> int: ...            # in-memory projection, capped, spoils
def durables_of(agent_id: str) -> Mapping[str, int]: ...
def esteem_reference_bp(agent_id: str, tick: int, ctx) -> int:      # district-median, 06 §5.4

# budget
@dataclass(frozen=True, slots=True)
class BudgetPlan:
    household_id: str; horizon_ticks: int; committed_cents: int; disposable_cents: int
    buffer_cents: int; savings_share_bp: int; spend_by_sku_cents: Mapping[str, int]
def plan_budget(hh: HouseholdState, tick: int, ctx) -> BudgetPlan:
    """06 §5.5 Stone-Geary. Monthly, PHASE 7. Integer bp throughout."""

# CPI
@dataclass(frozen=True, slots=True)
class Basket:
    version: int; quantities: Mapping[str, int]; base_prices_cents: Mapping[str, int]
def build_basket(tick: int, ctx) -> Basket:
    """Genesis only. q_s = per-adult annual consumption in the calibration run, integer,
    dropping q_s == 0. |B| ~ 18. NEVER rebased during a run."""
def transaction_price_cents(sku: str, tick: int, window_ticks: int, ctx) -> tuple[int, bool]:
    """(volume-weighted price over the trailing window, carried_forward). 06 §5.6."""
def cpi_bp(tick: int, ctx) -> int                       # Laspeyres, fixed base, base 10_000
def cpi_category_bp(tick: int, ctx) -> Mapping[Category, int]
def cpi_core_bp(tick: int, ctx) -> int                  # excludes food, health
def cpi_fisher_bp(tick: int, ctx) -> int                # chained secondary series
def inflation_yoy_bp(tick: int, ctx) -> int; def inflation_mom_annualised_bp(tick, ctx) -> int

# PHASE 7
def pay_rent(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]      # step 7, monthly
def compute_cpi(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]   # daily
def expire_durables(tick: int, ctx: InstitutionContext) -> Sequence[NewEvent]
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C11 | `Ledger.post_transaction`, `transfer`, `liquid`, `CommitmentLedger` | every purchase and rent payment |
| C11 | `money.bp`, `bp_ceil`, `allocate`, `round_to_tick`, `MONEY_CTX` | tax, subsidy, budget shares, Fisher geometric mean |
| C11 | `RuntimeOverlay.bp("tax.sales_bp")`, `flag("tax.exempt_necessities")`, `bp("spend.health_subsidy_bp")` | **never** read these from static config |
| C11 | `firms.apply_set_price / apply_produce / apply_restock`, `inventory` read API | slot-4 delegation |
| C06 | `places.rent_cents`, `places.owner_id`, district distance matrix | rent payment, distance bands |
| C07 | `AgentState.needs`, `restore()`, `Observation` | need restoration, `PerceptionSources` slices |
| C10 | `InstitutionResolver`, `ValidatedAction`, `BuyGoodParams` | the boundary |
| C04 | `Clock`, `Scheduler`, `RngRegistry.get("goods.search", …)`, `stable`, `@mechanism` | cadence, draws, ablation |

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `skus` | W at genesis, R after | seeded from `configs/skus.yaml` |
| `inventory` | W (`qty` on sale only) | C11 writes `unit_cost_cents` and `price_cents`; agree the split and never both write `qty` |
| `goods_transactions` | W | one row per fill; the CPI's only price source |
| `places` | R | rent, landlord, district |
| `ledger_*` | via C11 only | never directly |
| `agents` | R | needs, wealth, household, district |
| `households` | R | `member_ids`, `tenure`, `rent_cents` (incl. the ratified `shelter` tenure) |
| `metrics` | W | M8 `cpi`, M9 `inflation_yoy`, and the category/core series |

## 8. Event kinds owned

**Range: 6100–6999.** `06 §5.1` prints these numbers 100 lower, inside C11's firm range, where
they collide with `6022 PRICE_SET` / `6030 DIVIDEND_DECLARED` / `6040 FIRM_PERIOD_CLOSED`. The
ratified allocation is **`new = old + 100`**; every `06` reference to a goods kind reads through
this map. Record it in the handback along with the one-line edit it forces on the illustrative
`6020 GOODS_PURCHASED` row in `02 §3.2` (`polis/events/kinds.py` is the single source of truth).

| Kind | `06 §5.1` | Name | Payload |
|---|---|---|---|
| 6120 | 6020 | `GOODS_PURCHASED` | `txn_id, buyer_id, seller_firm_id, sku, qty, unit_price_cents, gross_cents, sales_tax_cents, subsidy_cents, ledger_txn_id` |
| 6121 | 6021 | `PURCHASE_FAILED` | `buyer_id, sku, qty, reason(stockout\|unaffordable\|no_seller_visible\|price_above_cap\|rationed)` |
| 6124 | 6024 | `NEED_SATISFIED` | `agent_id, need, sku, from_bp, to_bp` |
| 6125 | 6025 | `DURABLE_EXPIRED` | `agent_id, sku, acquired_tick, life_ticks` |
| 6141 | 6041 | `CPI_COMPUTED` | `basket_version, index_bp, category_index_bp{}, carried_forward_skus[], window_ticks` |
| 6142 | 6042 | `INFLATION_COMPUTED` | `yoy_bp, mom_annualised_bp, core_bp` |
| 6143 | 6043 | `SECTOR_OUTPUT` | `sector, units, value_cents, firms_n` |
| 6150 | 6050 | `RENT_PAID` | `place_id, tenant_id, landlord_id, cents, period_ticks, txn_id` |
| 6151 | 6051 | `RENT_ARREARS` | `place_id, tenant_id, owed_cents, periods_missed` |
| 6144 | — | `BASKET_FIXED` | `basket_version, quantities{}, base_prices_cents{}, tick` (genesis only) |

6101–6119, 6122–6123, 6126–6140, 6145–6149, 6152–6999 reserved, unused.

## 9. Implementation notes

**9.1 Search slice.** Ordering is `(district_distance, price_cents, firm_id)` **after** a seeded
shuffle *within each distance band* — the shuffle is inside the band, not across it, or the
distance ordering stops meaning anything. `rng.get("goods.search", agent_id, tick)`.
Tag `@mechanism("goods.search_slice", entails=…)` with the `06 §5.3` string verbatim: price
dispersion can persist, nearby sellers capture more demand, spatial price inequality is
possible — the *level* of dispersion and its cyclicality are not implied.

**9.2 `BUY_GOOD` resolution, exact order.**

```
group all BUY_GOOD actions this tick by (seller_firm_id, sku)      # stable(key=(firm, sku))
for each group, in (seller_firm_id, sku) order:
    order buyers by rng.get("goods.search", seller_firm_id, tick).permutation(len(group))
    #                        ^ NEVER by agent_id — that is a permanent alphabetical advantage
    for buyer in that order:
        px = inventory[firm, sku].price_cents              # posted; the action's cap only gates
        if px > action.max_unit_price_cents: emit 6121 price_above_cap; continue
        fill = min(action.qty, inventory.qty)
        if fill == 0: emit 6121 rationed (or stockout if qty was 0 before the tick); continue
        legs, brk = purchase_legs(...)
        ledger.post_transaction(legs, tick=tick, cause=<the 6120 event>)
        inventory.qty -= fill;  goods_transactions row;  emit 6120
        if sku is capital and buyer is a firm: firms.add_capital(...) -> 6011 (C11)
        else if durable: register in agent_durables
        else: food_on_hand / immediate consume -> 6124
```

Price does **not** clear within the tick. Residual buyers are rationed and prices move in
PHASE 7 step 3, one period later (`06 §5.3`). That lag is the whole point.

**9.3 Necessity, luxury and the LES.** `is_necessity` drives three separate things and they are
easy to conflate: (a) reflex eligibility — a reflex agent may only `BUY_GOOD` a necessity at
the posted price under a value cap (`06 §15`); (b) sales-tax exemption when
`tax.exempt_necessities`; (c) a positive subsistence quantity `γ_s` in the LES. Keep them
separate flags in code even though they are correlated in the seed catalogue.

`@mechanism("consumption_rule", entails=…)` with the `06 §5.5` string verbatim. Two things in
it are load-bearing and must survive review: **an Engel curve is not a finding** (income
elasticity below one for necessities is imposed by construction), and the subsistence floor
`γ_s > 0` is the **deflation guard** (`06 §13.3`, F7) — it bounds aggregate demand below while
anyone has income. It is declared and ablatable precisely because it is doing that work; the
`γ = 0, benefit_replacement_bp = 0` run is the stress test and it is expected to be ugly.

**9.4 CPI, exactly as `06 §5.6`.** Fixed genesis basket, never rebased. `p_s(t)` is the
volume-weighted mean **transaction** price over the trailing 30 sim-days, integer floor; on zero
volume carry the previous value forward and list the SKU in `carried_forward_skus[]` — a CPI
computed from posted prices instead of transactions silently ignores rationing and stockouts.
Sales tax is **included** in `unit_price_cents` for CPI purposes; subsidies are netted out.
The chained Fisher series is secondary and exists so substitution bias is measurable rather than
unknown. Name the analogue in a separate column (T11): `CPI(t)` ~ **CPI-U**; the Fisher series ~
a superlative chained index. That naming asserts identical construction from micro-records and
nothing about magnitudes.

**9.5 Carried-forward risk.** If more than `cpi_carry_warn_frac` of the basket is carried
forward in a window, emit a WARN metric. A basket that is mostly carried forward is a CPI that
is measuring nothing, and it silently poisons real GDP, real wages and the Taylor rule.
`INV-PRICE` (yoy in `[−5,000, +40,000]` bp, WARN then HALT at the bound) is registered here.

**9.6 Rent.** `places.owner_id` is the landlord; if `NULL` the landlord is `gv_treasury`
(public housing), so there is never a payment without a counterparty (Corollary L1a). The
ratified `shelter` tenure and `places.type = shelter` pay zero rent and must not fall through
into an arrears path. Missed rent emits 6151 and becomes an unsecured claim; eviction is C06's.

**9.7 Durables and food-on-hand are in-memory projections**, rebuilt from 6120/6125 and
6120/`EAT`. No agent inventory table exists and none is wanted (`06 §5.2`). Both must be
`Checkpointable` and must survive `polis rebuild` — the projection-rebuild test diffs them.

**9.8 Subsidised health.** `hl_*` at `spend.health_subsidy_bp`: the buyer pays the net price,
the treasury pays the difference **to the seller in the same transaction**. Two separate
transactions would let a mid-tick failure strand half the payment (F19-shaped).

**9.9 Price collusion is a finding, not a bug — unless you leak.** F16: if sector price
dispersion collapses, first confirm that no competitor's price reaches an LLM prompt outside
the agent's visible slice. Leaking the full price vector into a prompt turns emergent norms
(**B3**) into an artefact. The visible slice is the only channel.

## 10. Configuration keys

```yaml
goods:
  search_k: 5
  search_radius_districts: 2
  food_stock_cap_units: 14
  reflex_value_cap_cents: 0            # 0 => derived from median wage
  purchase_max_qty: 1000
consumption:
  subsistence_gamma: {fd_staple: 365, fd_fresh: 180, hs_utilities: 12, gd_clothing: 4,
                      sv_transport: 240, hl_primary: 2, hl_medicine: 6}
  beta_bp: {}                          # Σ β_s == 10_000 − savings_share_bp
  savings_share_bp_table: {}           # f(time_preference, security, age)
  buffer_bp_table: {}
cpi:
  window: 30d
  basket_min_skus: 12
  carry_warn_frac: 0.25
  fisher_enabled: true
rent:
  cadence: monthly
  arrears_grace_periods: 2
mechanisms:
  goods_search_slice: on
  consumption_rule: linear_expenditure   # | llm_only (ablation, unaffordable at 1k agents)
```

## 11. Acceptance criteria

1. Every purchase posts exactly one balanced transaction; a 200-tick stub run of purchases
   alone leaves `INV-MONEY` closed to the cent.
2. The **E2** worked example of `06 §1.6` reproduces leg-for-leg, including the cross-bank
   reserve legs and the tax leg to `dep:gv_treasury@bk_cb`.
3. Sales tax is `bp_ceil` (the buyer never gains from rounding); a necessity is exempt when
   `tax.exempt_necessities` and the exemption flips when the runtime overlay changes it.
4. A buyer whose action cap is below the posted price gets `PURCHASE_FAILED{price_above_cap}`
   and pays nothing.
5. When demand for `(firm, sku)` exceeds `inventory.qty`, exactly the served buyers get 6120 and
   the rest get `PURCHASE_FAILED{rationed}`; the price is unchanged for the remainder of the tick.
6. Buyer service order is a seeded permutation: renaming every `agent_id` to a reverse-sorted
   alias leaves the multiset of fills unchanged.
7. `visible_sellers` never returns more than `search_k`, never returns a firm with `qty == 0`,
   and returns the sole seller even when it is outside the radius.
8. `inventory.qty` never goes negative, and `Σ qty sold == Σ qty decremented` over a run.
9. A capital SKU bought by an agent is a **capability** rejection; bought by a firm it increments
   `firms.capital_cents` and emits 6011, never `inventory`.
10. The LES: `Σ spend_s + savings == disposable_cents` exactly, for random budgets (property
    test); `spend_s >= price_s × γ_s` for every necessity while disposable covers subsistence.
11. Necessity income elasticity < 1 and luxury > 1 by construction — asserted, and the
    mechanism's `entails` says so, so no Engel-curve finding can be claimed.
12. CPI at tick 0 is exactly 10,000 bp. The basket is fixed at genesis and `basket_version`
    never changes during a run.
13. A synthetic transaction stream with a known 10% price rise across the basket produces
    `inflation_yoy_bp == 1_000` ± 1 bp.
14. Zero-volume SKUs carry forward and are listed in `carried_forward_skus[]`; the index does
    not move on a carried SKU.
15. Fisher and Laspeyres agree at tick 0 and diverge only in the documented direction when
    relative prices move.
16. `INV-PRICE` WARNs above +40,000 bp yoy and HALTs at the bound.
17. Rent is paid to `places.owner_id` or to `gv_treasury` when null; `shelter` tenure pays zero
    and generates no arrears.
18. Durables expire exactly `life_ticks` after purchase and emit 6125; food-on-hand spoils at
    the SKU rate and is capped.
19. `GoodsResolver` is the only slot-4 resolver, handles all four types, tolerates an empty
    batch, and delegates `SET_PRICE`/`PRODUCE`/`RESTOCK` to C11 without reimplementing them.
20. Determinism: same seed twice → identical 6100-range events and `goods_transactions`.
21. `mypy --strict polis/economy/goods.py`, `ruff`, and the import-linter contract pass.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/economy/test_goods_search.py` | Slice cap; distance-then-price ordering; shuffle inside bands only; sole-seller-outside-radius rule; determinism under agent renaming |
| `tests/unit/economy/test_purchase_legs.py` | E2 leg-for-leg; same-bank vs cross-bank; sales tax `bp_ceil`; necessity exemption; health subsidy leg; Σ legs == 0 |
| `tests/unit/economy/test_purchase_resolution.py` | Rationing; stockout vs rationed reason codes; price cap; inventory decrement; capital-SKU routing; no negative inventory |
| `tests/property/test_goods_money_neutral.py` | **Hypothesis.** For any random stream of purchases with `sales_bp = 0` and no subsidy, `Σ(buyer + seller + treasury balances)` is invariant |
| `tests/unit/economy/test_les_budget.py` | `Σ spend + savings == disposable` for random budgets; subsistence honoured first; supernumerary split by `β`; `Σ β == 10_000 − savings_share` |
| `tests/unit/economy/test_needs_consumption.py` | Need restoration per SKU; food-on-hand cap and spoilage; durable expiry; esteem relative term against a district median |
| `tests/unit/economy/test_cpi_construction.py` | Basket fixed at genesis; CPI == 10,000 at t0; known 10% shift → 1,000 bp; category and core sub-indices; carried-forward listing |
| `tests/unit/economy/test_cpi_laspeyres_vs_fisher.py` | Both equal at base; substitution bias appears in the documented direction; integer-only arithmetic |
| `tests/unit/economy/test_rent_payment.py` | Landlord resolution incl. `NULL` → treasury; `shelter` tenure pays zero; arrears after grace; balanced legs |
| `tests/unit/economy/test_goods_resolver_contract.py` | Slot 4; four handled types; empty batch calls `resolve`; delegation to `firms.apply_*` is a call, not a copy |
| `tests/invariants/test_inv_price.py` | WARN and HALT bounds; a runaway markup fixture trips it |
| `tests/integration/test_consumption_loop.py` | 50 agents, 300 ticks: wages → purchases → inventory drawdown → restock → price move; CPI series is finite and INV-MONEY holds every tick |
| `tests/determinism/test_goods_determinism.py` | Same seed twice → identical events, transactions and CPI series |

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. Kinds 6100–6999 registered with payload schemas, and the `old + 100` renumbering recorded in
   the handback together with the `02 §3.2` doc edit it implies.
2. `configs/skus.yaml` ships the seed catalogue of `06 §5.2` — 23 SKUs including the three
   `cap_*` — and is hashed into the run manifest.
3. `INV-PRICE` registered with C04's `InvariantRunner` at sim-day frequency.
4. Two `@mechanism` declarations (`goods.search_slice`, `consumption_rule`) with the `06`
   `entails` strings verbatim, both ablatable.
5. `cpi_bp()` and `inflation_yoy_bp()` exposed for C14's Taylor rule and C24b's metric
   catalogue; neither recomputes the index itself.
6. A note to C24b stating that M8/M9 come from here and must not be recomputed from posted
   prices.

## 14. Traps

1. **Computing the CPI from posted prices.** Posted prices exist for goods nobody can buy.
   The index then misses every stockout and every episode of rationing, real GDP inherits the
   error, and the Taylor rule reacts to a number that describes nothing. Transactions only.
2. **Rebasing the basket mid-run** because a SKU stopped trading. The whole point of a fixed
   base is comparability across the run; carry the price forward and report the carry.
3. **Serving buyers in `agent_id` order.** `ag_aaron` never sees a stockout for five sim-years
   and every distributional result is contaminated. Seeded permutation, per `(firm, sku)`.
4. **Clearing the price within the tick.** Raising the price until demand equals supply turns
   the goods market into an auction, deletes stockouts and inventory dynamics, and imports a
   market-clearing assumption that `06 §5.3` explicitly rejects.
5. **A one-sided leg for the sales tax or the subsidy.** Both must be inside the same
   transaction as the purchase. A separate transaction can be interrupted; a one-sided leg
   fails P3 immediately, which is the good outcome — the bad outcome is "netting" it out of the
   buyer's payment and never crediting the treasury.
6. **Spoilage posting a ledger transaction.** Inventory is a real asset (Rule L1). A spoilage
   write-down with a ledger leg is a one-sided leg and V2 fails at an unpredictable tick.
7. **`float` in the LES.** Budget shares are basis points, subsistence is integer units, and
   `Σ spend + savings` must equal disposable **exactly**. Use `allocate()` for the residual.
8. **Setting `γ = 0` to "let the market decide".** That is the deflation guard (F7). Removing
   it is a legitimate ablation and an illegitimate default; a run with it off can spiral, which
   is the finding, not a reason to quietly turn it back on.
9. **Conflating `is_necessity` with reflex eligibility with tax exemption.** They coincide in
   the seed catalogue and diverge the moment policy changes; three flags, not one.
10. **Letting an agent buy a capital SKU.** `06 §5.2` rejects it at the capability gate. Silent
    acceptance creates inventory for an entity with no inventory table and the units vanish.
11. **Writing `inventory.qty` from two chunks.** C11 writes production and spoilage, C12 writes
    sales. Both writing both produces a phantom-goods bug that looks like a pricing bug.
12. **Leaking competitor prices into prompts.** Beyond the visible slice, this converts F16
    (possible emergent collusion, research question B3) into an artefact of prompt construction.
13. **A durable that never expires.** Utility accrues forever, `esteem` saturates, status
    competition dies, and the luxury sector's demand goes to zero after year one.
14. **Treating `PURCHASE_FAILED` as an error path.** It is data: it drives perception, it is how
    an agent learns a shop is empty, and its reason-code distribution is a headline diagnostic.
15. **Ignoring the `shelter` tenure.** The ratified `shelter` place type and tenure exist so
    homelessness has somewhere to go; falling through into the rent-arrears path bills a
    destitute agent for a public shelter and cascades into a spurious bankruptcy.
