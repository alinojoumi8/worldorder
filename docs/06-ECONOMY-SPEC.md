# POLIS — Economy Specification

**Version:** 1.0
**Status:** Normative. Every rule here is binding on chunks C11–C15.
**Owner module:** `polis/economy/` (`ledger.py`, `labour.py`, `firms.py`, `goods.py`, `exchange/`, `banking.py`, `ventures.py`)
**Depends on:** `02-ARCHITECTURE.md` (tick phases, determinism, action envelope, invariants), `03-DATA-MODEL.md` §4–§7 (ledger, firms, exchange, banking, ventures), `04-AGENT-SPEC.md` §3 (skills), §7 (salience routing), §11 (validation), §12.3 (death)
**Milestones:** M2 (labour, firms, goods, banking, government — V2 must hold), M3 (exchange, ventures, M&A, bankruptcy)

> This is the highest-risk document in the project. `01-PRD.md §8` states that M2 is where
> simulations of this kind quietly break, and `§11` names *"money doesn't close"* as the only
> **Critical**-severity risk. Section 1 exists to make that impossible. Read it before any
> other section, and do not implement any economic feature that is not expressible as
> balanced ledger legs.

---

## 0. Conventions binding on this document

| # | Rule | Consequence of violating it |
|---|---|---|
| **C0.1** | Money is `BIGINT` minor units (cents), suffix `_cents`. **Never** `float`, never `NUMERIC`. `02-ARCHITECTURE.md §4.6`. | Silent drift; V2 fails at an unpredictable tick |
| **C0.2** | Exchange prices are integer **price ticks**; 1 tick = 1 cent unless `exchange.tick_size_cents` says otherwise. | Crossed books, un-reproducible fills |
| **C0.3** | All money movement goes through `polis.economy.ledger.post_transaction(legs)`. No code outside `ledger.py` touches `ledger_accounts` or `ledger_entries`. Enforced by `import-linter`. | The one bug class that cannot be found by inspection |
| **C0.4** | Rates, ratios, shares, margins, and index levels are integers in **basis points** (`_bp`, 10,000 bp = 1.0). Percentages never appear as floats in state or payloads. | Rounding non-determinism |
| **C0.5** | Randomness only via `rng.get(namespace, entity_id, tick)`. Namespaces are enumerated in §2.5. | Determinism gate fails |
| **C0.6** | Entity ID prefixes: `ag_` agent, `fm_` firm (incl. banks-as-firms and VC funds), `bk_` bank, `hh_` household, `pl_` place, `st_` startup, `pt_` party, `ol_` outlet. Government is the reserved id `gv_treasury`; the exchange is `mk_exchange`. | Prompt and payload confusion |
| **C0.7** | Institutions never import agent cognition (`02-ARCHITECTURE.md §7.1`). Every module here consumes `Action` and emits `Event`. | Untestable market logic |
| **C0.8** | Every hard-coded behavioural rule carries `@mechanism(id, entails="…")` and a `mechanisms:` config key. The `entails` string states what the rule *analytically implies*. `02-ARCHITECTURE.md §8.1`, threat **T6**. | An "emergent" finding that is a tautology |
| **C0.9** | PHASE 5 resolution order is fixed: movement, communication, **labour, goods, exchange, banking, ventures**, polity, law, misc. Scheduled institutional steps go in PHASE 7 (§16). | Iteration-order artefacts |
| **C0.10** | IDs are minted deterministically, never from `uuid4()` or the clock: `mint(prefix, tick, ordinal)` → `"<prefix>_<tick:08d>_<ordinal:04d>"`. Transaction IDs use `uuid5(run_id, f"{tick}:{txn_ordinal}")`. | Byte-identical replay fails |

### 0.1 Required amendments to `03-DATA-MODEL.md`

Two small extensions are needed. They are stated here rather than assumed.

| Location | Amendment | Justification |
|---|---|---|
| §4.1 `ledger_accounts.account_type` | Add `issuance` to the enumerated comment. §4 rule 2 already names the central bank's `issuance` account; it is absent from the column comment. | Without it the only money-creation account is undeclared |
| §4.2 `ledger_entries.reason` | Add `write_off` and `escrow` to the enumerated comment. | `write_off` must be distinguishable from `loan` so credit losses can be measured separately from repayments (metric M13, §12). `escrow` must be distinguishable from `transfer` so INV-ORDERS can be reconstructed from the log. |

No other schema change is required by this document. Everything else uses the tables in
`03-DATA-MODEL.md §4–§7` as written.

### 0.2 Requested additions outside this document's authority

| Item | Where it lives | Request | Justification |
|---|---|---|---|
| `ActionType.DECLARE_DIVIDEND` | `02-ARCHITECTURE.md §6.2`, **ventures** group | **Add one action type.** | A mechanical payout ratio is the default (§4.9), but owner discretion over distributions is the channel that separates "price tracks fundamentals" from "price tracks narrative" (**A3**) and is a direct lever on capital allocation (**A6**). With a fixed payout ratio, dividend policy is not studiable at all. |
| `ACCEPT_OFFER` / `DECLINE_OFFER` are **polymorphic** | `02-ARCHITECTURE.md §6.2`, labour group | **Clarification, not an addition.** | `04-AGENT-SPEC.md §5` already types `Observation.offers` as *"job offers, term sheets, proposals awaiting me"*. This document therefore treats these two action types as operating on any `Offer` with `offer_kind ∈ {job, term_sheet, acquisition, settlement, loan_terms}`. No new action type is needed for term-sheet acceptance or tender participation. |
| LLM purpose `CREDIT_EVAL` | `02-ARCHITECTURE.md §8` routing table, `09-MODEL-ROUTING.md` | **Optional purpose.** | Underwriting is mechanical by default (§7.4), so M2 does not depend on it. The purpose exists so `banking.underwriting: llm` can be swept as an ablation against the scorecard. |

Nothing else in the closed `ActionType` enum is extended. Every other economic decision maps
onto an existing type; the full mapping is §15.

---

## 1. Money and the ledger

### 1.1 Units and the fundamental representation

| Concept | Representation |
|---|---|
| Currency | `POL`. One POL = 100 cents. `ledger_accounts.currency` is always `'POL'` in v1; the column exists so a second currency is a data change, not a code change. |
| Amount | `BIGINT` cents, always `> 0` on a leg (`03-DATA-MODEL.md §4.2` CHECK). Sign lives in `direction`, never in the amount. |
| Direction | `+1` debit, `-1` credit. |
| Signed value of a leg | `signed(leg) = direction × amount_cents` |
| Account balance | `balance_cents(a) = Σ signed(leg) over legs on a`. **Assets carry positive balances; liabilities carry negative balances.** |
| Net worth of an owner | `net_worth(o) = Σ balance_cents over accounts owned by o`. Negative net worth is legal and meaningful (insolvency). |

Because every transaction sums to zero, the whole book sums to zero at all times:

```
Σ_{all accounts} balance_cents  ==  0      ← INV-LEDGER, every tick
```

This is the single cheapest and strongest bug detector in the system: it is a sum over a few
thousand rows, it runs every tick, and no economic feature can be implemented incorrectly
without breaking it.

### 1.2 Rule L1 — the ledger holds money and money-denominated claims only

**Real assets are never on the ledger.** Inventory, capital equipment, shares, land, skills,
and goodwill live in their own projection tables (`inventory`, `firms.capital_cents`,
`holdings`, `cap_table`, `places`, `agent_skills`) and touch the ledger *only* at the moment
they are exchanged for money.

This is not a simplification, it is the load-bearing defence against money leaks. If
inventory were a ledger account, a spoilage write-down would be a one-sided leg and V2 would
fail. Under Rule L1, spoilage is a real write-off (`INVENTORY_WRITTEN_OFF`, kind 6013) with
**no ledger transaction at all**, and closure is untouched.

**Corollary L1a — there is no outside.** Every payment has an in-world counterparty account.
A firm cannot "buy capital from the rest of the world"; capital goods are SKUs produced by
firms in the `industrial` sector (§5.2). Government spending cannot vanish; it lands in some
agent's or firm's account. Any code path that debits an account without naming the credited
account is a bug, and `post_transaction` will reject it.

### 1.3 Chart of accounts

`account_id` has the form `<code>:<owner_id>[@<bank_id>][#<ref>]`. The `code` is a member of
the closed vocabulary below and determines **polarity**; `account_type` is the coarse bucket
stored in `ledger_accounts.account_type` (`03-DATA-MODEL.md §4.1`).

| Code | `account_type` | Polarity | Owner types | Cardinality | Contents |
|---|---|---|---|---|---|
| `cash` | `cash` | **ASSET** | agent, firm, bank, government | one per owner | Physical currency. Bank-owned `cash` is vault cash. |
| `dep` | `deposit` | **ASSET** | agent, firm, government, fund | one per (owner, bank) | Demand-deposit claim on a bank. Spendable. |
| `esc` | `escrow` | **ASSET** | agent, firm, fund, bank | one per (owner, bank, purpose) | Frozen deposit sub-account at the same bank. Counts as a deposit for the bank's liability identity and for M1; **unspendable** except by the reserving institution. |
| `res` | `reserve` | **ASSET** | commercial bank | one per bank | Reserve balance at the central bank. |
| `lnr` | `loan_receivable` | **ASSET** | bank, agent, fund, government | one per loan | Principal outstanding, lender side. `#<loan_id>`. |
| `txr` | `tax_receivable` | **ASSET** | government | one | Tax arrears (§11.3). |
| `dpl` | `deposit` | **LIABILITY** | bank | one per bank | Aggregate customer deposit liability (deposits + escrow). |
| `lnp` | `loan_payable` | **LIABILITY** | agent, firm, bank, government | one per loan | Principal owed, borrower side. `#<loan_id>`. |
| `iss` | `issuance` | **LIABILITY** | central bank only | **exactly one** (`iss:bk_cb`) | All base money outstanding. The only account that may be credited without a corresponding asset transfer, and only via `MONEY_ISSUED` (kind 8032). |
| `eqy` | `equity` | — | — | **reserved, unused in v1** | Equity is a *derived residual* (`net_worth`), not a posted account. Posting it would double-count. Documented so nobody adds it back. |

**There are no income, expense, or retained-earnings accounts.** Polis runs a balance-sheet-only
ledger. Income and expense are *flows*, measured by querying `ledger_entries` filtered by
`reason` and `tick`. A firm's profit for a period is derived, not stored. This is what lets a
wage payment be two legs instead of four, and it makes every transaction in §1.6 short enough
to check by eye.

#### 1.3.1 Why a bank's interest income needs no leg

When a borrower pays interest `I` out of a deposit at the lending bank:

- borrower's `dep` falls by `I` → borrower net worth `−I`
- bank's `dpl` falls by `I` → bank liabilities `−I`, so bank net worth `+I`

Assets unchanged on both sides; the transfer of net worth is automatic. Two legs, sums to
zero. The same trick handles fees, commissions, penalties, and haircuts.

### 1.4 The `post_transaction` contract

```python
# polis/economy/ledger.py

@dataclass(frozen=True, slots=True)
class Leg:
    account_id:   str
    direction:    int          # +1 debit, -1 credit
    amount_cents: int          # strictly > 0
    reason:       str          # 03-DATA-MODEL §4.2 vocabulary (+ write_off, escrow: §0.1)

def post_transaction(
    legs:            Sequence[Leg],
    *,
    tick:            int,
    cause:           EventRef,                 # the event that caused this transaction
    allow_negative:  frozenset[str] = frozenset(),   # account_ids permitted to go negative
) -> UUID:                                     # txn_id
    """Atomically append a balanced set of legs. Never fails for a business reason."""
```

**Preconditions (all are assertions; failure is a bug, not a rejection):**

| # | Precondition |
|---|---|
| P1 | `len(legs) >= 2` |
| P2 | `all(l.amount_cents > 0 for l in legs)` |
| P3 | `sum(l.direction * l.amount_cents for l in legs) == 0` |
| P4 | Every `account_id` exists, is open (`closed_tick IS NULL`), and has `currency == 'POL'` |
| P5 | Legs are canonicalised: at most one leg per `(account_id, direction, reason)`; duplicates are netted before the call |
| P6 | After application, no ASSET account has `balance_cents < 0` and no LIABILITY account has `balance_cents > 0`, **unless** the account is in `allow_negative`. The only accounts ever passed in `allow_negative` are `res:<bank>` (overdraft at the central bank, immediately converted to a discount-window loan in PHASE 7, §7.9) and `dep:gv_treasury@bk_cb` (converted to a bond issue, §11.5). |
| P7 | `cause` refers to an event being emitted in the current tick |

**Postconditions:**

1. `ledger_entries` gains one row per leg, all sharing one `txn_id` and one `event_seq`.
2. `ledger_accounts.balance_cents` is updated incrementally, in memory, in the same call.
3. The transaction is appended to the tick buffer; the DB write happens in PHASE 6 as part of
   the batched commit, and `event_seq` is bound at that point (§1.4.1).
4. The returned `txn_id` is deterministic: `uuid5(run_id, f"{tick}:{txn_ordinal}")` where
   `txn_ordinal` is a per-tick monotonic counter starting at 0.

**Failure semantics.** `post_transaction` raises `LedgerError`, which is an *unhandled
institution exception* under `02-ARCHITECTURE.md §10` and therefore **HALTs the run**. This is
deliberate: affordability is a PHASE 4 concern (`04-AGENT-SPEC.md §11`, Resources gate), so by
the time the ledger is called, the money is known to be there. A ledger error means the
validator and the resolver disagree, which is exactly the bug class V2 exists to catch.

#### 1.4.1 Sequencing and the tick buffer

Ledger posts happen in PHASE 5 and PHASE 7; event sequence numbers are assigned in PHASE 6.
`post_transaction` therefore takes an `EventRef` handle rather than a `seq`, mutates in-memory
balances immediately, and stages `(txn_id, legs, EventRef)` in the tick buffer. At COMMIT the
buffer is flushed: events get their `seq`, the handles resolve, and `ledger_entries.event_seq`
is written. Ordering within the buffer is insertion order, which is deterministic because
PHASE 5 and PHASE 7 are single-threaded and their internal orderings are fixed (§16).

#### 1.4.2 Helper: `transfer`

Almost every economic transaction is a transfer between two owners. `transfer` expands to two
or six legs depending on whether payer and payee bank at the same institution.

```python
def transfer(src: str, dst: str, amount_cents: int, reason: str, **kw) -> list[Leg]:
    """src, dst are dep/esc/cash account_ids."""
    if bank_of(src) == bank_of(dst):
        return [Leg(src, -1, amount_cents, reason),          # payer claim down
                Leg(dst, +1, amount_cents, reason)]          # payee claim up
    b1, b2 = bank_of(src), bank_of(dst)
    return [Leg(src,          -1, amount_cents, reason),     # payer deposit down
            Leg(f"dpl:{b1}",  +1, amount_cents, reason),     # B1 liability down
            Leg(f"res:{b1}",  -1, amount_cents, reason),     # B1 pays reserves
            Leg(f"res:{b2}",  +1, amount_cents, reason),     # B2 receives reserves
            Leg(f"dpl:{b2}",  -1, amount_cents, reason),     # B2 liability up
            Leg(dst,          +1, amount_cents, reason)]     # payee deposit up
```

Same-bank transfers leave `dpl` untouched — correct, because the bank's total liability is
unchanged. Cross-bank transfers settle in central-bank reserves, which is what makes reserve
scarcity, the interbank market, and the discount window real rather than decorative.

The government banks at the central bank (`dep:gv_treasury@bk_cb`), so every tax payment
drains reserves from a commercial bank and every transfer payment injects them. This is not
cosmetic: it is the fiscal–monetary linkage that makes A4 (policy transmission) answerable.

### 1.5 Money creation and destruction

There are exactly **two** channels, and no third is permitted.

| Channel | What it changes | Mechanism |
|---|---|---|
| **Outside money (M0)** — central bank issuance | `iss:bk_cb` | `MONEY_ISSUED` (8032) / `MONEY_WITHDRAWN` (8033). Occurs at genesis (§13.2) and thereafter only via open-market operations (8034) and discount-window lending (8041). |
| **Inside money (M1 − M0)** — commercial bank credit | `dpl:<bank>` | Loan origination creates a deposit (§1.6 E4); principal repayment destroys it (E5); write-off destroys the asset without destroying the deposit (E8). |

Base money issuance is the only place in the entire codebase where an account is credited
without an offsetting asset movement, and it is confined to `ledger.issue_base_money()`, which
is the sole caller permitted to name `iss:bk_cb` on a leg.

```
M0(t) = Σ balance(cash:*) + Σ balance(res:*) + balance(dep:gv_treasury@bk_cb)
      = − balance(iss:bk_cb)                                        ← exact identity
```

```
Currency in circulation   CIC(t) = Σ balance(cash:o) for o ∉ banks
Commercial bank deposits  D(t)   = Σ_{B ≠ bk_cb} −balance(dpl:B)
                                 = Σ balance(dep:*@B) + Σ balance(esc:*@B)
M1(t) = CIC(t) + D(t) − (government deposits at commercial banks)
```

Escrowed funds are still M1 — they are a hold on an account, exactly as a pending card
authorisation is. Excluding them would make M1 jump every time an order rested.

### 1.6 Worked examples — the legs

Every example below sums to zero. These are the canonical reference implementations; a
transaction type not derivable from one of these patterns needs a spec amendment, not
improvisation.

#### E1 — Wage payment (with income tax withheld at source)

`fm_acme` banks at `bk_first`. `ag_maya` banks at `bk_first`. Gross 320,000 ¢; withholding
25% = 80,000 ¢; net 240,000 ¢. Government banks at `bk_cb`.

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `dep:fm_acme@bk_first` | −1 | 320,000 | `wage` |
| 2 | `dep:ag_maya@bk_first` | +1 | 240,000 | `wage` |
| 3 | `dpl:bk_first` | +1 | 80,000 | `tax` |
| 4 | `res:bk_first` | −1 | 80,000 | `tax` |
| 5 | `dep:gv_treasury@bk_cb` | +1 | 80,000 | `tax` |

Σ = −320,000 + 240,000 + 80,000 − 80,000 + 80,000 = **0** ✔
Effects: M1 falls by 80,000 (tax drains inside money into a central-bank deposit); `bk_first`
loses 80,000 of reserves; Maya's net worth `+240,000`; Acme's `−320,000`; government `+80,000`.

#### E2 — Goods purchase (with sales tax), cross-bank

`ag_maya@bk_first` buys 3 × `fd_prepared` at 250 ¢ from `fm_bakery@bk_second`. Sales tax
800 bp on 750 ¢ = 60 ¢. Buyer pays 810 ¢.

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `dep:ag_maya@bk_first` | −1 | 810 | `purchase` |
| 2 | `dpl:bk_first` | +1 | 810 | `purchase` |
| 3 | `res:bk_first` | −1 | 810 | `purchase` |
| 4 | `res:bk_second` | +1 | 750 | `purchase` |
| 5 | `dpl:bk_second` | −1 | 750 | `purchase` |
| 6 | `dep:fm_bakery@bk_second` | +1 | 750 | `purchase` |
| 7 | `dep:gv_treasury@bk_cb` | +1 | 60 | `tax` |

Σ = −810 + 810 − 810 + 750 − 750 + 750 + 60 = **0** ✔
Real side (no legs): `inventory[fm_bakery, fd_prepared].qty -= 3`, a `goods_transactions` row,
`GOODS_PURCHASED` (6020).

#### E3 — Stock trade through a broker (three transactions)

`ag_kim@bk_first` buys 100 `ACME` at limit 1,260 ¢; resting ask from `ag_rao@bk_second` at
1,250 ¢. Commission 20 bp per side, floor 1 ¢. Broker `fm_broker@bk_first`.

**E3a — reservation at order submission.** Reserve `100 × 1,260 + ceil(126,000 × 20/10,000)`
= 126,000 + 252 = 126,252 ¢.

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `dep:ag_kim@bk_first` | −1 | 126,252 | `escrow` |
| 2 | `esc:ag_kim@bk_first#ord` | +1 | 126,252 | `escrow` |

Σ = **0** ✔ Net worth unchanged; `dpl:bk_first` unchanged (both are claims on `bk_first`).

**E3b — fill at 1,250 ¢ × 100.** Consideration 125,000 ¢; commission 250 ¢ each side. Buyer
pays 125,250; seller receives 124,750; broker receives 500.

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `esc:ag_kim@bk_first#ord` | −1 | 125,250 | `trade` |
| 2 | `dpl:bk_first` | +1 | 124,750 | `trade` |
| 3 | `res:bk_first` | −1 | 124,750 | `trade` |
| 4 | `res:bk_second` | +1 | 124,750 | `trade` |
| 5 | `dpl:bk_second` | −1 | 124,750 | `trade` |
| 6 | `dep:ag_rao@bk_second` | +1 | 124,750 | `trade` |
| 7 | `dep:fm_broker@bk_first` | +1 | 500 | `trade` |

Σ = −125,250 + 124,750 − 124,750 + 124,750 − 124,750 + 124,750 + 500 = **0** ✔
Real side: `holdings[ag_kim, ACME].qty += 100`, `holdings[ag_rao, ACME].qty -= 100`,
`reserved_qty -= 100`, a `trades` row, `TRADE_EXECUTED` (7020). Shares never touch the ledger
(Rule L1).

**E3c — release of unused reservation** (126,252 − 125,250 = 1,002 ¢):

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `esc:ag_kim@bk_first#ord` | −1 | 1,002 | `escrow` |
| 2 | `dep:ag_kim@bk_first` | +1 | 1,002 | `escrow` |

Σ = **0** ✔

#### E4 — Loan origination (inside money creation)

`bk_first` lends 5,000,000 ¢ to `fm_startup` (also at `bk_first`), loan `ln_00004120_0003`.

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `lnr:bk_first#ln_…0003` | +1 | 5,000,000 | `loan` |
| 2 | `dpl:bk_first` | −1 | 5,000,000 | `loan` |
| 3 | `dep:fm_startup@bk_first` | +1 | 5,000,000 | `loan` |
| 4 | `lnp:fm_startup#ln_…0003` | −1 | 5,000,000 | `loan` |

Σ = +5,000,000 − 5,000,000 + 5,000,000 − 5,000,000 = **0** ✔
Effects: **M1 rises by 5,000,000; M0 unchanged.** Bank assets and liabilities both rise;
capital unchanged. Borrower's assets and liabilities both rise; net worth unchanged. The bank
did not need the money first — this is credit creation, and it is what makes an endogenous
credit cycle (§7.12) possible.

#### E5 — Loan repayment (amortising instalment)

Payment 120,000 ¢ = interest 30,000 + principal 90,000.

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `dep:fm_startup@bk_first` | −1 | 120,000 | `loan` |
| 2 | `dpl:bk_first` | +1 | 120,000 | `loan` |
| 3 | `lnr:bk_first#ln_…0003` | −1 | 90,000 | `loan` |
| 4 | `lnp:fm_startup#ln_…0003` | +1 | 90,000 | `loan` |

Σ = −120,000 + 120,000 − 90,000 + 90,000 = **0** ✔
Effects: **M1 falls by 120,000.** Bank: assets −90,000, liabilities −120,000, so capital
**+30,000** — the interest income appears as a residual with no equity account. Borrower: assets
−120,000, liabilities −90,000, so net worth −30,000. Principal repayment destroys money;
interest transfers net worth. Both are correct and neither needed a special case.

Interest legs use `reason='interest'` when posted separately (deposit interest, coupon); an
amortising instalment is posted as one transaction with `reason='loan'` and the split recorded
in `loan_payments` and in `LOAN_PAYMENT_MADE` (8013).

#### E6 — Corporate tax payment

`fm_acme@bk_first` pays 1,000,000 ¢ of assessed corporate tax.

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `dep:fm_acme@bk_first` | −1 | 1,000,000 | `tax` |
| 2 | `dpl:bk_first` | +1 | 1,000,000 | `tax` |
| 3 | `res:bk_first` | −1 | 1,000,000 | `tax` |
| 4 | `dep:gv_treasury@bk_cb` | +1 | 1,000,000 | `tax` |

Σ = **0** ✔ Note the asymmetry versus `transfer`: the central bank has no `dpl` account, so a
payment *to* the government is five legs collapsed to four — reserves leave `bk_first` and
reappear as a government claim on the central bank. Total claims on the central bank are
unchanged, so M0 is unchanged and only its composition shifts.

#### E7 — Inheritance (`04-AGENT-SPEC.md §12.3` steps 4–5)

`ag_dec` dies with 4,200,000 ¢ on deposit and a 1,000,000 ¢ loan. Two heirs, equal shares.

**E7a — settle the debt from the estate:**

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `dep:ag_dec@bk_first` | −1 | 1,000,000 | `loan` |
| 2 | `dpl:bk_first` | +1 | 1,000,000 | `loan` |
| 3 | `lnr:bk_first#ln_…0031` | −1 | 1,000,000 | `loan` |
| 4 | `lnp:ag_dec#ln_…0031` | +1 | 1,000,000 | `loan` |

**E7b — distribute the residual 3,200,000 ¢:**

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `dep:ag_dec@bk_first` | −1 | 3,200,000 | `inheritance` |
| 2 | `dep:ag_h1@bk_first` | +1 | 1,600,000 | `inheritance` |
| 3 | `dep:ag_h2@bk_first` | +1 | 1,600,000 | `inheritance` |

Both Σ = **0** ✔ The decedent's accounts are then closed, which asserts `balance_cents == 0`.
Odd cents are allocated by the largest-remainder rule (§2.3), ties broken by ascending
`agent_id`. If there are no heirs, the residual escheats to `dep:gv_treasury@bk_cb` via
`transfer`. If the estate is insufficient, §10.9 (death-with-insolvency) applies instead.

#### E8 — Bankruptcy write-off

`fm_dead` owes `bk_first` 8,000,000 ¢. Liquidation yields 3,000,000 ¢ for this creditor.

**E8a — recovery:** same shape as E7a, for 3,000,000 ¢.

**E8b — write-off of the 5,000,000 ¢ deficiency:**

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `lnr:bk_first#ln_…0044` | −1 | 5,000,000 | `write_off` |
| 2 | `lnp:fm_dead#ln_…0044` | +1 | 5,000,000 | `write_off` |

Σ = **0** ✔ **No money moves.** The bank's asset disappears and so does the borrower's
liability. Bank capital falls by 5,000,000 (assets down, liabilities unchanged); the debtor's
net worth rises by the same amount (discharge). M0 and M1 are both unchanged — a credit loss
destroys *capital*, not money. Getting this right is why write-offs cascade into bank failure
(§7.11) without leaking a cent.

#### E9 — Interest capitalisation on a delinquent loan (no cash movement)

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `lnr:bk_first#ln_…0044` | +1 | 42,500 | `interest` |
| 2 | `lnp:fm_dead#ln_…0044` | +1 | 42,500 | — |

**Wrong.** Both legs are debits and Σ = +85,000. The correct form is:

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `lnr:bk_first#ln_…0044` | +1 | 42,500 | `interest` |
| 2 | `lnp:fm_dead#ln_…0044` | −1 | 42,500 | `interest` |

Σ = **0** ✔ (`lnp` is a LIABILITY, so crediting it *increases* the debt.) This example is kept
in the spec deliberately: sign errors on liability accounts are the most common ledger bug,
and P6 catches them because the wrong version drives `lnp` positive.

#### E10 — Deposit haircut in a bank resolution (bail-in)

Uninsured depositor `ag_x` takes a 300,000 ¢ haircut at failing `bk_third`.

| # | Account | Dir | Amount ¢ | Reason |
|---|---|---|---|---|
| 1 | `dep:ag_x@bk_third` | −1 | 300,000 | `write_off` |
| 2 | `dpl:bk_third` | +1 | 300,000 | `write_off` |

Σ = **0** ✔ M1 falls by 300,000; the depositor's net worth falls; the failed bank's negative
capital moves toward zero. A bail-in is arithmetically identical to a write-off with the sides
reversed, which is the correct economics.

### 1.7 INV-MONEY — the accounting-closure invariant (V2)

`INV-MONEY` is the conjunction of six checks. **Any failure emits `INVARIANT_VIOLATED` (1010)
and HALTs the run** (`02-ARCHITECTURE.md §9`). It runs in PHASE 9, every tick, with no
sampling and no tolerance. There is no `--continue-on-violation` path that produces
publishable data.

| Sub-check | Statement | Cost | Frequency |
|---|---|---|---|
| **M-1** Transaction balance | For every txn posted this tick, `Σ direction × amount == 0` | O(legs) | at post time (assertion P3) |
| **M-2** Global closure | `Σ_{all accounts} balance_cents == 0` | O(accounts) ≈ 5k | every tick |
| **M-3** Materialisation | For every account, `balance_cents == Σ signed(entries)` | O(entries) | every checkpoint (`03-DATA-MODEL.md §4` rule 3); an incremental running accumulator is compared every tick |
| **M-4** Base-money identity | `Σ cash + Σ res + balance(dep:gv_treasury@bk_cb) + balance(iss:bk_cb) == 0` | O(banks) | every tick |
| **M-5** Deposit identity | For every commercial bank `B`: `Σ balance(dep:*@B) + Σ balance(esc:*@B) + balance(dpl:B) == 0` | O(accounts) | every tick |
| **M-6** Denormalisation | For every agent, `agents.wealth_cents == net_worth(agent)`; for every firm, `firms.capital_cents` reconciles per §4.4; for every bank, `banks.capital_cents == net_worth(bank)` | O(agents) | every tick |

M-2 alone would catch most bugs. M-4 and M-5 are included because they localise the failure:
a break in M-4 means someone touched `iss`, a break in M-5 means a deposit moved without its
bank leg, and a break in M-6 means a projection handler diverged from the ledger. The
Observatory's halt page prints which sub-check failed and the tick's legs grouped by `txn_id`
(`polis ledger explain --run <id> --tick <n>`).

**The V2 gate for M2** (`01-PRD.md §7.2`, `§8`) is: INV-MONEY holds at every tick of a
5-consecutive-sim-year run with a stub LLM and a scripted shock schedule that exercises death
during an open bankruptcy, a bank failure, a partial fill at session close, and an inheritance
with negative net worth. Those four cases are where closure actually breaks.

---

## 2. Determinism, arithmetic, and shared machinery

### 2.1 Integer arithmetic rules

| Situation | Rule | Rationale |
|---|---|---|
| Price derived from a division | `floor` | Never round a price up into a band |
| Amount owed by an agent (payment, tax, fee, commission) | `ceil` | The institution is never short; the agent never gains from rounding |
| Amount owed *to* an agent (interest received, dividend, distribution) | `floor`, remainder allocated by largest-remainder (§2.3) | Sums exactly |
| Splitting a pool among `n` claimants | **Largest-remainder only** (§2.3) | The only allocator that provably sums to the pool |
| Ratios, rates, index levels | Basis points, integer | No float in state |
| Annuity factors, DCF discounting, compound growth | `decimal.Decimal`, context `prec=28, rounding=ROUND_HALF_EVEN`, converted to `int` cents at the boundary | The only sanctioned non-integer intermediate; the context is fixed so it is reproducible across platforms |
| Any other floating-point intermediate touching money | **Banned** | `02-ARCHITECTURE.md §4.6` |

### 2.2 Basis-point helpers

```python
def bp(amount_cents: int, rate_bp: int) -> int:      # floor
    return (amount_cents * rate_bp) // 10_000

def bp_ceil(amount_cents: int, rate_bp: int) -> int:
    return -((-amount_cents * rate_bp) // 10_000)
```

Both take and return `int`. There is no `float` path.

### 2.3 The largest-remainder allocator

The single sanctioned way to split a pool. Used for dividends, pro-rata bankruptcy
distributions, IPO allocations at the marginal price level, fund distributions, inheritance,
and short-borrow fee sharing.

```python
def allocate(pool_cents: int, weights: Sequence[tuple[str, int]]) -> dict[str, int]:
    """weights: (claimant_id, weight). Deterministic; Σ result == pool_cents exactly."""
    W = sum(w for _, w in weights)
    assert W > 0 and pool_cents >= 0
    base = {cid: (pool_cents * w) // W for cid, w in weights}
    rema = {cid: (pool_cents * w) %  W for cid, w in weights}
    short = pool_cents - sum(base.values())
    # Deterministic ordering: larger remainder first, then larger weight, then id ascending.
    order = sorted(weights, key=lambda t: (-rema[t[0]], -t[1], t[0]))
    for cid, _ in order[:short]:
        base[cid] += 1
    return base
```

Property test `tests/unit/test_allocate_exact.py` (Hypothesis): for any pool and any weight
vector, `sum(allocate(...).values()) == pool_cents` and every value is `>= 0`. Failure of this
property is failure mode **F13** (§14).

### 2.4 Force-routed obligations owned by the economy

`04-AGENT-SPEC.md §7` routing step 2 force-routes agents with a **MANDATORY** scheduled
obligation to DELIBERATE before budget allocation. The economy registers these:

| Obligation | Registered by | Deadline |
|---|---|---|
| A pending job offer | `labour` | offer `expires_tick` |
| A pending term sheet | `ventures` | term sheet `expires_tick` |
| A pending acquisition offer on shares you hold | `ventures` | tender close |
| A margin call | `exchange` | next session |
| A loan payment due within `grace_ticks` with insufficient liquid funds | `banking` | due tick |
| Runway below `fundraise_trigger_ticks` (founder only) | `ventures` | monthly |
| A payroll shortfall (owner only) | `firms` | payroll tick |
| A bankruptcy filing against you | `ventures` | filing tick |

This is how the "everything with a counterparty is LLM-only" rule in `04-AGENT-SPEC.md §8`
remains compatible with a market that has to clear: the agent with the decision to make is
guaranteed cognition, and the guarantee is a scheduling fact, not a special case in the
institution.

### 2.5 RNG namespaces owned by `polis/economy/`

Every draw uses `rng.get(namespace, entity_id, tick)`. Tick-scoped streams keep each
subsystem's draws independent of how many draws other subsystems made
(`02-ARCHITECTURE.md §4.1`).

| Namespace | Entity | Use |
|---|---|---|
| `labour.visibility` | `agent_id` | Which vacancies enter the observation slice |
| `labour.screen` | `vacancy_id` | Tie-breaking among equal-scoring applicants |
| `labour.separation` | `firm_id` | Selecting redundancies under a payroll shortfall |
| `goods.search` | `agent_id` | Which sellers enter the observation slice |
| `firms.productivity` | `firm_id` | Idiosyncratic productivity innovation |
| `firms.spoilage` | `firm_id` | Inventory spoilage draw |
| `exchange.arrival` | `symbol` | Intra-tick arrival permutation (§6.4) |
| `exchange.liquidation` | `case_id` | Order slicing during forced liquidation |
| `banking.queue` | `bank_id` | Withdrawal service order under a liquidity shortfall |
| `banking.interbank` | `bank_id` | Counterparty selection among equal offers |
| `ventures.comparables` | `startup_id` | Comparable-set sampling for valuation |
| `ventures.outcome` | `startup_id` | Idiosyncratic revenue realisation |
| `bankruptcy.impact` | `case_id` | Liquidation price impact draw |

**No draw is used to decide a behaviour that a spec calls LLM-only.** Randomness in this module
exists to break ties, sample visibility slices, and generate idiosyncratic shocks — never to
choose an action on an agent's behalf.

### 2.6 Economy invariants

Extends the minimum set in `02-ARCHITECTURE.md §9`.

| ID | Statement | Frequency | On violation |
|---|---|---|---|
| **INV-MONEY** | §1.7, six sub-checks | every tick | **HALT** |
| **INV-LEDGER** | M-1 and M-3 | post time / checkpoint | **HALT** |
| **INV-SHARES** | `Σ holdings.qty == securities.shares_outstanding` per symbol | every tick | **HALT** |
| **INV-CAPTABLE** | `Σ cap_table.shares(f,'common') == securities.shares_outstanding(symbol(f))` for listed firms | every tick | **HALT** |
| **INV-ORDERS** | §6.6 — reservations cover every resting order, and no reservation is negative | every tick | **HALT** |
| **INV-BOOK** | `best_bid(y) < best_ask(y)` for every symbol after matching | every session tick | **HALT** |
| **INV-EMPLOY** | (from `02-ARCHITECTURE.md §9`) one live agent, one live firm per open employment | every tick | **HALT** |
| **INV-SHORT** | `Σ max(0, −qty) ≤ bp(shares_outstanding, max_short_bp)` per symbol | every tick | **HALT** |
| **INV-INTEREST** | At loan close, `Σ interest legs == Σ scheduled interest − forgiven` | at loan close | **HALT** |
| **INV-GDP** | `abs(GDP_expenditure − GDP_production) ≤ n_firms` cents | every sim-quarter | WARN |
| **INV-PRICE** | CPI year-over-year in `[−5,000, +40,000]` bp | every sim-day | WARN; HALT at the bound |
| **INV-PRODUCTION** | Units produced ≤ the production-function bound for the recorded inputs | per production run | WARN |
| **INV-LABSHARE** | `labour_share ≤ 12,000` bp | every sim-day | **HALT** (above 1.2 is arithmetically impossible and indicates a wage or GDP bug) |

---

## 3. Labour market

**Module:** `polis/economy/labour.py` · **Kinds:** 5000–5999 · **PHASE 5 slot 3, PHASE 7 steps 5–6**

### 3.1 Event kinds

| Kind | NAME | Payload fields |
|---|---|---|
| 5001 | `VACANCY_POSTED` | `vacancy_id, firm_id, occupation, skill_reqs{skill:min_level_bp}, wage_offer_cents, headcount, posted_tick, expires_tick, district_id` |
| 5002 | `VACANCY_CLOSED` | `vacancy_id, reason(filled\|withdrawn\|expired\|firm_exit), applicants_n, days_open` |
| 5003 | `JOB_APPLICATION_SUBMITTED` | `application_id, vacancy_id, agent_id, asked_wage_cents, referral_id` |
| 5004 | `APPLICATION_SCREENED` | `application_id, match_score_bp, rank, shortlisted, reject_reason` |
| 5005 | `OFFER_MADE` | `offer_id, vacancy_id, firm_id, agent_id, wage_cents, occupation, expires_tick` |
| 5006 | `OFFER_ACCEPTED` | `offer_id, employment_id, wage_cents` |
| 5007 | `OFFER_DECLINED` | `offer_id, agent_id, reason_code, counter_wage_cents` |
| 5008 | `OFFER_EXPIRED` | `offer_id, agent_id` |
| 5009 | `WAGE_NEGOTIATED` | `offer_id\|employment_id, from_cents, to_cents, round, initiator, outcome(agreed\|withdrawn\|stalled)` |
| 5010 | `HIRED` | `agent_id, firm_id, employment_id, occupation, wage_cents, match_score_bp, search_duration_ticks` |
| 5011 | `FIRED` | `employment_id, agent_id, firm_id, reason(redundancy\|performance\|death\|firm_exit\|acquisition), severance_cents, notice_ticks` |
| 5012 | `QUIT` | `employment_id, agent_id, firm_id, destination(new_job\|unemployment\|self_employment\|retirement\|education)` |
| 5013 | `LAYOFF_BATCH` | `firm_id, employment_ids[], headcount_before, headcount_after, trigger` |
| 5020 | `WORK_PERFORMED` | `employment_id, agent_id, firm_id, hours_bp, effort_bp, effective_labour_bp, skill_deltas{skill:Δ}` |
| 5021 | `ABSENCE` | `employment_id, cause(illness\|energy\|conflict\|strike), hours_lost_bp` |
| 5030 | `PAYROLL_RUN` | `firm_id, period_start_tick, period_end_tick, n_employees, gross_cents, income_tax_cents, employer_tax_cents, net_cents, txn_ids[]` |
| 5031 | `WAGE_PAID` | `employment_id, agent_id, firm_id, gross_cents, income_tax_cents, net_cents, hours_bp, txn_id` |
| 5032 | `PAYROLL_SHORTFALL` | `firm_id, required_cents, available_cents, unpaid_employment_ids[], accrued_claim_cents` |
| 5040 | `SKILL_DECAYED` | `agent_id, skill, from_level_bp, to_level_bp, ticks_unused` |
| 5041 | `UNEMPLOYMENT_SPELL_STARTED` | `agent_id, prior_employment_id, prior_wage_cents, cause` |
| 5042 | `UNEMPLOYMENT_SPELL_ENDED` | `agent_id, duration_ticks, exit(job\|nilf\|death\|self_employment), new_wage_cents, wage_change_bp` |
| 5050 | `LABOUR_SESSION_SUMMARY` | `tick, vacancies_open, searchers, applications, offers, hires, mean_match_score_bp, mean_offer_wage_cents, median_hire_wage_cents` |
| 5060 | `SELF_EMPLOYMENT_STARTED` | `agent_id, firm_id, sector` |
| 5061 | `SELF_EMPLOYMENT_ENDED` | `agent_id, firm_id, reason` |
| 5070 | `RETIRED` | `agent_id, age_years, final_wage_cents, pension_entitlement_cents` |
| 5080 | `BENEFIT_CLAIM_OPENED` | `agent_id, weekly_benefit_cents, entitlement_ticks, base_wage_cents` |
| 5081 | `BENEFIT_EXHAUSTED` | `agent_id, ticks_claimed` |

### 3.2 Vacancy posting

A vacancy is a `vacancies` row (`03-DATA-MODEL.md §5`) plus a `VACANCY_POSTED` event.

| Field | Source |
|---|---|
| `occupation` | Chosen by the poster from the closed occupation catalogue (§3.3) |
| `skill_reqs` | The occupation's requirement profile, optionally raised by the poster |
| `wage_offer_cents` | Per pay period. LLM-set by the owner, or the mechanical anchor (§3.9) |
| `headcount` | Number of positions; each fill decrements it, `0` closes the vacancy |
| `expires_tick` | `posted_tick + vacancy_ttl_ticks` (default 30 sim-days) |

`POST_VACANCY` is an LLM-only action (`04-AGENT-SPEC.md §8`) issued by the firm's owner or a
manager-occupation employee with delegated capability. A firm may have at most
`max_open_vacancies_per_firm` (default 5) open at once — a rate limit, checked in the PHASE 4
capability gate.

> **MECHANISM `labour.vacancy_autopost`** — *entails:* "A firm whose realised output has fallen
> short of its inventory target for `autopost_window` consecutive sim-days posts one vacancy at
> the occupation-median wage. This guarantees the labour market does not freeze when owners are
> not routed to DELIBERATE, and it makes vacancy creation weakly increasing in unmet demand. It
> does **not** determine how many vacancies are filled, at what wage, or by whom." Default on;
> ablatable via `mechanisms.labour_vacancy_autopost: off`.

### 3.3 Occupations

A closed catalogue, indexed on the 14 skills of `04-AGENT-SPEC.md §3`. Each occupation carries
a requirement vector `req[skill] ∈ [0,10_000] bp`, an intensity vector `int[skill]` (which
skills grow on the job, `04-AGENT-SPEC.md §3` growth-on-work rule), and a productivity weight
vector `w[skill]` used in the production function (§4.2) and the wage anchor (§3.9).

| Occupation | Dominant skills (req ≥ 5,000 bp) | Sectors |
|---|---|---|
| `labourer` | manual | industrial, food, retail |
| `operator` | manual, operations | industrial |
| `clerk` | operations, writing | services, finance, retail |
| `sales_rep` | sales, persuasion | retail, services, media |
| `accountant` | finance, operations | finance, services |
| `analyst` | finance, research | finance |
| `engineer` | engineering, research | industrial, services |
| `researcher` | research, writing | health, education, industrial |
| `lawyer` | law, negotiation, persuasion | services |
| `physician` | medicine, research | health |
| `nurse` | medicine, manual | health |
| `teacher` | teaching, writing | education |
| `journalist` | writing, research, persuasion | media |
| `designer` | design, writing | media, industrial |
| `manager` | management, negotiation, operations | all |
| `executive` | management, negotiation, finance, persuasion | all |
| `founder` | ambition-gated; no requirement floor | all (self-employment, §4.10) |
| `officer` | manual, law | government (police, §11.4) |

Occupation catalogue lives in `configs/occupations.yaml` and is hashed into the run manifest.
Adding an occupation is a config change; the 14-skill vocabulary is closed and is not.

### 3.4 Search, visibility, and application

Search is **decentralised and agent-initiated**. There is no aggregate matching function
anywhere in the codebase. See §3.10 for why this matters.

**Visibility slice.** `Observation.offers` and the place view expose at most
`vacancy_visibility_k` (default 8) open vacancies to a searching agent, selected as:

```
pool = open vacancies with headcount > 0
priority order:
  1. referrals: vacancies at firms where a strong tie (relationships.strength ≥ 0.6) works
  2. same district as agent.home_place_id or agent.current_place_id
  3. occupation matching agent's declared occupation or education_level
  4. remainder
take the first vacancy_visibility_k after a seeded shuffle within each priority band:
  rng.get("labour.visibility", agent_id, tick)
```

> **MECHANISM `labour.vacancy_visibility`** — *entails:* "An agent can only apply to vacancies in
> its visibility slice, so application volume per agent per tick is bounded by 1 (the action
> slot) and the reachable set is bounded by `k`. This makes referral networks matter and makes
> spatial mismatch possible. It does **not** imply any relationship between the aggregate stock
> of vacancies and the aggregate number of matches, because the number of applications, offers,
> and acceptances are each separate agent decisions."

**Application.** `APPLY_FOR_JOB{vacancy_id, asked_wage_cents?}` — LLM-only, one per action slot.
Creates a `job_applications` row with `outcome='pending'` and emits 5003. An agent may hold at
most `max_open_applications` (default 6) pending applications; the PHASE 4 resources gate
rejects beyond that.

**Frictions that exist, explicitly enumerated:**

| Friction | Source | Minimum delay imposed |
|---|---|---|
| Information | visibility slice `k` | — |
| Action budget | 1 application per tick (`02-ARCHITECTURE.md §6.3`) | 1 tick per application |
| Screening cadence | applications screened in PHASE 7 step 5, once per sim-day | ≤ 24 ticks |
| Offer decision | agent must be routed to DELIBERATE (force-routed, §2.4) | 1 tick |
| Acceptance settlement | employment starts the following tick | 1 tick |

Minimum time from vacancy post to first day of work: **3 ticks**; realistic path 30–120 ticks.

### 3.5 Match scoring from the 14-skill vector

Computed mechanically in PHASE 7 step 5 for every pending application. Pure function, no LLM,
no randomness except tie-breaking.

```python
def match_score_bp(agent: AgentState, vac: Vacancy, occ: Occupation) -> int:
    req = vac.skill_reqs                      # {skill: min_level_bp}
    if not req:
        return 10_000
    # 1. Requirement satisfaction: how much of each requirement is met, capped at 1.
    met      = sum(min(agent.skill_bp[s], lvl) for s, lvl in req.items())
    demanded = sum(req.values())
    fit      = (10_000 * met) // demanded                      # 0..10_000

    # 2. Surplus: skill above requirement, weighted by the occupation's productivity weights,
    #    with sharply diminishing returns (overqualification is worth little).
    surplus  = sum(occ.w[s] * max(0, agent.skill_bp[s] - lvl) for s, lvl in req.items())
    surplus  = min(2_000, surplus // (10_000 * max(1, len(req))))

    # 3. Modifiers from simulation state only.
    edu_bp   = EDU_BONUS_BP[agent.education_level]             # none 0 … graduate 800
    exp_bp   = min(1_000, agent.ticks_worked_in(occ.id) // ticks_per_sim_year * 200)
    rep_bp   = int(2_000 * (agent.reputation - 0.5))           # -1000 .. +1000
    rec_bp   = -min(1_500, agent.unemployed_ticks // ticks_per_sim_day * 5)   # recency penalty
    crim_bp  = -400 * min(3, agent.criminal_record)

    return clamp(0, 10_000, fit + surplus + edu_bp + exp_bp + rep_bp + rec_bp + crim_bp)
```

Firms shortlist applicants with `match_score_bp ≥ vacancy.min_match_score_bp` (default 5,500),
ranked descending, ties broken by `rng.get("labour.screen", vacancy_id, tick)` — **not** by
`agent_id`, which would create a systematic alphabetical advantage. Top `headcount ×
shortlist_multiple` (default 3) are shortlisted; the rest are rejected with a reason code that
appears in the applicant's next `Observation` (`04-AGENT-SPEC.md §11`), so agents can learn
what they are unqualified for.

> **MECHANISM `labour_matching: stochastic_skill_match`** (named in `02-ARCHITECTURE.md §8`) —
> *entails:* "Offer probability is weakly increasing in an applicant's skill levels relative to
> the posted requirement, weakly decreasing in spell length through `rec_bp`, and weakly
> decreasing in criminal record. Any finding that skilled workers are hired more often, or that
> the long-term unemployed are hired less often *cross-sectionally*, follows analytically and is
> **not** a result. Findings about duration-dependence *dynamics*, wage outcomes, cyclical
> variation, or the vacancy–unemployment relationship do not follow from this rule."

The `rec_bp` term is deliberately small (max −1,500 bp) because the *intended* scarring channel
is skill decay (§3.8), which is a state change rather than a screening penalty. `rec_bp` exists
so employer-side statistical discrimination is present and separately ablatable
(`mechanisms.labour_recency_penalty: off`).

### 3.6 Offer, negotiation, acceptance

```
PHASE 7 step 5 (daily, 09:00):
  for each open vacancy, sorted by (firm_id, vacancy_id):
      screen pending applications  → APPLICATION_SCREENED (5004)
      shortlist                    → the firm owner receives an "offer decision" obligation
PHASE 5 slot 3 (any tick):
  firm owner issues MAKE_OFFER{application_id, wage_cents} → OFFER_MADE (5005)
      LLM-only. Mechanical fallback if the owner is not routed for offer_stale_ticks
      (default 3 sim-days): offer the top-ranked shortlisted applicant at wage_offer_cents.
  applicant (force-routed, §2.4) issues one of:
      ACCEPT_OFFER{offer_id}                  → OFFER_ACCEPTED (5006) → HIRED (5010)
      DECLINE_OFFER{offer_id, reason}         → OFFER_DECLINED (5007)
      NEGOTIATE_WAGE{offer_id, counter_cents} → WAGE_NEGOTIATED (5009)
  no action by expires_tick                   → OFFER_EXPIRED (5008)
```

**Wage bargaining.** At most `max_bargaining_rounds` (default 2) exchanges per offer. Each
round consumes one action slot on each side. Both sides' moves are LLM decisions; neither has a
scripted concession schedule. The only mechanical elements are:

| Element | Rule |
|---|---|
| Firm's acceptance ceiling | The PHASE 4 resources gate rejects `MAKE_OFFER` if `wage_cents × pay_periods_per_year > firm liquid + expected revenue over the period`. A firm cannot offer a wage it demonstrably cannot pay. |
| Agent's floor | None. An agent may accept any wage, including below subsistence. Imposing a reservation wage would manufacture the very labour-supply elasticity the model is meant to measure. |
| Round limit | After 2 rounds without agreement, the standing offer must be accepted, declined, or expires. |
| Withdrawal | The firm may withdraw at any round; the applicant returns to the pool with `outcome='withdrawn'`. |

**Hiring** creates an `employments` row, sets `agents.employer_id` and `occupation`, sets
`employment_status='employed'`, closes the agent's other pending applications with
`outcome='withdrawn'`, decrements `vacancies.headcount`, and emits `HIRED` (5010) and
`UNEMPLOYMENT_SPELL_ENDED` (5042) with `wage_change_bp` relative to the prior job — the raw
material for wage-scarring analysis.

### 3.7 Separation: firing, quitting, and unpaid wages

| Path | Trigger | Action | Consequences |
|---|---|---|---|
| **Fire (discretionary)** | Owner decision | `FIRE_EMPLOYEE{employment_id, reason}` (LLM-only) | `FIRED` (5011). Severance `= bp(period_wage × severance_periods_bp, 10_000)`, default `severance_periods_bp = 0`; a policy parameter (§11.2). Notice period `notice_ticks` (default 0) during which wages accrue. |
| **Redundancy (mechanical)** | `PAYROLL_SHORTFALL` (5032) | none — institutional | The firm must shed headcount until projected payroll ≤ projected liquidity. Selection order: ascending `match_score_bp × sqrt(tenure_ticks)`, ties by `rng.get("labour.separation", firm_id, tick)`. `LAYOFF_BATCH` (5013). |
| **Firm exit** | Dissolution or bankruptcy discharge | none | All employments end with `reason='firm_exit'`; accrued unpaid wages become a **class-2 priority claim** (§10.5). |
| **Quit** | Agent decision | `QUIT_JOB{employment_id, destination}` (LLM-only) | `QUIT` (5012). Accrued wages for ticks already worked are paid immediately (see F15, §14). |
| **Death** | PHASE 8 | none | `FIRED{reason:'death'}` per `04-AGENT-SPEC.md §12.3` step 2; the firm gains a vacancy. |
| **Retirement** | Agent decision at age ≥ `retirement_age` | `QUIT_JOB{destination:'retirement'}` | `RETIRED` (5070); pension entitlement computed from contribution history (§11.4). |

> **MECHANISM `labour.redundancy_selection`** — *entails:* "Under a payroll shortfall the firm
> sheds its lowest-scoring, shortest-tenure employees first. This implies that layoffs are
> concentrated among low-skill, recently-hired workers, so 'last in, first out' patterns and
> the skill composition of unemployment inflows are **implied**, not emergent. The *frequency*
> and *timing* of shortfalls are not implied."

**Wage accrual.** Wages accrue per tick worked, tracked in an in-memory projection
`accrued_wage_cents[employment_id]` rebuilt from `WORK_PERFORMED` (5020) and `WAGE_PAID` (5031)
events. Payroll (PHASE 7 step 6) pays the accrued balance and zeroes it. Separation of any kind
pays the accrued balance immediately if the firm can; if not, it becomes an unsecured claim and
a `PAYROLL_SHORTFALL` is emitted. This closes the timing exploit in which an agent works a full
period and quits before payday, or a firm fires everyone the day before payroll.

### 3.8 Unemployment scarring via skill decay

`04-AGENT-SPEC.md §3` specifies decay: `Δ = −decay_rate × level` per sim-month unused, default
`decay_rate = 0.004`. The economy supplies the definition of *used*:

```
a skill s is "used" by agent a in tick t  iff
    ∃ open employment e for a with occupation o such that o.int[s] > 0
    and a emitted WORK_PERFORMED this tick
  or a is enrolled and the curriculum weights s
  or a is self-employed in a sector whose SKU production weights s
```

`agent_skills.last_used_tick` is updated on use. Decay is applied in PHASE 7 monthly, emitting
`SKILL_DECAYED` (5040). Because `match_score_bp` (§3.5) reads skill levels directly, a long
unemployment spell mechanically lowers future match scores, which lowers offer probability,
which lengthens the spell. **This is the poverty-trap channel and it is the interesting one**
because the trap is a consequence of two independently-motivated rules rather than a rule that
says "the long-term unemployed stay unemployed."

> **MECHANISM `skill_decay`** — *entails:* "Unused skills fall at a constant proportional rate.
> Combined with skill-based screening this implies re-employment probability is weakly
> decreasing in spell length. It does **not** imply the shape of the hazard, the existence of a
> poverty trap at any particular income level, the interaction with the aggregate cycle, or any
> effect on wages conditional on re-employment." Ablation: `mechanisms.skill_decay: off`.

### 3.9 Wage anchors and mechanical fallbacks

Wages are set by agents. The anchors below exist only so the mechanical fallback policy and
the `--reflex-only` baseline (§4.11) have something to compute; they are never applied to an
LLM-set wage.

```
skill_value(a, occ) = Σ_s occ.w[s] × agent.skill_bp[s] / Σ_s occ.w[s]        # 0..10_000
anchor_cents(occ)   = base_wage_cents[occ] × (5_000 + skill_value) // 10_000
```

`base_wage_cents[occ]` is a config table calibrated so that the population-weighted median
anchor equals `calibration.median_wage_cents` (§13). The realised wage distribution is an
outcome, not this table.

### 3.10 Unemployment: the formal definition (threat T11)

Stated purely in terms of simulation state. `04-AGENT-SPEC.md §12.2` supplies the life stages;
`03-DATA-MODEL.md §5` supplies `employments` and `job_applications`.

```
employed(a, t)  ≡ ∃ e ∈ employments :
                      e.agent_id = a ∧ e.started_tick ≤ t ∧
                      (e.ended_tick IS NULL ∨ e.ended_tick > t)

self_emp(a, t)  ≡ agents[a].employment_status = 'self_employed' at tick t

searching(a, t) ≡ ∃ ap ∈ job_applications :
                      ap.agent_id = a ∧ 0 ≤ t − ap.tick ≤ search_window_ticks
                  ∨ ∃ ev ∈ events : ev.kind = 5003 ∧ ev.actor_id = a ∧
                      0 ≤ t − ev.tick ≤ search_window_ticks

age_eligible(a,t)≡ 18 ≤ agents[a].age_years < retirement_age            (default 65)

E(t)  = { a : alive(a,t) ∧ age_eligible(a,t) ∧ (employed(a,t) ∨ self_emp(a,t)) }
U(t)  = { a : alive(a,t) ∧ age_eligible(a,t) ∧ ¬employed(a,t) ∧ ¬self_emp(a,t)
              ∧ agents[a].employment_status ∉ {'child','student','retired','dead'}
              ∧ searching(a, t) }
NILF(t) = { a : alive(a,t) ∧ age_eligible(a,t) } \ (E(t) ∪ U(t))

LF(t)      = |E(t)| + |U(t)|
u(t)       = 10_000 × |U(t)| / LF(t)                        # basis points
lfpr(t)    = 10_000 × LF(t) / |{a : alive(a,t) ∧ age_eligible(a,t)}|
```

`search_window_ticks` defaults to 4 sim-weeks. Two auxiliary series are computed and reported
alongside the headline:

| Series | Definition | Purpose |
|---|---|---|
| `u_marginal` | as `u(t)` but `search_window_ticks = 12 sim-weeks` | Captures discouraged searchers who have paused |
| `u_broad` | `(|U| + |marginally attached| + |involuntary part-time|) / (LF + marginally attached)` | Involuntary part-time = employed with `hours_bp < 5,000` and ≥1 application in the window |

**Real-world analogue, named separately (T11):** `u(t)` corresponds to the ILO/BLS **U-3**
unemployment rate; `u_broad` to **U-6**; `lfpr(t)` to the prime-age labour force participation
rate. The correspondence is *structural* — same construction from micro-records — and is not a
claim that the magnitudes are comparable to any real economy.

**Vacancy rate**, needed for the Beveridge curve:

```
V(t)      = Σ over open vacancies of remaining headcount
v_rate(t) = 10_000 × V(t) / (V(t) + |E(t)|)
```

Analogue: JOLTS job-openings rate.

### 3.11 Why search friction here does not analytically imply a Beveridge curve (threat T6)

This is the sharpest instance of T6 in the whole system, and `01-PRD.md §9` names it by name.

**What Polis does not do.** There is no aggregate matching function. No line of code computes
`M = A·U^α·V^(1−α)`, or any other function whose arguments are the aggregate stocks of
unemployed and vacancies. `grep -r "def match" polis/economy/labour.py` returns only
`match_score_bp`, which is a function of **one agent and one vacancy**.

**What Polis does instead.** The number of hires at tick `t` is
`Σ_a Σ_v 1[a applied to v] · 1[firm offered a] · 1[a accepted]`. Each indicator is a separate
decision by a separate LLM-driven actor with its own information set. The aggregate is a
sum over microdecisions and is not computed anywhere as an aggregate.

**Which mechanisms could still smuggle a curve in, and why they do not.** A Beveridge curve
requires a *negative* co-movement of `u` and `v_rate`. The candidate mechanisms are:

| Mechanism | Could it imply the curve? | Why not, or what it would take |
|---|---|---|
| `labour.vacancy_visibility` | It bounds the reachable set per agent, not the fill rate. If `k ≥ V`, every agent sees every vacancy and the friction vanishes; the curve, if any, must survive that limit. | Ablation `vacancy_visibility_k: ∞` is part of the A1 protocol |
| `labour_matching` (§3.5) | Gives an offer probability per (agent, vacancy) pair. It is silent on aggregates. Holding `V` fixed and doubling `U` changes the *composition* of applicants, not a fill rate parameter. | — |
| `labour.vacancy_autopost` | Ties vacancy creation to unmet demand, which is where a spurious correlation would come from. | Ablation `labour_vacancy_autopost: off` is **mandatory** for any A1 Beveridge claim |
| Action slots | Bounds applications per agent per tick to 1, so aggregate application flow is proportional to the number of searchers. This *is* a mechanical link between `U` and match flow. | Reported explicitly; the curve must be shown to have a slope that does not follow from `applications ∝ U` alone, which is testable by regressing hires on applications and vacancies separately |

**The falsification protocol.** `mechanisms.labour_matching: aggregate_cobb_douglas` implements
the textbook matching function as an explicit **comparison baseline**, not as the default. Any
claimed emergent Beveridge curve must be accompanied by:

1. The same statistic computed under `--reflex-only` (§4.11), which has mechanical decisions
   throughout. If the curve is indistinguishable, the LLM contributed nothing (threat **T9**).
2. The same statistic under `labour_matching: aggregate_cobb_douglas`. If the decentralised
   curve is indistinguishable from the mechanically-implied one *in slope and in the size of
   loops around it*, the finding is not evidence.
3. A statement of which mechanisms were active, with their `entails` strings, per the reviewer
   checklist in `10-RESEARCH-AND-OBSERVABILITY.md`.

The interesting object is not the existence of a downward-sloping cloud — that is nearly
unavoidable in any model where hiring reduces both stocks. It is the **counter-clockwise loops**
around the curve over a cycle, the shift of the curve after a shock, and the relationship
between the curve and match efficiency, none of which follow from any mechanism listed above.

---

## 4. Firms and production

**Module:** `polis/economy/firms.py` · **Kinds:** 6000–6099 · **PHASE 7 steps 1–4**

### 4.1 Event kinds (firms)

| Kind | NAME | Payload fields |
|---|---|---|
| 6001 | `FIRM_FOUNDED` | `firm_id, founder_id, name, sector, place_id, initial_capital_cents, ledger_account_id, is_startup, registration_fee_cents` |
| 6002 | `FIRM_DISSOLVED` | `firm_id, reason(voluntary\|bankruptcy\|acquisition\|founder_death), residual_cents, headcount_at_exit, age_ticks` |
| 6003 | `FIRM_STATUS_CHANGED` | `firm_id, from, to, trigger, net_worth_cents, liquid_cents` |
| 6004 | `OWNERSHIP_TRANSFERRED` | `firm_id, from_holder, to_holder, shares, share_class, cause(inheritance\|sale\|acquisition\|escheat)` |
| 6010 | `PRODUCTION_RUN` | `firm_id, sku, labour_bp, capital_cents_used, productivity_bp, output_micro, units_produced, unit_cost_cents, carry_micro_after` |
| 6011 | `CAPITAL_PURCHASED` | `firm_id, seller_firm_id, sku, units, cents, capital_cents_after, txn_id` |
| 6012 | `CAPITAL_DEPRECIATED` | `firm_id, from_cents, to_cents, rate_bp` |
| 6013 | `INVENTORY_WRITTEN_OFF` | `firm_id, sku, units, unit_cost_cents, value_cents, reason(spoilage\|obsolescence\|liquidation)` |
| 6014 | `PRODUCTIVITY_UPDATED` | `firm_id, from_bp, to_bp, cause(learning\|capital_deepening\|shock\|integration)` |
| 6022 | `PRICE_SET` | `firm_id, sku, from_cents, to_cents, rule(markup\|inventory_adjust\|llm_owner), markup_bp, inventory_days` |
| 6023 | `RESTOCK_ORDERED` | `firm_id, sku, from_firm_id, units, cents, txn_id` |
| 6030 | `DIVIDEND_DECLARED` | `firm_id, per_share_cents, total_cents, record_tick, payable_tick, decided_by(policy\|owner)` |
| 6031 | `DIVIDEND_PAID` | `firm_id, holder_id, shares, cents, txn_id` |
| 6040 | `FIRM_PERIOD_CLOSED` | `firm_id, period, revenue_cents, wage_cents, input_cents, depreciation_cents, interest_cents, tax_cents, profit_cents, cumulative_losses_cents` |

### 4.2 Production function

Applied per firm per SKU in PHASE 7 step 1 (daily, 06:00). Cobb–Douglas in effective labour
and capital, with a firm-specific productivity term.

```
L_f  = Σ_{e ∈ open employments of f} hours_bp(e) × effort_bp(e) × skill_value(a_e, occ_e) / 10_000²
       # effective labour in "worker-day" units, integer bp
K_f  = firms.capital_cents

Y_micro(f, sku) = A_f × (K_f/K_ref)^β × (L_f/10_000)^(1−β) × yield[sku] × 1_000_000
units           = (carry_micro + Y_micro) // 1_000_000
carry_micro'    = (carry_micro + Y_micro) %  1_000_000
```

| Symbol | Meaning | Default |
|---|---|---|
| `A_f` | `firms.productivity`, stored as bp of the sector mean | 10,000 |
| `β` | capital share | 3,000 bp |
| `K_ref` | capital normaliser so `K = K_ref` gives factor 1 | `calibration.capital_ref_cents` |
| `yield[sku]` | units of SKU per effective worker-day at `A=1, K=K_ref` | per-SKU config |

The exponentiation is the only place a non-integer intermediate appears; it uses the
`Decimal` context of §2.1 and is floored into `Y_micro`. The **carry** (`carry_micro`) is an
in-memory projection rebuilt from `PRODUCTION_RUN.output_micro` and is what allows a
one-person firm producing 0.7 units/day to produce 7 units in 10 days rather than 0 forever.

`effort_bp` is `10,000` for an agent who emitted `WORK_PERFORMED` this tick, scaled by
`health` and `energy`; `0` on absence. Multi-SKU firms split `L_f` across their SKUs in
proportion to the previous period's revenue share, allocated by §2.3.

> **MECHANISM `firms.production_cobb_douglas`** — *entails:* "Output is homogeneous of degree
> one in labour and capital jointly, with diminishing returns to each separately, and is
> multiplicatively separable in productivity. This implies constant returns to scale, a
> constant factor-share split under competitive pricing, and that doubling all inputs doubles
> output. Any claimed finding about returns to scale, the labour share's *level*, or capital
> deepening's effect on output per worker follows analytically. Findings about the *dynamics*
> of the labour share, the distribution of firm productivity, or investment timing do not."

### 4.3 Productivity

```
A_f(t+1) = clamp(2_000, 40_000,
             A_f(t)
             + learning_bp × cumulative_output_growth
             + rng.get("firms.productivity", firm_id, tick).normal(0, σ_A)
             + integration_delta )                                # from M&A, §9.5
```

`learning_bp` (default 3 bp/sim-day at full utilisation) gives learning-by-doing; `σ_A`
(default 40 bp) gives idiosyncratic dispersion, which is what allows a productivity
distribution and reallocation effects to exist at all. Declared MECHANISM
`firms.productivity_drift`, *entails:* "productivity is a random walk with a small positive
drift proportional to utilisation, bounded. Aggregate productivity growth is therefore
weakly positive by construction; only its *rate*, dispersion, and reallocation component are
outcomes."

### 4.4 Capital and inventory

| Quantity | Where | Ledger? | Lifecycle |
|---|---|---|---|
| Capital | `firms.capital_cents` | **No** (Rule L1) | Increased by purchase of a `cap_*` SKU at cost; decreased by `CAPITAL_DEPRECIATED` at `depreciation_bp` per sim-year (default 1,000 bp, straight line, applied monthly); realised at liquidation value in bankruptcy |
| Inventory | `inventory(firm_id, sku)` | **No** | Increased by `PRODUCTION_RUN` at `unit_cost_cents`; decreased by sale, spoilage, or liquidation |
| Unit cost | `inventory.unit_cost_cents` | — | Weighted-average cost: `(old_qty × old_cost + new_qty × new_cost) // (old_qty + new_qty)`; new production's `unit_cost = (period wage bill + input cost + depreciation charge) // units_produced` |

Spoilage: perishable SKUs (`fd_fresh`, `fd_prepared`) lose `spoilage_bp` (default 2,000 bp/day)
of units, drawn via `rng.get("firms.spoilage", firm_id, tick)` to give idiosyncratic variation.
`INVENTORY_WRITTEN_OFF` (6013) with **no ledger transaction**.

M-6 reconciliation for firms: `firms.capital_cents` is a real-asset projection and is *not*
checked against the ledger; what is checked is that every `CAPITAL_PURCHASED` event has a
matching balanced transaction whose debited amount equals the capital increment.

### 4.5 Price setting

Two rules, selected by `mechanisms.price_setting` (`02-ARCHITECTURE.md §8` names
`markup_over_cost` as the default).

**Rule A — `markup_over_cost` (mechanical, PHASE 7 step 3, weekly).**

```
inventory_days = 10_000 × inventory.qty // max(1, avg_daily_units_sold_28d)
if inventory_days < target_low_bp:    markup_bp += step_bp
if inventory_days > target_high_bp:   markup_bp -= step_bp
markup_bp = clamp(0, max_markup_bp, markup_bp)
price_cents = max(1, round_to_tick(unit_cost_cents × (10_000 + markup_bp) // 10_000))
```

Defaults: `target_low_bp = 70,000` (7 days), `target_high_bp = 300,000` (30 days),
`step_bp = 200`, `max_markup_bp = 8,000`, initial `markup_bp = 2,500`.

> **MECHANISM `price_setting: markup_over_cost`** — *entails:* "Prices move in the opposite
> direction to inventory and are bounded below by unit cost. This implies a negative
> contemporaneous correlation between inventory and price change at the firm level, and it
> implies that a cost shock passes through to prices within `1/step_bp` periods. It does
> **not** imply any relationship between aggregate demand and the aggregate price level, nor
> any Phillips-curve relation, because demand is the sum of agent purchase decisions and wages
> are bargained. An emergent Phillips curve is therefore not excluded by this mechanism, but
> the reviewer must confirm that the *cost channel* alone does not produce it — the check is
> the `--reflex-only` baseline."

**Rule B — `llm_owner`.** `SET_PRICE{sku, price_cents}` by the firm's owner, DELIBERATE only.
Rule A still runs as a default when the owner is not routed; an LLM-set price persists until
the owner changes it or `price_override_ttl_ticks` (default 30 sim-days) elapses.
`mechanisms.price_setting: hybrid` (Rule A default, Rule B override) is the recommended
research setting because it makes the LLM's contribution to price dynamics measurable as the
difference between `hybrid` and `markup_over_cost`.

### 4.6 Firm decisions: LLM versus mechanical

This table is normative. "Mechanical" means the institution decides without any LLM call, and
the rule is a declared MECHANISM. "LLM" means the decision only happens if the owner or a
delegated employee is routed to DELIBERATE.

| Decision | Default | Actor | Action type | Fallback if not routed |
|---|---|---|---|---|
| Daily production quantity | **Mechanical** | — | — | n/a — produce to capacity given labour on hand |
| Split labour across SKUs | **Mechanical** | — | — | n/a |
| Input restock / reorder | **Mechanical** (reorder point `s`, order-up-to `S`) | — | `RESTOCK` | n/a |
| Price change | **Mechanical** markup rule | owner | `SET_PRICE` | Rule A |
| Post a vacancy | **LLM** | owner / manager | `POST_VACANCY` | `labour.vacancy_autopost` after `autopost_window` |
| Screen applications | **Mechanical** | — | — | n/a — screening is a filter, not a commitment |
| Make an offer and set its wage | **LLM** | owner / manager | `MAKE_OFFER` | Top-ranked shortlisted applicant at the posted wage, after `offer_stale_ticks` |
| Fire an employee | **LLM** | owner / manager | `FIRE_EMPLOYEE` | none — only the shortfall path (§3.7) fires mechanically |
| Raise an incumbent's wage | **LLM** | owner | `NEGOTIATE_WAGE{employment_id}` | none |
| Buy capital | **LLM** | owner | `BUY_GOOD{sku: cap_*}` | none |
| Borrow | **LLM** | owner | `APPLY_FOR_LOAN` | none |
| Repay a loan | **Mechanical** for the scheduled instalment (reflex-eligible, `04-AGENT-SPEC.md §8`); **LLM** for early or partial repayment | owner | `REPAY_LOAN` | scheduled instalment |
| Declare a dividend | **Mechanical** payout policy | owner | `DECLARE_DIVIDEND` (§0.2) | `payout_ratio_bp` of positive retained profit, quarterly |
| Go public | **LLM** | owner | `IPO_LIST` | none |
| Acquire another firm | **LLM** | owner | `ACQUIRE` | none |
| Accept an acquisition offer | **LLM** | each holder | `ACCEPT_OFFER` / `DECLINE_OFFER` | none — offer expires |
| Found a firm | **LLM** | any adult | `FOUND_COMPANY` | none |
| File for bankruptcy | **LLM** for voluntary; **mechanical** for the creditor/insolvency triggers (§10.2) | owner | `FILE_BANKRUPTCY` | insolvency trigger fires regardless |

**Rationale for the split.** `04-AGENT-SPEC.md §8` is the governing rule: anything with a
counterparty, a negotiated price, or a commitment is LLM-only. Production scheduling and
inventory reordering have none of those properties; wages, prices, hiring, firing, borrowing,
and M&A all do. The split is also what keeps cost inside the budget: a 60-firm economy
generates a few hundred owner decisions per sim-day, not one per firm per tick.

### 4.7 Firm entry

`FOUND_COMPANY{name, sector, place_id, initial_capital_cents, is_startup, thesis?}` — LLM-only.

| Gate | Check (PHASE 4) |
|---|---|
| Age | `age_years ≥ 18` |
| Funds | `liquid(founder) ≥ initial_capital_cents + registration_fee_cents` |
| Minimum capital | `initial_capital_cents ≥ min_founding_capital_cents` (default 1 sim-month of median wage) |
| Place | A vacant `office`/`shop`/`factory` place exists in `place_id`'s district with capacity |
| Concurrency | Founder has `< max_firms_per_founder` (default 3) active firms |

No trait gate. An agent with low `ambition` is *less likely to choose* to found a firm because
its cognition says so, not because a rule forbids it. Founding posts one transaction (founder's
deposit → firm's new deposit, plus the registration fee to the treasury), creates `firms` and
`cap_table` rows (founder holds 100% of `common`), and emits `FIRM_FOUNDED` (6001).

### 4.8 Firm exit and the size distribution

| Exit path | Trigger | Terminal status |
|---|---|---|
| Voluntary dissolution | Owner action, solvent, no employees | `dissolved` |
| Bankruptcy | §10 | `bankrupt` → `dissolved` |
| Acquisition (absorb) | §9.5 | `acquired` |
| Founder death, no heirs, no buyer | `04-AGENT-SPEC.md §12.3` step 5 → escheat → government dissolves | `dissolved` |

**Firm size distribution.** Size is `headcount` (primary) and `revenue_cents` (secondary).
Zipf's law of firm sizes is research question **A1**. There is deliberately **no** growth rule:
firm growth is `Σ (hires − separations)`, each of which is an individual decision. Gibrat's law
(growth independent of size) is *not* imposed; if it appears, it is a finding.

> **T6 note.** Two things could smuggle a size distribution in. (a) The initial seed: §13 seeds
> firm sizes log-normally with small dispersion, **not** as a power law, precisely so the tail
> has to be produced by the run. Any A1 firm-size claim must be measured at least 3 sim-years
> after genesis and must show that the tail exponent moved away from the seed. (b)
> `max_open_vacancies_per_firm` caps hiring flow per firm per period, which mechanically bounds
> growth rates for large firms; the ablation `max_open_vacancies_per_firm: ∞` is part of the A1
> protocol.

### 4.9 Dividends

Default policy (mechanical, PHASE 7 step 11, quarterly):

```
distributable = max(0, cumulative_profit_cents − cumulative_losses_cents − retained_floor_cents)
total_cents   = bp(distributable, payout_ratio_bp)            # default 3,000 bp
per_holder    = allocate(total_cents, [(h, shares_h) for h in holders])
```

`retained_floor_cents` = 1 sim-month of payroll, so a dividend never causes a payroll
shortfall. `DECLARE_DIVIDEND` (§0.2) lets an owner override `total_cents` upward or downward,
subject to the same floor in the PHASE 4 resources gate. Payment legs are `transfer` per holder
with `reason='dividend'`, batched into one transaction per firm.

Dividends are what give an equity a fundamental value, which is what makes "price diverges from
discounted fundamentals" (**A3**) a measurable statement rather than a rhetorical one. The
fair-value reference used by the Observatory is

```
fair_value_cents(y) = ttm_dividend_per_share_cents × 10_000 // max(1, discount_rate_bp − growth_bp)
discount_rate_bp    = policy_rate_bp + equity_risk_premium_bp        (default ERP 500 bp)
```

reported as a series next to `last_price`, never used by any agent-facing code path.

### 4.10 Self-employment

An agent with no employer may `PRODUCE{sku}` directly, converting its own labour into
inventory in a single-person firm created on first use (`SELF_EMPLOYMENT_STARTED`, 5060). This
gives a real outside option to wage employment — without it, unemployment is the only
alternative to a job and labour supply is degenerate. Self-employed agents are in `E(t)`
(§3.10) and pay income tax on realised profit rather than through withholding.

### 4.11 The mechanical baseline (`--reflex-only`)

`01-PRD.md §9` threat **T9** requires a pure-ABM baseline: "if 92% of behaviour is
deterministic code, the LLM society may be classical ABM wearing a hat." But the reflex action
set (`04-AGENT-SPEC.md §8`) deliberately excludes everything with a counterparty, so under
`--reflex-only` the economy would simply freeze — which makes the ablation useless.

Therefore `ablations.reflex_only: true` additionally activates **`MechanicalPolicy`**, an
explicit classical-ABM decision set that substitutes for every LLM decision in this document:

| Decision | Mechanical substitute |
|---|---|
| Apply for a job | Apply to the highest-`match_score_bp` vacancy in the visibility slice if unemployed |
| Make an offer | Offer the top shortlisted applicant at the posted wage |
| Accept an offer | Accept iff `wage_cents ≥ current wage` (or any wage if unemployed) |
| Negotiate | Never |
| Quit | Quit iff a strictly better offer is in hand |
| Fire | Only on payroll shortfall |
| Set price | Rule A |
| Buy goods | Linear expenditure system (§5.5) |
| Submit an order | Zero-intelligence trader: uniform limit price in `[last × 0.9, last × 1.1]`, side by coin flip, budget-constrained (Gode & Sunder) |
| Borrow | Borrow iff liquid < 1 sim-month of expenses and score ≥ threshold |
| Underwrite | Scorecard (§7.4) |
| Found a firm | Never |
| Invest / acquire / IPO | Never |

`MechanicalPolicy` is the null model for every headline result. A result that does not differ
from it is a result about the mechanisms, not about LLM agents.

---

## 5. Goods market

**Module:** `polis/economy/goods.py` · **Kinds:** 6020–6029, 6040–6049 · **PHASE 5 slot 4**

### 5.1 Event kinds (goods)

| Kind | NAME | Payload fields |
|---|---|---|
| 6020 | `GOODS_PURCHASED` | `txn_id, buyer_id, seller_firm_id, sku, qty, unit_price_cents, gross_cents, sales_tax_cents, subsidy_cents, ledger_txn_id` |
| 6021 | `PURCHASE_FAILED` | `buyer_id, sku, qty, reason(stockout\|unaffordable\|no_seller_visible\|price_above_cap\|rationed)` |
| 6024 | `NEED_SATISFIED` | `agent_id, need, sku, from_bp, to_bp` |
| 6025 | `DURABLE_EXPIRED` | `agent_id, sku, acquired_tick, life_ticks` |
| 6041 | `CPI_COMPUTED` | `basket_version, index_bp, category_index_bp{}, carried_forward_skus[], window_ticks` |
| 6042 | `INFLATION_COMPUTED` | `yoy_bp, mom_annualised_bp, core_bp` |
| 6043 | `SECTOR_OUTPUT` | `sector, units, value_cents, firms_n` |
| 6050 | `RENT_PAID` | `place_id, tenant_id, landlord_id, cents, period_ticks, txn_id` |
| 6051 | `RENT_ARREARS` | `place_id, tenant_id, owed_cents, periods_missed` |

> **Boundary note.** Rent *levels* are owned by `05-WORLD-SPEC.md` (`places.rent_cents`,
> `districts.land_value_cents`). The rent *payment* is an economy transaction and is emitted
> here so that all money movement carries an economy kind and a ledger `txn_id`. Landlord is
> `places.owner_id`; if `NULL`, the landlord is `gv_treasury` (public housing), so there is
> never a payment without a counterparty (Corollary L1a).

### 5.2 SKU catalogue

`skus` (`03-DATA-MODEL.md §5`) carries `category ∈ {food, housing, goods, services, luxury,
health}`, `is_necessity`, and `base_utility`. The seed catalogue:

| SKU | Category | Necessity | Perishable | Sector producing it | Notes |
|---|---|---|---|---|---|
| `fd_staple` | food | ✔ | — | food | Subsistence good; `γ` > 0 in the LES |
| `fd_fresh` | food | ✔ | ✔ | food | Spoils at 2,000 bp/day |
| `fd_prepared` | food | — | ✔ | food, retail | |
| `fd_restaurant` | food | — | n/a (service) | services | Also restores `social` |
| `hs_utilities` | housing | ✔ | — | industrial | Metered monthly |
| `hs_furnishing` | housing | — | — | industrial, retail | Durable, 5 sim-years |
| `gd_clothing` | goods | ✔ | — | retail | Durable, 2 sim-years |
| `gd_household` | goods | — | — | retail | Durable, 3 sim-years |
| `gd_electronics` | goods | — | — | industrial, retail | Durable, 3 sim-years; status good |
| `sv_transport` | services | ✔ | n/a | services | Reduces `travel_ticks` (see `05-WORLD-SPEC.md`) |
| `sv_childcare` | services | — | n/a | services | Required for a working parent of an infant |
| `sv_repair` | services | — | n/a | services | Extends a durable's `life_ticks` |
| `sv_legal` | services | — | n/a | services | Consumed by `RETAIN_COUNSEL` (`07-SOCIETY-SPEC.md`) |
| `hl_primary` | health | ✔ | n/a | health | Restores `health` |
| `hl_medicine` | health | ✔ | — | health | |
| `hl_hospital` | health | ✔ | n/a | health | Large, lumpy, the classic bankruptcy trigger for households |
| `lx_dining` | luxury | — | n/a | services | Status good |
| `lx_travel` | luxury | — | n/a | services | Status good |
| `lx_jewellery` | luxury | — | — | retail | Durable, status good, holds resale value |
| `ed_tuition` | services | — | n/a | education | Paid by `enrolments` (`03-DATA-MODEL.md §9`) |
| `cap_machine` | goods | — | — | industrial | **Capital SKU** |
| `cap_fixture` | goods | — | — | industrial | **Capital SKU** |
| `cap_software` | goods | — | — | industrial | **Capital SKU** |

**Capital SKUs** are the members of `economy.capital_skus`. A purchase of a capital SKU by a
firm increments `firms.capital_cents` instead of creating inventory; a purchase by an agent is
rejected in the PHASE 4 capability gate. This is how firm investment is a real transaction with
an in-world seller (Corollary L1a) rather than money vanishing into a capital-stock variable.

**Durables** confer their utility for `life_ticks` and are tracked in the in-memory projection
`agent_durables`, rebuilt from `GOODS_PURCHASED` and `DURABLE_EXPIRED`. There is no agent
inventory table and none is needed: consumables are consumed at the moment of purchase.

### 5.3 Posted-price search

Firms post `inventory.price_cents` per SKU. Buyers see a capped slice:

```
visible_sellers(a, sku, t) =
    take(goods_search_k,
         sort( firms with inventory[f, sku].qty > 0,
               key = (district_distance(a, f), price_cents, firm_id) )
         after a seeded shuffle within each distance band:
             rng.get("goods.search", agent_id, tick) )
```

`goods_search_k` default 5; `goods_search_radius_districts` default 2 (a firm outside the radius
is invisible unless it is the only seller). Prices, quantities available, and the seller's
district are visible; unit cost is not.

> **MECHANISM `goods.search_slice`** — *entails:* "A buyer transacts only with sellers in its
> slice, which is ordered by distance then price. This implies that price dispersion can persist
> in equilibrium, that nearby sellers capture more demand, and that spatial inequality in prices
> is possible. It does **not** imply the level of dispersion, its cyclicality, or any relation
> between the number of sellers and the price level."

`BUY_GOOD{sku, qty, seller_firm_id, max_unit_price_cents}`. Reflex agents may issue it only for
`is_necessity` SKUs at the posted price below a value cap (`04-AGENT-SPEC.md §8`); everything
else requires DELIBERATE.

**Resolution (PHASE 5 slot 4).** All purchase actions for the tick are grouped by
`(seller_firm_id, sku)` and served in a seeded permutation order (never by `agent_id`). If
demand exceeds `inventory.qty`, the residual buyers receive `PURCHASE_FAILED{reason:'rationed'}`
— the price does **not** clear within the tick. Prices adjust in PHASE 7 step 3, one period
later. This lag is what makes stockouts, queues, and inventory dynamics real.

### 5.4 Consumption from needs

`04-AGENT-SPEC.md §4` defines six needs and their decay. The goods layer supplies the mapping
from purchases to need restoration:

| Need | Restored by | Rule |
|---|---|---|
| `hunger` | `EAT` consuming a `fd_*` unit purchased this tick or held as food-on-hand | `+need_restore_bp[sku]`; at `hunger = 0` the health penalty and death hazard of `04-AGENT-SPEC.md §4` apply |
| `energy` | `SLEEP` at a `home` place | Free; requires housing, so homelessness has a real cost |
| `health` | `hl_primary`, `hl_medicine`, `hl_hospital` | `hl_hospital` is triggered by a health threshold, is lumpy, and is the main household-bankruptcy channel |
| `social` | `fd_restaurant`, `lx_dining`, co-location | |
| `esteem` | Status goods (`gd_electronics`, `lx_*`) **relative to the district median holding** | The relative term is what makes status competition possible |
| `security` | Employment, savings buffer ≥ `n` months of expenses, owned housing | Read by the reflex policy and by salience `stakes` |

Food-on-hand is an in-memory projection: `Σ fd_* units purchased − Σ units consumed by EAT`,
capped at `food_stock_cap_units` (default 14 days), spoiling at the SKU's spoilage rate.

### 5.5 Budget allocation

Default rule is a **linear expenditure system** (Stone–Geary), computed monthly per household
in PHASE 7 and used by the reflex policy and by `MechanicalPolicy`:

```
committed_cents  = rent + scheduled loan payments + utilities over the horizon
disposable_cents = liquid_cents − committed_cents − precautionary_buffer_cents
precautionary_buffer_cents = bp(monthly_expenses, buffer_bp), buffer_bp from time_preference

# subsistence quantities γ_s for necessity SKUs, from config
subsistence_cost = Σ_{s ∈ necessities} price_s × γ_s
supernumerary    = max(0, disposable_cents − subsistence_cost)
spend_s          = price_s × γ_s + bp(supernumerary, β_s)          for necessities
spend_s          = bp(supernumerary, β_s)                          for everything else
Σ β_s = 10_000 − savings_share_bp
savings_share_bp = f(time_preference, security need, age)          # config table, integer bp
```

> **MECHANISM `consumption_rule: linear_expenditure`** — *entails:* "Necessities have income
> elasticity strictly below one and luxuries strictly above one, by construction. **An Engel
> curve is therefore not a finding.** Marginal budget shares are constant in income above
> subsistence. This mechanism does **not** imply the aggregate marginal propensity to consume
> out of a transitory shock (which depends on `savings_share_bp` and the buffer), the
> distribution of consumption, or any relation between consumption and unemployment."

Alternative `consumption_rule: llm_only` routes every purchase decision to DELIBERATE. It is
correct and unaffordable at 1,000 agents; it exists as the ablation that measures how much of
the consumption function is the LES.

**The subsistence floor is also the deflation guard.** Because `γ_s > 0`, aggregate demand
cannot fall to zero as long as anyone has income or benefit, which is what stops the
deflationary death spiral (F7, §14). It is declared and ablatable precisely because it is doing
that work.

### 5.6 CPI — the formal construction

```
Basket B                Fixed at genesis. For each SKU s, q_s is the per-adult annual
                        consumption of s in the calibration run (§13), rounded to an
                        integer, dropping SKUs with q_s = 0. |B| ≈ 18.
Base prices p_s(0)      The posted price of s at tick 0, volume-weighted across sellers.
Window                  W = trailing 30 sim-days.

Transaction price of s at t:
    p_s(t) = ( Σ_{g ∈ goods_transactions, s, tick ∈ W} unit_price_cents × qty )
             // ( Σ qty )                                          # integer, floor
    if Σ qty == 0:  p_s(t) := p_s(t−1)      and s is listed in carried_forward_skus[]

Index (Laspeyres, fixed base, integer basis points):
    CPI(t) = 10_000 × ( Σ_{s ∈ B} q_s × p_s(t) ) // ( Σ_{s ∈ B} q_s × p_s(0) )

Category sub-indices: the same formula restricted to s in one `skus.category`.
Core index:           the same formula excluding categories {food, health}.
Inflation:
    π_yoy(t)  = 10_000 × CPI(t) // CPI(t − ticks_per_sim_year) − 10_000     # bp
    π_mom(t)  = annualised from the 30-day change, integer bp
```

The basket is **never rebased during a run**. A chained **Fisher** variant (geometric mean of
Laspeyres and Paasche over consecutive 30-day windows) is computed as a secondary series so
that substitution bias is a measurable quantity rather than an unknown. Sales tax is included
in `unit_price_cents` for CPI purposes; subsidies are netted out.

**Real-world analogue, named separately (T11):** `CPI(t)` corresponds to a fixed-basket
consumer price index of the **CPI-U** type; the Fisher series to a superlative chained index.
Naming the analogue is not a claim that the basket, weights, or magnitudes correspond to any
real economy's.

---

## 6. Exchange

**Module:** `polis/economy/exchange/` · **Kinds:** 7000–7999 · **PHASE 5 slot 5, PHASE 7 step 11**

### 6.1 Event kinds (exchange)

| Kind | NAME | Payload fields |
|---|---|---|
| 7001 | `SECURITY_LISTED` | `symbol, issuer_firm_id, class(common\|preferred\|bond), shares_outstanding, listing_price_cents, ipo_round_id, lockup_until_tick` |
| 7002 | `SECURITY_DELISTED` | `symbol, reason(acquisition\|bankruptcy\|voluntary\|maturity), final_price_cents, holders_n` |
| 7003 | `SESSION_OPENED` | `session_id, tick, symbols[], opening_auction{symbol:{price_cents,volume}}, reference_prices{}` |
| 7004 | `SESSION_CLOSED` | `session_id, tick, closing_auction{}, trades_n, volume, notional_cents` |
| 7010 | `ORDER_SUBMITTED` | `order_id, symbol, trader_id, side, order_type, limit_price_cents, qty, tif, reserved_cents, reserved_qty, arrival_ordinal` |
| 7011 | `ORDER_REJECTED` | `trader_id, symbol, reason(price_band\|no_security\|session_closed\|insufficient_reservation\|self_cross_only\|rate_limit\|halted\|lockup\|stay), detail` |
| 7012 | `ORDER_CANCELLED` | `order_id, remaining_qty, released_cents, released_qty, initiator(trader\|stp\|death\|stay\|session)` |
| 7013 | `ORDER_EXPIRED` | `order_id, remaining_qty, released_cents, released_qty` |
| 7020 | `TRADE_EXECUTED` | `trade_id, symbol, price_cents, qty, buy_order_id, sell_order_id, buyer_id, seller_id, aggressor, commission_buy_cents, commission_sell_cents, ledger_txn_id` |
| 7021 | `ORDER_FILLED` | `order_id, total_qty, avg_price_cents, commission_cents` |
| 7022 | `ORDER_PARTIALLY_FILLED` | `order_id, filled_qty, remaining_qty, avg_price_cents` |
| 7030 | `BOOK_SNAPSHOT` | `symbol, best_bid_cents, best_ask_cents, bid_depth, ask_depth, levels[] (ephemeral twin **90700**)` |
| 7040 | `OHLCV_COMPUTED` | `symbol, session_tick, open_cents, high_cents, low_cents, close_cents, volume, vwap_cents, trades_n` |
| 7041 | `INDEX_COMPUTED` | `index_name, value_bp, divisor, constituents[], mcap_cents` |
| 7050 | `CIRCUIT_BREAKER_TRIGGERED` | `symbol, reference_cents, last_cents, move_bp, band_bp, halt_until_tick, breaker_count` |
| 7051 | `TRADING_RESUMED` | `symbol, reopen_auction_price_cents, new_band_bp` |
| 7060 | `SHORT_OPENED` | `trader_id, symbol, qty, price_cents, borrow_fee_bp, collateral_cents, margin_ratio_bp` |
| 7061 | `SHORT_COVERED` | `trader_id, symbol, qty, price_cents, realised_pnl_cents, fees_paid_cents` |
| 7062 | `BORROW_FEE_CHARGED` | `trader_id, symbol, cents, distributed_to[], txn_id` |
| 7063 | `MARGIN_CALL` | `trader_id, symbol, equity_cents, required_cents, deadline_tick` |
| 7064 | `FORCED_LIQUIDATION` | `trader_id, symbol, qty, avg_price_cents, shortfall_cents` |
| 7070 | `IPO_ANNOUNCED` | `firm_id, symbol, shares_offered, primary_shares, secondary_shares, price_low_cents, price_high_cents, underwriter_bank_id, book_close_tick` |
| 7071 | `IPO_INDICATION` | `firm_id, investor_id, qty, limit_price_cents` |
| 7072 | `IPO_PRICED` | `firm_id, symbol, clearing_price_cents, offer_price_cents, discount_bp, oversubscription_bp` |
| 7073 | `IPO_COMPLETED` | `firm_id, symbol, allocations{}, gross_proceeds_cents, primary_cents, secondary_cents, underwriting_fee_cents, listing_fee_cents, txn_id` |
| 7080 | `BOND_LISTED` | `symbol, issuer(gv_treasury\|firm), face_cents, coupon_bp, matures_tick` |

### 6.2 Sessions

| Clock profile | Session structure |
|---|---|
| `microscope` (1 tick = 1 sim hour) | Session on ticks with sim-hour ∈ [9, 16) on non-holiday days. **Opening call auction** at hour 9; **continuous double auction** hours 10–15; **closing call auction** at hour 16, which also computes OHLCV and the index. |
| `chronicle` (1 tick = 1 sim day) | One **call auction per tick**. No continuous phase. OHLCV is degenerate (`o = h = l = c = p*`) and is documented as such; VWAP equals the auction price. |

`SUBMIT_ORDER` outside a session is rejected with `reason='session_closed'`. All orders are
**day orders**: any unfilled remainder is cancelled at the closing auction with
`ORDER_EXPIRED` (7013) and its reservation released. v1 has no GTC, no stop orders, no iceberg,
and no hidden liquidity. Rationale: day-only bounds the book, makes INV-ORDERS a per-session
check, and forces agents to re-express intent each session — which is the behaviour of interest.

### 6.3 Order types and admission

| Type | Semantics | Reservation |
|---|---|---|
| `limit` buy | Rest at `limit_price_cents`; execute at the resting counterparty's price when crossed (price improvement accrues to the aggressor) | `qty × limit_price + ceil(commission)` in `esc` |
| `limit` sell | As above | `qty` shares → `holdings.reserved_qty` |
| `market` buy | Execute against the book immediately; **never rests**; unfilled remainder cancelled with `reason='market_unfilled'` | `qty × band_upper_price + ceil(commission)`; rejected if the book has no ask |
| `market` sell | As above | `qty` shares |

Admission checks, in order, in PHASE 4 and again at the book (defence in depth):

1. Security exists, is listed, not delisted, not halted.
2. Session is open.
3. `qty > 0`, `qty ≤ max_order_qty` (default 10% of shares outstanding).
4. `limit_price_cents` is a positive multiple of `tick_size_cents` and lies within the price
   band (§6.9).
5. Reservation is fundable / holdable (§6.6). Insufficient → `ORDER_REJECTED`.
6. Rate limit: at most `action_slots` orders per trader per tick — identical for native and
   external agents (`02-ARCHITECTURE.md §6.3`, threat T12).
7. Not under an automatic stay (§10.4) and not lockup-restricted (§6.11).

### 6.4 Arrival ordering and time priority

`03-DATA-MODEL.md §6` makes `orders.submitted_seq` the time-priority tiebreaker and states the
reason: using the event `seq` rather than a timestamp makes matching deterministic. Two
problems must be solved to honour that.

**Problem 1 — the seq does not exist yet.** Matching happens in PHASE 5; sequence numbers are
assigned at PHASE 6 COMMIT.

**Problem 2 — actions arrive sorted by `actor_id`** (`02-ARCHITECTURE.md §5`, PHASE 3 output).
Assigning priority in that order would give every agent whose id sorts early a permanent
queue-position advantage. That is a systematic, exploitable artefact and an obvious target for
threat T10.

**Resolution.**

```
At the start of PHASE 5 slot 5, per symbol:
    new_orders = [actions of type SUBMIT_ORDER/SHORT for this symbol this tick]
    perm       = rng.get("exchange.arrival", symbol, tick).permutation(len(new_orders))
    for ordinal, o in enumerate(apply(perm, new_orders)):
        o.arrival_ordinal = ordinal
        emit ORDER_SUBMITTED(o)          # emission order == arrival order
    # events are appended to the tick buffer in emission order, so the seq assigned at
    # COMMIT is monotone in arrival_ordinal. Priority computed on arrival_ordinal is
    # therefore identical to priority computed on submitted_seq.
```

`orders.submitted_seq` is written at COMMIT as the `seq` of that order's `ORDER_SUBMITTED`
event, exactly as `03-DATA-MODEL.md §6` requires. The permutation is seeded, so it is
reproducible; it is symbol-and-tick-scoped, so it is independent of what any other subsystem
drew (`02-ARCHITECTURE.md §4.1`).

**Cancellations are processed before new orders.** `CANCEL_ORDER` applies only to orders
resting from a *previous* tick. You cannot cancel an order you submitted in the same tick.
This removes a whole family of same-tick quote-stuffing and priority-gaming strategies without
needing a minimum-resting-time rule.

### 6.5 Matching algorithm

Price–time priority. Bids sorted by `(−price, submitted_seq)`; asks by `(price, submitted_seq)`.

```python
def on_aggressor(book: Book, o: Order, tick: int) -> None:
    opp = book.asks if o.side == BUY else book.bids
    while o.remaining > 0 and opp:
        best = opp.peek()                       # best price, earliest submitted_seq
        if o.type == LIMIT and not crosses(o, best):
            break
        if best.trader_id == o.trader_id:       # self-trade prevention
            cancel(best, initiator="stp"); opp.pop(); continue
        px = best.limit_price_cents             # resting order sets the price
        if not in_band(o.symbol, px):
            trigger_breaker(o.symbol, px, tick); break
        qty = min(o.remaining, best.remaining)
        execute(o, best, px, qty, tick)         # §6.7
        if best.remaining == 0:
            opp.pop()
    if o.type == MARKET and o.remaining > 0:
        cancel(o, initiator="market_unfilled")  # market orders never rest
    elif o.remaining > 0:
        book.insert(o)                          # rests; reservation stays in place

def crosses(o: Order, best: Order) -> bool:
    return (o.limit_price_cents >= best.limit_price_cents) if o.side == BUY \
      else (o.limit_price_cents <= best.limit_price_cents)
```

Aggressors are processed in `arrival_ordinal` order. The engine is deterministic given
`(book state, ordered aggressors)`, which is exactly the pair that replay reproduces.

**Call auction** (open, close, and post-halt reopen):

```python
def uncross(orders: list[Order], prev_close: int) -> tuple[int, int]:
    """Return (clearing_price, executable_volume)."""
    prices = sorted({o.limit_price_cents for o in orders if o.type == LIMIT})
    best = []                                   # (volume, -abs(imbalance), -abs(p-prev), p)
    for p in prices:
        dem = sum(o.remaining for o in orders if o.side == BUY  and o.limit_price_cents >= p) \
            + sum(o.remaining for o in orders if o.side == BUY  and o.type == MARKET)
        sup = sum(o.remaining for o in orders if o.side == SELL and o.limit_price_cents <= p) \
            + sum(o.remaining for o in orders if o.side == SELL and o.type == MARKET)
        vol = min(dem, sup)
        best.append((vol, -abs(dem - sup), -abs(p - prev_close), -p))
    vol, _, _, negp = max(best)                 # tie-break: volume, imbalance, proximity, low
    return -negp, vol
```

All auction executions print at the single clearing price. Allocation at the marginal price
level is by price–time priority; if the marginal level must be split, the split uses
`allocate()` (§2.3) with weights equal to remaining quantity, ordered by `submitted_seq`.

### 6.6 Reservation and INV-ORDERS

| Event | Cash side | Share side |
|---|---|---|
| Buy order accepted | `dep → esc` for `qty × limit + ceil(commission)` (E3a) | — |
| Sell order accepted | — | `holdings.reserved_qty += qty` |
| Fill (buy) | `esc → counterparties` for `px × qty + commission` (E3b) | `holdings.qty += qty` |
| Fill (sell) | `→ dep` for `px × qty − commission` | `holdings.qty -= qty`, `reserved_qty -= qty` |
| Cancel / expiry / partial residual | `esc → dep` for the exact remainder (E3c) | `reserved_qty -= remaining` |
| Trader death | Cancel all, release all (`04-AGENT-SPEC.md §12.3` step 1) | as above |
| Automatic stay | Cancel all, release all (§10.4) | as above |

**INV-ORDERS**, every tick, HALT on failure:

```
∀ traders T:  Σ_{open/partial buy orders o of T} (o.remaining × o.limit_price + commission(o))
                 ≤ Σ balance(esc:T@*)
∀ (holder H, symbol y): holdings[H,y].reserved_qty == Σ_{open/partial sell orders} remaining
∀ accounts: balance(esc:*) ≥ 0
∀ holdings: reserved_qty ≥ 0  ∧  reserved_qty ≤ max(0, qty)
```

The inequality on the cash side is `≤` rather than `==` because a partially filled order may
have released rounding residue; the exact-release rule makes the gap bounded by the number of
open orders in cents, and a gap larger than that is a bug.

### 6.7 Execution and settlement

`execute()` performs, in one atomic step:

1. `post_transaction` with the E3b leg set (buyer escrow → seller deposit, commissions to the
   broker, inter-bank settlement legs as needed).
2. `holdings` update for both sides, including `reserved_qty` on the sell side.
3. `cap_table` mirror update for the issuer's `common` class (INV-CAPTABLE).
4. Capital-gains tax on the seller's realised gain, if `tax.capgains_bp > 0`:
   `gain = (px − holdings.avg_cost_cents) × qty`, tax `= bp_ceil(max(0, gain), capgains_bp)`,
   deducted in the same transaction and credited to the treasury.
5. `holdings.avg_cost_cents` update on the buy side (weighted average).
6. `trades` row and `TRADE_EXECUTED` (7020).

Commission: `max(commission_floor_cents, bp_ceil(px × qty, commission_bp))`, default
`commission_bp = 20`, floor 1 ¢, credited to `fm_broker` — a real firm with employees and a
P&L, not a sink. A brokerage that receives commissions and pays wages is the difference between
a closed economy and a leak.

### 6.8 Market data

```
Session S for symbol y = { trades with symbol y and tick in the session }
open   = price of min(S, key=(tick, trade_id))
close  = price of max(S, key=(tick, trade_id))
high   = max price in S ; low = min price in S
volume = Σ qty
vwap   = ( Σ price_cents × qty ) // ( Σ qty )              # integer floor, exact in cents
S empty → open = high = low = close = previous close ; volume = 0 ; vwap = NULL
```

Written to `ohlcv` keyed by `session_tick` = the tick of the closing auction.

**Market index** — capitalisation-weighted with a divisor, so listings and delistings do not
create artificial jumps:

```
MCAP(t)  = Σ_y last_price_cents(y) × securities[y].shares_outstanding      (class = common)
INDEX(t) = 10_000 × MCAP(t) // D(t)
On any change to the constituent set or shares outstanding at time t:
    D(t⁺) = D(t) × MCAP_after // MCAP_before          (integer, floor, Decimal intermediate)
D is initialised at the first listing so INDEX = 10_000.
```

**Real-world analogue, named separately (T11):** a float-adjusted capitalisation-weighted
equity index with a continuity divisor, of the **S&P 500** type.

### 6.9 Circuit breakers — the decision and its cost

**Polis has circuit breakers, at the symbol level, in the form of a price band plus a short
halt. They are on by default and are ablatable.**

The decision is not free and the reasoning is recorded here because A3 depends on it.

| Option | Consequence |
|---|---|
| **No breakers at all** | A single market order into a thin book prints an arbitrarily large price. That print becomes `last_price`, which propagates into the index, into `fair_value` comparisons, into collateral valuations, into margin calls, and into bankruptcy estate valuations. It is also the single most obvious exploit for an agent that discovers it (threat **T10**), and the resulting "bubble" would be a bug, not a finding. |
| **Market-wide halts** | Would suppress exactly the cascade dynamics A3 and A5 exist to study. Rejected. |
| **Per-symbol band + short halt** (chosen) | Bounds single-print pathologies without bounding multi-session moves. A bubble can still inflate by 20% per session indefinitely; a flash crash within one session cannot. |

```
reference_cents(y, session) = previous session's close, or the IPO offer price on day 1
band_bp        default 2,000        # no trade may print more than ±20% from reference
halt_bp        default 3,000        # a ±30% cumulative move halts the symbol
halt_ticks     default 2
max_halts_per_session  2            # after which band_bp widens to 5,000 and trading continues
```

An order that would print outside the band is **rejected** (`ORDER_REJECTED{reason:
'price_band'}`); the market is not halted. A cumulative move beyond `halt_bp` halts the symbol
for `halt_ticks` and reopens with a call auction, which sets a new reference.

> **MECHANISM `exchange.circuit_breakers`** — *entails:* "Single-session returns are bounded to
> ±`band_bp` (or ±`halt_bp` with a reopen). Therefore **any claim about tail risk, crash
> magnitude, or the extreme quantiles of the return distribution must be made with
> `exchange.circuit_breakers.enabled: false`, or must be explicitly conditioned on the band.**
> Claims about bubble *duration*, the divergence of price from `fair_value`, or the correlation
> of price with social-media sentiment are unaffected, because the band binds within a session
> and those objects are multi-session."

### 6.10 Short selling

A short position is simply a **negative `holdings.qty`**. This keeps INV-SHARES exact: the
lender's holding is unchanged while on loan, the shorter's is negative, and the buyer's is
positive, so `Σ qty == shares_outstanding` continues to hold with no synthetic shares anywhere.

| Rule | Value |
|---|---|
| Opening | `SHORT{symbol, qty, limit_price_cents}` — an ordinary sell order flagged `opens_short`. Requires no borrow location step in v1; the aggregate cap substitutes. |
| Aggregate cap | `Σ_H max(0, −qty) ≤ bp(shares_outstanding, max_short_bp)`, default 1,000 bp. **INV-SHORT**, HALT. An order breaching it is rejected. |
| Initial margin | `collateral_cents ≥ bp(qty × price, initial_margin_bp)`, default 15,000 bp (150%). Held in `esc:trader@bank#margin`. |
| Maintenance margin | `equity = collateral − max(0, (mark − entry) × qty)`; if `equity < bp(mark × qty, maintenance_margin_bp)` (default 3,000 bp) → `MARGIN_CALL` (7063), which is a MANDATORY force-routed obligation (§2.4) with a one-session deadline. |
| Forced liquidation | On deadline miss: a market buy for the full short at the next session, `FORCED_LIQUIDATION` (7064). Any shortfall beyond collateral is an unsecured claim on the trader and can trigger §10. |
| Borrow fee | `bp_ceil(mark × qty, borrow_fee_bp) // ticks_per_sim_year × ticks`, default 200 bp/yr, charged daily, distributed by `allocate()` across long holders pro rata (7062). |
| Recall | If the aggregate cap is breached by a share cancellation or buyback, the largest shorts are recalled first, deterministically by `(−|qty|, trader_id)`. |

Shorting is what makes a bubble *contestable*. Without it, price can only be bid up, and A3
degenerates into a study of buying pressure.

### 6.11 IPO listing mechanics

```
1. IPO_LIST{shares_offered, primary_shares, secondary_shares, price_low, price_high,
            underwriter_bank_id}                         — LLM-only, firm owner
   Eligibility (PHASE 4 capability gate):
       firm age ≥ ipo_min_age_ticks            (default 2 sim-years)
       trailing revenue ≥ ipo_min_revenue_cents
       net worth > 0
       underwriter is a solvent bank that has accepted the mandate
       listing fee paid to mk_exchange and gv_treasury
2. IPO_ANNOUNCED (7070). Book-building window ipo_book_ticks (default 3 sim-days).
3. Investors submit IPO_INDICATION (7071) — limit orders flagged `ipo`, funds reserved as
   in §6.6. Indications are visible only to the underwriter.
4. Pricing at book close:
       clearing = highest price at which cumulative demand ≥ shares_offered
       offer    = round_to_tick(clearing × (10_000 − underwriter_discount_bp) // 10_000)
       oversubscription_bp = 10_000 × demand_at_offer // shares_offered
       IPO_PRICED (7072)
5. Allocation at `offer` by price–time priority; the marginal level split by allocate().
   Unallocated reservations released.
6. Settlement (one transaction):
       buyers' escrow  → firm's deposit          for primary_shares × offer
       buyers' escrow  → selling holders         for secondary_shares × offer
       firm's deposit  → underwriter bank        underwriting_fee = bp(gross, uw_fee_bp)
       firm's deposit  → gv_treasury             listing_fee_cents
   securities row created; cap_table common rows converted to holdings; SECURITY_LISTED
   (7001); IPO_COMPLETED (7073).
7. Lockup: insiders' shares carry reserved_qty for lockup_ticks (default 180 sim-days), so
   they are held but unsellable. Orders against locked shares are rejected with
   reason='lockup'.
```

> **MECHANISM `exchange.ipo_underpricing`** — *entails:* "`underwriter_discount_bp` (default
> 500) makes the offer price strictly below the book-clearing price, so a positive first-day
> return is **implied by construction**. IPO underpricing is therefore **not** an emergent
> finding in Polis. Set `underwriter_discount_bp: 0` to make first-day returns an outcome."

Bonds list through the same table with `class='bond'` and trade on the same book; government
bond auctions are described in §11.5.

### 6.12 Property tests

`tests/unit/test_exchange_properties.py`, Hypothesis-driven over random order streams.

| # | Property | Fails if |
|---|---|---|
| **P1** | After every match cycle, `best_bid_cents < best_ask_cents` for every symbol (**INV-BOOK**) | The matcher stops early or a resting order is inserted without crossing checks |
| **P2** | `Σ_H holdings[H, y].qty == securities[y].shares_outstanding` after every trade (**INV-SHARES**) | Shares are created or destroyed in settlement |
| **P3** | `reserved_qty ≥ 0` for every holding; `balance(esc:*) ≥ 0` for every escrow account (**INV-ORDERS**) | Double release, or release before fill |
| **P4** | Every trade's ledger transaction sums to zero, and `Σ(buyer paid) == Σ(seller received) + Σ(commission)` | Commission arithmetic |
| **P5** | `filled_qty ≤ qty`; status transitions are only `open → partial → {filled, cancelled, expired}` | Refill after terminal state |
| **P6** | With breakers enabled, no `trades` row lies outside the band around its session reference | Band checked on the order but not the print |
| **P7** | Replaying a recorded `(book, ordered aggressors)` stream produces a byte-identical `trades` sequence | Hidden iteration-order or clock dependence |
| **P8** | For any stream with `commission_bp = 0` and `capgains_bp = 0`, `Σ_T (balance(dep:T) + balance(esc:T))` is invariant across the session | Money leaking through the exchange |
| **P9** | No `trades` row has `buyer_id == seller_id` | Self-trade prevention |
| **P10** | Price–time priority: for two resting orders on the same side at the same price, the one with lower `submitted_seq` reaches `filled` before the other's `filled_qty` becomes non-zero | Heap comparator |
| **P11** | `Σ max(0, −qty) ≤ bp(shares_outstanding, max_short_bp)` (**INV-SHORT**) | Short cap not enforced on partial fills |
| **P12** | Cancelling an order releases exactly `remaining × limit + commission(remaining)`, never more | Rounding on partial-fill releases |

---

## 7. Banking and credit

**Module:** `polis/economy/banking.py` · **Kinds:** 8000–8999 · **PHASE 5 slot 6, PHASE 7 steps 8–12, 19**

### 7.1 Event kinds (banking, monetary, treasury finance)

| Kind | NAME | Payload fields |
|---|---|---|
| 8001 | `BANK_FOUNDED` | `bank_id, name, place_id, founder_id, capital_cents, reserve_ratio_bp, is_central` |
| 8002 | `ACCOUNT_OPENED` | `account_id, owner_id, owner_type, bank_id, account_type, code` |
| 8003 | `ACCOUNT_CLOSED` | `account_id, final_balance_cents, reason` |
| 8004 | `DEPOSIT_MADE` | `owner_id, bank_id, cents, source(cash\|transfer), txn_id` |
| 8005 | `WITHDRAWAL_MADE` | `owner_id, bank_id, cents, txn_id` |
| 8006 | `WITHDRAWAL_REFUSED` | `owner_id, bank_id, requested_cents, available_cents, queue_position` |
| 8011 | `LOAN_APPLICATION_SUBMITTED` | `application_id, borrower_id, lender_id, requested_cents, purpose, term_ticks, collateral{}` |
| 8012 | `LOAN_APPLICATION_DECIDED` | `application_id, approved, credit_score_bp, score_components{}, offered_rate_bp, offered_cents, reason_codes[]` |
| 8010 | `LOAN_ORIGINATED` | `loan_id, lender_id, borrower_id, principal_cents, annual_rate_bp, term_ticks, payment_cents, payments_n, collateral{}, credit_score_bp, txn_id` |
| 8013 | `LOAN_PAYMENT_MADE` | `loan_id, payment_no, principal_cents, interest_cents, outstanding_after_cents, txn_id` |
| 8014 | `LOAN_PAYMENT_MISSED` | `loan_id, due_cents, available_cents, days_past_due` |
| 8015 | `LOAN_DELINQUENT` | `loan_id, days_past_due, capitalised_interest_cents, txn_id` |
| 8016 | `LOAN_DEFAULTED` | `loan_id, outstanding_cents, trigger(dpd\|strategic\|bankruptcy\|death)` |
| 8017 | `LOAN_WRITTEN_OFF` | `loan_id, written_off_cents, recovery_cents, loss_given_default_bp, txn_id` |
| 8018 | `LOAN_REPAID` | `loan_id, total_interest_cents, ticks_to_repay, early` |
| 8019 | `COLLATERAL_SEIZED` | `loan_id, asset_ref, appraised_cents, realised_cents, txn_id` |
| 8020 | `INTEREST_ACCRUED` | `loan_id, cents, annual_rate_bp, period_ticks, accrued_total_cents` |
| 8021 | `DEPOSIT_INTEREST_PAID` | `bank_id, total_cents, accounts_n, rate_bp, txn_id` |
| 8030 | `POLICY_RATE_SET` | `rate_bp, prev_rate_bp, setter(rule\|council\|fixed), inflation_bp, output_gap_bp` |
| 8031 | `RESERVE_REQUIREMENT_SET` | `ratio_bp, prev_bp, setter` |
| 8032 | `MONEY_ISSUED` | `amount_cents, recipient_account_id, instrument(reserves\|currency), purpose(genesis\|omo\|discount_window), txn_id` |
| 8033 | `MONEY_WITHDRAWN` | `amount_cents, source_account_id, instrument, purpose, txn_id` |
| 8034 | `OPEN_MARKET_OPERATION` | `direction(inject\|drain), amount_cents, counterparty_bank_id, symbol, qty, price_cents, txn_id` |
| 8040 | `INTERBANK_LOAN` | `loan_id, lender_bank_id, borrower_bank_id, cents, rate_bp, term_ticks` |
| 8041 | `DISCOUNT_WINDOW_BORROWED` | `bank_id, cents, penalty_rate_bp, reserve_shortfall_cents` |
| 8042 | `INTERBANK_REFUSED` | `borrower_bank_id, lender_bank_id, cents, reason(capital_ratio\|concentration\|liquidity)` |
| 8050 | `BANK_RATIOS_COMPUTED` | `bank_id, capital_cents, rwa_cents, capital_ratio_bp, reserve_ratio_bp, ldr_bp, npl_bp` |
| 8051 | `BANK_UNDERCAPITALISED` | `bank_id, capital_ratio_bp, threshold_bp, new_lending_frozen` |
| 8052 | `BANK_RUN_DETECTED` | `bank_id, requested_cents, served_cents, refused_n, deposits_before_cents, deposits_after_cents` |
| 8053 | `BANK_FAILED` | `bank_id, capital_cents, deposits_cents, shortfall_cents, resolution(assume\|liquidate)` |
| 8054 | `DEPOSIT_INSURANCE_PAID` | `bank_id, covered_cents, depositors_n, txn_id` |
| 8055 | `DEPOSIT_HAIRCUT` | `bank_id, depositor_id, haircut_cents, recovery_bp, txn_id` |
| 8060 | `BOND_ISSUED` | `symbol, face_cents, coupon_bp, matures_tick, auction_id` |
| 8061 | `BOND_AUCTION_CLEARED` | `auction_id, offered_cents, bid_cents, clearing_yield_bp, allocations{}, txn_id` |
| 8062 | `BOND_AUCTION_FAILED` | `auction_id, offered_cents, bid_cents, shortfall_cents` |
| 8063 | `COUPON_PAID` | `symbol, holders_n, total_cents, txn_id` |
| 8064 | `BOND_MATURED` | `symbol, face_cents, holders_n, txn_id` |
| 8070 | `TAX_ASSESSED` | `taxpayer_id, tax_type, base_cents, rate_bp, assessed_cents, period, due_tick` |
| 8071 | `TAX_COLLECTED` | `taxpayer_id, tax_type, cents, txn_id` |
| 8072 | `TAX_ARREARS` | `taxpayer_id, cents, loan_id, penalty_rate_bp` |
| 8073 | `TRANSFER_PAID` | `recipient_id, programme(unemployment\|pension\|welfare\|subsidy), cents, txn_id` |
| 8074 | `GOV_BUDGET_CLOSED` | `period, receipts_cents, spending_cents, debt_service_cents, balance_cents, debt_cents, debt_to_gdp_bp` |

> **Boundary note.** Kinds 8060–8074 are treasury finance. They sit in the banking range
> because they are money-movement events with ledger transactions. **Policy-parameter changes**
> (tax rates, spending levels, the policy-rate mandate) are `POLICY_ENACTED` (12030), owned by
> `07-SOCIETY-SPEC.md`. The split is: 07 decides the number, 06 moves the money.

### 7.2 Bank balance sheet

| Assets | Liabilities |
|---|---|
| `res:B` reserves at the central bank | `dpl:B` customer deposits (including `esc` sub-accounts) |
| `cash:B` vault cash | `lnp:B#…` interbank borrowings and discount-window loans |
| `lnr:B#…` loans outstanding | |
| `dep:B@other` nostro balances | |
| `holdings[B, bond]` government bonds (**real asset**, not on the ledger) | |

```
capital(B) = net_worth(B) = Σ balance_cents over B's ledger accounts
           + mark-to-market value of holdings[B, *]        # bonds and equities held
```

`banks.capital_cents` is the denormalised cache, reconciled every tick by INV-MONEY sub-check
M-6. Bond and equity holdings are marked to `last_price_cents`; the mark is a real-asset
valuation and never posts a ledger leg (Rule L1), so a mark-down reduces capital without
touching the money supply, which is the correct economics.

### 7.3 Deposits, reserves, and the reserve constraint

| Rule | Statement |
|---|---|
| Account opening | `OPEN_ACCOUNT{bank_id}` — any agent or firm; creates `dep:<owner>@<bank>` and, on first order, `esc:<owner>@<bank>`. One deposit account per (owner, bank); an owner may bank at several. |
| Deposit / withdrawal | `DEPOSIT{cents}` moves `cash → dep`; `WITHDRAW{cents}` moves `dep → cash`. Both are single transactions. Withdrawal converts vault cash: if `cash:B` is short, the bank draws down `res:B` first (a `MONEY_ISSUED{instrument:'currency'}` swap within M0). |
| Liquidity limit | A bank can serve withdrawals only up to `cash:B + res:B`. Requests are served in a seeded permutation order (`rng.get("banking.queue", bank_id, tick)`), never by `agent_id`. Unserved requests get `WITHDRAWAL_REFUSED` (8006) and `BANK_RUN_DETECTED` (8052) is emitted. |
| Reserve requirement | End of each sim-day: `res:B ≥ bp(deposits(B), reserve_ratio_bp)`. Shortfall → interbank borrowing (§7.10) → discount window (§7.9). Default `reserve_ratio_bp = 1,000`. |

**Critical: there is no rule that says depositors run when a withdrawal is refused.** A refusal
is a visible event; it enters the perception of the refused agent and, through
`07-SOCIETY-SPEC.md`'s communication and media layer, of others. Whether that produces a run is
an LLM outcome. Scripting it would make bank runs a mechanism rather than a finding.

### 7.4 Underwriting and credit scoring

Mechanical by default. All inputs are simulation state; there is no LLM call on the default
path. `credit_score_bp ∈ [0, 10,000]`.

```python
def credit_score_bp(b: BorrowerState, req: LoanRequest, mkt: MarketState) -> tuple[int, dict]:
    income_stability = min(10_000,
          10_000 * b.employed_ticks_last_year // ticks_per_sim_year) \
        * min(10_000, 10_000 * b.annual_income_cents // max(1, mkt.median_wage_cents)) // 10_000
    dti              = min(10_000, 10_000 * b.annual_debt_service_cents
                                          // max(1, b.annual_income_cents))
    history          = 10_000 * (b.on_time_payments + 1) // (b.total_payments + 2)   # Laplace
    leverage         = min(10_000, 10_000 * b.total_debt_cents
                                          // max(1, b.total_assets_cents))
    coverage         = min(10_000, 10_000 * req.collateral_value_cents
                                          // max(1, req.principal_cents))
    raw = ( 3_000 * income_stability
          + 2_500 * (10_000 - dti)
          + 2_000 * history
          + 1_500 * (10_000 - leverage)
          + 1_000 * coverage ) // 10_000
    raw = raw * (10_000 - 5_000 * b.bankruptcy_flag) // 10_000
    raw = raw * (10_000 - 2_500 * b.delinquency_flag) // 10_000
    return clamp(0, 10_000, raw), {...}          # components logged in 8012
```

| Decision element | Rule |
|---|---|
| Approval | `credit_score_bp ≥ bank.min_score_bp` (default 4,500) **and** the bank passes the capital and reserve constraints (§7.2, §7.3) **and** single-borrower concentration `≤ concentration_bp` of capital (default 2,500 bp) |
| Amount | `min(requested, bp(annual_income, max_loan_income_multiple_bp), collateral-implied cap)` |
| Rate | `annual_rate_bp = policy_rate_bp + base_spread_bp + risk_spread_bp + term_premium_bp`, where `risk_spread_bp = k × (10_000 − score)² // 10_000²` with `k = credit.risk_spread_k` (default 6,000) |
| Term | Requested, capped at `max_term_ticks` by purpose (consumer 3y, mortgage 25y, corporate 7y) |

> **MECHANISM `credit_scoring: linear_scorecard`** — *entails:* "Credit access is weakly
> increasing in employment stability, income, and repayment history, and weakly decreasing in
> debt-to-income, leverage, and a prior bankruptcy. The offered rate is weakly decreasing in the
> score. **Therefore any cross-sectional finding that the unemployed, the indebted, or the
> previously bankrupt are denied credit or charged more is implied and is not a result.** The
> mechanism says nothing about the time series: the *volume* of credit, its clustering, its
> covariance with the cycle, and the amplification of shocks through the capital constraint are
> all outcomes."

`banking.underwriting: llm` swaps the approval decision for a `CREDIT_EVAL` call (§0.2) that
receives the same state plus the borrower's free-text stated purpose, with the scorecard's
output supplied as a reference. It exists to measure how much of credit allocation is the
scorecard. M2 does not depend on it.

### 7.5 Origination, accrual, amortisation

**Origination** posts the E4 leg set. `loans` row with `status='current'`, `outstanding_cents =
principal_cents`, `credit_score_at_origination` recorded.

**Payment schedule** — level-payment annuity, integer cents, `Decimal` context per §2.1:

```
r_periodic = Decimal(annual_rate_bp) / 10_000 * payment_interval_ticks / ticks_per_sim_year
n          = term_ticks // payment_interval_ticks
payment    = ceil( principal × r / (1 − (1+r)^(−n)) )     if r > 0 else ceil(principal / n)
per payment: interest  = floor(outstanding × r)
             principal = payment − interest
final payment: principal = outstanding ; payment = outstanding + interest   # balloon-adjusted
```

`payment_interval_ticks` defaults to one sim-month. The ceiling on `payment` guarantees the
loan amortises to exactly zero — a floor would leave a residual cent that never clears and
would eventually trip INV-INTEREST.

**Interest accrual** runs daily (PHASE 7 step 8): `accrued += floor(outstanding × annual_rate_bp
/ 10_000 / 360)`, emitted as `INTEREST_ACCRUED` (8020). Accrued interest is a **memo**, not a
ledger balance, until it is either paid (E5) or capitalised on delinquency (E9). Keeping it off
the ledger until it moves is what keeps closure trivially true for the accrual step.

**INV-INTEREST**, at loan close: `Σ interest legs on this loan == Σ scheduled interest −
forgiven interest`. A mismatch means a rounding path diverged.

### 7.6 Delinquency, default, write-off

| State | Entry condition | Effects |
|---|---|---|
| `current` | on origination | — |
| `delinquent` | `days_past_due ≥ 30` sim-days | Missed interest capitalised (E9). `delinquency_flag` set on the borrower, which enters the scorecard. Penalty rate `+ delinquency_penalty_bp` (default 300). |
| `default` | `days_past_due ≥ 90` sim-days, **or** `DEFAULT` action, **or** bankruptcy filing, **or** death with an insufficient estate | Lender may seize collateral (8019), file suit (`FILE_SUIT`, `07-SOCIETY-SPEC.md`), or write off. Loan moves to non-performing; the bank's NPL ratio rises. |
| `written_off` | Lender decision after `writeoff_ticks` (default 180 sim-days in default) or bankruptcy discharge | E8b legs. Capital falls. RWA falls. `LOAN_WRITTEN_OFF` (8017). |
| `repaid` | `outstanding_cents == 0` | `LOAN_REPAID` (8018). |

`DEFAULT` is a first-class action (`02-ARCHITECTURE.md §6.2`, banking group) so that
**strategic default** is available to an agent that judges it worthwhile. Its consequences —
collateral seizure, a credit flag, a civil suit — are real, so whether it is worthwhile is a
decision, not a foregone conclusion. This is the cleanest available test of whether LLM agents
behave like the strategic-default literature or like the moral-obligation literature.

**Collateral.** `loans.collateral` is a JSONB reference to a real asset: a `places` row, a
`holdings` position, or firm capital. Seizure transfers the asset in its own table and posts a
recovery transaction at the realised sale price (not the appraised price — the difference is
the loss-given-default, and it must be realised through an actual sale, §10.6).

### 7.7 Bank capital

```
RWA(B) = Σ_loans outstanding_cents × risk_weight_bp[purpose] // 10_000
         + Σ holdings equity value × 10_000 bp
risk_weight_bp: sovereign 0 · mortgage 5,000 · corporate secured 7,500
                corporate unsecured 10,000 · consumer 10,000 · interbank 2,000
capital_ratio_bp(B) = 10_000 × capital(B) // max(1, RWA(B))
```

| Threshold | Default | Consequence |
|---|---|---|
| `capital_ratio_min_bp` | 800 | Below → `BANK_UNDERCAPITALISED` (8051); **new lending frozen**; dividends blocked |
| `capital_buffer_bp` | 1,050 | Below → lending allowed but `min_score_bp` raised by `stress_score_bump_bp` |
| Insolvency | `capital(B) < 0` | `BANK_FAILED` (8053), §7.11 |

The capital constraint, not the reserve requirement, is the binding brake on credit expansion —
which is both the modern institutional reality and the mechanism that makes a credit cycle turn.

### 7.8 The central bank and the policy rate

`bk_cb` is a `banks` row with `is_central = true`. It holds `iss:bk_cb` and is the only entity
permitted to call `ledger.issue_base_money()`.

| Instrument | Mechanism |
|---|---|
| **Policy rate** | `POLICY_RATE_SET` (8030). Sets the floor for all lending spreads and the discount-window rate. |
| **Open-market operations** | `OPEN_MARKET_OPERATION` (8034): the CB buys or sells government bonds from banks in the secondary market. Buying credits `res:B` against `iss:bk_cb` → **M0 rises**. Selling drains reserves → **M0 falls**. This is the only routine money-creation channel after genesis. |
| **Discount window** | §7.9. Lending at `policy_rate_bp + discount_penalty_bp` (default 200) against collateral, creating reserves. |
| **Reserve requirement** | `RESERVE_REQUIREMENT_SET` (8031). |
| **Deposit insurance** | Administered by the treasury, funded by a premium `insurance_premium_bp` on deposits, collected quarterly (§7.11). |

Policy-rate rule, `banking.policy_rate_rule ∈ {taylor, fixed, political}`:

```
taylor:  policy_rate_bp = clamp(0, 4_000,
             neutral_rate_bp
           + φ_π × (π_yoy_bp − π_target_bp) // 10_000
           + φ_y × output_gap_bp            // 10_000 )
         defaults: neutral 250, π_target 200, φ_π 15_000, φ_y 5_000
political: the rate is a policy parameter set by the elected council (07-SOCIETY-SPEC.md);
           the CB executes whatever POLICY_ENACTED specifies.
```

Reviewed every `policy_review_interval` (default 6 sim-weeks), PHASE 7 step 19 — **last**, so a
new rate applies from the following tick and never mid-tick.

> **MECHANISM `banking.policy_rate_rule: taylor`** — *entails:* "The policy rate is an
> increasing function of inflation and of the output gap by construction. **A correlation
> between the policy rate and inflation, or between the policy rate and output, is therefore
> not a finding.** Research question A4 concerns the *transmission* — the impulse response of
> unemployment, consumption, and default rates to a rate change — none of which is implied by
> the rule. Runs used for A4 should prefer `policy_rate_rule: fixed` with an injected rate shock
> (`SHOCK_INJECTED`, 99001), which identifies the response without the rule's feedback."

### 7.9 Discount window and reserve overdraft

If a bank ends the sim-day with `res:B` below the requirement and cannot borrow interbank, the
central bank lends automatically. `res:B` is one of the two accounts permitted in
`allow_negative` (§1.4 P6): an intraday negative reserve balance is converted at PHASE 7 step 12
into a discount-window loan (E4 leg shape, lender `bk_cb`), restoring non-negativity before
INV-MONEY runs in PHASE 9. Discount-window borrowing is public in the event log and enters the
perception of other banks — a stigma channel that can matter and is not scripted.

### 7.10 Interbank market

PHASE 7 step 12, after all customer money has moved.

```
shorts = banks with reserve shortfall, sorted by (−shortfall, bank_id)
longs  = banks with excess reserves,   sorted by (−excess,    bank_id)
rate   = policy_rate_bp + interbank_spread_bp                    # default spread 50 bp
for b in shorts:
    for l in longs:
        if l is b: continue
        if capital_ratio_bp(b) < interbank_min_ratio_bp:          # default 900
            emit INTERBANK_REFUSED(reason='capital_ratio'); continue
        if exposure(l → b) + amount > bp(capital(l), interbank_concentration_bp):
            emit INTERBANK_REFUSED(reason='concentration'); continue
        amount = min(shortfall(b), excess(l))
        originate an overnight loan (E4 shape, both parties are banks)
    if shortfall(b) > 0: discount window (§7.9)
```

> **MECHANISM `banking.interbank_refusal`** — *entails:* "A bank below `interbank_min_ratio_bp`
> is refused interbank funding and must use the penalty-rate discount window. This implies that
> a capital-impaired bank faces a higher marginal funding cost, and it creates a direct
> contagion channel from one bank's losses to another's funding. **The existence of a contagion
> channel is therefore assumed, not discovered.** What is not implied: whether contagion
> actually propagates, how far, how fast, or whether it clusters — those depend on the realised
> network of exposures, which is an outcome of lending decisions."

### 7.11 Bank failure and resolution

Triggered when `capital(B) < 0` at PHASE 7 step 12. Resolution mode is
`banking.resolution ∈ {assume, liquidate}`.

**`assume` (default).** The solvent bank with the largest capital assumes `B`'s deposits and
performing loans:

1. Deposit insurance covers each depositor up to `insurance_cap_cents` (default 6 sim-months of
   median wage). The treasury transfers the covered amount to the assuming bank
   (`DEPOSIT_INSURANCE_PAID`, 8054).
2. Uninsured balances above the cap take a haircut equal to the residual shortfall, allocated
   by `allocate()` in proportion to the uninsured excess (E10 legs, `DEPOSIT_HAIRCUT`, 8055).
3. Performing loans transfer: `lnr:B#…` closes and `lnr:B'#…` opens, one balanced transaction
   per loan, with the borrower's `lnp` untouched (the obligor does not change, the creditor
   does).
4. Non-performing loans are written off (E8b) before the transfer.
5. `BANK_FAILED` (8053); `banks.failed_tick` set; employees fired with `reason='firm_exit'`.

**`liquidate`.** No assuming bank. Loans are sold to solvent banks at
`bp(outstanding, fire_sale_bp)` (default 7,000 bp) — realising the loss immediately — deposits
are paid to the extent of realised assets plus insurance, and the residual is haircut. If no
solvent bank exists, the central bank assumes the loan book at the fire-sale price, which is a
recorded and reportable intervention rather than a silent bailout.

### 7.12 How a credit cycle can emerge

Nothing in this section schedules a cycle. The ingredients are four independently-motivated
rules; the dynamics are not implied by any of them.

```
        ┌──────────────────── amplification ─────────────────────┐
        │                                                        │
  repayment history ↑ ──► credit_score ↑ ──► loans approved ↑ ──►│
  collateral value ↑ ──►                                         │
        ▲                                                        ▼
        │                                              deposits created (E4)
        │                                                        │
        │                                                        ▼
  firm revenue ↑ ◄── consumption ↑ ◄── income ↑ ◄── hiring ↑ ◄── spending ↑
        │
        └──► equity & property prices ↑ ──► collateral value ↑  (back to top)

        ┌──────────────────── contraction ───────────────────────┐
  a shock (firm failure · rate rise · demand fall)                │
        │                                                        ▼
        ▼                                              defaults ↑ (§7.6)
  write-offs (E8b) ──► bank capital ↓ ──► capital_ratio < min ──► NEW LENDING FROZEN
        │                                                        │
        ▼                                                        ▼
  interbank refusal (§7.10) ──► funding cost ↑           credit supply ↓
        │                                                        │
        └──────────────► spending ↓ ──► layoffs ↑ ──► defaults ↑ ┘
```

**What is hard-coded:** the monotonicity of the scorecard, the capital constraint, the
interbank refusal threshold, and the fact that write-offs reduce capital. **What is not:** the
amplitude, the period, whether defaults cluster in time, whether the cycle is symmetric, how
long the frozen-lending state persists, whether banks anticipate the turn, and whether agents
deleverage before or after the shock.

**Falsification protocol for A5.** `mechanisms.credit_supply: exogenous` fixes the volume of
new lending to a constant path, removing the amplification loop entirely. If default clustering
survives that ablation, the clustering is not credit-driven and the A5 claim fails. A claimed
credit cycle must also be shown to differ from the `--reflex-only` baseline, where the
`MechanicalPolicy` borrower rule produces whatever cycle the scorecard alone implies.

---

## 8. Ventures

**Module:** `polis/economy/ventures.py` · **Kinds:** 9000–9019, 9040–9049 · **PHASE 5 slot 7, PHASE 7 steps 13–14**

### 8.1 Event kinds (ventures)

| Kind | NAME | Payload fields |
|---|---|---|
| 9001 | `STARTUP_FOUNDED` | `startup_id, firm_id, founder_id, thesis, sector, initial_capital_cents, burn_rate_cents` |
| 9002 | `THESIS_REVISED` | `startup_id, from_thesis, to_thesis, trigger(pivot\|investor_pressure\|reflection)` |
| 9003 | `RUNWAY_UPDATED` | `startup_id, liquid_cents, burn_rate_cents, runway_ticks, stage, revenue_ttm_cents` |
| 9004 | `STARTUP_DIED` | `startup_id, cause(out_of_cash\|founder_death\|founder_quit\|failed_round), age_ticks, total_raised_cents, investors_loss_cents` |
| 9005 | `VC_FUND_FORMED` | `fund_id, firm_id, gp_agent_id, committed_cents, lps[], vintage_tick, thesis, mgmt_fee_bp, carry_bp, hurdle_bp` |
| 9006 | `CAPITAL_CALLED` | `fund_id, lp_id, called_cents, cumulative_called_cents, txn_id` |
| 9007 | `LP_DEFAULTED` | `fund_id, lp_id, called_cents, forfeited_units, reallocated_to[]` |
| 9008 | `FUND_DISTRIBUTION` | `fund_id, source_exit_id, gross_cents, lp_cents, carry_cents, hurdle_met, txn_id` |
| 9009 | `MANAGEMENT_FEE_CHARGED` | `fund_id, cents, period, txn_id` |
| 9011 | `PITCH_MADE` | `pitch_id, startup_id, founder_id, investor_id, ask_cents, pre_money_ask_cents, deck_text, traction{}` |
| 9012 | `PITCH_EVALUATED` | `pitch_id, investor_id, conviction_bp, thesis_fit_bp, valuation_view_cents, check_size_cents, verdict, concerns[], llm_call_id` |
| 9013 | `TERM_SHEET_ISSUED` | `term_sheet_id, startup_id, investor_id, pre_money_cents, amount_cents, security, liq_pref_bp, participating, pro_rata, board_seat, option_pool_bp, anti_dilution, expires_tick` |
| 9014 | `TERM_SHEET_ACCEPTED` | `term_sheet_id, round_id` |
| 9015 | `TERM_SHEET_DECLINED` | `term_sheet_id, reason_code, counter_pre_money_cents` |
| 9016 | `TERM_SHEET_EXPIRED` | `term_sheet_id` |
| 9010 | `ROUND_CLOSED` | `round_id, startup_id, stage, pre_money_cents, amount_cents, post_money_cents, price_per_share_cents, new_shares, lead_investor_id, participants{}, option_pool_shares, txn_id` |
| 9017 | `DOWN_ROUND` | `round_id, prior_price_per_share_cents, new_price_per_share_cents, decline_bp, anti_dilution_applied, extra_shares_issued{}` |
| 9018 | `CAP_TABLE_UPDATED` | `firm_id, holder_id, share_class, shares_before, shares_after, cause, fully_diluted_after` |
| 9019 | `OPTION_POOL_SET` | `firm_id, pool_shares, pool_bp, pre_money_pool, granted_to{}` |
| 9040 | `EXIT_COMPLETED` | `startup_id, type(acquisition\|ipo\|shutdown), gross_proceeds_cents, distribution{}, multiple_bp, holding_period_ticks` |
| 9041 | `WATERFALL_APPLIED` | `firm_id, proceeds_cents, tranches[{class, holder, pref_cents, participation_cents, total_cents}]` |

### 8.2 Startup formation

`FOUND_COMPANY{…, is_startup: true, thesis: "<free text>"}` creates a `firms` row **and** a
`startups` row with `stage='idea'`. The thesis is LLM-authored free text stored verbatim in
`startups.thesis` and **never parsed by code** — the same discipline as `Action.reasoning`
(`02-ARCHITECTURE.md §6.1`). It is read by other LLMs (investors evaluating a pitch) and by
researchers, never by a branch.

A startup is a firm in every respect: it hires through §3, produces through §4, sells through
§5, banks through §7. The `startups` row adds stage, burn, and runway; nothing else is special
about it.

### 8.3 Burn rate and runway

Recomputed in PHASE 7 step 13 (monthly), emitting `RUNWAY_UPDATED` (9003).

```
monthly_costs_cents = payroll + rent + input purchases + interest + fixed
monthly_revenue_cents = trailing 30-day goods revenue
burn_rate_cents     = max(0, monthly_costs_cents − monthly_revenue_cents)
liquid_cents        = balance(dep:firm@*) + balance(cash:firm)
runway_ticks        = INT32_MAX if burn_rate_cents == 0
                      else liquid_cents × ticks_per_sim_month // burn_rate_cents
```

When `runway_ticks < fundraise_trigger_ticks` (default 180 sim-days), the founder receives a
MANDATORY force-routed obligation (§2.4). The obligation carries no prescribed response — the
founder may raise, cut costs, pivot (`THESIS_REVISED`, 9002), sell, or shut down. That the
decision is forced but the choice is free is the whole design.

`runway_ticks == 0` with unpaid obligations for `grace_ticks` triggers the insolvency path
(§10.2 B1).

### 8.4 VC fund structure

**A fund is a firm.** `vc_funds.fund_id` references a `firms` row with `sector='finance'`; the
GP is an agent; LP interests are `cap_table` rows on that firm with `share_class='lp'`, where
one unit = `lp_unit_cents` (default 10,000 ¢) of commitment. This reuses the cap table, gives
pro-rata distribution for free, and keeps uncalled commitments off the ledger — a commitment is
a promise, and posting it would create money that does not exist.

| Element | Rule |
|---|---|
| Formation | `FOUND_COMPANY{sector:'finance', is_fund:true}` by the GP, then `INVEST{fund_id, commitment_cents}` by each LP. `VC_FUND_FORMED` (9005) at first close. |
| Commitment | Off-ledger. `vc_funds.committed_cents = Σ lp units × lp_unit_cents`. |
| Capital call | On a round close, the fund calls `amount_cents` pro rata across LPs by units, via `allocate()`. `CAPITAL_CALLED` (9006); ledger legs LP deposit → fund deposit. |
| LP default | An LP that cannot fund a call within `call_grace_ticks` (default 14 sim-days) forfeits its units, which are reallocated to performing LPs pro rata. `LP_DEFAULTED` (9007). This is a real contagion channel from household distress into the venture layer. |
| Management fee | `bp(committed_cents, mgmt_fee_bp)` per sim-year (default 200 bp), charged quarterly from called capital to the GP's deposit. `MANAGEMENT_FEE_CHARGED` (9009). |
| Distribution waterfall | On an exit: return called capital to LPs; then the `hurdle_bp` preferred return (default 800 bp, applied as a multiple of called capital, not an IRR — integer-friendly and deterministic); then split the residual `carry_bp` (default 2,000) to the GP, remainder to LPs by units. `FUND_DISTRIBUTION` (9008). |
| Deployment | `vc_funds.deployed_cents` incremented on each round; dry powder = `committed − deployed`, visible to the GP's `VC_EVAL` prompt. |

### 8.5 Pitch and evaluation (`VC_EVAL`)

```
PITCH{investor_id, ask_cents, pre_money_ask_cents, deck_text}          — LLM-only, founder
    → PITCH_MADE (9011), payload includes traction computed from state:
        revenue_ttm_cents, revenue_growth_bp, headcount, burn, runway_ticks,
        months_since_founding, prior_rounds[], founder track record from the event log
    → the investor receives a pitch obligation (force-routed, §2.4)
```

The investor's evaluation uses LLM purpose **`VC_EVAL`** (already present in
`02-ARCHITECTURE.md §8`, `temperature: 0.4`). Structured output:

```json
{
  "conviction": 0.0,              // 0..1 → conviction_bp
  "thesis_fit": 0.0,              // 0..1, fit against the fund's declared thesis
  "valuation_view_cents": 0,      // the investor's own pre-money view
  "check_size_cents": 0,
  "verdict": "pass | explore | term_sheet",
  "concerns": ["string", "..."]
}
```

Prompt inputs (all from simulation state; no hidden information, per `04-AGENT-SPEC.md §5`
rule 4): the thesis text, the traction block, the cap table, the ask, comparable recent rounds
in the sector (§8.6), the fund's remaining dry powder and thesis, and the investor's retrieved
memories about the founder and the sector. The prompt never says "you are an AI" and never
names a provider (`04-AGENT-SPEC.md §13`).

`verdict == "term_sheet"` makes `ISSUE_TERM_SHEET` legal for that investor for
`term_sheet_window_ticks`. `verdict == "explore"` schedules a second pitch obligation after a
diligence delay. `pass` closes the pitch. A founder may pitch at most `max_pitches_per_tick`
(1, by action slot) and hold at most `max_open_pitches` (default 5).

### 8.6 Valuation

```
comparables(sector, stage) = the last comparable_window rounds (default 8) in the same
    sector and stage, sampled via rng.get("ventures.comparables", startup_id, tick)
    when more than `comparable_window` are available.

pre_money_anchor_cents =
    if revenue_ttm_cents == 0:
        median(comparables.pre_money_cents)  or  seed_default_pre_money_cents
    else:
        revenue_ttm_cents × sector_multiple_bp // 10_000
          × (10_000 + min(growth_cap_bp, revenue_growth_bp)) // 10_000

pre_money_cents = ( (10_000 − w_llm_bp) × pre_money_anchor_cents
                  + w_llm_bp × valuation_view_cents ) // 10_000
```

Default `w_llm_bp = 5,000` — half anchor, half investor judgement.

> **MECHANISM `venture_valuation: comparables_blend`** — *entails:* "Valuations are anchored to
> the median of recent comparable rounds and to a revenue multiple. **Valuation momentum — a
> rise in recent valuations mechanically raising the next valuation — therefore follows in part
> from the anchoring rule, and a private-market bubble is partly implied.** Set `w_llm_bp:
> 10,000` for the ablation in which valuation is entirely investor-determined; any A3 or A6
> claim about venture valuations must report both settings."

### 8.7 Term sheets and round mechanics

| Term | Field | Default |
|---|---|---|
| Pre-money | `pre_money_cents` | §8.6 |
| Amount | `amount_cents` | investor's `check_size_cents` |
| Security | `security` | `preferred` for priced rounds, `common` for friends-and-family |
| Liquidation preference | `liq_pref_bp` | 10,000 bp (1×) |
| Participating | `participating` | `false` |
| Pro-rata rights | `pro_rata` | `true` |
| Board seat | `board_seat` | `true` if `amount ≥ bp(post_money, board_seat_threshold_bp)` |
| Option pool | `option_pool_bp` | 1,000 bp, created **pre-money** (so it dilutes founders, not the new investor) |
| Anti-dilution | `anti_dilution` | `broad_weighted` |
| Expiry | `expires_tick` | `+ 14 sim-days` |

The founder responds with the polymorphic `ACCEPT_OFFER` / `DECLINE_OFFER` (§0.2), optionally
after a `NEGOTIATE_WAGE`-equivalent counter carried in `DECLINE_OFFER.counter_pre_money_cents`
(one counter, then the term sheet stands or expires).

**Round close arithmetic** (all integer):

```
shares_pre       = Σ cap_table.shares(firm, all classes)
pool_shares      = ceil(shares_pre × option_pool_bp / (10_000 − option_pool_bp))   # pre-money
shares_pre_pool  = shares_pre + pool_shares
price_per_share  = max(1, pre_money_cents // shares_pre_pool)
new_shares       = amount_cents // price_per_share
post_money_cents = pre_money_cents + amount_cents
dilution_bp      = 10_000 × new_shares // (shares_pre_pool + new_shares)
```

Residual cents (`amount_cents − new_shares × price_per_share`) remain with the company as
premium — the full `amount_cents` moves on the ledger regardless, so closure is unaffected.
Multiple participants in one round split `new_shares` by `allocate()` on their check sizes.

Settlement is one transaction: each investor's deposit → the startup's deposit, with
`reason='trade'`. Cap-table rows are written, `ROUND_CLOSED` (9010) and `CAP_TABLE_UPDATED`
(9018) emitted, `startups.stage` advanced, `vc_funds.deployed_cents` incremented.

### 8.8 Follow-on rounds, down rounds, anti-dilution

A round is a **down round** iff `price_per_share < previous round's price_per_share`. Emits
`DOWN_ROUND` (9017) and applies the prior rounds' anti-dilution protection:

```
broad_weighted:
    A = fully diluted shares before this round
    B = amount_cents // old_price_per_share        # shares the money would have bought
    C = new_shares
    new_conversion_price = old_price × (A + B) // (A + C)
full_ratchet:
    new_conversion_price = price_per_share

extra_shares[h] = old_shares[h] × old_price // new_conversion_price − old_shares[h]
```

Extra shares are issued to protected holders, diluting common (founders and employees) further.
This is the mechanism by which a down round transfers ownership from founders to earlier
investors, and it is one of the clearer places to look for **A6** (does the VC regime improve
or worsen allocation).

`pro_rata` rights give existing investors a right of first refusal on
`bp(new_shares, their ownership_bp)` before the round is offered to new investors.

### 8.9 Exits

| Exit | Path | Distribution |
|---|---|---|
| **Acquisition** | §9 | §8.10 waterfall on the cash/stock consideration |
| **IPO** | §6.11 | Preferred converts to common 1:1 at listing; no waterfall; holders keep shares and may sell after lockup |
| **Shutdown** | `STARTUP_DIED` (9004); §10 if liabilities exist, voluntary dissolution otherwise | Residual, if any, through the §10.5 waterfall |

`EXIT_COMPLETED` (9040) records `multiple_bp = 10,000 × proceeds_to_investor // invested` per
investor and `holding_period_ticks` — the raw material for a fund-level return distribution and
for A6.

### 8.10 The venture liquidation waterfall

Applied to acquisition consideration or to the equity tranche of a bankruptcy distribution
(class 5, §10.5).

```python
def waterfall(proceeds: int, rounds: list[Round], common: list[tuple[str,int]]) -> dict[str,int]:
    remaining = proceeds
    out = defaultdict(int)
    # 1. Preferences, senior first = reverse chronological order of rounds.
    for r in sorted(rounds, key=lambda r: -r.tick):
        pref_total = bp(r.amount_cents, r.liq_pref_bp)
        pay = min(remaining, pref_total)
        for h, sh in allocate(pay, [(h, s) for h, s in r.holders]).items():
            out[h] += sh
        remaining -= pay
    # 2. Residual to common and to participating preferred, pro rata on as-converted shares.
    part = [(h, s) for r in rounds if r.participating for h, s in r.holders] + common
    for h, sh in allocate(remaining, part).items():
        out[h] += sh
    # 3. "Greater of" test: a preferred holder takes the larger of pref or as-converted.
    as_conv = allocate(proceeds, [(h, s) for r in rounds for h, s in r.holders] + common)
    for h in out:
        if h in as_conv and as_conv[h] > out[h] and not participating(h):
            out = recompute_with_conversion(h)     # deterministic fixed-point, ≤ len(rounds) passes
    assert sum(out.values()) == proceeds
    return out
```

The final assertion is not decorative: the waterfall is the most arithmetically intricate money
split in the system and is the natural home of failure mode **F13**. `WATERFALL_APPLIED` (9041)
records every tranche so the split is auditable per holder.

### 8.11 Failure

| Cause | Sequence |
|---|---|
| Out of cash | `runway_ticks = 0` → grace period → missed payroll (`PAYROLL_SHORTFALL`, 5032) → §10.2 trigger B1 → `STARTUP_DIED` (9004) + `BANKRUPTCY_FILED` (9030) |
| Failed round | Term sheets all declined or expired while runway < 0 | as above |
| Founder death | `04-AGENT-SPEC.md §12.3`: shares pass to heirs; if no heir accepts the role and no buyer emerges within `orphan_firm_ticks`, the firm dissolves |
| Founder quits | `QUIT_JOB{destination:'unemployment'}` from a founder role → the board (holders > 50%) may appoint a replacement or wind down |

`STARTUP_DIED` records `investors_loss_cents`, the sum over investors of capital invested minus
distributions — which, aggregated over vintages, is the fund-level loss rate that A6 needs.

---

## 9. Mergers and acquisitions

**Module:** `polis/economy/ventures.py` · **Kinds:** 9020–9029 · **PHASE 5 slot 7**

### 9.1 Event kinds (M&A)

| Kind | NAME | Payload fields |
|---|---|---|
| 9020 | `ACQUISITION_PROPOSED` | `deal_id, acquirer_id, target_id, offer_cents, per_share_cents, consideration(cash\|stock\|mixed), stock_ratio_bp, premium_bp, integration_mode, expires_tick, financing(cash\|loan_id\|share_issue)` |
| 9021 | `ACQUISITION_APPROVED` | `deal_id, accepting_holders[], accepting_bp, threshold_bp, drag_along_applied` |
| 9022 | `ACQUISITION_REJECTED` | `deal_id, accepting_bp, reason(insufficient_tender\|board_rejection\|expired)` |
| 9023 | `ACQUISITION_COMPLETED` | `deal_id, price_cents, per_share_cents, integration_mode, txn_id, waterfall_ref` |
| 9024 | `ASSET_SALE` | `deal_id, seller_id, buyer_id, assets{inventory,capital,skus,places}, cents, txn_id` |
| 9025 | `INTEGRATION_COMPLETED` | `deal_id, headcount_retained, redundancies, sku_transfers[], productivity_delta_bp, loans_transferred[]` |
| 9026 | `ACQUISITION_BLOCKED` | `deal_id, blocker(government), hhi_before, hhi_after, sector, policy_ref` |

### 9.2 Valuation

```
fcf_ttm      = revenue_ttm − wages_ttm − inputs_ttm − tax_ttm − capex_ttm
r_bp         = policy_rate_bp + equity_risk_premium_bp                # default ERP 500
g_bp         = min(growth_cap_bp, revenue_growth_bp_ttm)
dcf_cents    = Σ_{h=1..H} fcf_ttm × (10_000+g_bp)^h // (10_000+r_bp)^h
               + terminal: fcf_ttm × (10_000+g_bp)^H // (r_bp − g_bp)     if r_bp > g_bp
comps_cents  = revenue_ttm × sector_multiple_bp // 10_000
mkt_cents    = last_price_cents × shares_outstanding                   # listed targets only
anchor_cents = max(mkt_cents, median(dcf_cents, comps_cents, book_value_cents))
offer_cents  = anchor_cents × (10_000 + premium_bp) // 10_000          # default premium 2,500 bp
```

`H` default 10 sim-years. All exponentiation via the `Decimal` context of §2.1, floored into
cents. The acquirer's LLM sets `premium_bp` and may override `offer_cents` in either direction;
the formula is the anchor shown in its prompt, not a constraint.

> **MECHANISM `ma.valuation_anchor`** — *entails:* "Offer prices are anchored at or above the
> market capitalisation of listed targets and above a DCF/comparables blend for private ones.
> **A positive acquisition premium is therefore implied.** Not implied: the *level* of premiums,
> their cyclicality, whether acquirers overpay relative to realised synergies, or the
> post-acquisition productivity path."

### 9.3 Offer

`ACQUIRE{target_id, offer_cents, consideration, integration_mode, financing}` — LLM-only, by
the acquiring firm's owner. PHASE 4 gates: the acquirer must be able to fund the cash portion
(from deposits, an approved loan, or a share issue), and must not be under an automatic stay.

Consideration:

| Type | Mechanics |
|---|---|
| `cash` | One transaction: acquirer deposit → the §8.10 waterfall across target holders |
| `stock` | Acquirer issues new shares at `last_price` (listed) or its last round price (private); target holders receive `cap_table`/`holdings` rows. **No ledger transaction** — this is a real-asset exchange (Rule L1) |
| `mixed` | `stock_ratio_bp` of consideration in shares, the remainder in cash |

### 9.4 Approval

| Target | Threshold | Mechanism |
|---|---|---|
| Private | > 5,000 bp of voting shares accept | Each holder receives a polymorphic acquisition offer (§0.2, force-routed §2.4) and responds `ACCEPT_OFFER` / `DECLINE_OFFER` |
| Private, drag-along | ≥ 7,500 bp accept | Remaining holders are dragged at the same per-share price |
| Public | Tender offer: holders tender via `SELL_STAKE{deal_id, qty}`; completes iff tendered ≥ `min_tender_bp` (default 5,000) by `expires_tick` | Untendered shares remain outstanding |
| Public, squeeze-out | ≥ 9,000 bp tendered | Remainder compulsorily purchased at the offer price; symbol delisted |
| **Antitrust** | `HHI_after(sector) > hhi_block_threshold` (default 2,500) **and** `ΔHHI > 200` | The government **may** block: `ACQUISITION_BLOCKED` (9026). Whether it does is a polity decision under `07-SOCIETY-SPEC.md`, driven by the enacted competition policy, not a hard rule here |

Antitrust being a *policy* rather than a hard constraint is deliberate: degenerate monopoly
(failure mode **F4**, §14) then becomes an observable consequence of the enacted policy regime,
which is a finding, rather than an artefact of a threshold in the market code.

### 9.5 Integration or asset sale

| Mode | Effects |
|---|---|
| `absorb` | Employments transfer to the acquirer (`employments.firm_id` updated, wage unchanged). Overlapping roles: `bp(overlap, redundancy_bp)` (default 3,000) are fired with `reason='acquisition'` and severance, selected by §3.7's redundancy ordering. Inventory and SKUs transfer at cost. `firms.capital_cents` adds. Loans transfer with the acquirer as obligor: `lnp:target#…` closes and `lnp:acquirer#…` opens in one balanced transaction. Target `status='acquired'`, `dissolved_tick` set, symbol delisted (7002). Productivity: `A_acquirer' = (A_a×K_a + A_t×K_t)//(K_a+K_t) + integration_delta_bp`, where `integration_delta_bp` is drawn from `rng.get("ventures.outcome", deal_id, tick)` with mean `integration_synergy_bp` (default 0 — synergies are **not** assumed positive). |
| `standalone` | Target keeps its `firms` row, employments, and SKUs. Cap table becomes 100% acquirer. Consolidated only for metric purposes (`firm_group_id`). |
| `asset_sale` | Named assets transfer for cash (`ASSET_SALE`, 9024). The target shell retains all liabilities and, being now asset-poor, typically satisfies §10.2 trigger B2 within `insolvency_persist_ticks`. This is the mechanism by which a lender can be left holding a shell — and it is exactly the abuse the priority waterfall exists to police. |

`INTEGRATION_COMPLETED` (9025) records headcount, redundancies, and the productivity delta.
Setting `integration_synergy_bp = 0` by default matters: assuming positive synergies would make
"acquisitions improve productivity" a mechanism, and A6 asks whether they do.

---

## 10. Bankruptcy

**Module:** `polis/economy/ventures.py` · **Kinds:** 9030–9039 · **PHASE 5 slot 7, PHASE 7 steps 14–15**

### 10.1 Event kinds (bankruptcy)

| Kind | NAME | Payload fields |
|---|---|---|
| 9030 | `BANKRUPTCY_FILED` | `case_id, entity_id, entity_type(agent\|firm\|bank\|fund), trigger, assets_cents, liabilities_cents, filed_by(self\|creditor\|institution), petitioning_creditor_id` |
| 9031 | `AUTOMATIC_STAY_IMPOSED` | `case_id, entity_id, cancelled_order_ids[], released_cents, released_shares{}, blocked_action_types[], stay_until_tick` |
| 9032 | `CLAIM_REGISTERED` | `case_id, creditor_id, claim_cents, priority_class(1..5), collateral_ref, loan_id` |
| 9033 | `ASSETS_LIQUIDATED` | `case_id, item(deposits\|securities\|inventory\|capital\|collateral), book_cents, realised_cents, haircut_bp, buyer_id, txn_id` |
| 9034 | `DISTRIBUTION_MADE` | `case_id, priority_class, creditor_id, claim_cents, paid_cents, class_recovery_bp, txn_id` |
| 9035 | `BANKRUPTCY_DISCHARGED` | `case_id, outcome(liquidated\|reorganised\|dismissed), written_off_cents, blended_recovery_bp, resolved_tick` |
| 9036 | `CREDIT_FLAG_SET` | `entity_id, flag(bankruptcy\|delinquency), set_tick, expires_tick` |
| 9037 | `EXEMPTION_APPLIED` | `case_id, entity_id, exempt_cents, basis` |
| 9038 | `ESTATE_DEFERRED_TO_CASE` | `case_id, deceased_agent_id, estate_cents, heirs[]` |

### 10.2 Trigger conditions

Evaluated in PHASE 7 step 14 (daily, 22:00), after all money has moved for the day.

| # | Trigger | Condition | Applies to |
|---|---|---|---|
| **B1** | Cash-flow insolvency | An obligation (payroll, loan instalment, rent, tax, coupon) has been due and unpaid for `grace_ticks` (default 14 sim-days) | agent, firm, bank, fund |
| **B2** | Balance-sheet insolvency | `net_worth < 0` continuously for `insolvency_persist_ticks` (default 30 sim-days) | firm, fund |
| **B3** | Creditor petition | A creditor holding a defaulted claim ≥ `petition_min_cents` files. Routed through `FILE_SUIT` in `07-SOCIETY-SPEC.md`; a judgment for the creditor triggers the case | agent, firm |
| **B4** | Voluntary | `FILE_BANKRUPTCY{}` — LLM-only | agent, firm, fund |
| **B5** | Bank insolvency | `capital(B) < 0` | bank — resolved under §7.11, **not** through this section's waterfall |

Agents are **not** subject to B2. An agent with negative net worth but a wage that services its
debts is simply indebted, which is the normal state of a mortgaged household; forcing a filing
would make household leverage impossible.

### 10.3 Filing

`BANKRUPTCY_FILED` (9030) creates a `bankruptcies` row with `assets_cents` and
`liabilities_cents` snapshotted at filing. `firms.status` → `bankrupt`;
`agents.employment_status` is unchanged (a bankrupt agent keeps working — that is where the
estate's income comes from).

### 10.4 Automatic stay

`AUTOMATIC_STAY_IMPOSED` (9031), effective immediately and lasting until `resolved_tick` or
`stay_max_ticks` (default 60 sim-days), whichever is first.

| Effect | Detail |
|---|---|
| Exchange | All resting orders cancelled, all escrow and `reserved_qty` released (`ORDER_CANCELLED{initiator:'stay'}`). New orders rejected with `reason='stay'`. Short positions are force-covered at the next session. |
| Actions | PHASE 4 capability gate rejects every `ActionType` for the entity except `FILE_BANKRUPTCY`, `TESTIFY`, `SAY`, `DIRECT_MESSAGE`, `WORK`, `EAT`, `SLEEP`, `MOVE_TO`, `NULL_ACTION`. |
| Creditors | Collateral seizure, `FILE_SUIT` against the entity, and set-off are rejected. Existing judgments are stayed. |
| Interest | Accrual stops on all the entity's loans. Penalty rates stop. |
| Employment | Employees keep working; wages continue to accrue as a **class-2 priority claim** and are paid ahead of lenders. |
| Contracts | Open goods orders and vacancies are cancelled; open offers to the entity expire. |

The stay is what makes bankruptcy an orderly process rather than a race, and it is also what
prevents the run from deadlocking on an entity that keeps transacting while insolvent.

### 10.5 Claims, liquidation, and the priority waterfall

**Claim registration.** Every creditor of record is registered automatically from state — loans
(`loans` where `borrower_id = entity`), accrued unpaid wages, tax arrears (`txr`), judgment
debts (`court_cases.penalty_cents`), and equity (`cap_table` / `holdings`). `CLAIM_REGISTERED`
(9032) per claim. No creditor can fail to file; there is no notice mechanic and none is wanted.

**Liquidation** (PHASE 7 step 15, over `liquidation_ticks`, default 5 sim-days):

| Asset | Realisation |
|---|---|
| Deposits, cash, released escrow | At face |
| Listed securities | Sold via **market orders** sliced over `liquidation_ticks` sessions, sized by `rng.get("exchange.liquidation", case_id, tick)`. The price impact is **realised on the book**, not assumed. |
| Unlisted equity stakes | Offered to existing holders at `bp(last_round_price, unlisted_haircut_bp)` (default 5,000 bp); unsold → written to zero |
| Inventory | Offered to solvent firms in the same sector at `bp(unit_cost, inventory_haircut_bp)` (default 5,000 bp), allocated by capacity. **Unsold inventory is scrapped: a real write-off with no ledger transaction** (Rule L1) |
| Capital | Same, `capital_haircut_bp` default 4,000 |
| Collateral | Returned to the secured creditor, realised at the sale price; the deficiency drops to class 4 |

Every sale needs an in-world buyer (Corollary L1a). If none exists, the asset is scrapped and
recovery is simply lower — which is the correct outcome and leaks nothing.

**The waterfall.** Classes are paid in full in order; within a class, pro rata by claim size via
`allocate()` (§2.3).

| Rank | Class | Contents |
|---|---|---|
| **1** | **Secured** | Each secured claim up to the realised value of its specific collateral. Deficiency → class 4. |
| **2** | **Administration and wages** | The bankruptcy administration fee to `gv_treasury` (`bp(estate, admin_fee_bp)`, default 300 bp), then unpaid wages up to `wage_priority_cap_cents` per employee (default 90 sim-days of that employee's wage). Wage excess → class 4. |
| **3** | **Tax** | Government tax arrears (`txr` claims and assessed-unpaid amounts). |
| **4** | **Unsecured** | Remaining loans, trade payables, judgment debts, class-1 deficiencies, class-2 wage excess. |
| **5** | **Equity** | Only if classes 1–4 are paid in full. Distributed through the §8.10 venture waterfall (liquidation preferences, then common). |

```
class_recovery_bp(c) = 10_000 × Σ paid(c) // Σ claimed(c)
blended_recovery_bp  = 10_000 × Σ paid    // Σ claimed
```

Recorded per class in `DISTRIBUTION_MADE` (9034) and blended in `bankruptcies.recovery_rate`.
Every distribution is a balanced transaction; the sum of all distributions plus the residual
write-off equals the total claimed, by construction.

### 10.6 Discharge and its effects

`BANKRUPTCY_DISCHARGED` (9035). Residual claims in classes 4 and 5 are written off with the E8b
leg pattern — **no money moves, and neither M0 nor M1 changes**.

| Party | Effect |
|---|---|
| **Firm** | `status='dissolved'`, `dissolved_tick` set. All employments end (`FIRED{reason:'firm_exit'}`), which puts its workforce into `U(t)` next tick. Securities delisted at 0 (7002) — holders realise a total loss, which flows into their net worth and hence into wealth-distribution metrics. SKUs removed from the goods market. |
| **Agent** | Debts in classes 4–5 discharged. `EXEMPTION_APPLIED` (9037) preserves `exempt_cents` (default 1 sim-month of median wage) plus necessary housing tenure — **without an exemption, bankruptcy would be functionally identical to death**, which would destroy the research value of both. `CREDIT_FLAG_SET` (9036) for `credit_flag_ticks` (default 7 sim-years); the flag halves the credit score (§7.4). |
| **Lender** | `LOAN_WRITTEN_OFF` (8017) per loan. Capital falls by the written-off amount; RWA falls by the loan's risk-weighted amount, so the capital *ratio* may rise or fall. If capital goes negative → §7.11 bank failure → possible cascade. |
| **Fund** | LP units are written to zero; `investors_loss_cents` recorded on the corresponding `STARTUP_DIED`. |

`outcome='reorganised'` is available when the entity is a firm whose class 1–3 claims are
covered and whose creditors (holders of > 5,000 bp of class-4 claims, each responding with the
polymorphic `ACCEPT_OFFER`) agree to a plan: debts are partially written off, equity is diluted
or cancelled, and the firm continues with `status='active'`. `dismissed` occurs when the entity
becomes solvent during the stay.

### 10.7 Interaction with agent death (`04-AGENT-SPEC.md §12.3`)

This interaction is where accounting closure is most likely to break, and the ordering rules
are therefore normative.

**Case A — the agent dies with an open bankruptcy case.**
PHASE 8 death settlement **defers**. `ESTATE_DEFERRED_TO_CASE` (9038) is emitted; steps 4 and 5
of `04-AGENT-SPEC.md §12.3` (settle debts, distribute residual) are **skipped**. The
`bankruptcies` row continues with `gv_treasury` as administrator. Heirs receive only the class-5
residual, if any, when the case is discharged. Steps 1, 2, 6, 7, and 8 of §12.3 (cancel orders,
terminate employment, vacate housing, archive memories, obituary) run normally on the death
tick. The agent's ledger accounts stay open until discharge — this is the one case where an
account outlives its owner, and `ledger.close_account` must not be called before then.

**Case B — the agent dies insolvent with no open case.**
No case is opened, no stay is imposed, and no `bankruptcies` row is created. Instead PHASE 8
step 4 executes a **simplified waterfall** in a single atomic transaction, using the same class
ordering as §10.5, over whatever the estate realises. Any shortfall is written off with the E8b
pattern, exactly as §12.3 step 4 requires ("shortfall → creditor write-off, a real loss on the
lender's balance sheet"). Step 5 then distributes zero, and no heir receives anything.

**Case C — the agent dies solvent.**
`04-AGENT-SPEC.md §12.3` runs unmodified: E7a settles debts, E7b distributes to heirs, escheat
to `gv_treasury` if intestate with no heirs.

**Case D — the deceased owns a firm.**
Shares pass to heirs through `OWNERSHIP_TRANSFERRED` (6004, `cause='inheritance'`). If there are
no heirs, the stake escheats to `gv_treasury`, which either sells it (a market order, or an
offer to existing holders for private firms) or dissolves the firm after `orphan_firm_ticks`
(default 90 sim-days). If the firm is itself insolvent, §10.2 B2 fires on its own schedule and
the two cases proceed independently — a firm bankruptcy and its owner's death are separate
proceedings against separate estates.

**Ordering within the death tick** is fixed:

```
1. cancel resting orders, release escrow and reserved shares      (§12.3 step 1)
2. terminate employment; pay accrued wages if the employer can    (§12.3 step 2, §3.7)
3. determine case: A (open bankruptcy) | B (insolvent) | C (solvent)
4. run the corresponding settlement, as ONE transaction per case  (§12.3 steps 3–5)
5. vacate housing, restructure household, reassign dependants     (§12.3 step 6)
6. archive memories, end relationships, bereave ties              (§12.3 steps 7–8)
7. close ledger accounts (asserting zero balance) — SKIPPED in case A
```

`tests/invariants/test_death_settlement.py` covers all four cases plus the combinations that
actually break things: death while holding a partially-filled order, death during another
entity's bankruptcy in which the deceased is a creditor, death of a fund's GP, and death of the
sole owner of a bank.

---

## 11. Government finance

**Module:** `polis/economy/banking.py` (fiscal agent) · **Kinds:** 8060–8074 · **PHASE 7 steps 16–18**

The government is the entity `gv_treasury`, banking at `bk_cb` via `dep:gv_treasury@bk_cb`. It
is **not** an agent and has no cognition. Every parameter below is a policy value that
`07-SOCIETY-SPEC.md`'s polity layer can change through `POLICY_ENACTED` (12030); this section
specifies only how the money moves once a number has been set.

### 11.1 Taxes

| Tax | Parameter | Default | Base (simulation state) | Collection point | Cadence |
|---|---|---|---|---|---|
| Labour income | `tax.income.brackets` | `[(0, 0), (2_000_000, 1_500), (6_000_000, 2_500), (15_000_000, 3_500)]` — (annualised threshold ¢, marginal rate bp) | Gross wage on each `WAGE_PAID`, annualised for bracket lookup | Withheld by the employer (E1 legs 3–5) | Each payroll |
| Employer payroll | `tax.payroll_employer_bp` | 500 | Gross wage | Employer pays in addition to gross | Each payroll |
| Self-employment | `tax.income.brackets` | — | Realised profit of a self-employed agent | Assessed and collected | Quarterly |
| Corporate profit | `tax.corporate_bp` | 2,000 | `max(0, revenue − wages − inputs − depreciation − interest)` over the sim-quarter, with losses carried forward indefinitely (`firms` cumulative-loss projection) | Assessed on the firm | Quarterly |
| Sales | `tax.sales_bp` | 800 | Goods transaction value; exempt when `skus.is_necessity` and `tax.exempt_necessities` (default true) | Added at purchase (E2 leg 7) | Per transaction |
| Capital gains | `tax.capgains_bp` | 1,500 | `max(0, (price − avg_cost) × qty)` on the seller's realised gain | At trade settlement (§6.7 step 4) | Per trade |
| Property | `tax.property_bp` | 100 | `districts.land_value_cents` share attributable to the place, or capitalised `places.rent_cents` | Owner of `places` | Annual |
| Estate | `tax.estate_bp` | 0 (off) | Estate value above `tax.estate_exempt_cents` | At death, before distribution (§10.7 case C, between E7a and E7b) | On death |
| Registration | `tax.registration_fee_cents` | 1 sim-week of median wage | Flat | `FOUND_COMPANY` | Per event |
| Listing | `tax.listing_fee_cents` | — | Flat | `IPO_COMPLETED` | Per event |
| Deposit insurance premium | `banking.insurance_premium_bp` | 5 | Bank deposits | Levied on banks | Quarterly |

Progressive income tax is computed bracket-by-bracket on the annualised wage, then scaled back
to the pay period:

```
annualised   = gross_cents × pay_periods_per_sim_year
liability    = Σ_b bp_ceil(max(0, min(annualised, upper_b) − lower_b), rate_b)
withheld     = bp_ceil(liability, 10_000) // pay_periods_per_sim_year
```

### 11.2 Assessment, collection, and arrears

**Taxes are cash-basis.** `TAX_ASSESSED` (8070) records a liability in a projection; only
`TAX_COLLECTED` (8071) posts a ledger transaction. Assessing without collecting posts nothing,
so an assessment can never create money.

If an assessed tax is unpaid by `due_tick`, the arrear is converted into a **loan** with
`lender_id = 'gv_treasury'`, `annual_rate_bp = tax.arrears_penalty_bp` (default 800), using the
`lnr`/`lnp` machinery: the government's side is a `txr` account (`account_type =
tax_receivable`), the taxpayer's is `lnp`. `TAX_ARREARS` (8072). This keeps closure exact — a
receivable always has a matching payable — and it gives tax debt the same delinquency, default,
and class-3 bankruptcy priority as any other claim, at no extra machinery cost.

### 11.3 Spending

| Programme | Parameter | Default | Recipient | Effect |
|---|---|---|---|---|
| Education | `spend.education_cents_per_student` | — | The school's operating firm; teachers' wages | `schools.quality_{t+1} = clamp(0,1, quality_t + η × (funding_ratio − 10_000)/10_000)` with `η = 0.02` per sim-year |
| Police | `spend.police_headcount`, `spend.officer_wage_cents` | 12 officers | Agents employed in occupation `officer` | Headcount enters the detection probability in `07-SOCIETY-SPEC.md` |
| Health | `spend.health_subsidy_bp` | 4,000 | Reduces the price paid for `hl_*` at point of sale; the government pays the difference to the seller | Subsidy leg in the purchase transaction |
| Unemployment benefit | `spend.benefit_replacement_bp`, `spend.benefit_max_ticks` | 4,000 bp of last wage, 26 sim-weeks | Agents in `U(t)` with a prior employment | Weekly transfer; `BENEFIT_CLAIM_OPENED` (5080), `BENEFIT_EXHAUSTED` (5081) |
| Pension | `spend.pension_replacement_bp` | 3,000 bp of career-average wage | Agents with `RETIRED` (5070) | Monthly transfer |
| Welfare floor | `spend.welfare_floor_cents` | 0 (off by default) | Adults with liquid below the floor | Weekly transfer; the ablation lever for "does a basic income change the labour market" |
| Infrastructure | `spend.infra_cents` | — | Firms in `industrial` (a real purchase of `cap_*` SKUs) | Raises `districts.amenity_score`, lowers `place_paths.travel_ticks` (see `05-WORLD-SPEC.md`) |
| Debt service | derived | — | Bondholders | §11.5 |

Every one of these is a `transfer` (§1.4.2) with an in-world recipient, `reason ∈ {transfer,
wage, purchase}`, emitted as `TRANSFER_PAID` (8073). **Government spending with no recipient is
forbidden** and `post_transaction` cannot express it.

The unemployment benefit is doing double duty as a demand floor (§13.3), so it is a declared
MECHANISM: *entails:* "Agents in `U(t)` receive a positive income, so aggregate consumption
cannot fall to zero with unemployment. This bounds the depth of a demand contraction and
therefore **damps any deflationary spiral by construction**. Any claim about the depth or
duration of a downturn must report the replacement rate, and the `benefit_replacement_bp: 0`
ablation is part of the protocol."

### 11.4 The budget

```
receipts(period)        = Σ TAX_COLLECTED.cents over the period
primary_spending(period)= Σ TRANSFER_PAID.cents + government wage bill + purchases
debt_service(period)    = Σ COUPON_PAID.cents
balance(period)         = receipts − primary_spending − debt_service       # deficit if < 0
debt_cents(t)           = Σ face value of outstanding treasury bonds
debt_to_gdp_bp(t)       = 10_000 × debt_cents // GDP_nom_ttm
```

`GOV_BUDGET_CLOSED` (8074) per sim-quarter.

### 11.5 Deficit, bonds, and debt service

`dep:gv_treasury@bk_cb` is the second account permitted in `allow_negative` (§1.4 P6): an
intra-period overdraft is converted at PHASE 7 step 18 into a bond issue.

```
if balance(dep:gv_treasury@bk_cb) < treasury_floor_cents:
    face = round_up(treasury_floor_cents − balance, bond_denomination_cents)
    issue a `securities` row: class='bond', issuer='gv_treasury',
        coupon_bp = policy_rate_bp + sovereign_spread_bp(debt_to_gdp_bp),
        matures_tick = t + bond_term_ticks   (ladder: 1y, 5y, 10y)
    BOND_ISSUED (8060); auction at the next session.
```

**Auction.** Uniform-price (single-price) Dutch auction. Banks, agents, and firms submit
`SUBMIT_ORDER` bids flagged `auction` with a limit **price** (yield is derived). Bids are sorted
descending by price; the clearing price is the lowest accepted; all winners pay the clearing
price. The marginal level is split by `allocate()`. `BOND_AUCTION_CLEARED` (8061).

**Failed auction.** If total bids < offered, `BOND_AUCTION_FAILED` (8062). Consequences, in
order: (1) the central bank may buy the residual at the clearing price if
`banking.cb_backstop: true` — this is monetary financing, is logged as such, and is off by
default; (2) otherwise the treasury imposes proportional spending cuts across discretionary
programmes until the projected balance clears the floor. A fiscal crisis is therefore a real,
observable state rather than an impossible one.

**Coupon and maturity.** `COUPON_PAID` (8063) semi-annually, `bp(face, coupon_bp) // 2` per
holder allocated by holdings; `BOND_MATURED` (8064) repays face and delists the symbol. Bonds
trade on the ordinary order book (§6), so a sovereign yield curve exists and can move — which
is what makes the fiscal channel of A4 visible.

Bank holdings of government bonds carry `risk_weight_bp = 0` (§7.7), so the sovereign–bank
loop (banks buy bonds, bond prices fall, bank capital falls, credit contracts) is available
without being scripted.

---

## 12. Macro metrics

Every metric is defined **purely in terms of simulation state**, and its real-world analogue is
named in a **separate column**. This is the concrete discharge of threat **T11**
(anthropomorphic metric transfer). Definitions are duplicated in
`10-RESEARCH-AND-OBSERVABILITY.md`; if the two ever disagree, this document governs for
economic metrics.

**Standing caveat, to be reproduced in any output.** Naming the analogue asserts only that the
statistic is constructed the same way from micro-records. It does not assert that the
magnitudes, the units, or the population are comparable to any real economy, and per threat
**T1** every result statement takes the form *"LLM agents of family X, under prompt Y, produced
Z"* — never *"people do Z"*.

| # | Metric | Formal definition in simulation state | Real-world analogue |
|---|---|---|---|
| **M1** | `gdp_nominal` | `Σ` over `goods_transactions` in the period of `qty × unit_price_cents` where the buyer is a household **(C)** `+ Σ CAPITAL_PURCHASED.cents + Δ(Σ inventory.qty × unit_cost_cents)` **(I)** `+` government purchases of goods and services, **excluding transfers** **(G)**. Asset trades, loan flows, and transfer payments are excluded by construction. | Nominal GDP, expenditure approach, closed economy (`C + I + G`) |
| **M2** | `gdp_production` | `Σ` over firms of `(revenue_cents − intermediate purchases_cents)` in the period | Nominal GDP, production approach (gross value added) |
| **M3** | `gdp_real` | `10_000 × gdp_nominal // CPI` | Real GDP, CPI-deflated |
| **M4** | `unemployment_rate` | `10_000 × |U(t)| // LF(t)`, with `U`, `E`, `LF` exactly as §3.10 | ILO/BLS **U-3** unemployment rate |
| **M5** | `u_broad` | §3.10 | **U-6** broad underutilisation |
| **M6** | `lfpr` | `10_000 × LF(t) // |{alive, 18 ≤ age < retirement_age}|` | Prime-age labour force participation rate |
| **M7** | `vacancy_rate` | `10_000 × V(t) // (V(t) + |E(t)|)`, `V(t) = Σ` remaining headcount over open vacancies | JOLTS job-openings rate |
| **M8** | `cpi` | §5.6, Laspeyres, fixed genesis basket, integer bp, base 10,000 | **CPI-U** fixed-basket consumer price index |
| **M9** | `inflation_yoy` | `10_000 × CPI(t) // CPI(t − ticks_per_sim_year) − 10_000` | Year-over-year CPI inflation |
| **M10** | `gini_wealth` | Over alive adults sorted ascending by `w_i = net_worth(a)`: `G = (2 Σ i·w_i)/(n Σ w_i) − (n+1)/n`, in bp. Negative net worths are included; `share_negative_networth` is reported alongside because `G` can exceed 1 when they are present | Gini coefficient of household net worth |
| **M11** | `gini_income` | As M10 over trailing-12-sim-month gross income (wages + transfers + realised capital income) | Gini coefficient of gross household income |
| **M12** | `median_wage` | Median over open `employments` of `wage_cents × pay_periods_per_sim_year` | Median annual earnings of employees |
| **M13** | `labour_share` | `10_000 × Σ WAGE_PAID.gross_cents // gdp_nominal` over the period | Labour share of income |
| **M14** | `firm_entry_rate` | `10_000 × |{f : founded_tick ∈ window}| // |{f : active at window start}|`, annualised | BDS establishment entry rate |
| **M15** | `firm_exit_rate` | Same with `dissolved_tick` | BDS establishment exit rate |
| **M16** | `firm_size_tail_bp` | Hill estimator of the tail exponent of the `headcount` distribution above the 80th percentile, ×10,000 | Zipf exponent of the firm-size distribution |
| **M17** | `hhi_sector` | `Σ_f (10_000 × revenue_f // sector revenue)²  // 10_000` per sector | Herfindahl–Hirschman index |
| **M18** | `market_index` | §6.8, divisor-adjusted cap-weighted, base 10,000 | Float-adjusted cap-weighted equity index (S&P 500 type) |
| **M19** | `price_fair_value_gap_bp` | `10_000 × (Σ_y last_price × shares) // (Σ_y fair_value_cents × shares) − 10_000`, with `fair_value` from §4.9 | Deviation of price from a dividend-discount fundamental |
| **M20** | `credit_growth_yoy` | `10_000 × Σ loans.outstanding_cents(t) // Σ loans.outstanding_cents(t − 1y) − 10_000` | Growth of bank credit to the private non-financial sector |
| **M21** | `credit_to_gdp_bp` | `10_000 × Σ loans.outstanding_cents // gdp_nominal_ttm` | BIS credit-to-GDP ratio (and its gap versus an HP-filtered trend) |
| **M22** | `default_rate` | `10_000 × |{loans entering status='default' in window}| // |{loans status='current' at window start}|`, annualised | Loan default / charge-off rate |
| **M23** | `bank_capital_ratio` | `10_000 × capital(B) // RWA(B)` per bank; system-weighted mean reported | Tier-1 capital ratio |
| **M24** | `m0`, `m1`, `velocity` | §1.5; `velocity = 10_000 × gdp_nominal_ttm // M1` | Monetary base, M1, M1 velocity |
| **M25** | `policy_rate_bp`, `lending_rate_bp`, `term_spread_bp` | From 8030; deposit-weighted mean loan rate; 10y minus 1y bond yield | Policy rate, average lending rate, term spread |
| **M26** | `intergenerational_elasticity` | OLS slope of `log(child lifetime income)` on `log(parent lifetime income)` across completed lifetimes | Intergenerational income elasticity |
| **M27** | `wage_scar_bp` | Mean `UNEMPLOYMENT_SPELL_ENDED.wage_change_bp`, conditioned on spell length bucket | Wage scarring from job displacement |
| **M28** | `venture_moic_bp` | `10_000 × Σ distributions // Σ capital called`, per fund vintage | Multiple on invested capital (MOIC) |

All are written to `metrics` (`03-DATA-MODEL.md §10`) in PHASE 9 at the cadence in §16 and
exported wide to Parquet.

**Beveridge curve, Okun's law, Phillips curve, and Zipf's law (research question A1)** are not
metrics; they are *relationships between* M4, M7, M3, M9, and M16. Each is computed by the
research layer, and each requires the falsification protocol of §3.11 (Beveridge), the
`--reflex-only` baseline (all four), and a statement of active mechanisms with their `entails`
strings before it may be claimed.

---

## 13. Calibration and initial conditions

Validity gate **V1** (`01-PRD.md §7.2`) requires that macro series are neither monotonically
exploding nor collapsing over 5 sim-years absent a shock. That is a calibration problem, and it
is solved at genesis or not at all.

### 13.1 Initial conditions at tick 0

| Quantity | Seed | Rationale |
|---|---|---|
| Population | 1,000 agents, `age_distribution: pyramid_ca_2020` | `01-PRD.md §5` |
| Adults 18–64 | ≈ 620 | Follows from the pyramid |
| Sectors | 8: `food, retail, industrial, services, health, education, finance, media` | Enough for input–output linkage and sector-specific shocks |
| Firms | 60, sizes drawn `lognormal(μ=1.6, σ=0.5)` on headcount, **not** a power law | See the T6 note below |
| Banks | 3 commercial + 1 central (`bk_cb`) | Three is the minimum for interbank contagion to be a network rather than a pair; more than five makes a failure illegible |
| Brokerage | 1 (`fm_broker`), a firm with employees | Commissions must be received by something with a P&L |
| Exchange | `mk_exchange`, 0 listed securities | The first IPO must be earned; a pre-listed market would be a toy from tick 0 |
| VC funds | 0 | Founded in-run |
| Median wage | `median_wage_cents = 3_600_000` per sim-year | The numéraire |
| M0 | `m0_cents = 1_000 × 1_800_000 = 1.8e9` ≈ 6 sim-months of median wage per capita | Sets the price level |
| M0 distribution | 70% households (log-normal, initial wealth Gini ≈ 5,500 bp), 20% firms as working capital, 10% banks as capital | Starting near a plausible dispersion avoids a decade-long transient; the value is declared and swept |
| Firm working capital | 3 sim-months of that firm's payroll | The single biggest determinant of the first-year firm death rate |
| Inventory | 30 sim-days of expected demand per SKU per firm | Prevents an opening stockout spiral |
| Subsistence basket cost | Calibrated so 1 sim-year of `γ` consumption = 40% of median wage | Necessities affordable but binding |
| Vacancies | Seeded so initial `u ≈ 600 bp` | Avoids an artificial mass-hiring or mass-layoff transient at tick 0 |
| Loans | **None** | Credit starts at zero and must be created, so `credit_growth` is measurable from the first tick |
| Government debt | 0; treasury seeded with 1 sim-quarter of projected spending | |
| Policy rate | 400 bp; reserve ratio 1,000 bp; min capital ratio 800 bp | |
| CPI | 10,000 bp by construction | Base period |
| Market index | Undefined until the first listing, then 10,000 bp | |

> **T6 note on the firm-size seed.** Seeding firm sizes as a power law would pre-suppose
> research question **A1**. The default seed is log-normal with modest dispersion, which is a
> *different* distribution from the Zipf tail A1 asks about. Any A1 firm-size claim must (a) be
> measured at least 3 sim-years after genesis, (b) report the seed's tail exponent alongside the
> measured one, and (c) show the exponent moved. A `firm_seed: zipf` warm-start exists for
> experiments that are not about A1 and must never be used for one that is.

### 13.2 Genesis money issuance

Genesis is the only bulk `MONEY_ISSUED` (8032). One transaction per recipient class:

```
DR cash:<recipient> or dep:<recipient>@<bank>   ...           CR iss:bk_cb   (total m0_cents)
DR res:<bank>                                    ...           CR iss:bk_cb
```

After genesis, `−balance(iss:bk_cb) == m0_cents` and INV-MONEY sub-check M-4 holds by
construction. Every later change to M0 goes through an open-market operation or the discount
window (§7.8), each of which is a logged, auditable event.

### 13.3 Making sure the economy neither explodes nor dies

The dangerous transients, their causes, and the specific guard for each.

| Failure at genesis | Cause | Guard | Ablatable? |
|---|---|---|---|
| Deflationary death spiral | Households hoard, firms cannot sell, fire everyone, incomes vanish, demand falls further | (a) Subsistence floor `γ > 0` in the LES (§5.5) means demand has a positive lower bound while anyone has income; (b) unemployment benefit (§11.3) gives the unemployed income | Yes — both are declared MECHANISMs; the `γ = 0, benefit = 0` run is the stress test |
| Hyperinflation | Markups compound; wages chase prices | (a) `max_markup_step_bp = 200` per weekly review and `max_markup_bp = 8,000` cap; (b) the Taylor rule; (c) `INV-PRICE` WARN at +40,000 bp/yr and HALT at the bound | Caps are config, not mechanism |
| Money leak | Any unbalanced path | INV-MONEY, every tick, HALT | **No** |
| Mass firm death in sim-year 1 | Working capital too thin against lumpy payroll | `firm.working_capital_months = 3`; `PAYROLL_SHORTFALL` sheds headcount before insolvency (§3.7) | — |
| Everyone unemployed | Owners never routed to DELIBERATE, so no vacancies are ever posted | `labour.vacancy_autopost` (§3.2) | Yes |
| Zero-trade equilibrium on the exchange | No listings, or commission exceeds any plausible edge | Dividends give equities a fundamental value; V3 warns after 3 empty sessions. **No market maker is seeded** — a scripted liquidity provider would manufacture the microstructure being studied | — |
| Credit never starts | `min_score_bp` too high for a population with no repayment history | Laplace smoothing in `history` (§7.4) gives a first-time borrower a neutral 5,000 bp rather than 0 | — |
| Runaway inequality in year 1 | Initial wealth distribution too skewed and compounding | Initial Gini 5,500 bp; `INV-NONDEGEN` warns at top-1 share > 0.9 | — |

### 13.4 Parameters most likely to need tuning, ranked

Ranked by leverage on V1 and V3. Tune in this order; each is a single scalar.

| Rank | Parameter | What it controls | Symptom of a bad value |
|---|---|---|---|
| 1 | `m0_cents` per capita | The price level and the resolution of integer prices | Too low: prices hit the 1 ¢ floor and relative prices collapse. Too high: no scarcity |
| 2 | `markup.initial_bp`, `markup.step_bp` | Price level and its volatility | Oscillating CPI, or monotone inflation |
| 3 | `consumption.subsistence_gamma` | The demand floor | Too high: no saving, no investment, no credit. Too low: deflation |
| 4 | `labour.min_match_score_bp` | Equilibrium unemployment | 12,000 bp of vacancies unfilled against 30% unemployment |
| 5 | `firm.working_capital_months` | First-year firm death rate | Half the firms dead by tick 3,000 |
| 6 | `labour.vacancy_visibility_k` | Search friction intensity | `v_rate` and `u` both high and uncorrelated |
| 7 | `credit.min_score_bp`, `credit.risk_spread_k` | Credit availability, cycle amplitude | No loans ever, or universal default |
| 8 | `banking.capital_ratio_min_bp` | Whether the credit brake binds | Unbounded credit growth |
| 9 | `salience.weights` for economic obligations | Whether economic decisions get cognition at all | Offers expiring unanswered; the market frozen despite a healthy budget |
| 10 | `exchange.commission_bp`, `tick_size_cents` | Whether trade happens | Zero-trade equilibrium, or one-cent arbitrage spam |
| 11 | `spend.benefit_replacement_bp` | Demand floor and labour supply | Nobody accepts a job, or destitution |
| 12 | `production.yield[sku]` | Whether the economy can feed itself | Permanent stockout of `fd_staple` and mass starvation |

### 13.5 Calibration protocol

| Stage | Configuration | Duration | Pass criteria |
|---|---|---|---|
| **0** | `--reflex-only` (`MechanicalPolicy`, §4.11), `StubLLM`, 1 seed | 2 sim-years | INV-MONEY holds every tick; `u ∈ [300, 1_200] bp`; `inflation_yoy ∈ [−200, +800] bp`; firm count within `[0.5×, 2×]` of seed; ≥ 1 trade per session once a security exists |
| **1** | 10% deliberate rate, real model, 3 seeds | 1 sim-year | V2 holds; V3 (non-degenerate) holds; V4 (action entropy) above floor |
| **2** | Full budget, 5 seeds | 5 sim-years | V1 (stationarity), V2, V3 all hold. **This is the M2 exit gate.** |
| **3** | Add M3 subsystems (exchange, ventures, bankruptcy), 5 seeds | 5 sim-years | V1–V3 hold with the capital layer active |

`01-PRD.md §8` is explicit: **do not start M3 until V2 holds for 5 consecutive sim-years.**
Stage 0 exists because tuning 12 parameters against a stochastic LLM is not tractable; tune
against the mechanical baseline first, where the economy is a classical ABM and every effect is
attributable.

---

## 14. Threats and failure modes

Symptom → cause → detector → response. Every row has a detector; a failure mode with no
detector is not on this list because it would not be findable.

| # | Failure | Symptom | Detector | Response |
|---|---|---|---|---|
| **F1** | **Money leak** — an unbalanced code path | `Σ balances ≠ 0` | INV-MONEY M-2, every tick | **HALT.** The offending transaction is the last in the tick buffer. `polis ledger explain --run <id> --tick <n>` prints the tick's legs grouped by `txn_id` with running sums |
| **F2** | Phantom money — balances drift from entries | M-2 passes but M-3 fails | INV-LEDGER M-3, incremental every tick, full at checkpoint | **HALT.** Indicates a write outside `ledger.py` (which `import-linter` should have caught) or a projection handler with a side effect |
| **F3** | Intra-tick double-spend — two actions each affordable alone | Negative balance caught by P6 | PHASE 4 must debit a **cumulative tick-scoped commitment ledger**, not the committed balance. Property test `test_no_double_commit` | Reject the second action with `reason='resources'` |
| **F4** | **Degenerate monopoly** | One firm's sector revenue share > 5,000 bp, sustained | `hhi_sector` (M17); WARN at HHI > 2,500; run flagged non-degenerate-failing at HHI > 6,000 for a sim-year | **Not automatically a bug.** Antitrust (§9.4) is the in-world response and is a policy, so the outcome is interpretable. But check first for a pricing or matching bug that gives one firm a mechanical edge |
| **F5** | **Order-book exploits** — wash trading, self-crossing, quote stuffing, priority gaming | Volume spikes with no price change; one trader on both sides; orders cancelled the tick they are placed | STP cancels self-crosses (§6.5); cancels apply only to prior-tick orders (§6.4); orders per trader per tick bounded by `action_slots`; arrival order is a seeded permutation, not `actor_id`; `metric self_cross_attempts` | Exploits found are logged as findings (threat **T10**), patched, and the run re-labelled per `01-PRD.md §9` |
| **F6** | **Hyperinflation** | `inflation_yoy > 40,000 bp` | INV-PRICE, every sim-day | WARN, then **HALT** at the bound. Almost always a markup feedback bug or a mis-scaled `m0_cents` |
| **F7** | Deflationary death spiral | CPI < 5,000 bp and employment < 2,000 bp | INV-NONDEGEN (V3), every sim-day | WARN → investigate the subsistence floor and the benefit replacement rate (§13.3) |
| **F8** | **Zero-trade equilibrium** | No trades for > 3 consecutive sessions | V3, `01-PRD.md §7.2` | WARN. Check `commission_bp`, `tick_size_cents`, whether any firm pays a dividend, and whether traders are being routed to DELIBERATE at all. **Do not fix it by seeding a market maker** |
| **F9** | Negative reservations / phantom shares | `reserved_qty < 0`, `esc < 0`, or `Σ holdings ≠ shares_outstanding` | INV-ORDERS, INV-SHARES, INV-CAPTABLE, every tick | **HALT.** Usually a partial-fill release path |
| **F10** | Bankruptcy cascade consuming the economy | > 3,000 bp of firms bankrupt within a sim-quarter | `firm_exit_rate` (M15) | WARN. Genuinely interesting for **A5** — but rule out a payroll-shortfall or interest-arithmetic bug first |
| **F11** | Wage/productivity spiral | `labour_share > 12,000 bp` | INV-LABSHARE, every sim-day | **HALT.** A labour share above 1.2 is arithmetically impossible and means wages or GDP are being double-counted |
| **F12** | Interest arithmetic drift | `Σ interest paid ≠ Σ interest scheduled` at loan close | INV-INTEREST | **HALT.** Usually a `Decimal` context divergence or a floor/ceil mismatch |
| **F13** | Rounding leak in a pro-rata split | Distributed total ≠ pool | `allocate()` is the only sanctioned splitter, and `post_transaction` P3 rejects the transaction anyway | **HALT.** Property test `test_allocate_exact` |
| **F14** | Free goods — inventory rising without inputs | Output exceeds the production-function bound | INV-PRODUCTION, per run | WARN. Usually a carry-micro bug |
| **F15** | Payroll-timing exploits — work then quit before payday; fire everyone the day before payroll | Systematic separations clustered just before payroll ticks | `metric separations_by_days_to_payroll` | **Design fix, already applied:** wages accrue per tick worked and are paid on separation (§3.7). The metric exists to confirm the fix holds |
| **F16** | LLM price collusion | Price dispersion within a sector → 0 | `metric price_dispersion_by_sector` | **Possibly a finding** (**B3**, emergent norms) rather than a bug — but first confirm that competitor prices are not leaking into prompts beyond the visible slice (§5.3), which would make it an artefact |
| **F17** | Mode collapse in economic action choice | Action-type entropy below the V4 floor within the economy groups | INV-ENTROPY (V4), every sim-day | WARN. `01-PRD.md §11` names this the characteristic LLM-society failure |
| **F18** | Budget-induced market freeze | Offers, term sheets, and margin calls expiring unanswered at a high rate | `metric mandatory_obligations_unserved` | Force-routing (§2.4) should make this zero. Non-zero means the MANDATORY class is not being honoured, which silently breaks every market |
| **F19** | Escrow orphaned by a mid-tick failure | `esc` balance with no matching open order | INV-ORDERS (the `≤` gap exceeds the open-order count in cents) | **HALT.** Release paths must be in the same transaction as the state change |
| **F20** | Simulation-aware trading | An agent reasons explicitly about exploiting the matching engine | `llm_calls.sim_aware_flag` (`03-DATA-MODEL.md §1.3`) plus a review of `Action.reasoning` on outlier-profit traders | Threat **T3**. Reported per run; not automatically a halt |

**The general rule.** Invariant failures HALT because a run that violates conservation of money
is not a run, it is a bug report (`02-ARCHITECTURE.md §9`). Distributional failures WARN because
they might be findings. The distinction is: *could a correct implementation produce this?* If
no, HALT.

---

## 15. Action-type coverage

Every economic decision maps onto the closed `ActionType` enum of `02-ARCHITECTURE.md §6.2`.
This table is the complete mapping; the only addition requested anywhere in this document is
`DECLARE_DIVIDEND` (§0.2).

| ActionType | Group | Params | Resolved in | Emits | Reflex-eligible? |
|---|---|---|---|---|---|
| `APPLY_FOR_JOB` | labour | `vacancy_id, asked_wage_cents?` | 5.3 | 5003 | No |
| `MAKE_OFFER` | labour | `application_id, wage_cents, occupation` | 5.3 | 5005 | No |
| `ACCEPT_OFFER` | labour | `offer_id` — **polymorphic** over job / term sheet / acquisition / settlement / loan terms | 5.3 / 5.7 | 5006, 9014, 9021 | No |
| `DECLINE_OFFER` | labour | `offer_id, reason_code, counter_*?` | 5.3 / 5.7 | 5007, 9015, 9022 | No |
| `NEGOTIATE_WAGE` | labour | `offer_id \| employment_id, counter_cents` | 5.3 | 5009 | No |
| `POST_VACANCY` | labour | `occupation, skill_reqs, wage_offer_cents, headcount` | 5.3 | 5001 | No |
| `FIRE_EMPLOYEE` | labour | `employment_id, reason` | 5.3 | 5011 | No |
| `QUIT_JOB` | labour | `employment_id, destination` | 5.3 | 5012 | No |
| `WORK` | labour | `employment_id, effort_bp?` | 5.3 | 5020 | **Yes** |
| `BUY_GOOD` | goods | `sku, qty, seller_firm_id, max_unit_price_cents` | 5.4 | 6020 / 6021 / 6011 | **Yes**, necessities only, posted price, under a value cap |
| `SET_PRICE` | goods | `sku, price_cents` | 5.4 | 6022 | No |
| `PRODUCE` | goods | `sku, hours_bp` — self-employed only | 5.4 | 6010 | No |
| `RESTOCK` | goods | `sku, target_qty, from_firm_id` | 5.4 | 6023 | No |
| `SUBMIT_ORDER` | exchange | `symbol, side, order_type, qty, limit_price_cents?, flags[]` | 5.5 | 7010 / 7011 | No |
| `CANCEL_ORDER` | exchange | `order_id` | 5.5 (before new orders) | 7012 | No |
| `SHORT` | exchange | `symbol, qty, limit_price_cents, collateral_cents` | 5.5 | 7060 | No |
| `IPO_LIST` | exchange | `shares_offered, primary_shares, secondary_shares, price_low, price_high, underwriter_bank_id` | 5.5 | 7070 | No |
| `OPEN_ACCOUNT` | banking | `bank_id` | 5.6 | 8002 | No |
| `DEPOSIT` | banking | `bank_id, cents` | 5.6 | 8004 | No |
| `WITHDRAW` | banking | `bank_id, cents` | 5.6 | 8005 / 8006 | No |
| `APPLY_FOR_LOAN` | banking | `lender_id, cents, purpose, term_ticks, collateral` | 5.6 | 8011 → 8012 | No |
| `REPAY_LOAN` | banking | `loan_id, cents` | 5.6 | 8013 | **Yes**, scheduled instalment only |
| `DEFAULT` | banking | `loan_id, reason` | 5.6 | 8016 | No |
| `FOUND_COMPANY` | ventures | `name, sector, place_id, initial_capital_cents, is_startup, is_fund, thesis?` | 5.7 | 6001, 9001, 9005 | No |
| `PITCH` | ventures | `investor_id, ask_cents, pre_money_ask_cents, deck_text` | 5.7 | 9011 | No |
| `ISSUE_TERM_SHEET` | ventures | full term set (§8.7) | 5.7 | 9013 | No |
| `INVEST` | ventures | `target_id, cents, instrument(round\|lp_commitment\|bond)` | 5.7 | 9010, 9005 | No |
| `ACQUIRE` | ventures | `target_id, offer_cents, consideration, integration_mode, financing` | 5.7 | 9020 | No |
| `SELL_STAKE` | ventures | `firm_id, qty, price_cents?, deal_id?` — private sale or tender | 5.7 | 6004, 9021 | No |
| `FILE_BANKRUPTCY` | ventures | `reason` | 5.7 | 9030 | No |
| **`DECLARE_DIVIDEND`** | ventures | `firm_id, total_cents` — **requested addition (§0.2)** | 5.7 | 6030 | No |

Reflex-eligible actions are exactly those listed in `04-AGENT-SPEC.md §8`: `WORK`,
`BUY_GOOD` (necessities), and `REPAY_LOAN` (scheduled). **Everything with a counterparty, a
negotiated price, or a commitment is LLM-only**, which is the guarantee that the economy is not
a classical ABM wearing a hat (threat **T9**).

---

## 16. Scheduling

### 16.1 PHASE 5 — action resolution (every tick, fixed order per `02-ARCHITECTURE.md §5.1`)

| Slot | Institution | Module | Actions consumed |
|---|---|---|---|
| 3 | labour | `labour.py` | `APPLY_FOR_JOB`, `MAKE_OFFER`, `ACCEPT_OFFER`, `DECLINE_OFFER`, `NEGOTIATE_WAGE`, `POST_VACANCY`, `FIRE_EMPLOYEE`, `QUIT_JOB`, `WORK` |
| 4 | goods | `goods.py`, `firms.py` | `BUY_GOOD`, `SET_PRICE`, `PRODUCE`, `RESTOCK` |
| 5 | exchange | `exchange/` | `CANCEL_ORDER` (first), then `SUBMIT_ORDER`, `SHORT`, `IPO_LIST` |
| 6 | banking | `banking.py` | `OPEN_ACCOUNT`, `DEPOSIT`, `WITHDRAW`, `APPLY_FOR_LOAN`, `REPAY_LOAN`, `DEFAULT` |
| 7 | ventures | `ventures.py` | `FOUND_COMPANY`, `PITCH`, `ISSUE_TERM_SHEET`, `INVEST`, `ACQUIRE`, `SELL_STAKE`, `FILE_BANKRUPTCY`, `DECLARE_DIVIDEND` |

Rationale for this ordering, as `02-ARCHITECTURE.md §5.1` requires each institution to state:
labour before goods so a wage agreed this tick is not spendable until next tick (no same-tick
income–expenditure loop); goods before exchange so consumption is not financed by a sale
executed in the same tick; exchange before banking so a margin shortfall is visible to the bank
in the same tick; banking before ventures so a loan approved this tick can fund a round next
tick, not this one. Every dependency that would require a different order is resolved by
splitting across two ticks, never by reordering.

### 16.2 PHASE 7 — scheduled institutional steps, fixed internal order

| # | Step | Cadence (sim-time) | Module |
|---|---|---|---|
| 1 | Production run | daily 06:00 | `firms` |
| 2 | Capital depreciation, inventory spoilage and write-off | monthly / daily | `firms` |
| 3 | Price review | weekly, Monday 08:00 | `firms` |
| 4 | Restock and input purchase | daily 05:00 | `firms` |
| 5 | Application screening, shortlisting, offer obligations | daily 09:00 | `labour` |
| 6 | Payroll, income-tax withholding, employer payroll tax | biweekly, days 1 and 15, 17:00 | `labour` |
| 7 | Rent | monthly, day 1 | `goods` (levels from `05-WORLD-SPEC.md`) |
| 8 | Interest accrual on all loans | daily 00:00 | `banking` |
| 9 | Loan amortisation payments | monthly, day 1 | `banking` |
| 10 | Deposit interest | monthly, day 28 | `banking` |
| 11 | Market close: closing auction, OHLCV, index, dividends | daily 16:00 | `exchange`, `firms` |
| 12 | Bank settlement: reserves → interbank → discount window → capital ratios → failure | daily 23:00 | `banking` |
| 13 | Venture: runway and burn, capital calls, fund distributions, management fees | monthly / quarterly | `ventures` |
| 14 | Solvency tests, bankruptcy triggers | daily 22:00 | `ventures` |
| 15 | Bankruptcy case advance: claims, liquidation, distribution, discharge | daily | `ventures` |
| 16 | Tax assessment and collection (corporate quarterly, property annual, arrears conversion) | quarterly / annual | `banking` (fiscal agent) |
| 17 | Transfers and spending: benefit, pension, subsidies, government wages, infrastructure | weekly / monthly | `banking` (fiscal agent) |
| 18 | Bond auction, coupon, maturity; budget close | monthly / semi-annual / quarterly | `banking` (fiscal agent) |
| 19 | Policy rate review | every 6 sim-weeks | `banking` (central) |

Ordering rationale: production before pricing (unit cost must be known); payroll after
production (hours worked are known); accrual before amortisation; bank settlement after all
customer money has moved; solvency tests after everything, so a firm that survives the day is
genuinely solvent; the policy rate last, so a new rate applies from the next tick and never
mid-tick.

### 16.3 PHASE 9 — metrics

| Cadence | Metrics |
|---|---|
| Every tick | M24 (`m0`, `m1`), all INV-* invariant results |
| Every sim-day | M4–M8, M18, M23, M25, and the WARN-class invariants |
| Every sim-week | M12, M20, M22 |
| Every sim-quarter | M1–M3, M9–M11, M13–M17, M19, M21, INV-GDP |
| Every sim-year | M14, M15, M16, M26, M27, M28 |

---

## 17. Test matrix

`02-ARCHITECTURE.md §12` defines the layers; this is what the economy owes each of them.

| Layer | Tests |
|---|---|
| **Unit** | Annuity schedule closes to zero; largest-remainder allocator; `match_score_bp`; CPI over a synthetic transaction stream; Laspeyres versus Fisher; waterfall over hand-built cap tables; auction uncrossing; production carry across 1,000 runs |
| **Property** (Hypothesis) | `test_allocate_exact` (§2.3); the twelve exchange properties P1–P12 (§6.12); `post_transaction` rejects any unbalanced leg set; for any random sequence of balanced transactions, `Σ balances == 0`; for any loan parameters, `Σ principal payments == principal` |
| **Determinism** | Same seed → identical `trades`, `ledger_entries`, and event-hash chain over 200 ticks with `StubLLM`; the arrival permutation is stable under a change in `actor_id` naming |
| **Invariant** | INV-MONEY (six sub-checks) over a 5,000-tick smoke run; `tests/invariants/test_death_settlement.py` covering §10.7 cases A–D plus death holding a partial fill, death as a creditor in an open case, death of a GP, death of a bank's sole owner; a scripted bank failure with a deposit haircut; a bankruptcy discharge cascading into a second bank failure |
| **Integration** | Full tick loop, 50 agents, 500 ticks, recorded completion cache: a firm founded, hires, produces, prices, sells, borrows, IPOs, is acquired; a startup raising two rounds and dying; a household bankruptcy with a discharge |
| **Golden** | The frozen 100-tick run of `tests/integration/golden/` must include at least one wage payment, one goods purchase, one trade, and one loan payment, so any change to the leg patterns shows up as a hash change and must be explained in the PR |

---

## 18. Open calibration questions (resolve in M2)

To be answered with a small sweep and written back into this document, in the manner of
`04-AGENT-SPEC.md §14`.

1. What `labour.min_match_score_bp` and `vacancy_visibility_k` jointly produce an unemployment
   rate in `[400, 900]` bp with a vacancy rate that moves? Calibrate on the Stage-0 mechanical
   baseline; do not guess.
2. Does `price_setting: hybrid` differ measurably from `markup_over_cost` in CPI volatility? If
   not, the LLM is contributing nothing to price dynamics and that is a reportable T9 result.
3. Is `w_llm_bp = 5,000` the right blend for venture valuation, or does the anchor dominate to
   the point where investor judgement is unmeasurable?
4. What `commission_bp` and `tick_size_cents` give a non-degenerate order book at 1,000 agents
   with fewer than 10 listed securities?
5. Does the credit cycle have any amplitude at 60 firms and 3 banks, or is 1,000 agents simply
   too small for **A5**? This is a finite-size question (threat **T7**) and should be answered
   on the scale ladder (250 / 500 / 1,000 / 2,000) before A5 is claimed either way.

---

*Next: `07-SOCIETY-SPEC.md`.*



