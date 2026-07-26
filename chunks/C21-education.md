# C21 — Schools, curricula, enrolment, skill accrual

**M1** · `polis/agents/education.py` (+ `polis/agents/education/` for curricula data) · **Depends on** C01 C02 C03 C04 C06 C07 · **Blocks** C11 (skills gate the labour market), C18 (education policy), C20 (intergenerational transmission) · **Size M**

---

## 1. Context

Education is the transmission channel that turns where you live into what you can earn. It is a small module — fourteen schools, four curricula, one enrolment lifecycle — but it closes the loop that research questions A2 and B6 are actually asking about: district land value funds school quality, school quality scales skill accrual, skills gate the labour market, wages buy housing, housing determines district. Every link in that chain is a tagged mechanism; only the loop is emergent. This chunk also owns the tertiary-financing decision, which is the one place in M1 where an agent has to trade money now against skills later, and therefore the earliest observable test of whether `time_preference` does anything.

## 2. Required reading

| Document | Sections | Why |
|---|---|---|
| `docs/02-ARCHITECTURE.md` | all | Binding. §5 tick phases, §5.2 sim-time cadences, §6.2 the `education` action group, §7.1 imports, §8.1 MECHANISM |
| `docs/03-DATA-MODEL.md` | §0, §2.1, §2.2, §9, §12 | Binding. `schools`, `enrolments`, `agents.education_level`, `agent_skills` |
| `docs/04-AGENT-SPEC.md` | §3 (skills), §8 (`STUDY` is reflex-legal), §11 (validation gates), §12.2 (stages) | |
| `docs/05-WORLD-SPEC.md` | §3.4 (`world.school_funding`), §4.2 (school place counts), §4.3 (term gating on opening hours), §4.4 (locality for `ENROL`/`STUDY`/`TAKE_EXAM`/`DROP_OUT`) | |
| `docs/06-ECONOMY-SPEC.md` | §3.3 (occupations), §3.5 (`EDU_BONUS_BP`), §3.8 (definition of a skill being "used"), §5.2 (`ed_tuition` SKU) | You feed these; you do not implement them |
| `docs/07-SOCIETY-SPEC.md` | §7.1–§7.2 (`RuntimeConfig`, `education.*` policy parameters) | |
| Chunk interfaces consumed | C06 `World`, C07 `AgentState`/`apply_skill_growth`/`Observation`, C04 clock/rng | |

## 3. Scope — in

1. **Schools** — seeded at world generation, one `schools` row per school place plus four university tracks; levels `primary · secondary · university · vocational`.
2. **Curricula** — skill weight vectors in basis points summing to 10,000, per level and per university track.
3. **Quality** — `schools.quality = clamp(district.school_quality * quality_offset, 0.01, 0.99)`, applied here on receipt of C06's kind 3063. C21 is the **sole writer** of `schools.quality`.
4. **Tuition** — `tuition_cents` per term, the `TUITION_DUE` → economy → arrears protocol, scholarships (default off), unpaid-tuition expulsion.
5. **Capacity and admission** — resolved at term boundaries, priority classes plus seeded lottery.
6. **Enrolment lifecycle** — request → admit → attend → examine → promote → graduate | drop out | expel, with `enrolments.outcome` and `gpa`.
7. **Mandatory schooling** — auto-enrolment by age stage, `education.compulsory_until_age` from the runtime overlay, `DROP_OUT` refused below it, truancy recorded.
8. **Actions** — `ENROL`, `STUDY`, `DROP_OUT`, `TAKE_EXAM`: params models, validators, and resolution.
9. **Skill accrual** — daily, from attendance, through C07's `apply_skill_growth`.
10. **Graduation and credentials** — `CREDENTIAL_AWARDED`, the mapping into `agents.education_level`, the in-memory `credentials` projection for vocational tracks.
11. **The spatial-inequality channel** — the district-quality → accrual link, tagged and ablatable.
12. **Tertiary financing** — the funding gap surfaced as an `Obligation`, and the outcomes when it is not met.
13. Event kinds **14000–14999**.

## 4. Scope — out

| Not yours | Whose |
|---|---|
| `districts.school_quality` and the `world.school_funding` mechanism itself | C06 (you consume kind 3063) |
| Any ledger write. `agents → economy` is forbidden by `02 §7.1` | C11/C14 drain `TUITION_DUE` |
| The skill growth arithmetic and the age curve | C07 (`apply_skill_growth`) |
| The definition of a skill being "used" for decay purposes — you supply the *enrolled* clause only | C11 |
| `EDU_BONUS_BP` in the labour match score | C11 |
| Student loans (`APPLY_FOR_LOAN`) | C14 |
| `education.spend_cents_per_student` / `compulsory_until_age` enactment | C18 (you read them via `RuntimeConfig`) |
| Teacher employment, school operating firms, wages | C11 |
| Peer effects, school social graph | C16 |
| Action dispatch framework and the five gates | C10 (you supply the education validators it calls) |

## 5. Interfaces you provide

```python
# polis/agents/education.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Final, Literal, Mapping, Sequence

SchoolLevel = Literal["primary","secondary","university","vocational"]
UniTrack    = Literal["engineering","commerce","medicine","humanities"]
EnrolOutcome = Literal["in_progress","graduated","dropped_out","expelled"]

@dataclass(frozen=True, slots=True)
class School:
    school_id: str; place_id: str; district_id: str
    level: SchoolLevel; track: UniTrack | None
    quality: float; quality_offset: float
    tuition_cents: int; capacity: int
    curriculum_bp: Mapping[Skill, int]        # sums to 10_000
    terms_required: int
    min_age: float; max_age: float | None
    prerequisite: EducationLevel

@dataclass(slots=True)
class Enrolment:
    enrolment_id: str; agent_id: str; school_id: str
    started_tick: int; ended_tick: int | None
    outcome: EnrolOutcome
    gpa: float
    terms_completed: int
    attendance_sessions: int                   # this term
    expected_sessions: int                     # this term
    terms_unpaid: int
    priority_class: int

CURRICULA: Final[Mapping[tuple[SchoolLevel, UniTrack | None], Mapping[Skill, int]]]
LEVEL_TERMS: Final[Mapping[SchoolLevel, int]]          # primary 18, secondary 18,
                                                       # vocational 6, university 12
LEVEL_AGE_BAND: Final[Mapping[SchoolLevel, tuple[float, float | None]]]
EDUCATION_ORDER: Final[tuple[EducationLevel, ...]]     # none < primary < ... < graduate

# --- generation -------------------------------------------------------------
def seed_schools(world: World, rng: RngRegistry, cfg: EducationConfig
                 ) -> tuple[list[School], list[Event]]:
    """Runs once, after world generation, before population init. Emits 14001 per school."""

# --- calendar ---------------------------------------------------------------
def in_term(sim_day: int, cfg: EducationConfig) -> bool: ...          # sim_day % 120 < 90
def term_index(sim_day: int, cfg: EducationConfig) -> int: ...
def is_term_start(clock: Clock, cfg: EducationConfig) -> bool: ...
def is_exam_window(clock: Clock, cfg: EducationConfig) -> bool: ...
def is_school_session(place_id: str, world: World, clock: Clock,
                      cfg: EducationConfig) -> bool: ...              # feeds World.is_open

# --- PHASE 5, institution slot 10 (misc/world) ------------------------------
def resolve_education_actions(actions: Sequence[Action], st: EducationState,
                              agents: Mapping[str, AgentState], world: World,
                              tick: int) -> list[Event]: ...

# --- PHASE 7, in this order ------------------------------------------------
def daily_accrual_step(st: EducationState, agents: Mapping[str, AgentState],
                       clock: Clock, cfg: EducationConfig) -> list[Event]: ...
def term_boundary_step(st: EducationState, agents: Mapping[str, AgentState],
                       world: World, clock: Clock, rt: RuntimeConfig,
                       rng: RngRegistry, cfg: EducationConfig) -> list[Event]: ...
def admission_step(st: EducationState, agents: Mapping[str, AgentState], world: World,
                   tick: int, rng: RngRegistry, cfg: EducationConfig) -> list[Event]: ...
def mandatory_enrolment_step(st: EducationState, agents: Mapping[str, AgentState],
                             world: World, tick: int, rt: RuntimeConfig,
                             cfg: EducationConfig) -> list[Event]: ...
def apply_tuition_arrears(st: EducationState, paid: Mapping[str, int],
                          tick: int) -> list[Event]: ...

# --- consumed by C06 (kind 3063) and C11 ------------------------------------
def apply_district_quality(st: EducationState, district_id: str, quality: float,
                           tick: int, cause_seq: int) -> list[Event]: ...
def pupils_by_district(st: EducationState, world: World) -> Mapping[str, int]: ...
def used_skills_for(agent_id: str, st: EducationState) -> frozenset[Skill]: ...
def credentials_of(agent_id: str, st: EducationState) -> frozenset[str]: ...
def is_enrolled(agent_id: str, st: EducationState) -> bool: ...
def school_of(agent_id: str, st: EducationState) -> School | None: ...

# --- consumed by C07/C09 perception -----------------------------------------
def education_obligations(agent_id: str, st: EducationState, clock: Clock,
                          cfg: EducationConfig) -> tuple[Obligation, ...]:
    """attend_school (mandatory=True in term on weekdays), take_exam (exam window),
    tuition_due (with the funding gap in amount_cents)."""
def tuition_gap_cents(agent_id: str, school: School, wealth_cents: int,
                      st: EducationState) -> int: ...

# --- action validators, called by C10's dispatcher --------------------------
class EnrolParams(BaseModel):    school_id: str
class StudyParams(BaseModel):    pass
class DropOutParams(BaseModel):  pass
class TakeExamParams(BaseModel): pass

def validate_enrol(a: Action, ag: AgentState, st: EducationState, world: World,
                   rt: RuntimeConfig, tick: int) -> RejectReason | None: ...
def validate_study(a: Action, ag: AgentState, st: EducationState, world: World,
                   clock: Clock) -> RejectReason | None: ...
def validate_drop_out(a: Action, ag: AgentState, st: EducationState,
                      rt: RuntimeConfig, tick: int) -> RejectReason | None: ...
def validate_take_exam(a: Action, ag: AgentState, st: EducationState,
                       world: World, clock: Clock) -> RejectReason | None: ...

# --- invariants -------------------------------------------------------------
def inv_education_monotone(st: EducationState,
                           agents: Mapping[str, AgentState]) -> Ok | Violation: ...
def inv_enrolment_live(st: EducationState,
                       agents: Mapping[str, AgentState]) -> Ok | Violation: ...
def on_agent_died(agent_id: str, st: EducationState, tick: int) -> list[Event]: ...
```

## 6. Interfaces you consume

| From | Symbol | Use |
|---|---|---|
| C01 | `EducationConfig`, `polis.config.runtime.RuntimeConfig.get(param, tick)` | `education.spend_cents_per_student`, `education.compulsory_until_age` — **read per call, never cached** |
| C02 | `Event`, `EventRef`, `register_kind` | kinds 14000–14999 |
| C03 | `SchoolRepository`, `EnrolmentRepository`, `AgentRepository` (education_level column) | batched writes in PHASE 6 |
| C04 | `Clock` (`sim_day`, `weekday`, `tick`, `ticks_per_sim_day`), `RngRegistry`, `stable`, `@mechanism`, `mint(prefix, tick, ordinal)` | |
| C06 | `World.places_of_type("school"/"university")`, `district_of`, `travel_ticks`, `is_open`, `affords`; kind **3063** `DISTRICT_SCHOOL_QUALITY_UPDATED` | |
| C07 | `AgentState`, `SKILLS`, `apply_skill_growth`, `learning_rate`, `Obligation`, `EducationLevel`, `stage_for_age` | |

`agents → kernel, events, world, llm, store, config`. **No import of `polis.economy` or `polis.society`, ever.**

## 7. Data model touched

| Table | R/W | Notes |
|---|---|---|
| `schools` | **own**, write | `quality` is written here and nowhere else. `curriculum` JSONB holds `{skill: weight_bp}` |
| `enrolments` | **own**, write | `outcome`, `gpa`, `ended_tick`. Soft delete only |
| `agents.education_level` | write | Monotone non-decreasing. C07 owns the row; coordinate via the repository, not by writing the whole row |
| `agent_skills` | write via C07's `apply_skill_growth` | Never write the table directly |
| `districts.school_quality` | read | C06 writes |
| `places` | read | `type ∈ {school, university}`, `capacity`, `district_id` |

**In-memory projections** (rebuildable from the log, `03 §12`): `EducationState.enrolments`, `.pending_requests`, `.credentials`, `.attendance`, `.terms_unpaid`. No new tables.

## 8. Event kinds owned

Range **14000–14999**, owner `polis.agents.education`, all persisted unless noted.

| Kind | Name | Payload |
|---|---|---|
| 14001 | `SCHOOL_OPENED` | `school_id, place_id, district_id, level, track, capacity, tuition_cents, curriculum_bp, quality, quality_offset, terms_required` |
| 14002 | `SCHOOL_QUALITY_CHANGED` | `school_id, from, to, district_quality, quality_offset, mechanism: "education.quality_to_accrual"` |
| 14005 | `TERM_STARTED` | `term_index, sim_day, schools_open, enrolled_total, admitted, rejected` |
| 14006 | `TERM_ENDED` | `term_index, graduations, dropouts, expulsions, mean_gpa, mean_attendance_bp` |
| 14010 | `ENROLMENT_REQUESTED` | `agent_id, school_id, source ∈ action·mandatory, tick` |
| 14011 | `ENROLMENT_ADMITTED` | `enrolment_id, agent_id, school_id, level, track, priority_class, term_index, tuition_cents` |
| 14012 | `ENROLMENT_REJECTED` | `agent_id, school_id, reason ∈ capacity·age·prerequisite·already_enrolled·unreachable` |
| 14020 | `ATTENDANCE_RECORDED` | `agent_id, enrolment_id, sim_day, sessions, at_home_sessions, expected_sessions` — sim-daily, sampled at `cognition_sample_rate` |
| 14021 | `SKILL_ACCRUED` | `agent_id, enrolment_id, school_id, deltas{skill: Δ}, quality, learning_rate, attendance_bp, sim_day` |
| 14030 | `EXAM_TAKEN` | `agent_id, enrolment_id, term_index, score, gpa_before, gpa_after, passed` |
| 14031 | `TRUANCY_RECORDED` | `agent_id, enrolment_id, term_index, attendance_bp, compulsory` |
| 14040 | `TUITION_DUE` | `payer_id, student_id, school_id, amount_cents, term_index, due_tick` — **drained by `economy` in PHASE 7** |
| 14041 | `TUITION_SETTLED` | `student_id, school_id, amount_cents, term_index` |
| 14042 | `TUITION_UNPAID` | `student_id, school_id, amount_cents, terms_behind, gap_cents` |
| 14043 | `SCHOLARSHIP_AWARDED` | `agent_id, school_id, amount_cents, basis ∈ merit·means, term_index` |
| 14050 | `CREDENTIAL_AWARDED` | `agent_id, school_id, level, track, gpa, terms_completed, education_level_before, education_level_after` |
| 14051 | `DROPPED_OUT` | `agent_id, enrolment_id, school_id, reason ∈ action·unpaid_tuition·attendance·age_out·death·migration, terms_completed, gpa` |
| 14060 | `EDUCATION_LEVEL_CHANGED` | `agent_id, from, to, cause, credential_school_id` |
| 14900 | `EDUCATION_INVARIANT_WARNING` | `invariant_id, agent_id, expected, actual` |

## 9. Implementation notes

**9.1 School seeding.** `05 §4.2` gives school place counts per archetype (core 1, uptown 2, midtown 2, industrial 1, suburb 4, periphery 3) and one `university` place in uptown. Assign one `schools` row per school place, by level, in place-id order:

| archetype | school places | levels, in order |
|---|---|---|
| `core` | 1 | secondary |
| `uptown` | 2 | primary, secondary |
| `midtown` | 2 | primary, secondary |
| `industrial` | 1 | primary |
| `suburb` | 4 | primary, primary, secondary, vocational |
| `periphery` | 3 | primary, secondary, vocational |

Totals: 6 primary (cap 1,200), 5 secondary (1,000), 2 vocational (400). The single `university` place (capacity 400) carries **four** `schools` rows, one per track, `capacity = 100` each. `quality_offset ~ U(0.85, 1.15)` from `rng.get("education.gen.offset", school_id)`. `school_id = mint("sc", 0, ordinal)` in a deterministic place-id order. Core has no primary school and industrial has no secondary; children there commute. That is the spatial channel working, not a bug — but check it against `education.unreachable_school_share` (§11).

**9.2 Curricula** (basis points over C07's 14 skills; each row sums to 10,000):

| level / track | weights |
|---|---|
| `primary` | writing 2000, operations 1500, manual 1500, research 1500, design 1000, negotiation 1000, persuasion 1000, teaching 500 |
| `secondary` | writing 1500, research 1500, operations 1200, finance 1000, engineering 1000, manual 800, design 800, negotiation 800, persuasion 700, sales 700 |
| `vocational` | manual 3000, operations 2500, engineering 1500, sales 1000, medicine 800, design 700, negotiation 500 |
| `university/engineering` | engineering 3500, research 2000, design 1500, operations 1000, manual 800, writing 700, management 500 |
| `university/commerce` | finance 3000, negotiation 1500, sales 1500, management 1500, operations 1000, persuasion 800, writing 700 |
| `university/medicine` | medicine 4000, research 2000, manual 1200, teaching 800, writing 800, operations 700, negotiation 500 |
| `university/humanities` | writing 2500, law 2000, persuasion 1500, research 1500, teaching 1200, negotiation 800, design 500 |

Curricula live in `configs/curricula.yaml` and are hashed into the run manifest. A row that does not sum to 10,000 is a config error.

**9.3 Calendar.** `terms_per_sim_year = 3`, `term_length_days = 90`, `break_days = 30`: `in_term(sim_day) = (sim_day % 120) < 90`. School sessions are weekdays 0–4 in term (`05 §4.3`); university adds day 5. That is 90 × 5/7 ≈ 64 sessions per term, ~193 per sim-year. `terms_required`: primary 18 (6 years), secondary 18, vocational 6 (2 years), university 12 (4 years). Exam window = the last 3 sim-days of a term. **Every one of these reads `clock.sim_day`; none reads `tick // 24`.**

**9.4 Skill accrual.** Applied once per sim-day in PHASE 7, from the attendance counter, through C07:

```
attendance_bp = 10_000 * (sessions + at_home_sessions * home_study_bp // 10_000) // expected
scale         = learning_scale * attendance_bp // 10_000        # cfg.learning_scale = 0.006
apply_skill_growth(agent, weights=curriculum_bp/10_000, scale=scale,
                   quality=school.quality, tick=tick)
```

Calibration check, and it is an acceptance criterion: at `quality = 0.60`, a primary curriculum weight of 0.20, `learning_rate ≈ 0.75`, and 193 sessions/year over 6 years (1,158 sessions), `Δ/session = 0.006 × 0.60 × 0.20 × 0.75 = 5.4e-4`, so `level = 1 - exp(-0.625) ≈ 0.465`. Six further years of secondary at weight 0.15 takes it to ≈ 0.67. If your numbers come out near 1.0 after one sim-year, `learning_scale` is wrong by an order of magnitude and every wage in M2 will be identical.

> `@mechanism("education.quality_to_accrual", entails="Skill growth is linear in school quality and school quality is monotone in district land value through world.school_funding. Therefore a positive correlation between parental district wealth and child skill attainment holds by construction, and no intergenerational-transmission finding (A2, B6) may be reported without --mechanism-off education.quality_to_accrual, which sets every school's quality to the population mean.")`

**9.5 Admission.** Requests accumulate (from `ENROL` actions and from `mandatory_enrolment_step`) and are resolved **only at a term start**, never on the action tick, so capacity allocation is order-independent. Priority classes, highest first: (1) resident of the school's district; (2) has a sibling currently enrolled at that school; (3) everyone else. Within a class, `rng.get("education.admission", f"{school_id}|{agent_id}", tick).random()`. Ties on `agent_id`.

> `@mechanism("education.admission_priority", entails="District residence and sibling presence determine access to a scarce school place. Therefore residential sorting maps onto school sorting by construction, and any finding about school segregation restates world.housing_match plus this rule. Ablation: pure lottery.")`

**9.6 Mandatory schooling.** At each term start, every agent with `6 <= age < rt.get("education.compulsory_until_age", tick)` (default 16) who is not enrolled gets an automatic `ENROLMENT_REQUESTED{source: "mandatory"}` for the nearest school of the level matching its `education_level` and age. Idempotent: guard on `is_enrolled` and on an existing pending request, or you will create a duplicate enrolment every tick. Mandatory schooling is enforced by three things and nothing else: auto-enrolment, a `mandatory=True` `Obligation` on session days (which force-routes to DELIBERATE in C09), and `validate_drop_out` rejecting at the capability gate below the compulsory age. There is no truancy fine — a fine needs a ledger leg and `agents` cannot post one.

**9.7 Exams, GPA, graduation.** `TAKE_EXAM` in the exam window scores:

```
curricular_skill = Σ_s curriculum_bp[s] * agent.skills[s] / 10_000        # 0..1
score = clamp(0.55*curricular_skill + 0.25*attendance_bp/10_000
              + 0.10*school.quality + 0.10*conscientiousness
              + noise, 0, 1)                     # noise ~ U(-0.05, +0.05),
                                                 # rng.get("education.exam", enrolment_id, tick)
gpa   = (gpa * terms_completed + score) / (terms_completed + 1)
```

An agent that does not sit the exam scores 0 for that term. `terms_completed += 1` at each term end regardless. Graduation at `terms_completed >= school.terms_required` **and** `gpa >= cfg.pass_threshold` (0.50); below threshold the agent repeats — capped at `cfg.max_repeat_terms` (4), after which `DROPPED_OUT{reason: "attendance"}`.

**9.8 Credentials and `education_level`.** `EDUCATION_ORDER = (none, primary, secondary, tertiary, graduate)`. On graduation, `education_level = max(current, mapped)` where primary→`primary`, secondary→`secondary`, vocational→`secondary`, university (first)→`tertiary`, university (second)→`graduate`. **Never decreasing** — `inv_education_monotone` checks it. Vocational carries no enum slot, so it is recorded as a credential string in the in-memory `credentials` projection rebuilt from 14050, which C11 may read for occupation gating. Emit 14060 whenever `education_level` actually changes.

**9.9 Tuition.** Primary, secondary, vocational are `gov`-run: `tuition_cents = 0`. University: `cfg.university_tuition_cents` (default 1_200_000 = 12,000.00). At each term start for every university enrolment, emit `TUITION_DUE{payer_id, student_id, school_id, amount_cents, term_index, due_tick}` where `payer_id` is the student if adult, else the household head. **C21 posts nothing.** The economy drains 14040 in PHASE 7 and posts the legs it owns:

```
debit  ledger_account_of(payer_id)      amount_cents   reason='purchase'   # sku ed_tuition
credit gv_treasury                      amount_cents   reason='purchase'
```

Unpaid after the term → `terms_unpaid += 1`, emit 14042. At `terms_unpaid > cfg.tuition_grace_terms` (1) → `DROPPED_OUT{reason: "unpaid_tuition"}`, `outcome = 'expelled'`. In M1 there is no economy: `TUITION_DUE` is emitted, nothing drains it, and `apply_tuition_arrears` receives an empty map. Guard the arrears path so M1 does not expel the entire university on term two — `cfg.enforce_tuition` defaults to `false` and C11 flips it at M2.

**9.10 The tertiary-financing decision.** `education_obligations` surfaces a `tuition_due` `Obligation` carrying `amount_cents = tuition_gap_cents(...)`, i.e. tuition minus scholarship minus liquid wealth, clamped at 0. That obligation is what the agent's DELIBERATE prompt sees; the choice among *pay / borrow / work / do not enrol / drop out* is the agent's, made with `APPLY_FOR_LOAN` (C14), `WORK` (C11), `ENROL`, or `DROP_OUT`. C21 encodes **no** rule for it. Scholarships: `cfg.scholarship_share_bp` defaults to **0** — a non-zero default would manufacture exactly the mobility finding B6 is trying to measure. When enabled, the top `share` of applicants by GPA get a waiver, tagged `education.means_tested_scholarship`.

**9.11 Phase placement.** PHASE 5 slot 10: `resolve_education_actions` (records `STUDY` attendance, queues `ENROL` requests, processes `DROP_OUT` and `TAKE_EXAM`). PHASE 7, in order: `apply_tuition_arrears` → `daily_accrual_step` (every sim-day) → `admission_step` and `term_boundary_step` and `mandatory_enrolment_step` (term boundaries only). `apply_district_quality` runs on receipt of 3063, which C06 emits earlier in PHASE 7 — so quality changes take effect from the *next* accrual, never the same one.

## 10. Configuration keys

```yaml
education:
  terms_per_sim_year: 3
  term_length_days: 90
  break_days: 30
  exam_window_days: 3
  learning_scale: 0.006              # §9.4 — calibrated, do not guess
  home_study_bp: 4000                # STUDY at home is worth 40% of a session
  min_attendance_bp: 5000            # below this for 3 consecutive terms -> expelled
  pass_threshold: 0.50
  max_repeat_terms: 4
  university_tuition_cents: 1_200_000
  tuition_grace_terms: 1
  enforce_tuition: false             # true from M2
  scholarship_share_bp: 0            # default OFF — see §9.10
  quality_offset_range: [0.85, 1.15]
  compulsory_until_age_default: 16   # overridden by runtime `education.compulsory_until_age`

mechanisms:
  education.quality_to_accrual: linear_in_quality
  education.admission_priority: district_then_sibling_then_lottery
  education.means_tested_scholarship: off
```

Runtime parameters read through `RuntimeConfig.get`: `education.spend_cents_per_student`, `education.compulsory_until_age`.

## 11. Acceptance criteria

- [ ] `seed_schools` produces exactly 18 school rows (6 primary, 5 secondary, 2 vocational, 4 university tracks) from the default world, deterministically, with `school_id`s stable across regenerations.
- [ ] Every curriculum sums to exactly 10,000 bp and references only the 14 skills of `04 §3`.
- [ ] `in_term`, `term_index`, and `is_exam_window` are functions of `clock.sim_day` only and give identical calendars under `microscope` and `chronicle`.
- [ ] Skill accrual is applied exactly once per sim-day per enrolled agent — 24 ticks of `STUDY` in `microscope` produce the same day's accrual as 1 tick in `chronicle`.
- [ ] Calibration: a cohort schooled from age 6 to 18 at `quality = 0.60` reaches a dominant-curriculum skill in `[0.55, 0.80]`, and no agent's skill vector is saturated (> 0.95 on more than two skills) at age 18.
- [ ] `--mechanism-off education.quality_to_accrual` equalises attainment across districts: the between-district variance of age-18 mean skill falls by > 70%.
- [ ] Admission never over-fills a school: `Σ live enrolments <= school.capacity`, every tick.
- [ ] Admission is permutation-invariant: shuffling the pending-request list produces an identical admitted set.
- [ ] Mandatory enrolment is idempotent — an eligible agent gets exactly one enrolment, not one per tick.
- [ ] `DROP_OUT` below `education.compulsory_until_age` is rejected with `reason: "capability"`; above it, accepted.
- [ ] Enacting `education.compulsory_until_age: 18` at tick T changes the rejection outcome from term T+1 and not before.
- [ ] `agents.education_level` is monotone non-decreasing for every agent over a 5,000-tick run (`inv_education_monotone`).
- [ ] A university dropout retains `education_level = 'secondary'`; a vocational graduate gets `secondary` plus the `vocational` credential.
- [ ] `TUITION_DUE` is emitted once per university enrolment per term, with `payer_id` = household head for minors.
- [ ] `polis/agents/education.py` imports nothing from `polis.economy` or `polis.society` (`import-linter`).
- [ ] Agent death terminates the enrolment with `outcome = 'dropped_out', reason = 'death'` in the same tick (`inv_enrolment_live`).
- [ ] `education.unreachable_school_share` — the fraction of compulsory-age agents with no school of their required level within `max_travel_ticks` of home — is reported and is 0 under the default world.
- [ ] `polis rebuild` reconstructs `schools`, `enrolments`, and the `credentials` projection byte-identically from the log.

## 12. Tests to write

| File | Asserts |
|---|---|
| `tests/unit/education/test_school_seeding.py` | counts by level, capacities, `quality_offset` determinism, `school_id` stability, curricula sum to 10,000 |
| `tests/unit/education/test_calendar.py` | `in_term` boundaries at days 89/90/119/120; term index across a sim-year; exam window; profile-invariance |
| `tests/unit/education/test_accrual.py` | once-per-sim-day; the §9.4 calibration arithmetic to 4 dp; home study at 40%; zero attendance → zero accrual; quality scaling; ablation flattens district variance |
| `tests/unit/education/test_admission.py` | capacity respected; priority classes ordered; sibling rule; lottery seeded; permutation invariance over the request list |
| `tests/unit/education/test_mandatory.py` | auto-enrolment at 6; idempotency over 100 ticks; `DROP_OUT` refusal below the compulsory age; runtime override takes effect next term, not this one |
| `tests/unit/education/test_exams_graduation.py` | GPA running mean; missed exam scores 0; graduation gate on both terms and GPA; repeat cap → expulsion; `CREDENTIAL_AWARDED` payload |
| `tests/unit/education/test_education_level.py` | full ladder; monotonicity under dropout, vocational, and second-degree paths; 14060 fires only on real change |
| `tests/unit/education/test_tuition.py` | `TUITION_DUE` payer selection; arrears accumulation; grace term; expulsion at `enforce_tuition: true` and no expulsion at `false`; the exact leg shape handed to economy is asserted as a payload contract |
| `tests/unit/education/test_validators.py` | `ENROL` age band, prerequisite, already-enrolled, capacity-at-request; `STUDY` locality (`school`/`university`/`home`) and term gating; `TAKE_EXAM` outside the window rejected |
| `tests/unit/education/test_quality_channel.py` | 3063 → `SCHOOL_QUALITY_CHANGED` → next-day accrual, never the same day; `quality_offset` applied; clamped to `[0.01, 0.99]` |
| `tests/unit/education/test_lifecycle_hooks.py` | `on_agent_died` closes the enrolment and frees capacity; `inv_enrolment_live` catches a dangling row |
| `tests/determinism/test_education_determinism.py` | same seed, 2,000 ticks, StubProvider → identical hash chain, twice |
| `tests/integration/test_education_cohort.py` | 200 agents, 3 sim-years accelerated: attainment distribution is non-degenerate, between-district gap is positive and disappears under the ablation, no HALT |

## 13. Definition of done

All of `chunks/README.md §5`. Specifically: acceptance criteria met; `pytest` green including §12; `mypy --strict polis/agents/education*` clean; `ruff` clean; `import-linter` shows no `polis.economy`/`polis.society` import; determinism test passes twice; `education:` block and `configs/curricula.yaml` in the pydantic schema and the run manifest; all 14000-range kinds registered with payload schemas; the three mechanisms decorated and ablatable. Write down: the level-to-place assignment you chose, the `learning_scale` calibration you landed on and the cohort curve that justifies it, the `enforce_tuition` M1/M2 switch, and the vocational-credential workaround for the `education_level` enum.

## 14. Traps

1. **Importing `polis.economy.ledger` to charge tuition.** It is the obvious thing to do and it is forbidden twice over — by `02 §7.1` (`agents` may not import `economy`) and by `03 §4.2` rule 4 (only `ledger.py` writes the ledger). `TUITION_DUE` is the whole protocol, exactly as the world's `RENT_DUE` is.
2. **Accruing skills per tick.** `04 §3` states growth per **sim-day**. A per-tick loop makes `microscope` agents learn 24× faster than `chronicle` agents from identical schooling, which silently invalidates every cross-profile result and makes the M2 wage distribution profile-dependent.
3. **`learning_scale` left at 1.0.** Without the scale constant the spec's formula saturates a curriculum skill in weeks. Every agent then has an identical maxed skill vector, wage dispersion collapses, `INV-NONDEGEN` (V3) warns, and the labour market in M2 produces nothing. The §9.4 arithmetic is the check; run it before you run anything else.
4. **`STUDY` at home worth a full session.** If home study is free and equal, school quality never binds, the spatial channel evaporates, and A2's headline result quietly becomes zero. `home_study_bp: 4000` is deliberate.
5. **Admitting on the action tick.** Capacity then depends on the order actions arrived, which is exactly the order-dependence `02 §1.4` forbids. Queue requests; resolve at the term boundary over the complete claimant set.
6. **Non-idempotent mandatory enrolment.** Guard on both `is_enrolled` and an existing pending request. Without both, a six-year-old accumulates one enrolment per tick and the `enrolments` table grows by 24,000 rows/day.
7. **Calendar arithmetic from ticks.** `tick // 24 % 120 < 90` is right in `microscope` and catastrophically wrong in `chronicle`. Go through `clock.sim_day`. This is the single most likely source of a silent profile divergence in this chunk.
8. **`education_level` regressing.** A university dropout must not fall back to `primary`. Always `max(current, mapped)` over `EDUCATION_ORDER`, and let `inv_education_monotone` prove it — because the failure is invisible until C11 starts computing `EDU_BONUS_BP` and wages jump around for no reason.
9. **Vocational overwriting `tertiary`.** A `tertiary` graduate who later takes a vocational course must stay `tertiary`. Same fix, and it is the case the naive mapping table gets wrong.
10. **Double-writing `schools.quality`.** `05 §3.4`'s pseudocode writes `s.quality` from the world step. C06 must not; C21 must. If both do, quality oscillates between the offset and un-offset values on alternating terms and nobody notices for a month.
11. **Applying quality in the same step it changed.** 3063 fires in PHASE 7; if `apply_district_quality` runs before `daily_accrual_step` in the same tick, that day's accrual uses a value the agents could not have observed. Order the PHASE 7 steps as in §9.11 and assert it.
12. **Floats in event payloads at full precision.** `gpa`, `score`, `quality`, and every entry of `deltas` must be rounded to 6 dp before hashing, per `02 §4.6`. A 17-digit float in a payload makes the hash chain platform-dependent.
13. **Dangling enrolments after death.** `on_agent_died` must close the row and free the capacity slot in the same tick, or a school fills with dead students and live children are rejected for capacity. `inv_enrolment_live` exists for this.
14. **Non-zero default scholarships.** Turning on merit or means-tested waivers by default manufactures precisely the intergenerational-mobility result B6 is trying to measure. Default 0, tag the mechanism, sweep it deliberately.
15. **Unsorted iteration over the attendance dict.** `daily_accrual_step` mutates agent state while iterating a `dict[agent_id, counter]`. Sort with `stable(..., key=agent_id)` or the accrual order — and therefore the floating-point accumulation — differs between runs.
16. **Enrolling a child in a school it cannot reach.** Core has no primary and industrial has no secondary. If `travel_ticks(home, school) > max_travel_ticks`, the child is auto-enrolled, never attends, accrues nothing, and is recorded as truant forever. Reject with `reason: "unreachable"` at admission, and watch `education.unreachable_school_share`.
