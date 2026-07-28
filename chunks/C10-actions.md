# C10 — Action schema, validators, budget, resolution dispatch

**M1** · `polis/agents/actions/` · **Depends on:** C02 (events), C03 (store), C04 (kernel), C07 (agent core) · **Blocks:** C09, C11, C12, C13, C14, C15, C16, C17, C18, C19, C21, C22 · **Size:** M

---

## 1. Context

Every institution in POLIS consumes exactly one input type and produces exactly one output
type: it takes `Action` objects and emits `Event` objects. This chunk defines that boundary.
It owns the closed `ActionType` enum, a pydantic params model per type, the five validation
gates of `04 §11`, the per-tick action-slot budget that is identical for native and external
agents (threat T12), and the PHASE 5 dispatcher that hands validated actions to institutions
in the fixed order of `02 §5.1`. **`InstitutionResolver` in §5 is the single most important
interface in the project** — every chunk from C11 to C19 implements it, and a change to it is
a change to eleven chunks. Get it right, then leave it alone.

One rule dominates the design: **the legality gate flags, it does not reject.** Crime has to
be *possible* or research question B5 (deterrence) cannot be asked at all.

---

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/02-ARCHITECTURE.md` | **all** — **§6 actions (primary source)**, §5 tick phases, §5.1 institutional order, §7.1 dependency rules, §10 error handling |
| `../docs/03-DATA-MODEL.md` | §0 conventions (money, IDs), §2.1 `agents`, §8 `crimes`, and every table an action names an id into |
| `../docs/04-AGENT-SPEC.md` | **§11 validation (primary source)**, §8 reflex action set, §9.2 output schema, §5 perception |
| `../docs/08-EXTERNAL-AGENT-PROTOCOL.md` | §4.3 `polis_act`, §4.4 `legal_actions`, §13 parity table, §15 conformance items 4 and 11 |
| `../docs/05-WORLD-SPEC.md` | §13.1 `RENT_HOME` |
| `../docs/06-ECONOMY-SPEC.md` | §0.2 `DECLARE_DIVIDEND` |
| `../docs/07-SOCIETY-SPEC.md` | §0.5 D-2 `FOUND_PARTY`, §8 crime taxonomy |
| Chunks | C02 (`Event`, `EventLog`), C04 (`stable`, `RngRegistry`), C07 (`Observation`, `AgentState`) |

---

## 3. Scope — in

1. The `Action` envelope (`02 §6.1`) and `ValidatedAction`.
2. The **closed** `ActionType` enum: the 68 types of `02 §6.2` plus the three ratified
   additions `RENT_HOME`, `DECLARE_DIVIDEND`, `FOUND_PARTY` — **71 total**.
3. One frozen pydantic params model per type, with `extra="forbid"`, and a completeness assert.
4. `ActionValidator` — the five gates in order, first failure rejects, legality never rejects.
5. `SlotLedger` — per-tick action slots, one config key, identical for native and external.
6. `RejectionLedger` — `last_action_outcome` visible in the next tick's `Observation`.
7. `NULL_ACTION` substitution.
8. `legal_actions()` — the filtered, schema-carrying action list consumed by C09 and C22.
9. `InstitutionResolver` protocol, `ResolverRegistry`, and `ActionDispatcher` (PHASE 5).
10. `export_action_schema_bundle()` → `polis/events/schemas/actions.v1.json` for the gateway.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| The *semantics* of any action — what `APPLY_FOR_JOB` does | the owning institution (C11–C19) |
| Capability/resource/legality *rules* — you own the framework, they own the predicates | the owning institution |
| The `crimes` table, detection, prosecution, kind 13010 | **C19** |
| Choosing an action (salience, prompts, reflex policy) | **C09**, **C07** |
| Signature verification, nonces, deadlines, Redis | **C22** |
| Money movement, `ledger.post_transaction` | **`polis/economy/ledger.py`** (C11/C14) |

---

## 5. Interfaces you provide

```python
# polis/agents/actions/types.py
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Mapping, Protocol, Sequence
from uuid import UUID

class ActionType(StrEnum):
    # world (5)
    MOVE_TO = "MOVE_TO"; IDLE = "IDLE"; SLEEP = "SLEEP"; EAT = "EAT"
    RENT_HOME = "RENT_HOME"                                   # ratified, 05 §13.1
    # speech (3)
    SAY = "SAY"; DIRECT_MESSAGE = "DIRECT_MESSAGE"; BROADCAST = "BROADCAST"
    # labour (9)
    APPLY_FOR_JOB = "APPLY_FOR_JOB"; ACCEPT_OFFER = "ACCEPT_OFFER"
    DECLINE_OFFER = "DECLINE_OFFER"; QUIT_JOB = "QUIT_JOB"
    NEGOTIATE_WAGE = "NEGOTIATE_WAGE"; POST_VACANCY = "POST_VACANCY"
    MAKE_OFFER = "MAKE_OFFER"; FIRE_EMPLOYEE = "FIRE_EMPLOYEE"; WORK = "WORK"
    # education (4)
    ENROL = "ENROL"; STUDY = "STUDY"; DROP_OUT = "DROP_OUT"; TAKE_EXAM = "TAKE_EXAM"
    # goods (4)
    BUY_GOOD = "BUY_GOOD"; SET_PRICE = "SET_PRICE"; PRODUCE = "PRODUCE"; RESTOCK = "RESTOCK"
    # exchange (4)
    SUBMIT_ORDER = "SUBMIT_ORDER"; CANCEL_ORDER = "CANCEL_ORDER"
    SHORT = "SHORT"; IPO_LIST = "IPO_LIST"
    # banking (6)
    OPEN_ACCOUNT = "OPEN_ACCOUNT"; DEPOSIT = "DEPOSIT"; WITHDRAW = "WITHDRAW"
    APPLY_FOR_LOAN = "APPLY_FOR_LOAN"; REPAY_LOAN = "REPAY_LOAN"; DEFAULT = "DEFAULT"
    # ventures (8)
    FOUND_COMPANY = "FOUND_COMPANY"; PITCH = "PITCH"; ISSUE_TERM_SHEET = "ISSUE_TERM_SHEET"
    INVEST = "INVEST"; ACQUIRE = "ACQUIRE"; SELL_STAKE = "SELL_STAKE"
    FILE_BANKRUPTCY = "FILE_BANKRUPTCY"
    DECLARE_DIVIDEND = "DECLARE_DIVIDEND"                     # ratified, 06 §0.2
    # media (8)
    POST = "POST"; REPOST = "REPOST"; LIKE = "LIKE"; COMMENT = "COMMENT"
    FOLLOW = "FOLLOW"; UNFOLLOW = "UNFOLLOW"
    PUBLISH_ARTICLE = "PUBLISH_ARTICLE"; RETRACT = "RETRACT"
    # polity (7)
    JOIN_PARTY = "JOIN_PARTY"; ANNOUNCE_CANDIDACY = "ANNOUNCE_CANDIDACY"
    CAMPAIGN = "CAMPAIGN"; VOTE = "VOTE"; PROPOSE_POLICY = "PROPOSE_POLICY"; LOBBY = "LOBBY"
    FOUND_PARTY = "FOUND_PARTY"                               # ratified, 07 §0.5 D-2
    # law (7)
    COMMIT_CRIME = "COMMIT_CRIME"; REPORT_CRIME = "REPORT_CRIME"; FILE_SUIT = "FILE_SUIT"
    RETAIN_COUNSEL = "RETAIN_COUNSEL"; TESTIFY = "TESTIFY"; SETTLE = "SETTLE"; RULE = "RULE"
    # social (5)
    BEFRIEND = "BEFRIEND"; COURT = "COURT"; PROPOSE_UNION = "PROPOSE_UNION"
    DISSOLVE_UNION = "DISSOLVE_UNION"; HAVE_CHILD_INTENT = "HAVE_CHILD_INTENT"
    # meta (1)
    NULL_ACTION = "NULL_ACTION"

Origin = Literal["reflex", "deliberate", "reflect", "external", "scripted"]
Gate   = Literal["schema", "capability", "locality", "resources", "legality"]
RejectReason = Literal["schema", "capability", "locality", "resources",
                       "unknown_type", "no_slots", "unavailable"]
#            NOTE: "legality" is deliberately absent. See §9.3.

@dataclass(frozen=True, slots=True)
class Action:
    action_id: UUID
    tick:      int
    actor_id:  str
    type:      ActionType
    params:    Mapping[str, Any]
    origin:    Origin
    salience:  float
    reasoning: str | None          # verbatim; NO code path may branch on its content
    sig:       str | None          # required iff origin == "external"

@dataclass(frozen=True, slots=True)
class LegalityVerdict:
    is_crime:     bool
    crime_type:   str | None = None      # 03 §8 crimes.type vocabulary
    victim_id:    str | None = None
    amount_cents: int | None = None
    crime_id:     str | None = None      # assigned by the oracle if it recorded a row

@dataclass(frozen=True, slots=True)
class ValidatedAction:
    action:           Action
    validated_params: ActionParams        # the parsed pydantic model — institutions use THIS
    legality:         LegalityVerdict
    slot_index:       int

@dataclass(frozen=True, slots=True)
class Rejection:
    action_id: UUID
    actor_id:  str
    type:      ActionType
    gate:      Gate | None
    reason:    RejectReason
    detail:    str
    substitute: Action                    # the NULL_ACTION that replaces it

@dataclass(frozen=True, slots=True)
class ActionOutcome:
    """Surfaces in the next tick's Observation as `last_action_outcome`
    (08 §4.4). Shape is fixed by the external protocol."""
    action_id: UUID
    tick:      int
    type:      ActionType
    status:    Literal["applied", "rejected"]
    reason:    RejectReason | None
    detail:    str | None
    effects:   tuple[str, ...]            # human-readable, from resolver event kinds

@dataclass(frozen=True, slots=True)
class GateFailure:
    reason: RejectReason
    detail: str = ""

GateResult = GateFailure | None          # None == pass. Every gate returns this.

@dataclass(frozen=True, slots=True)
class LegalAction:
    type:         ActionType
    param_schema: Mapping[str, Any]       # JSON Schema for this type's params model
    options:      tuple[Mapping[str, Any], ...]   # concrete targets where small and knowable
```

```python
# polis/agents/actions/protocol.py
class InstitutionResolver(Protocol):
    """THE contract. Implemented once per PHASE 5 slot by C11-C19.
    Institutions may import polis.agents.actions; they may NEVER import
    polis.agents.cognition or polis.agents.memory (02 §7.1)."""

    slot:    InstitutionSlot                  # fixed position in 02 §5.1
    handles: frozenset[ActionType]            # disjoint across all registered resolvers

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        """Standing: only a firm owner posts a vacancy, only a licensed lawyer files."""

    def check_locality(self, action: Action, ctx: ValidationContext) -> GateResult:
        """Physical/relational ability. Reads ctx.observation's place, NOT live position."""

    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        """Funds, shares, inventory, time. Slots are checked by C10, not here."""

    def resolve(self, actions: Sequence[ValidatedAction], tick: int,
                ctx: ResolutionContext) -> Sequence[Event]:
        """PHASE 5. `actions` arrives sorted by (actor_id, action_id). Resolution must be
        order-independent or explicitly price-time-priority (02 §5). Returns events in the
        order they are to be appended; the dispatcher does not reorder them."""

    def options_for(self, action_type: ActionType, ctx: ValidationContext
                    ) -> tuple[Mapping[str, Any], ...]:
        """Concrete targets for legal_actions(). Return () when the set is large or unknowable."""

class LegalityOracle(Protocol):
    """Implemented by C19 (polis.society.law). NEVER rejects — it classifies."""
    def assess(self, action: Action, params: ActionParams,
               ctx: ValidationContext) -> LegalityVerdict: ...

class PermissiveLegalityOracle:
    """M1 default until C19 lands: everything is legal, nothing is recorded."""

class InstitutionSlot(IntEnum):
    MOVEMENT = 1; COMMUNICATION = 2; LABOUR = 3; GOODS = 4; EXCHANGE = 5
    BANKING = 6; VENTURES = 7; POLITY = 8; LAW = 9; MISC = 10
```

```python
# polis/agents/actions/registry.py
class ResolverRegistry:
    def register(self, resolver: InstitutionResolver) -> None:
        """Raises DuplicateHandler if any ActionType is already claimed."""
    def for_type(self, t: ActionType) -> InstitutionResolver | None: ...
    def in_slot_order(self) -> tuple[InstitutionResolver, ...]:
        """Sorted by InstitutionSlot. A literal ordering, never a dict traversal."""
    def registered_types(self) -> frozenset[ActionType]: ...

# polis/agents/actions/validate.py
class ActionValidator:
    def validate(self, action: Action, ctx: ValidationContext
                 ) -> ValidatedAction | Rejection: ...
    def validate_batch(self, actions: Sequence[Action], tick: int,
                       ctxs: Mapping[str, ValidationContext]
                       ) -> tuple[tuple[ValidatedAction, ...], tuple[Rejection, ...]]:
        """PHASE 4. Processes in `stable()` order by (actor_id, action_id). Emits 2060/2061/2062."""

# polis/agents/actions/budget.py
class SlotLedger:
    def __init__(self, action_slots: int) -> None:
        """action_slots comes from ONE config key (02 §6.3): 1 microscope, 4 chronicle.
        The gateway reads the same key (08 §15 item 4)."""
    def consume(self, actor_id: str, tick: int) -> int | None:
        """Returns the slot index, or None when exhausted. A rejected action still spends."""
    def remaining(self, actor_id: str, tick: int) -> int: ...
    def reset(self, tick: int) -> None:
        """PHASE 0. Drops the previous tick's counters."""

# polis/agents/actions/outcomes.py
class RejectionLedger:
    def record_rejection(self, r: Rejection, tick: int) -> None: ...
    def record_applied(self, a: ValidatedAction, tick: int,
                       effects: Sequence[str]) -> None: ...
    def last_action_outcome(self, actor_id: str, tick: int) -> ActionOutcome | None:
        """Returns the outcome from tick-1. C07's PHASE 1 calls this."""
    def prune(self, tick: int) -> None:
        """Keeps 2 ticks."""

# polis/agents/actions/legal.py
def legal_actions(obs: Observation, state: AgentState,
                  registry: ResolverRegistry, ctx: ValidationContext
                  ) -> tuple[LegalAction, ...]:
    """Types whose resolver is registered, whose capability+locality gates pass, and which
    the agent's life stage permits. ActionType declaration order. Consumed by C09's prompt
    and, through the gateway, by external agents (08 §4.4)."""

REFLEX_ALLOWED: Final[frozenset[ActionType]]      # 04 §8, exactly ten types

# polis/agents/actions/dispatch.py
class ActionDispatcher:
    def dispatch(self, validated: Sequence[ValidatedAction], tick: int,
                 ctx: ResolutionContext) -> tuple[Event, ...]:
        """PHASE 5. Partitions by resolver, walks slots 1..10 in order, concatenates events."""

# polis/agents/actions/schema_export.py
def export_action_schema_bundle(path: Path) -> str:
    """Writes polis/events/schemas/actions.v1.json from the enum + params models.
    Returns its sha256. CI asserts the file on disk matches (08 §4.3)."""
```

---

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C02 | `EventLog.emit(...)`, `Event` | 2060–2062; resolver events pass through |
| C03 | `AgentRepository` (read-only) | capability context |
| C04 | `stable()`, `RngRegistry` | ordering; `action_id` generation via `rng.get("action.id", actor_id, tick)` |
| C07 | `Observation`, `AgentState` | `ValidationContext` construction |
| C07 | `stage`, `employer_id`, `wealth_cents`, `current_place_id` | gates |

`ValidationContext` is built once per agent per tick by the kernel and passed down; it holds
`observation`, `state`, `tick`, `runtime` (the `polis/config/runtime.py` overlay), and
read-only repository handles. It is frozen.

> **Coordination item for C07.** C07's `SelfView.last_action_rejected` is a
> `tuple[str, str] | None` (`(action_type, reason)`). `08 §4.4` requires the richer
> `{action_id, status, reason, effects[]}` object, and an external agent must not see more
> than a native one (T12). Widen `SelfView` to carry `ActionOutcome | None` and let the
> 2-tuple be derived from it, or the two views diverge. **Raise this with C07; do not ship
> two shapes.**

---

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `agents` | R | standing, place, stage, wealth for the gates |
| `events` | W | 2060–2062 only, via C02 |
| `crimes` | **never directly** | the `LegalityOracle` (C19) writes it; C10 carries the verdict |
| everything else | — | institutions own their tables |

**Architectural clarification, load-bearing.** `02 §7.1` lists `economy → kernel, events,
world, store, config (never: agents.cognition)`. The prose beneath it says the critical rule
is that institutions never import *agent cognition*. Institutions **must** import
`polis.agents.actions` — they cannot consume `Action` otherwise. Write the `import-linter`
contract as:

```ini
[importlinter:contract:institutions-no-cognition]
type = forbidden
source_modules   = polis.economy, polis.society, polis.world
forbidden_modules = polis.agents.cognition, polis.agents.memory, polis.agents.state
```

`polis.agents.actions` is explicitly permitted and must not import anything from
`polis.agents.cognition` or `polis.agents.memory`, or the contract becomes vacuous.

---

## 8. Event kinds owned

**Range: 2060–2079** inside the `polis.agents` range (2000–2999), **not** 4000–4099.
Rationale: `02 §3.3` subjects 4001–4099 to cognition sampling, and action events must never
be sampled — `sys.action.reject_rate.<reason>` (`10 §1.8`) and the agent's own
`last_action_outcome` both depend on completeness. 2000–2049 belong to C07 and 2050 is
reserved by it; 2060–2079 is free. Re-confirm against `polis/events/kinds.py` before
registering.

| Kind | Name | Payload |
|---|---|---|
| 2060 | `ACTION_SUBMITTED` | `action_id, actor_id, type, params, origin, salience, reasoning, llm_call_id, slot_index` |
| 2061 | `ACTION_REJECTED` | `action_id, actor_id, type, gate, reason, detail, origin, slot_consumed (always true), substituted_with: "NULL_ACTION"` |
| 2062 | `ACTION_FLAGGED_ILLEGAL` | `action_id, actor_id, type, crime_type, victim_id, amount_cents, crime_id, proceeded (always true)` |

2063–2079 reserved, unused.

`2060` is the native counterpart of `20020 EXTERNAL_ACTION_SUBMITTED` (`08 §13`) and serves
the same purpose: it is the `cause_seq` anchor every downstream institutional event points
back to, and it is the only durable home for `Action.reasoning` (`02 §6.1` requires it be
preserved for audit). Volume is ~1,000 rows/tick at 1,000 agents, inside the 15–25k/tick
budget of `02 §11`.

`2052.proceeded` is hard-coded `true`. If it is ever `false`, the legality gate has started
rejecting and B5 is dead.

---

## 9. Implementation notes

### 9.1 Params models

One frozen pydantic model per `ActionType`, in `polis/agents/actions/params/<group>.py`:

```python
class ActionParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

Cents  = Annotated[int, Field(ge=0)]          # BIGINT minor units. NEVER float (02 §4.6).
AgentId = Annotated[str, StringConstraints(pattern=r"^ag_[a-z0-9_]{1,64}$")]
ExternalAgentId = Annotated[
    str,
    StringConstraints(pattern=r"^ag_[0-9a-f]{64}$"),
]
FirmId  = Annotated[str, StringConstraints(pattern=r"^fm_[a-z0-9_]{1,32}$")]
PlaceId = Annotated[str, StringConstraints(pattern=r"^pl_[a-z0-9_]{1,32}$")]

class MoveToParams(ActionParams):      place_id: PlaceId
class IdleParams(ActionParams):        pass
class SleepParams(ActionParams):       place_id: PlaceId | None = None
class EatParams(ActionParams):         sku: str | None = None; place_id: PlaceId | None = None
class RentHomeParams(ActionParams):    place_id: PlaceId; offered_rent_cents: Cents
class SayParams(ActionParams):         text: Annotated[str, StringConstraints(max_length=1000)]
                                       # to_id: AgentId | None = None
class BuyGoodParams(ActionParams):     sku: str; qty: Annotated[int, Field(ge=1, le=1000)]
                                       seller_firm_id: FirmId
                                       max_unit_price_cents: Cents
class SubmitOrderParams(ActionParams): symbol: str; side: Literal["buy","sell"]
                                       order_type: Literal["limit","market"]
                                       qty: Annotated[int, Field(ge=1)]
                                       limit_price_cents: Cents | None = None
class DeclareDividendParams(ActionParams): firm_id: FirmId; per_share_cents: Cents
class FoundPartyParams(ActionParams):  name: Annotated[str, StringConstraints(max_length=64)]
                                       platform: Mapping[str, float]      # proposition -> stance
                                       founding_member_ids: tuple[AgentId, ...]
class CommitCrimeParams(ActionParams): crime_type: Literal["theft","fraud","insider_trading",
                                           "assault","contract_breach","embezzlement","perjury"]
                                       victim_id: str | None = None
                                       amount_cents: Cents | None = None
class NullActionParams(ActionParams):  replaced_type: ActionType | None = None
                                       reason: str | None = None
```

`AgentId` remains broad enough for native simulation identities. Every external-agent
admission boundary uses `ExternalAgentId` instead: registration derives it as
`"ag_" + <verified ed25519 public-key hex>`, and session, lifecycle, action-signature,
queue, routing, and authorization checks require that exact derived value. A shortened key
or any uppercase/non-hex spelling is display-only and is rejected at those boundaries.

```python
PARAMS_MODELS: Final[Mapping[ActionType, type[ActionParams]]] = {...}
assert set(PARAMS_MODELS) == set(ActionType), "every type needs a params model"
```

The three ratified additions get their models here; **their resolution belongs to their
institutions** — `RENT_HOME` to C06's housing matcher, `DECLARE_DIVIDEND` to C15,
`FOUND_PARTY` to C18. C10 ships the enum member, the model, the locality rule, and nothing else.

> **Reconciliation with C06.** C06's draft declares its own `RentHomeParams` in
> `polis/world/actions.py` and free-function validators returning `RejectReason | None`.
> **Params models must live here only** — they are the single source for the generated
> `actions.v1.json` bundle the gateway validates against, and a second definition guarantees
> drift (trap 12). Delete C06's copy and import `RentHomeParams` from
> `polis.agents.actions.params.world`. The free-function validator shape is fine as the
> *implementation*: `GateResult` is `RejectReason | None` plus a detail string, so
> `validate_rent_home` becomes the body of `WorldResolver.check_resources`. Agree this with
> C06 before either chunk merges.

### 9.2 The five gates, in order

| # | Gate | Owner of the predicate | Failure |
|---|---|---|---|
| 1 | **Schema** | C10 (`PARAMS_MODELS[type]`) | `reason: "schema"`, detail = the pydantic error path |
| 2 | **Capability** | resolver `.check_capability` | `reason: "capability"` |
| 3 | **Locality** | C10's `LOCALITY_REQUIREMENTS` table, then resolver `.check_locality` | `reason: "locality"` |
| 4 | **Resources** | C10 (slots) then resolver `.check_resources` | `reason: "resources"` |
| 5 | **Legality** | `LegalityOracle.assess` | **never rejects** |

**The first failure rejects and stops.** Do not accumulate. The reported `reason` is the
first gate that failed, deterministically, because `sys.action.reject_rate.<reason>` is a
distribution over first failures and an "all failures" variant is a different metric.

Pre-gate checks that produce a rejection without entering the ladder: `unknown_type` (a type
not in the enum — only reachable from the gateway), `no_slots`, and `unavailable` (the type's
resolver is not registered in this run, e.g. `SUBMIT_ORDER` before M3).

**Locality is evaluated against `ctx.observation`'s place — last tick's committed state.**
Not the live position. The legal-action list the agent was shown was built from that same
Observation, so validating against a position that PHASE 5 slot 1 is about to change would
reject agents for going where they were told they could go. The consequence is deliberate and
matches `02 §5.1`: an agent cannot move to the exchange and trade in the same tick. That is
the "split the action into two ticks" rule, not a bug.

`LOCALITY_REQUIREMENTS: Mapping[ActionType, LocalityRule]` where
`LocalityRule{place_types: frozenset[str] | None, requires_colocated_target: bool, remote_ok: bool}`.
`None` place types means "anywhere". `remote_ok=True` covers `POST`, `DIRECT_MESSAGE`,
`SUBMIT_ORDER`-with-brokerage, etc.

**Reflex guard.** An action with `origin == "reflex"` whose type is outside `REFLEX_ALLOWED`
(`MOVE_TO, IDLE, SLEEP, EAT, WORK, STUDY, BUY_GOOD, SAY, REPAY_LOAN, NULL_ACTION`) is a
**bug in C07, not a rejection**. Raise `ReflexActionViolation` and let it propagate — `02 §10`
says HALT. If reflex could produce a job application or a trade, the "LLM society" claim is
hollow (threat T9) and no test would catch it if we merely rejected the action.

### 9.3 Legality — the rule this chunk exists to protect

```python
verdict = self.oracle.assess(action, params, ctx)
if verdict.is_crime:
    self.log.emit(2062, actor_id=action.actor_id, subject_ids=(verdict.victim_id or ...),
                  cause_seq=submitted_seq, payload={..., "proceeded": True})
return ValidatedAction(action, params, verdict, slot)      # ALWAYS proceeds
```

There is no branch in which `is_crime` returns a `Rejection`. `RejectReason` does not contain
a `"legality"` member, so the type system refuses to express it, and
`tests/invariants/test_crime_is_possible.py` asserts it behaviourally. Detection, arrest,
prosecution and punishment happen downstream in `polis/society/law.py` with a *probability*
(`04 §11`) — never by making the action impossible.

The oracle records the `crimes` row (C19), not C10. C10 must not write society tables.

### 9.4 Rejection, substitution, and visibility

```python
substitute = Action(action_id=r.action_id, tick=tick, actor_id=r.actor_id,
                    type=ActionType.NULL_ACTION,
                    params={"replaced_type": r.type.value, "reason": r.reason},
                    origin=original.origin,      # NOT "reflex" — provenance is preserved
                    salience=original.salience, reasoning=original.reasoning, sig=None)
```

- The slot is **already consumed** by the time gate 1 runs. A rejected action costs the slot
  (`02 §5` PHASE 4, `08 §4.3`). Otherwise an agent retries within a tick until something passes.
- `origin` is preserved so that reject rates are attributable per origin — a deliberate
  rejection rate of 30% is a prompt problem; a reflex rejection rate of 30% is a C07 bug; an
  external rejection rate of 30% is an SDK documentation problem. Conflating them hides all three.
- `NULL_ACTION` is dispatched to `InstitutionSlot.MISC`, which does nothing. It is **not**
  `IDLE`. `IDLE` is a chosen action and must be counted as such by the V4 action-entropy
  metric; conflating them inflates entropy with rejections.
- `RejectionLedger.last_action_outcome(actor, tick)` returns tick-1's outcome and is read by
  C07's PHASE 1 into `Observation`. Its shape is fixed by `08 §4.4` — do not diverge, or the
  external and native views of "what happened to my action" differ and T12 is violated.

### 9.5 Slot budget and T12 parity

`action_slots` is read from **one** config key by both the engine and the gateway
(`08 §15` item 4). Default 1 in `microscope`, 4 in `chronicle` (`02 §6.3`). `SlotLedger` is
keyed `(actor_id, tick)` — never by actor alone — and `reset(tick)` runs in PHASE 0.

There is exactly one `SlotLedger` instance and one `ActionValidator` instance for the run.
External actions arrive as ordinary `Action` objects with `origin == "external"` and
`sig != None`, and traverse the identical gate ladder in the identical order. The only
difference permitted anywhere is that `sig` is required; C22 verifies it before the action
reaches C10.

### 9.6 PHASE 5 dispatch

```python
SLOT_ORDER: Final[tuple[InstitutionSlot, ...]] = (
    MOVEMENT, COMMUNICATION, LABOUR, GOODS, EXCHANGE,
    BANKING, VENTURES, POLITY, LAW, MISC)          # 02 §5.1, a literal, never derived

for slot in SLOT_ORDER:
    resolver = registry.by_slot.get(slot)
    if resolver is None:                            # institution not in this run
        continue
    batch = stable([a for a in validated if a.action.type in resolver.handles],
                   key=lambda a: (a.action.actor_id, str(a.action.action_id)))
    events.extend(resolver.resolve(batch, tick, ctx))
```

- The order is a literal tuple. Deriving it from registration order, dict iteration, or
  `sorted(registry)` reintroduces exactly the nondeterminism `02 §5.1` exists to remove.
- Each resolver receives its batch pre-sorted by `(actor_id, action_id)`.
- Events are concatenated in slot order and returned to PHASE 6 unmodified. C10 does not
  reorder, dedupe, or filter resolver output.
- An empty batch still calls `resolve()` — some institutions need the per-tick hook. Document
  that resolvers must tolerate an empty sequence.

### 9.7 `legal_actions()` and the schema bundle

`legal_actions()` returns only types that (a) have a registered resolver, (b) pass capability
and locality for this agent-tick, and (c) are permitted by the agent's life stage
(`04 §12.2`: infants none, children a limited set, adolescents part-time labour, adults all).
Order is `ActionType` declaration order — a stable order matters because the list is rendered
into prompts and any reshuffling changes every cache key.

`export_action_schema_bundle()` writes `polis/events/schemas/actions.v1.json` into
`polis.events` — the gateway may import `polis.events` but **not** `polis.agents`
(`02 §7.1`), so the bundle must live there. A CI test regenerates and diffs it; drift means
the gateway accepts a shape the engine rejects, which surfaces as an unexplained external
rejection rate.

---

## 10. Configuration keys

```yaml
actions:
  slots_per_tick:                     # 02 §6.3; ONE key, read by engine and gateway
    microscope: 1
    chronicle: 4
  max_params_bytes: 4096              # matches the gateway's payload guard
  max_reasoning_chars: 2000           # 08 §4.3
  max_speech_chars: 1000
  legality:
    oracle: permissive                # permissive (M1) | law (C19)
  reject_on_unregistered: true        # false => raise instead, for M1 debugging
```

No `mechanisms:` entries. The five gates encode institutional structure, not behavioural
rules; the *contents* of `LOCALITY_REQUIREMENTS` are documented in the owning domain specs.

---

## 11. Acceptance criteria

1. `ActionType` has exactly 71 members and `set(PARAMS_MODELS) == set(ActionType)`, asserted
   at import time.
2. Every params model has `extra="forbid"` and `frozen=True`; an unexpected key is a schema
   rejection, not a silent pass.
3. No params field is typed `float` for a monetary quantity; every money field is
   `Cents = Annotated[int, Field(ge=0)]`.
4. The gates run in the order schema → capability → locality → resources → legality, and the
   reported `reason` is the **first** failure.
5. **A `COMMIT_CRIME` action whose oracle returns `is_crime=True` is returned as a
   `ValidatedAction`, not a `Rejection`, emits `2062` with `proceeded: true`, and is
   dispatched to the LAW slot.** `"legality"` is not a member of `RejectReason`.
6. A rejected action consumes its slot; `SlotLedger.remaining` decrements identically for a
   rejected and an applied action.
7. A second action in the same tick beyond `slots_per_tick` is rejected with `no_slots`.
8. `SlotLedger` is keyed by `(actor_id, tick)`; slots do not leak across ticks.
9. Native and external actions traverse the same validator instance and the same gate order;
   `tests/invariants/test_action_budget_parity.py` asserts both read the same config key.
10. Every rejection produces a `NULL_ACTION` substitute preserving `origin`, `salience` and
    `reasoning`, and emits exactly one `2061`.
11. `last_action_outcome(actor, tick)` returns tick-1's outcome, matches the `08 §4.4` shape,
    and is `None` at tick 0 and after `prune`.
12. An `origin == "reflex"` action outside `REFLEX_ALLOWED` raises `ReflexActionViolation`
    and is **not** rejected.
13. `legal_actions()` excludes types with no registered resolver, and its order equals
    `ActionType` declaration order.
14. `ActionDispatcher` walks slots 1–10 in the literal order regardless of registration
    order; a resolver registered last but holding slot 1 still resolves first.
15. Registering two resolvers that both claim an `ActionType` raises `DuplicateHandler`.
16. Each resolver receives its batch sorted by `(actor_id, action_id)`, and an empty batch
    still invokes `resolve()`.
17. `export_action_schema_bundle()` output matches the checked-in
    `polis/events/schemas/actions.v1.json` byte for byte.
18. `Action.reasoning` never affects control flow: a test runs 500 actions twice with
    `reasoning` replaced by random strings and asserts identical events.
19. Kinds 2060–2062 are registered with payload schemas; nothing is emitted outside 2060–2079.
20. `mypy --strict polis/agents/actions` and the `institutions-no-cognition` import-linter
    contract pass.

---

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/actions/test_enum_completeness.py` | 71 members; `PARAMS_MODELS` total; group counts match `02 §6.2` + 3 ratified; no member added without a model |
| `tests/unit/actions/test_params_models.py` | `extra="forbid"` rejects unknown keys; money fields are int; id prefix patterns; frozen |
| `tests/unit/actions/test_gate_order.py` | An action failing three gates reports the first; each gate's reason string; `unknown_type`/`no_slots`/`unavailable` pre-gates |
| `tests/invariants/test_crime_is_possible.py` | **Merge gate.** `COMMIT_CRIME`, insider `SUBMIT_ORDER`, and a fraudulent `PITCH` all validate, emit `2062`, and reach their resolvers. `"legality" not in get_args(RejectReason)` |
| `tests/unit/actions/test_slots.py` | Consumption on reject and on apply; `(actor, tick)` keying; `reset` in PHASE 0; microscope vs chronicle values |
| `tests/invariants/test_action_budget_parity.py` | Native and external read one config key; identical gate sequence for an identical action differing only in `origin`/`sig` |
| `tests/unit/actions/test_rejection_substitution.py` | `NULL_ACTION` shape; `origin` preserved; `2061` payload; `NULL_ACTION != IDLE` in the entropy counter |
| `tests/unit/actions/test_outcome_visibility.py` | tick-1 lookup; `08 §4.4` field set; `None` at tick 0; pruned after 2 ticks |
| `tests/unit/actions/test_reflex_guard.py` | `REFLEX_ALLOWED` is exactly the ten types of `04 §8`; an out-of-set reflex action raises, does not reject |
| `tests/unit/actions/test_locality_prestate.py` | Locality uses `ctx.observation`'s place, not live position; move-then-trade in one tick is rejected with `locality` |
| `tests/unit/actions/test_dispatch_order.py` | Literal slot order independent of registration order; per-slot batch sorted; empty batch still calls `resolve`; events concatenated unmodified |
| `tests/unit/actions/test_registry.py` | `DuplicateHandler`; `for_type` returns `None` for unregistered; `registered_types` |
| `tests/unit/actions/test_schema_bundle.py` | Regenerate and diff `actions.v1.json`; every type present with its JSON Schema |
| `tests/unit/actions/test_reasoning_not_parsed.py` | Identical events with randomised `reasoning`; AST scan finds no `.reasoning` outside payload construction |
| `tests/integration/test_action_pipeline.py` | 50 agents, 200 ticks, stub resolvers: every action either applies or produces exactly one `2061` + one `NULL_ACTION`; slot accounting closes every tick |
| `tests/determinism/test_action_determinism.py` | Same seed twice → identical 2060–2062 sequence and identical dispatch order |

---

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. `polis/agents/actions/` exports the §5 symbols with exactly those signatures. **Freeze
   `InstitutionResolver` at handback** — eleven chunks build against it.
2. Kinds 2060–2062 registered in `polis/events/kinds.py` with payload schemas.
3. `polis/events/schemas/actions.v1.json` generated and checked in, with a CI drift test.
4. The `institutions-no-cognition` import-linter contract added to `.importlinter`, together
   with a note in the handback that `polis.agents.actions` is permitted to institutions and
   `polis.agents.cognition`/`memory` are not.
5. A one-page `InstitutionResolver` implementation note for C11–C19 authors: what `ctx`
   contains, what ordering guarantees they get, and the rule that `resolve()` must be
   order-independent or explicitly price-time-priority.
6. Handback records the M1 `PermissiveLegalityOracle` default and the exact tick at which
   C19 must replace it.

---

## 14. Traps

1. **Making the legality gate reject.** It reads like the other four and it is not. A
   `Rejection` here silently deletes research question B5, the deterrence experiment, and the
   entire crime-detection pipeline, and every test still passes because no crime ever occurs.
   `RejectReason` omits `"legality"` on purpose; do not "fix" it.
2. **Pydantic's default `extra="ignore"`.** An LLM emits `{"vacancy_id": "...", "salary": 90000}`
   and the extra key vanishes. The action validates, the institution reads a field the agent
   never set, and the behaviour is inexplicable. `extra="forbid"` on every model.
3. **A money field typed `float`.** `02 §4.6` forbids it, `INV-MONEY` will eventually catch
   it, and by then it is 40M ledger rows deep. `Cents` everywhere, always `ge=0`.
4. **`SlotLedger` keyed by `actor_id` alone.** Slots leak across ticks; agents get one action
   for the whole run or unlimited actions, depending on where you forgot the reset.
5. **A rejected action that does not consume its slot.** C09 or an external SDK will retry
   inside the same tick. The loop is not infinite — it is worse: it is a *biased* loop that
   gives persistent agents more attempts than others.
6. **Accumulating all gate failures.** `reason` becomes a set, `sys.action.reject_rate.<reason>`
   sums to more than 1.0, and nobody notices for a month.
7. **Validating locality against live position.** Movement resolves in slot 1; if locality
   reads post-movement state, an action that was legal when offered becomes illegal when
   validated, or vice versa, and the reject rate becomes a function of pathfinding.
8. **Deriving `SLOT_ORDER` from the registry.** Dict ordering, registration order, or
   `sorted(resolvers, key=type.__name__)` all look fine on 3 resolvers and produce a different
   economy on 10. It is a literal tuple in one file.
9. **Two resolvers claiming the same `ActionType`.** Last-write-wins is silent. `LIKE` handled
   by both comms and media means half the likes go to the wrong ledger. Assert disjointness at
   registration.
10. **Branching on `reasoning`.** Even `if "urgent" in action.reasoning`. It destroys the
    determinism boundary that lets institutions stay mechanical while cognition stays
    open-ended (`02 §6.1`), and it is the exact failure mode of Smallville's free-form
    adjudication that this design rejects. Grep for it in CI.
11. **`NULL_ACTION` counted as `IDLE`.** V4's action-type entropy floor is an invariant
    (`02 §9`, `INV-ENTROPY`). Rejections rendered as deliberate idling inflate entropy and mask
    mode collapse — the exact thing V4 exists to detect.
12. **The schema bundle drifting from the enum.** The gateway pre-validates against
    `actions.v1.json`; the engine validates against the pydantic models. Drift produces an
    external agent whose action is accepted by the gateway and rejected by the engine, which
    reads to the operator as "the world is broken" and to us as a T12 fairness violation.
13. **Giving external agents a different slot count, gate order, or rejection vocabulary.**
    T12 is the whole reason the external protocol is credible. One validator, one ledger, one
    config key. A "just for the gateway" branch is a research finding about our engineering.
14. **Adding a 72nd `ActionType` because an institution needs one.** The enum is closed
    (`README §0`). Three additions were ratified through a spec amendment; the fourth needs
    the same. Stop and raise it.
15. **`ValidatedAction` discarding the parsed model.** If institutions re-parse
    `action.params` themselves, eleven chunks implement eleven slightly different coercions.
    Hand them `validated_params` and make the raw mapping the exception.
16. **Assuming every institution exists.** At M1 only movement, communication, education and
    part of world are registered. `legal_actions()` must filter on registration or agents are
    offered `SUBMIT_ORDER` two milestones early and the reject rate is 60%.
17. **Reordering or filtering resolver output events.** The resolver decided the order, and
    `cause_seq` chains depend on it. Concatenate, do not curate.
18. **Rejecting a reflex action instead of raising.** A reflex policy that emits `APPLY_FOR_JOB`
    is a C07 bug that must halt the run. Rejecting it politely means the bug ships, the reflex
    action set silently widens, and the "everything with a counterparty is LLM-only" claim in
    `04 §8` is quietly false.
