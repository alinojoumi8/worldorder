# C19 — Crime, detection, police, courts, judgments, incarceration

**M4** · `polis/society/law.py` · **Depends on:** C02, C03, C04, C05, C06, C08, C10, C16, C17, C18, C11/C13/C14 (`economy.ledger`, exchange, banking) · **Blocks:** C20, C24b, C25 · **Size:** L

## 1. Context

This chunk makes crime possible, detectable, prosecutable and punishable — in that order, and
never by making an action impossible. C10's legality gate flags and proceeds; C19 supplies the
`LegalityOracle` that does the flagging and everything downstream of it: a detection hazard
that runs for months after commission, an investigation queue bounded by the police budget,
courts bounded by the court budget, lawyers who charge real fees, evidence drawn from the event
log under an admissibility rule, and a judge who is an agent with beliefs and ties, deciding
through a `JUDGE` call constrained to a closed verdict/penalty schema. **Five of the seven
offence types are derived from ordinary actions** rather than requiring an agent to select an
action named `COMMIT_CRIME` — that is the structural reason research question B5 survives a
safety-trained model that refuses the label but will happily default on a loan it could pay.
Deterrence is the object of study, so the *committed* crime rate, not the detected one, is the
quantity every result is stated over.

## 2. Required reading

| Source | Sections |
|---|---|
| `../docs/07-SOCIETY-SPEC.md` | **§8 in full (primary source)**, §0.2 resolution order, §0.3 ledger table, §0.5 D-3 (`prison`), §0.6 RNG, §10.7–10.9, §11 F2/F9, §12 cadences |
| `../docs/04-AGENT-SPEC.md` | **§11 (the five gates, legality never rejects)**, §5 perception, §12.2 stages |
| `../docs/02-ARCHITECTURE.md` | §3.2 kinds, §4 determinism, §5 PHASE 4/5 slot 9/PHASE 7, §7.1, §8 routing (`JUDGE`), §8.1 MECHANISM, §10 LLM failure |
| `../docs/03-DATA-MODEL.md` | §8 `crimes`/`court_cases`, §3.1 `places.type`, §4 ledger, §12 rebuild |
| Chunks | **C10 (`LegalityOracle`, `LegalityVerdict`, `InstitutionResolver`, `CommitCrimeParams`)**, C17 (`ClaimChecker`, `Claim`, `MemoryLookup`), C18 (`RuntimeOverlay`, `OfficeRegister`, `12034 BUDGET_SET`), C16 (`SocialGraph`), C05 (`Purpose.JUDGE`), C06 (`World.places_of_type`) |

## 3. Scope — in

1. **`LawLegalityOracle`** — C10's `LegalityOracle` (PHASE 4). The explicit path and the five derived predicates. Writes the `crimes` row, emits `13001` + `13010`. **Never returns a rejection.**
2. `LawResolver` — the `InstitutionResolver` registered in `InstitutionSlot.LAW` (slot 9): `COMMIT_CRIME`, `REPORT_CRIME`, `FILE_SUIT`, `RETAIN_COUNSEL`, `TESTIFY`, `SETTLE`, `RULE`.
3. `MnpiIndex` — the deterministic material-non-public-information test of `07 §8.3`.
4. `DetectionEngine` — the per-tick hazard over a 180-sim-day window, scaled by police budget and district share.
5. `PoliceService` — budget allocation by the `police_chief`, the investigation queue, evidence strength, charging and arrest.
6. `CourtService` — filing, counsel retention and public defenders, admissibility, testimony, the `JUDGE` call with its constraint clamps and the bench-rule fallback, sentencing, and civil suits and settlements.
7. `PenaltyService` — fines, damages, restitution, loser-pays, garnishment; every leg through `post_transaction`.
8. `Incarceration` — the `prison` place type, action-set restriction, employment termination, obligations continuing to accrue, doubled skill and tie decay, franchise, release.
9. Kinds 13000–13999 registered in `polis/events/kinds.py`.
10. `@mechanism` declarations for `crime_detection`, `bench_rule`, `ex_offender_wage_penalty`.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| The five-gate framework, `RejectReason`, `ValidatedAction` | **C10** |
| The claim checker itself — you *call* it for fraud and perjury | **C17** |
| `police.budget_cents`, `courts.budget_cents`, `prison.capacity`, `sentencing.multiplier` values | **C18** (you read them through `runtime.get`) |
| The `police_chief`/`judge` appointments and the franchise policy flag | **C18** |
| Wage offers (you supply the ex-offender multiplier, C11 applies it) | **C11** |
| Order cancellation and share release on incarceration/death | **C13**, **C20** |
| `places.type = 'prison'` generation | **C06** (you consume `places_of_type('prison')`) |
| Bankruptcy, `9030` | **C15** |

## 5. Interfaces you provide

```python
# polis/society/law.py
from polis.agents.actions import (Action, ActionParams, ActionType, GateResult, InstitutionSlot,
                                  LegalityVerdict, ResolutionContext, ValidatedAction,
                                  ValidationContext)

CrimeType = Literal["theft","fraud","insider_trading","assault",
                    "contract_breach","embezzlement","perjury"]
Path      = Literal["explicit", "derived"]

class LawLegalityOracle:
    """C10's LegalityOracle (C10 §5). Runs in PHASE 4 as gate 5. IT NEVER REJECTS.
    There is no code path in this class that returns a Rejection or raises to reject."""
    def __init__(self, *, log: EventLog, clock: Clock, runtime: RuntimeOverlay,
                 mnpi: "MnpiIndex", obligations: "ObligationIndex", checker: ClaimChecker,
                 memories: MemoryLookup, repo: "CrimeRepository",
                 cfg: LawSettings) -> None: ...
    def assess(self, action: Action, params: ActionParams,
               ctx: ValidationContext) -> LegalityVerdict:
        """Explicit path for COMMIT_CRIME; five derived predicates otherwise.
        On is_crime: insert `crimes` (detected=false), emit 13001 then 13010, and return a
        verdict carrying crime_id. Returns LegalityVerdict(is_crime=False) otherwise."""

class DerivedPredicate(Protocol):
    crime_type: CrimeType
    applies_to: frozenset[ActionType]
    def test(self, action: Action, params: ActionParams,
             ctx: ValidationContext) -> LegalityVerdict | None:
        """Deterministic. No LLM, no RNG, no judgement call. None == not an offence."""

DERIVED_PREDICATES: Final[tuple[DerivedPredicate, ...]]   # fraud, insider_trading,
                                                          # embezzlement, contract_breach, perjury

class LawResolver:
    """THE InstitutionResolver for InstitutionSlot.LAW. Resolves 9th, last of the
    institutions, so a crime flagged in PHASE 4 has all its tick-t consequences settled
    before detection is drawn."""
    slot:    Final[InstitutionSlot] = InstitutionSlot.LAW
    handles: Final[frozenset[ActionType]] = frozenset({
        ActionType.COMMIT_CRIME, ActionType.REPORT_CRIME, ActionType.FILE_SUIT,
        ActionType.RETAIN_COUNSEL, ActionType.TESTIFY, ActionType.SETTLE, ActionType.RULE})

    def __init__(self, *, log: EventLog, clock: Clock, rng: RngRegistry, world: World,
                 police: "PoliceService", courts: "CourtService", penalties: "PenaltyService",
                 graph: SocialGraph, beliefs: BeliefEngine, offices: OfficeRegister,
                 runtime: RuntimeOverlay, ledger: LedgerApi, cfg: LawSettings) -> None: ...

    def check_capability(self, action: Action, ctx: ValidationContext) -> GateResult:
        """RETAIN_COUNSEL: counsel has agent_skills.law >= 0.5, alive, not incarcerated,
        not a party. FILE_SUIT criminal: actor is the prosecutor (police_chief's office).
        RULE: actor holds the `judge` office. TESTIFY: actor is a listed witness.
        REPORT_CRIME: actor holds a memory of the crime (MemoryLookup)."""
    def check_locality(self, action: Action, ctx: ValidationContext)  -> GateResult:
        """COMMIT_CRIME theft/assault require co-location with the target (from
        ctx.observation, NOT live position). TESTIFY/RULE require the courthouse.
        Everything else is remote_ok."""
    def check_resources(self, action: Action, ctx: ValidationContext) -> GateResult:
        """Filing fee, counsel fee, settlement amount. NOTE: an agent who cannot afford to
        steal is not a thing; COMMIT_CRIME{theft} has no funds requirement."""
    def resolve(self, actions: Sequence[ValidatedAction], tick: int,
                ctx: ResolutionContext) -> Sequence[Event]: ...
    def options_for(self, action_type: ActionType,
                    ctx: ValidationContext) -> tuple[Mapping[str, Any], ...]:
        """Open cases for TESTIFY/SETTLE; available lawyers with fees for RETAIN_COUNSEL;
        crimes the actor remembers for REPORT_CRIME. () for COMMIT_CRIME."""

@dataclass(frozen=True, slots=True)
class Crime:
    crime_id: str; type: CrimeType; tick: int
    perpetrator_id: str; victim_id: str | None; amount_cents: int | None
    place_id: str | None; district_id: str | None; source_action_id: str
    concealment: float; path: Path
    detected: bool; detected_tick: int | None; reported_by: str | None

class MnpiIndex:
    MNPI_KINDS: Final[frozenset[int]]     # 9010, 9030, earnings, 6xxx shocks, mass 5011, M&A
    def holds(self, agent_id: str, symbol: str, tick: int) -> tuple[bool, int | None]:
        """07 §8.3, exactly: a memory with source_event_seq = e.seq, e.subject_ids ∋ issuer(S),
        e.kind ∈ MNPI_KINDS, NO public disclosure of e at t, and t - e.tick <= mnpi_window.
        Returns (holds, source_event_seq). Pure, replayable, no LLM."""
    def publicly_disclosed(self, event_seq: int, tick: int) -> bool:
        """Any 11010/11030 citing the seq, or any PUBLIC_KINDS event carrying it."""

class ObligationIndex:
    """Logged obligations whose non-performance is `contract_breach`."""
    def due(self, agent_id: str, tick: int) -> tuple["Obligation", ...]: ...
    def missed_with_capacity(self, agent_id: str, tick: int) -> tuple["Obligation", ...]:
        """Non-performance WHILE HOLDING SUFFICIENT FUNDS. Insolvency is not a crime."""

class DetectionEngine:
    @mechanism("crime_detection", entails="...")            # 07 §8.4 verbatim
    def p_detect(self, crime: Crime, tick: int) -> float: ...
    def run_hazard(self, tick: int) -> Sequence[Event]:
        """PHASE 7 daily over crimes live in the detection_window. Draw per crime with
        rng.get('law.detect', crime_id, tick) against p_detect / window_ticks. Emits 13011."""
    def concealment(self, agent_id: str) -> float: ...

class PoliceService:
    def allocate_budget(self, chief_id: str | None, tick: int) -> Event:     # 13050
        """Chief's district shares (one allocation per sim-month). No chief => uniform."""
    def investigation_slots(self, tick: int) -> int:
        """floor(runtime.get('police.budget_cents', tick) / cost_per_investigation_cents)"""
    def process_queue(self, tick: int) -> Sequence[Event]:
        """PHASE 7 daily. Severity-ordered, ties by crime_id. 13013/13014/13015."""
    def evidence(self, crime: Crime, tick: int) -> tuple[tuple[int, ...], float]:
        """(admissible seqs, evidence_strength in [0,1]). Directness: ledger 1.0,
        testimony 0.6, co-location 0.3, inference 0.1; corroboration 1 + 0.2·n."""
    def report(self, reporter_id: str, params: ReportCrimeParams, tick: int
               ) -> Sequence[Event]: ...                                     # 13012

@dataclass(frozen=True, slots=True)
class Judgment:
    verdict: Literal["guilty","not_guilty","liable","not_liable","dismissed"]
    findings: tuple[str, ...]
    fine_cents: int; sentence_ticks: int; damages_cents: int
    restitution_cents: int; disqualification_ticks: int
    clamped: tuple[str, ...]; origin: Literal["llm","bench"]; llm_call_id: str | None

class CourtService:
    def cases_per_session(self, tick: int) -> int:
        """floor(runtime.get('courts.budget_cents', tick) / cost_per_case_cents)"""
    def file(self, filer_id: str, params: FileSuitParams, tick: int
             ) -> Sequence[Event]: ...                                       # 13020 + fee txn
    def retain(self, client_id: str, params: RetainCounselParams, tick: int
               ) -> Sequence[Event]: ...                                     # 13021 + fee txn
    def assign_public_defender(self, case_id: str, defendant_id: str, tick: int
                               ) -> Sequence[Event] | None:
        """Below legal_aid_wealth_pct: cheapest available lawyer, government pays from
        courts.budget_cents (`purchase`)."""
    def admit_evidence(self, case_id: str, tick: int) -> tuple[tuple[int, ...], Event]:
        """07 §8.7 admissibility, then n_surfaced = round(3 + 8 * counsel.skill_law).
        Emits 13022 with admitted AND excluded counts."""
    def testify(self, witness_id: str, params: TestifyParams, tick: int
                ) -> Sequence[Event]:
        """Runs claims through C17's ClaimChecker. A `contradicted` claim on a matter the
        witness had first-hand memory of flags perjury AT THE LEGALITY GATE next tick."""
    async def hold_session(self, tick: int) -> Sequence[Event]:
        """PHASE 7 at polity.court_session. 13031, the JUDGE call, constraint clamps,
        13040, then PenaltyService. Cases in stable() order by case_id."""
    def bench_rule(self, evidence_strength: float, case_type: str,
                   statutory: "Range") -> Judgment: ...
    def settle(self, case_id: str, params: SettleParams, tick: int
               ) -> Sequence[Event]: ...                                     # 13030

@mechanism("bench_rule", entails="...")                     # 07 §8.8 verbatim
def bench_verdict(evidence_strength: float, threshold: float, lo: int, hi: int
                  ) -> tuple[bool, int]: ...

class PenaltyService:
    def apply(self, case_id: str, j: Judgment, tick: int) -> Sequence[Event]:
        """Fine -> government (`fine`); damages/restitution -> plaintiff/victim (`transfer`);
        loser-pays if runtime.get('courts.loser_pays', tick). Insufficient funds becomes a
        receivable and garnishes future income at garnishment_rate. NEVER written off."""
    def garnish(self, agent_id: str, income_cents: int, tick: int) -> int:
        """Returns cents diverted. Called by C11's payroll through a thin protocol."""
    def outstanding(self, agent_id: str) -> int: ...

class Incarceration:
    ALLOWED_ACTIONS: Final[frozenset[ActionType]] = frozenset({
        ActionType.IDLE, ActionType.SLEEP, ActionType.EAT,
        ActionType.SAY, ActionType.STUDY, ActionType.NULL_ACTION})
    def commit(self, agent_id: str, case_id: str, sentence_ticks: int, tick: int
               ) -> Sequence[Event]:
        """13043. Over prison.capacity => convert to a fine at fine_per_tick_cents and log
        the conversion. Moves the agent to a `prison` place, terminates employment
        (5011 reason=incarceration), doubles skill and tie decay."""
    def is_incarcerated(self, agent_id: str, tick: int) -> bool: ...
    def release_due(self, tick: int) -> Sequence[Event]: ...                 # 13044
    @mechanism("ex_offender_wage_penalty", entails="...")   # 07 §8.9 verbatim
    def wage_multiplier(self, agent_id: str) -> float:
        """(1 - penalty * criminal_record), floored. Consumed by C11's offer construction."""

@dataclass(frozen=True, slots=True)
class Range:
    fine_lo: int; fine_hi: int; sentence_lo_ticks: int; sentence_hi_ticks: int

STATUTORY: Final[Mapping[CrimeType, Range]]     # 07 §8.1, before sentencing.multiplier
def statutory_range(t: CrimeType, amount_cents: int | None, tick: int,
                    runtime: RuntimeOverlay) -> Range:
    """Applies the amount multipliers of 07 §8.1 then runtime.get('sentencing.multiplier')."""
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C10 | `LegalityOracle` protocol, `LegalityVerdict`, `InstitutionResolver`, `CommitCrimeParams`, `ValidationContext` | gate 5 and slot 9 |
| C17 | `ClaimChecker.check`, `Claim`, `CheckResult`, `MemoryLookup` | fraud and perjury derivation; testimony consistency |
| C18 | `RuntimeOverlay.get`, `OfficeRegister.holder/holds_office`, `12034 BUDGET_SET` | budgets, judges, prosecutor, `felon_franchise`, `sentencing.multiplier` |
| C16 | `SocialGraph.stage_interaction/end_all_for`, `Platform` | victim/adversary/testimony tie effects, public disclosure check |
| C05 | `LLMRouter.call`, `Purpose.JUDGE` | the judgment, temperature 0.2 |
| C06 | `World.places_of_type("prison")`, `place_view`, co-location | custody, arrest location |
| C13 | order cancellation, realised profit on a flagged trade | insider-trading `amount_cents` |
| C14 | loan schedules, `loan_payments.missed`, capacity to pay | `contract_breach` |
| C11 | `Ledger.transfer(src, dst, cents, reason)`, `Ledger.post_transaction(legs, *, tick, cause: Event)`, `Leg(account_id, direction, amount_cents, reason)`, `account_id(code, owner, …)`, `money.bp`, `money.allocate` | fines, fees, damages, garnishment |

**Overlay accessors.** C11's `RuntimeOverlay` exposes `bp(key, tick) -> int`,
`cents(key, tick) -> int`, `flag(key, tick) -> bool`, `brackets(...)` and `as_of(tick)` —
there is no untyped `get`. Where this brief writes `runtime.get('x', t)`, use the accessor
matching the key's type (`police.budget_cents` → `cents`, `sentencing.multiplier_bp` → `bp`,
`courts.loser_pays` → `flag`). Settle the exact key strings with C18.

> **Coordination item 1 — the oracle must exist before it is needed.** C10 ships
> `PermissiveLegalityOracle` as the M1 default. C19's handback must record the exact tick/commit
> at which the composition root swaps in `LawLegalityOracle`, and a test must assert the
> permissive oracle is not wired in any M4+ config.
>
> **Coordination item 2 — insider-trading profit.** `amount_cents` for `insider_trading` is
> *profit realised*, which is not known at PHASE 4. Flag with `amount_cents = None` at
> commission and backfill on realisation; the `crimes` row is updated, not re-inserted. Agree
> the backfill hook with C13.
>
> **Coordination item 3 — garnishment.** `PenaltyService.garnish` must be called from C11's
> payroll. Define the protocol here and have C11 depend on it, so that an unpaid fine is never
> silently written off (`INV-MONEY`).
>
> **Coordination item 4 — `prison` place type.** Ratified addition. Confirm C06 generates at
> least one and that district crime-rate metrics exclude custodial occupancy.

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `crimes` | **W** | the only writer. `detected` starts `false`; `amount_cents` may be backfilled |
| `court_cases` | **W** | `evidence_event_seqs`, counsel, judge, verdict, penalty, sentence |
| `agents` | **W (one column)** | `criminal_record += 1` on conviction. Everything else is C07's |
| `events` | R | evidence, MNPI, admissibility, `PUBLIC_KINDS` |
| `memories` | R | **only through `MemoryLookup`** — society may not import `polis.agents.memory` |
| `places` | R | `type = 'prison'`, capacity, courthouse |
| `employments`, `loans`, `loan_payments`, `orders`, `trades`, `holdings` | R | derived predicates and evidence |
| `beliefs`, `relationships` | R/W via C17/C16 | victimisation trust effects, tie damage |
| `ledger_*` | never directly | `post_transaction` only |

Register `Projection`s for `crimes` and `court_cases`, plus a narrow handler for
`agents.criminal_record` coordinated with C07's agents projection (disjoint `tables` is asserted
by `register_projection`, so this must be a column-level agreement, not a second projection).

## 8. Event kinds owned

**Range: 13000–13999**, owner `polis.society.law`.

| Kind | Name | Payload |
|---|---|---|
| 13001 | `LEGALITY_FLAGGED` | `action_id, actor_id, action_type, offence_type, path, predicate_id, crime_id` |
| 13010 | `CRIME_COMMITTED` | `crime_id, type, perpetrator_id, victim_id, amount_cents, place_id, district_id, source_action_id, concealment, detected` |
| 13011 | `CRIME_DETECTED` | `crime_id, detector, p_detect, ticks_since_commission` |
| 13012 | `CRIME_REPORTED` | `crime_id, reporter_id, latency_ticks, evidence_event_seqs[]` |
| 13013 | `INVESTIGATION_OPENED` | `case_file_id, crime_id, officer_id, severity, queue_position` |
| 13014 | `INVESTIGATION_CLOSED` | `case_file_id, outcome, evidence_strength, evidence_event_seqs[]` |
| 13015 | `ARREST_MADE` | `crime_id, suspect_id, officer_id, place_id, evidence_strength` |
| 13020 | `SUIT_FILED` | `case_id, type, plaintiff_id, defendant_id, crime_id, cause_of_action, claim_cents, filing_fee_cents, txn_id` |
| 13021 | `COUNSEL_RETAINED` | `case_id, party_id, counsel_id, fee_cents, counsel_skill_law, public_defender, txn_id` |
| 13022 | `EVIDENCE_ADMITTED` | `case_id, admitted_seqs[], excluded_seqs[], excluded_reasons[], evidence_strength, surfaced_by_counsel` |
| 13023 | `TESTIMONY_GIVEN` | `case_id, witness_id, statement, claims[], consistency_score, perjury_flagged` |
| 13030 | `CASE_SETTLED` | `case_id, amount_cents, offered_by, txn_id` |
| 13031 | `TRIAL_HELD` | `case_id, judge_id, session_tick, plaintiff_counsel_id, defence_counsel_id, evidence_strength` |
| 13040 | `JUDGMENT_RENDERED` | `case_id, judge_id, verdict, findings[], fine_cents, sentence_ticks, damages_cents, restitution_cents, disqualification_ticks, clamped[], origin, llm_call_id` |
| 13041 | `FINE_LEVIED` | `case_id, payer_id, amount_cents, txn_id, garnished, shortfall_cents` |
| 13042 | `DAMAGES_AWARDED` | `case_id, from_id, to_id, amount_cents, txn_id` |
| 13043 | `INCARCERATION_STARTED` | `agent_id, case_id, ticks, place_id, converted_to_fine, capacity_at_sentencing` |
| 13044 | `INCARCERATION_ENDED` | `agent_id, ticks_served, skill_delta, ties_lost, returns_to_household_id` |
| 13050 | `POLICE_BUDGET_ALLOCATED` | `total_cents, chief_id, district_shares{}, patrol_units, audit_units, investigation_slots` |

`13010` is the denominator of every B5 result. It is emitted for **every** flagged crime,
detected or not, and it must never be suppressed, sampled, or emitted conditionally on
detection.

## 9. Implementation notes

### 9.1 The oracle flags; it does not block

```python
def assess(self, action, params, ctx) -> LegalityVerdict:
    for pred in DERIVED_PREDICATES:                    # fixed tuple order, never a set
        if action.type in pred.applies_to:
            v = pred.test(action, params, ctx)
            if v is not None:
                return self._record(v, action, ctx)    # crimes row + 13001 + 13010
    if action.type is ActionType.COMMIT_CRIME:
        return self._record(explicit_verdict(params), action, ctx)
    return LegalityVerdict(is_crime=False)
```

There is no branch here that returns anything C10 could turn into a `Rejection`, and
`RejectReason` has no `"legality"` member so the type system refuses to express one. If you
ever feel the need to "prevent" a crime, you have deleted B5, the deterrence sweep, and the
entire detection pipeline — and every test will still pass because no crime ever occurs.

Ordering matters: derived predicates are evaluated before the explicit path so that a
`COMMIT_CRIME{fraud}` that also satisfies the fraud predicate produces one crime, not two.
`DERIVED_PREDICATES` is a **tuple**, iterated in declaration order, because the first match wins.

### 9.2 The five derived predicates, deterministically

| Type | Predicate | Notes |
|---|---|---|
| `fraud` | a claim in a `PITCH`, loan application, `SET_PRICE` or listing that C17's checker scores `contradicted`, **and** a counterparty relied on it | reliance = a resulting `9010`/`8010`/`6020` in the same or the following tick with the claimant as counterparty |
| `insider_trading` | `SUBMIT_ORDER`/`SHORT` in `S` while `MnpiIndex.holds(i, S, t)` **and** `runtime.get("regulation.finance.insider_trading_enforced", t)` | the society can legalise it, and the effect is directly measurable |
| `embezzlement` | a transfer from a firm account by an agent with firm authority to an account they control, outside payroll and dividends | detected structurally from the ledger legs, not from intent |
| `contract_breach` | non-performance of a logged obligation **while holding sufficient funds** | insolvency is not a crime. `ObligationIndex.missed_with_capacity` is the whole test |
| `perjury` | `TESTIFY` whose claims the checker scores `contradicted` on a matter the witness had **first-hand memory of** | `MemoryLookup.holds_memory_of` is required; a witness repeating a false rumour is wrong, not a perjurer |

**No LLM, no RNG, no judgement call in any of them.** They must be replayable from the log
alone, or `polis rebuild` diverges and every crime statistic becomes non-reproducible.

Because these five require no criminal intent in text, `crime_action_refusal_rate` and the
`13001` split by `path` are both reported every run: `explicit ≈ 0` with healthy `derived` is a
finding about model refusal behaviour (F2), not a broken simulation.

### 9.3 Detection is a hazard over months, not a draw at commission

```
capacity_d(t)  = runtime.get("police.budget_cents", t) * district_share_d
                 / (population_d * cost_per_patrol_cents)
p_detect(c, t) = clip(base[type] * capacity_d^0.6 * (1 + witness_bonus)
                      * victim_awareness[type] * (1 - concealment(perp)), 0, 0.98)
detected iff rng.get("law.detect", crime_id, tick).random() < p_detect / window_ticks
```

The lag is the point: a fraud discovered eighteen sim-months later is how bubbles end. Run the
hazard daily in PHASE 7 over every crime still inside `detection_window`, in `stable()` order
by `crime_id`. `victim_awareness` differs by an order of magnitude across types
(theft 0.95, insider 0.05) and that asymmetry is what produces displacement rather than
deterrence when the budget rises.

The declared MECHANISM says plainly: *more police means more crimes detected* is definitional
and is **not** a finding. Every B5 result is stated over `13010` counts.

### 9.4 The judgment: a closed schema and hard clamps

Prompt: admitted evidence as a structured record, the charge, the statutory range **already
multiplied by `sentencing.multiplier`**, counsel submissions, and the judge's own retrieved
memories. Output is the closed schema of `07 §8.8`. After parsing, before effect:

| Constraint | Action |
|---|---|
| `verdict` in the enum and matching the case type | router repair ×2, then bench rule |
| `fine_cents`, `sentence_ticks` outside the statutory range | **clamp**, append to `13040.clamped[]` |
| `damages_cents > claim_cents` | clamp |
| `restitution_cents > crime.amount_cents` | clamp |
| zero penalty on guilty/liable | legal; record as `nominal` |
| a finding citing a non-admitted seq | drop the finding, count it |

`bench_rule` fires only on LLM failure and is a monotone function of `evidence_strength` alone.
`court.bench_share > 0.3` means the `JUDGE` call is failing and the bench rule is deciding the
docket — a run-invalidating condition for any judicial-bias claim, so report the share always.

Judges are agents with beliefs and ties, which is what makes bias *measurable*: verdict against
defendant wealth percentile, shared party membership, `relationships.strength(judge, party)`,
and defendant district are all queries over `13040`, not studies.

### 9.5 Counsel, evidence, and the money

`n_surfaced = round(3 + 8 * counsel.skill_law)` — counsel quality determines how much of the
admissible record reaches the judge, which is the mechanism behind `court.counsel_gap`.
Lawyers set their own fees and can price themselves out of the market; public defenders are
assigned below `legal_aid_wealth_pct` and paid by government out of `courts.budget_cents`.

Every cent moves through `post_transaction`:

| Flow | Debit | Credit | reason |
|---|---|---|---|
| Filing fee | filer cash | government cash | `transfer` |
| Counsel fee | client cash | lawyer cash | `purchase` |
| Public defender | government cash | lawyer cash | `purchase` |
| Fine / forfeiture | convict cash | government cash | `fine` |
| Damages / restitution / settlement | defendant cash | plaintiff/victim cash | `transfer` |
| Loser-pays costs | loser cash | winner cash | `transfer` |
| Theft (at commission) | victim cash | perpetrator cash | `transfer` |
| Embezzlement (at commission) | firm cash | officer cash | `transfer` |

**Insufficient funds never becomes a write-off here.** The shortfall is a government (or
plaintiff) receivable and future income is garnished at `garnishment_rate` (0.20) until
satisfied. `INV-MONEY` must hold through a fine larger than the convict's wealth — that is the
case that breaks naive implementations.

### 9.6 Incarceration

Move the agent to a `prison` place. Over `runtime.get("prison.capacity", t)` the sentence
converts to a fine at `fine_per_tick_cents` and the conversion is logged in `13043`. While
incarcerated: action set restricted to six types (C10's `legal_actions` must see it — expose
`Incarceration.is_incarcerated` through the `ValidationContext`), employment terminated with
`5011{reason: incarceration}`, **obligations continue to accrue** (rent, loans, child costs —
the debt consequences of custody are one of the more interesting things this layer produces),
skills and ties decay at 2×, franchise removed unless `polity.felon_franchise`,
`criminal_record += 1`. On release, `13044` and a return to the household home or a state
household.

C09 already treats the incarcerated as ineligible for cognition; confirm that and the
restricted action set do not double-count.

### 9.7 Deterrence requires that agents can learn the regime — and nothing more

Agents learn enforcement from the news (arrests and judgments are `PUBLIC_KINDS`), from
memories of victimisation, and from their social graph. **No `Observation` ever contains
`p_detect`, `concealment`, `evidence_strength`, or any crime statistic.** If deterrence
appears, it appears because agents inferred the regime from observable enforcement. Anything
else makes B5 circular.

## 10. Configuration keys

```yaml
law:
  detection_window_sim_days: 180
  mnpi_window_sim_days: 14
  base_detect: {theft: 0.35, assault: 0.45, fraud: 0.12, insider_trading: 0.06,
                embezzlement: 0.10, contract_breach: 0.30, perjury: 0.20}
  victim_awareness: {theft: 0.95, assault: 0.95, fraud: 0.30, insider_trading: 0.05,
                     embezzlement: 0.15, contract_breach: 0.85, perjury: 0.40}
  capacity_exponent: 0.6
  witness_bonus_per_witness: 0.4
  witness_bonus_cap: 1.2
  cost_per_patrol_cents: 20000
  cost_per_investigation_cents: 150000
  cost_per_case_cents: 400000
  charge_threshold: 0.45
  conviction_threshold: 0.60          # bench rule only
  evidence_window_sim_days: 30
  strength_norm: 6.0
  counsel_base_evidence: 3
  counsel_skill_factor: 8
  legal_aid_wealth_pct: 0.25
  min_counsel_skill_law: 0.5
  filing_fee_cents: 50000
  filing_fee_waiver_pct: 0.25
  garnishment_rate: 0.20
  fine_per_tick_cents: 8000           # custody -> fine conversion when over capacity
  ex_offender_wage_penalty: 0.08      # per criminal_record point
  ex_offender_penalty_floor: 0.6
  incarceration_decay_multiplier: 2.0
  civil_causes: [contract_breach, negligence, fraud, defamation, wrongful_dismissal]

mechanisms:
  crime_detection: budget_scaled
  bench_rule: evidence_threshold
  ex_offender_wage_penalty: "on"

ablations:
  no_record_penalty: false
```

Note that `police.budget_cents`, `courts.budget_cents`, `prison.capacity`,
`sentencing.multiplier`, `courts.loser_pays`, `regulation.finance.insider_trading_enforced`
and `polity.felon_franchise` are **not** in this block — they are policy parameters owned by
C18 and read through `runtime.get`.

## 11. Acceptance criteria

- [ ] **`LawLegalityOracle.assess` has no code path that returns a rejection or raises to prevent an action.** A `COMMIT_CRIME` with `is_crime=True` is returned as a `ValidatedAction` and reaches the LAW slot.
- [ ] `13010` is emitted for every flagged crime regardless of detection, and is never sampled.
- [ ] All five derived predicates fire without any `COMMIT_CRIME` action being selected: a fraudulent `PITCH`, an MNPI `SUBMIT_ORDER`, a firm-to-self transfer, a missed payment with funds, and a contradicted `TESTIFY` each produce a `crimes` row.
- [ ] Every derived predicate is pure: no LLM call, no RNG draw, replayable from the log.
- [ ] `contract_breach` does **not** fire when the agent lacks the funds to perform.
- [ ] `perjury` does **not** fire when the witness had no first-hand memory of the matter.
- [ ] `insider_trading` stops firing when `regulation.finance.insider_trading_enforced` is enacted `false`, at `effective_tick`, read through `runtime.get`.
- [ ] `p_detect` rises monotonically with `police.budget_cents` and the hazard runs daily over the whole `detection_window`, not once at commission.
- [ ] Committed and detected rates are both computed and reported; the crime metric suite is stated over `13010`.
- [ ] The `JUDGE` output is clamped to the statutory range × `sentencing.multiplier`, with every clamp recorded in `13040.clamped[]`.
- [ ] On `JUDGE` failure the bench rule decides and `13040.origin == "bench"`; `court.bench_share` is reported every run.
- [ ] Findings citing non-admitted seqs are dropped and counted.
- [ ] **`INV-MONEY` holds across: a fine larger than the convict's wealth (garnishment receivable), a settlement, a public defender payment, and a theft at commission.**
- [ ] No penalty shortfall is ever silently written off.
- [ ] An incarcerated agent's legal action set is exactly the six allowed types; employment is terminated; rent and loan obligations continue to accrue.
- [ ] Over `prison.capacity`, the sentence converts to a fine and the conversion is logged.
- [ ] **No `Observation` contains `p_detect`, `concealment`, `evidence_strength`, `criminal_record` of another agent, or any crime rate.**
- [ ] `polis rebuild` reproduces `crimes` and `court_cases` exactly.
- [ ] `mypy --strict polis/society/law.py`; no import of `polis.agents.memory`/`.cognition`/`.state`; memory access only via `MemoryLookup`.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/invariants/test_crime_is_possible.py` | **Merge gate (extends C10's).** With `LawLegalityOracle` wired: `COMMIT_CRIME`, an insider `SUBMIT_ORDER` and a fraudulent `PITCH` all validate, emit `2062` + `13001` + `13010`, and reach their resolvers. `"legality" not in get_args(RejectReason)` |
| `tests/invariants/test_derived_crime_without_intent.py` | **Merge gate.** A 300-tick run in which **zero `COMMIT_CRIME` actions are ever submitted** still produces ≥ 1 crime of each derived type |
| `tests/unit/society/test_derived_predicates.py` | Each predicate's positive and negative case; insolvency ≠ breach; no-memory ≠ perjury; enforcement flag gating |
| `tests/unit/society/test_mnpi.py` | All four MNPI conditions; public disclosure clears it; window expiry; purity under replay |
| `tests/unit/society/test_detection.py` | Monotonicity in budget; hazard spread over the window; `victim_awareness` asymmetry; determinism from `rng.get('law.detect', crime_id, tick)` |
| `tests/unit/society/test_investigation.py` | Severity ordering with `crime_id` tie-break; slots bounded by budget; evidence strength arithmetic; charge threshold |
| `tests/unit/society/test_evidence_admissibility.py` | Each of the four admissibility routes; excluded counts recorded; `n_surfaced` scales with counsel skill |
| `tests/unit/society/test_judgment_constraints.py` | Every clamp; verdict/case-type mismatch triggers repair then bench; uncited findings dropped; zero-penalty guilty recorded as `nominal` |
| `tests/unit/society/test_bench_rule.py` | Monotone in `evidence_strength`; fires only on LLM failure; `origin == "bench"` |
| `tests/invariants/test_law_money.py` | **Merge gate.** `INV-MONEY` to the cent across a fine exceeding wealth, a garnished payroll, a settlement, a public defender fee and a theft |
| `tests/unit/society/test_incarceration.py` | Action-set restriction; employment termination; obligations still accruing; 2× decay; capacity conversion; release path |
| `tests/invariants/test_no_enforcement_leakage.py` | Reflective scan: `p_detect`, `concealment`, `evidence_strength` and crime rates absent from every perception type and prompt variable |
| `tests/integration/test_deterrence_sweep_smoke.py` | Two `police.budget_cents` cells × 3 seeds, 400 ticks: committed and detected rates both computed; displacement across types reported |
| `tests/integration/test_court_pipeline.py` | Crime → detection → report → investigation → arrest → filing → counsel → evidence → testimony → judgment → penalty, end to end with StubProvider |
| `tests/determinism/test_law_determinism.py` | Same seed twice → identical 13000–13999 sequence including detection draws and judgments |
| `tests/integration/test_rebuild_law.py` | `polis rebuild` diff-clean on `crimes` and `court_cases` |

## 13. Definition of done

All of `chunks/README.md §5`, plus:

1. `polis/society/law.py` exports the §5 symbols with those exact signatures; `LawLegalityOracle` satisfies C10's `LegalityOracle` with no adapter.
2. Kinds 13000–13999 registered with payload schemas; `13010` registered `Persistence.PERSISTED` and explicitly excluded from any sampling policy.
3. `prompts/judge/{system,user}.v1.jinja` + two paraphrase siblings + `prompts/schemas/judge.schema.json`, in `runs.prompt_manifest`, with the statutory range stated prominently (the clamp-rate detector in F9 is a prompt-quality signal).
4. Three `@mechanism` declarations with `entails` matching `07 §8.4`, `§8.8` and `§8.9` verbatim, all ablatable.
5. The composition-root swap from `PermissiveLegalityOracle` to `LawLegalityOracle` recorded, with a test asserting no M4+ config wires the permissive one.
6. A note for C11 on garnishment and the ex-offender wage multiplier, and for C13 on the insider-trading profit backfill.
7. Handback records: the four coordination items in §6; measured `crime.committed_rate`, `crime.dark_figure`, `conviction.rate`, `court.bench_share` and the `13001` split by `path` from a 400-tick calibration run, checked against the F2 and F9 thresholds.

## 14. Traps

1. **Nobody commits crimes (F2).** The likeliest outcome, and the reason five of seven types are derived. If `crime.committed_rate < 0.005` per adult per sim-year, check the `13001` split by `path` before anything else: `explicit ≈ 0` with healthy `derived` is a *result about model refusal*, and B5 proceeds on the derived types. Both zero means the predicates are not firing — usually because reliance, capacity-to-pay, or first-hand-memory conditions are too strict, or `mnpi_window` is shorter than the time it takes an agent to act on a rumour.
2. **Making the legality gate reject.** It reads like the other four gates and it is not. One `Rejection` here silently deletes B5, the deterrence sweep and the entire law layer, and every test still passes because no crime ever occurs.
3. **Detecting at commission.** A single draw at PHASE 4 destroys the lag that makes fraud interesting and makes the dark figure a constant. The hazard runs daily for 180 sim-days.
4. **Stating a B5 result over detected crimes.** "More police, more crime detected" is definitional. The reviewer checklist rejects it. Every elasticity is over `13010`.
5. **Mistaking displacement for deterrence.** A fall in total crime that is entirely a shift from theft (detection 0.35, awareness 0.95) to insider trading (0.06, 0.05) is displacement. Report the type-share change alongside the total, always.
6. **An LLM or an RNG draw inside a derived predicate.** It makes crime non-replayable, breaks `polis rebuild`, and turns the crime series into noise that nobody can reproduce.
7. **Treating insolvency as `contract_breach`.** Every bankrupt agent becomes a criminal, the courts fill with the poor, and the incarceration-by-quintile metric measures your predicate rather than the society. Capacity to pay is the whole test.
8. **Perjury for repeating a rumour.** Without the first-hand-memory condition, every sincerely mistaken witness is a perjurer and testimony becomes strictly dominated by silence.
9. **Silently writing off an unpayable fine.** The single most likely `INV-MONEY` break in this chunk. Shortfall becomes a receivable and garnishes future income; it never vanishes.
10. **Forgetting that obligations accrue in prison.** If rent and loans pause during custody, incarceration becomes financially neutral and the most interesting second-order effect in the layer disappears.
11. **Courts that always or never convict (F9).** `conviction.rate > 0.95` or `< 0.05`, or a near-zero verdict–evidence correlation, means the judge is not reading the record. Check the clamp rate first: `> 0.5` means the statutory range is not prominent enough in the prompt.
12. **A high bench share treated as fine.** `court.bench_share > 0.3` means a deterministic threshold rule is deciding the docket, and no judicial-bias finding survives it.
13. **Exposing enforcement parameters to agents.** `p_detect` in an `Observation` makes deterrence a lookup and B5 circular. Agents learn the regime from news, victimisation and gossip or not at all.
14. **Reading `police.budget_cents` from the static config.** Then C18's police-budget policy has no effect, the deterrence sweep measures nothing, and F3's runtime-read audit is the only thing that would have caught it.
15. **Double-flagging.** A `COMMIT_CRIME{fraud}` that also satisfies the fraud predicate must produce one `crimes` row. Derived predicates run first and the first match wins.
16. **Letting `13010` be sampled.** It is the denominator of every crime metric in the project. Completeness is not negotiable.
