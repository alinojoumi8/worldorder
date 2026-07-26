# POLIS — Society Specification

**Version:** 1.0
**Status:** Normative. Table names, kind integers, and update rules here are binding.
**Owner modules:** `polis/society/` (`comms.py`, `graph.py`, `media/`, `beliefs.py`,
`polity.py`, `law.py`) plus `polis/agents/demography.py`.
**Depends on:** `02-ARCHITECTURE.md` (tick phases, determinism, action envelope, kind
registry), `03-DATA-MODEL.md` §2.4–2.6 and §8 (tables), `04-AGENT-SPEC.md` (perception,
memory, salience, validation gates, birth/death).

> Institutions in this document **never import agent cognition** (`02-ARCHITECTURE.md §7.1`).
> They consume `Action` objects and emit `Event` objects. Every behavioural rule that is not
> an agent's own decision is tagged `MECHANISM` with an `entails:` string (threat T6).

---

## 0. Scope, ownership, and conventions

### 0.1 What this document owns

| Domain | Module | Kind range | Resolves in |
|---|---|---|---|
| Speech, DMs, broadcast, conversation | `society/comms.py` | 10000–10059 | PHASE 5 slot **2** |
| Social graph, ties, network stats | `society/graph.py` | 10040–10059 | PHASE 5 slot 2 (side effects), PHASE 7 (snapshots) |
| Beliefs | `society/beliefs.py` | 10060–10069 (see §0.5) | PHASE 5 (applied), PHASE 7 (drift) |
| Social media platform, feed, cascades | `society/media/platform.py`, `media/feed.py` | 11000–11029 | PHASE 5 slot 2; feed built in PHASE 1 |
| News, outlets, claim checking | `society/media/news.py`, `media/checker.py` | 11030–11069 | PHASE 7 (news cycle) |
| Parties, elections, offices | `society/polity.py` | 12000–12029 | PHASE 5 slot **8**; PHASE 7 (election day) |
| Policy engine | `society/polity.py` + `polis/config/runtime.py` | 12030–12049 | PHASE 7 (policy review) |
| Crime, police, courts | `society/law.py` | 13000–13999 | PHASE 4 (flagging), PHASE 5 slot **9**, PHASE 7 (court sessions) |
| Households, fertility, migration | `agents/demography.py` | 15000–15999 | PHASE 8 |

### 0.2 Resolution order and visibility

Communication resolves **2nd** in PHASE 5, polity **8th**, law **9th**
(`02-ARCHITECTURE.md §5.1`). Consequences, binding:

1. Speech resolves before labour, goods, exchange, banking, ventures, polity and law. A
   thing said in tick *t* can therefore be *cited* by an institution in tick *t*, but is
   **never perceived** by another agent until tick *t+1* — perception is a pure function of
   last tick's committed state (`04-AGENT-SPEC.md §5` rule 1).
2. Polity resolves after every economic institution, so a vote or campaign spend in tick *t*
   sees the tick-*t−1* economy. Policy never changes a parameter mid-tick; enactment carries
   an `effective_tick` (§7.4).
3. Law resolves last so that a crime flagged at the legality gate in PHASE 4 has all of its
   tick-*t* consequences already settled before detection is drawn.

### 0.3 Money

All monetary values are `BIGINT` cents suffixed `_cents`. **Every** money movement in this
document — campaign spend, candidacy deposits, counsel fees, court filing fees, fines,
damages, restitution, settlements, advertising revenue, child-rearing costs, inheritance —
goes through `polis.economy.ledger.post_transaction(legs)` (`03-DATA-MODEL.md §4.2`). No
module here writes `ledger_entries` or `ledger_accounts` directly.

`ledger_entries.reason` codes used by this document, all drawn from the existing closed list
in `03-DATA-MODEL.md §4.2`. **No new reason codes are introduced.**

| Flow | Debit | Credit | `reason` |
|---|---|---|---|
| Campaign advertising bought from an outlet | candidate cash | outlet firm cash | `purchase` |
| Campaign rally venue hire | candidate cash | place owner cash | `rent` |
| Candidacy deposit / refund | candidate ↔ government | | `transfer` |
| Counsel fee | client cash | lawyer cash | `purchase` |
| Public defender fee | government cash | lawyer cash | `purchase` |
| Court filing fee | filer cash | government cash | `transfer` |
| Criminal fine / forfeiture | convict cash | government cash | `fine` |
| Civil damages / restitution / settlement | defendant cash | plaintiff cash | `transfer` |
| Theft (successful) | victim cash | perpetrator cash | `transfer` |
| Embezzlement | firm cash | officer cash | `transfer` |
| Outlet advertising revenue (firm advertiser) | firm cash | outlet cash | `purchase` |
| Outlet subscription revenue | subscriber cash | outlet cash | `purchase` |
| Child-rearing cost | household head cash | firm cash (goods/services) | `purchase` |
| Child benefit / pension / unemployment benefit | government cash | recipient cash | `transfer` |
| Inheritance distribution | estate escrow | heir cash | `inheritance` |
| Inheritance tax | estate escrow | government cash | `tax` |

> **No revenue is ever created without an identified counterparty.** There is no
> "advertising" income account. Every cent an outlet earns debits a firm, a candidate, a
> subscriber, or the government. This is what keeps `INV-MONEY` closable through the media
> layer.

### 0.4 Entity ID prefixes

`ag_` agent · `fm_` firm · `bk_` bank · `hh_` household · `pl_` place · `st_` startup ·
`pt_` party · `ol_` outlet. This document additionally uses **non-entity** identifiers that
are not new prefixed entity types and require no schema change: `cv_` (candidacy),
`el_` (election), `cs_` (court case), `cr_` (crime), `po_` (post), `ar_` (article),
`pol_` (policy), `conv_` (conversation), `msg_` (direct message), `clm_` (claim).

### 0.5 Declared deviations and requested additions

Four items are called out explicitly rather than smuggled in.

| # | Item | Status | Justification |
|---|---|---|---|
| **D-1** | Kinds **10060–10069** allocated to belief updates, extending the 10000 block's owner set from `polis.society.comms` to also include `polis.society.beliefs` | **Declared deviation** from `02-ARCHITECTURE.md §3.2` | Beliefs have no range in the registry. Belief propagation is a communication phenomenon and shares the block's subject-index access patterns. Allocating a fresh top-level range for ten kinds is worse. `kinds.py` records the owner per kind, so this is expressible without a registry redesign. |
| **D-2** | New `ActionType.FOUND_PARTY` | **Requested addition** to the closed enum in `02-ARCHITECTURE.md §6.2` | Research question B3 asks whether agents form institutions *without being instructed to*. If parties are seeded at genesis or spawned by a clustering rule, B3 is unanswerable by construction. `JOIN_PARTY` cannot express founding because it takes an existing `pt_` id. Params: `{name, platform: {proposition: stance}, founding_member_ids}`. **Fallback if refused:** `JOIN_PARTY{party_id: null, charter: {...}}` with a union validator — worse, because the validator then branches on a null and the action's name lies about what it does. |
| **D-3** | New `places.type` value `prison` | **Requested addition** to `03-DATA-MODEL.md §3.1` / `05-WORLD-SPEC.md` | Incarceration must remove an agent from the labour market and from co-location-driven tie formation (§8.9). Reusing `police` conflates the enforcement place with the custodial one and corrupts co-location statistics for officers. **Fallback if refused:** `police` with `places.name` prefixed `HMP `, and district crime-rate metrics must then exclude custodial occupancy. |
| **D-4** | New module `polis/config/runtime.py` | **Requested addition** to `02-ARCHITECTURE.md §7` | The policy loop (§7) requires a tick-keyed parameter overlay that both `economy` and `society` can read and that `society` can write. `config` is the only package both may import under the §7.1 dependency rules, so this is the sole legal home. Contains no behaviour, only a versioned `get(param, tick)`. |

No other `ActionType`, table, column, or kind range is required. Everything else in this
document is expressed with the existing enum, the existing schema, and the four assigned
kind ranges.

### 0.6 RNG namespaces

All randomness via `rng.get(namespace, entity_id, tick)` (`02-ARCHITECTURE.md §4.1`). No
other source. Complete list for this document:

```
comms.attention        comms.conversation      graph.form         graph.decay
feed.random            feed.pool               feed.tiebreak      media.impression
media.audit            beliefs.noise           beliefs.drift      polity.vote
polity.turnout         polity.platform_drift   law.detect         law.evidence
law.bench              law.jury_pool           demog.courtship    demog.conception
demog.emigration       demog.migration_in      metrics.louvain
```

Every draw is tick-scoped (`tick=` supplied) except `graph.form` at genesis and
`beliefs.noise` at birth, which are entity-scoped and one-shot.

### 0.7 Configuration

Extends `02-ARCHITECTURE.md §8`. Everything under `society:` is a run-level researcher lever;
everything under `polity.policy:` is the initial value of a parameter that agents may
subsequently change (§7).

```yaml
society:
  feed_algorithm: engagement       # chronological | engagement | random | adversarial
  feed_slice: 15                   # 04-AGENT-SPEC §5; do not raise without re-costing prompts
  feed_candidate_cap: 300
  feed_window_sim_hours: 72
  feed_out_of_network_quota: 0.30  # share of the 15 slots the ranker may fill from outside the follow graph
  outlets: 4
  outlet_slant_dispersion: 0.55
  cpm_cents: 40
  news_cycle: daily
  stories_per_reporter_per_cycle: 1
  claim_tolerance: 0.10
  misinfo_audit_rate: 0.05
  conversation_idle_ticks: 2
  tie_halflife_sim_days: {acquaintance: 30, friend: 90, colleague: 120, rival: 180, kin: null, partner: null}
  homophily_bias: 0.0              # MECHANISM, default OFF — see §2.5

polity:
  election_method: plurality       # plurality | approval | irv | proportional
  offices:
    president: {seats: 1, term_sim_years: 4, term_limit: 2}
    council:   {seats: 7, term_sim_years: 2, method: proportional}
  council_session: weekly
  policy_review: weekly
  court_session: twice_weekly
  can_regulate_feed: false         # if true, feed_algorithm becomes a policy parameter (§7.2)
  vote_model: fitted_from_deliberate   # MECHANISM
  llm_election_multiplier: 6.0     # budget multiplier on election-day ticks

mechanisms:
  fertility_hazard: income_conditional     # MECHANISM (02 §8)
  belief_social_influence: bounded_confidence   # MECHANISM
  belief_backfire: on                      # MECHANISM
  party_platform_drift: member_mean        # MECHANISM
  crime_detection: budget_scaled           # MECHANISM
  ex_offender_wage_penalty: on             # MECHANISM
  emigration_hazard: precarity_conditional # MECHANISM

ablations:
  social_influence_off: false      # belief change becomes LLM-only
  backfire_off: false
  no_record_penalty: false
  feed_off: false                  # empty feed slice; isolates the platform channel
```

---

## 1. Communication

`polis/society/comms.py`. Resolves 2nd in PHASE 5. No resource effects, so it is safe to
resolve before every market.

### 1.1 The three speech actions

| Action | Reach | Preconditions | Cost |
|---|---|---|---|
| `SAY` | Agents co-located at the same `place_id`, capped at the perception cap of 12 (`04-AGENT-SPEC.md §5`), ranked by tie strength then `agent_id` | Awake; place occupancy > 1 | 1 action slot |
| `DIRECT_MESSAGE` | Exactly one recipient, anywhere | An existing `relationships` row of any type, **or** a `follows` edge in either direction, **or** recipient holds public office. Rate-limited to `max_dms_per_tick` (default 2) per sender per recipient per sim-day | 1 action slot |
| `BROADCAST` | All agents at a place, uncapped by the 12-agent perception cap | Capability gate: office holder, declared candidate, employee of an outlet, or owner of the place. Non-owned venue charges `place.rent_cents` pro-rata | 1 action slot + ledger leg (`rent`) if venue hired |

`SAY` params: `{text, addressed_to: [ag_…] | null, conversation_id | null, topic,
stance_proposition | null, stance_value | null}`.
`DIRECT_MESSAGE` params: `{recipient_id, text, in_reply_to: msg_… | null, topic}`.
`BROADCAST` params: `{place_id, text, topic, stance_proposition | null, stance_value | null}`.

`stance_proposition` / `stance_value` are optional structured annotations from the same
deliberate call that produced the text (`04-AGENT-SPEC.md §9.2`). They are what makes speech
a measurable persuasion channel (§5.4) rather than opaque prose. If absent, the utterance
carries no mechanical belief effect and reaches other agents only through memory.

### 1.2 Attention: who actually hears an utterance

An utterance does not automatically enter every co-located agent's head. For each candidate
listener *j* of speaker *i*:

```
attention(i→j) = clip(
      0.30
    + 0.50 · tie_strength(i, j)
    + 0.20 · [j ∈ addressed_to]
    - 0.15 · log1p(occupancy(place) - 2) / log1p(place.capacity)
    + u,  0, 1)                       u ~ U(-0.05, 0.05) via rng.get("comms.attention", conv_or_speech_id, tick)

heard  iff  attention(i→j) >= hearing_threshold          (default 0.35)
```

**MECHANISM `comms_attention: tie_weighted`** —
`entails: "utterances propagate preferentially along strong ties and decay in crowded places; therefore any finding that information travels faster within cliques than across them is partly entailed. Ablate with comms_attention: uniform, which sets attention = 1 for all co-located agents."`

`heard_by` is written into the `10010` payload as `[{agent_id, attention}]`, capped at 12
and sorted by `agent_id`. There is no separate per-listener event: one utterance, one event,
with the audience in `subject_ids` so the GIN index answers "what did agent X hear".

### 1.3 From utterance to perception to memory

The path is fully specified elsewhere; this section only fixes the joins.

| Step | Where | Rule |
|---|---|---|
| 1. Utterance resolved | PHASE 5 slot 2, tick *t* | `10010 SPEECH_UTTERED` produced, not yet visible to anyone |
| 2. Committed | PHASE 6, tick *t* | Event appended, `heard_by` fixed |
| 3. Perceived | PHASE 1, tick *t+1* | Enters listener's `Observation.co_located[*].last_utterance` (for `SAY`/`BROADCAST`) or `Observation.inbox` (for `DIRECT_MESSAGE`, cap 10) — `04-AGENT-SPEC.md §5` |
| 4. Salience | PHASE 2, tick *t+1* | `social` term = 1.0 if the listener is in `addressed_to` or is the DM recipient; 0.4 if a strong tie spoke; else 0 — `04-AGENT-SPEC.md §7` |
| 5. Memory | PHASE 3/6, tick *t+1* | Written iff `event_salience > memory_threshold` per `04-AGENT-SPEC.md §6.2`. Heuristic importance tier: base importance for `10010` is 0.20, modulated by `attention`, tie strength, and whether the utterance carried a `stance_*` annotation on a proposition the listener holds |
| 6. Belief effect | PHASE 5, tick *t+1* | If annotated, the `social` channel update of §5.4 fires with the speaker as source |

**Speech is never heard in the tick it is spoken.** This is not a limitation to be worked
around; it is the simultaneous-submission guarantee (`02-ARCHITECTURE.md §1.4`) and it is
what makes the tick order-independent.

### 1.4 Conversation is turn-based across ticks

A conversation is a lightweight projection object, not a table: `conversation_id`,
`place_id`, `participants[]`, `topic`, `turn_index`, `last_turn_tick`, `opener_id`. It is
rebuildable from `10011`/`10010`/`10012`.

**Why not a single multi-turn LLM call.** Five independent reasons, any one of which is
sufficient:

| # | Reason |
|---|---|
| **C1 — Determinism.** | The completion cache (`02-ARCHITECTURE.md §4.4`) keys on one prompt, one model, one seed. A multi-turn exchange is one opaque blob whose internal turns are not individually addressable; changing anything about participant B's state invalidates the whole transcript, so cache hit rates collapse in exactly the sweeps we run most (feed-algorithm arms differing only in what B saw). Replay fidelity would depend on the provider replaying a conversation identically, which it will not. |
| **C2 — Simultaneity.** | `02-ARCHITECTURE.md §1.4` forbids an agent observing another agent's same-tick action. An intra-tick multi-turn call lets B respond to A's tick-*t* utterance inside tick *t*, which reintroduces exactly the iteration-order artefact that decision D2 exists to eliminate. Two conversations resolved in different orders would then produce different worlds. |
| **C3 — Budget.** | The LLM budget is allocated top-down by salience once per tick (`04-AGENT-SPEC.md §7`). A multi-turn call consumes an unbounded, unknown number of turns *after* allocation, so the hard cap in `01-PRD.md §G4` becomes unenforceable. |
| **C4 — Fairness (T12).** | External agents get one action slot per tick and a fixed deadline (`08-EXTERNAL-AGENT-PROTOCOL.md §5`). They cannot be dragged into an intra-tick multi-turn exchange. Native agents would gain a conversational affordance foreign agents structurally cannot have. |
| **C5 — Auditability.** | Every utterance must be an `Action` with its own `action_id`, `salience`, `origin`, and `reasoning`, and its own `Event` with `cause_seq` pointing at the turn it answers. That is what lets the Observatory walk a persuasion cascade backwards (G3, G6). A transcript blob has one `action_id` and one cause. |

**How it works instead.**

```
tick t     A: SAY{text, addressed_to:[B], conversation_id: null}
           → 10011 CONVERSATION_OPENED{conv_id, place, participants:[A,B], opener:A, topic}
           → 10010 SPEECH_UTTERED{conv_id, turn_index: 0, ...}
tick t+1   B perceives the turn; salience.social = 1.0 (directly addressed) → force-boosted
           up the DELIBERATE ranking, or answers from the reflex SAY template set
           B: SAY{conversation_id: conv_id, ...}
           → 10010 SPEECH_UTTERED{conv_id, turn_index: 1, cause_seq: <A's turn seq>}
tick t+n   no turn for conversation_idle_ticks (default 2), or participants no longer
           co-located, or a turn carries closing=true
           → 10012 CONVERSATION_CLOSED{conv_id, turns, reason, duration_ticks}
```

Three consequences, stated rather than hidden:

1. **Granularity.** In `microscope` (1 tick = 1 sim hour) a four-turn conversation occupies
   four sim-hours. A turn therefore represents an entire conversational *contribution* — a
   paragraph, a position, a proposal — not a sentence. The sim-hour is the unit of social
   contact, not of speech. In `chronicle` (1 tick = 1 sim day) conversations are effectively
   single-exchange, and reported social-contact statistics are not comparable across
   profiles. The clock profile is reported with any B-track result.
2. **Graceful degradation under budget.** A conversation does not die when its participants
   fall below the salience cutoff, because reflex agents may emit `SAY` from a small template
   set to a co-located strong tie (`04-AGENT-SPEC.md §8`). Conversations degrade in richness,
   not in existence.
3. **Interleaving is legal.** An agent may hold turns in more than one conversation across
   consecutive ticks, bounded only by action slots. Conversations do not lock an agent.

### 1.5 Kinds 10000–10039

| Kind | Name | Payload |
|---|---|---|
| 10010 | `SPEECH_UTTERED` | `speaker_id, place_id, text, addressed_to[], heard_by[{agent_id, attention}], topic, stance_proposition, stance_value, conversation_id, turn_index, closing, claims[]` |
| 10011 | `CONVERSATION_OPENED` | `conversation_id, place_id, participants[], opener_id, topic` |
| 10012 | `CONVERSATION_CLOSED` | `conversation_id, turns, reason (idle\|dispersed\|closed\|death\|incarceration), duration_ticks, participants[]` |
| 10020 | `MESSAGE_SENT` | `message_id, sender_id, recipient_id, text, in_reply_to, topic, stance_proposition, stance_value, claims[]` |
| 10021 | `MESSAGE_READ` | `message_id, reader_id, latency_ticks, entered_memory (bool)` |
| 10030 | `BROADCAST_MADE` | `broadcaster_id, place_id, text, topic, audience_ids[], audience_size, venue_fee_cents, txn_id, stance_proposition, stance_value` |

`claims[]` on 10010/10020/10030 uses the same structured-claim shape as posts and articles
(§4.5) and is optional; when present, the utterance is checkable against the log.

---

## 2. Social graph

`polis/society/graph.py`. Projection: `relationships` (`03-DATA-MODEL.md §2.6`). Symmetric
types are stored once with `a_id < b_id`; `creditor` is directed and stores the lender as
`a_id`. `follows` (§3) is a **separate, directed** platform edge and is not a relationship.

### 2.1 Types

| `type` | Symmetric | Created by | Ends when | Notes |
|---|---|---|---|---|
| `kin` | yes | Birth, adoption, union of parents | Never (`ended_tick` set only on death) | Strength floors at 0.25; no decay |
| `partner` | yes | `15003 UNION_FORMED` | `15004 UNION_DISSOLVED`, death | At most one live `partner` row per agent |
| `friend` | yes | Upgrade from `acquaintance` (§2.3) or accepted `BEFRIEND` | Decay below floor, or valence ≤ −0.4 (→ `rival`) | |
| `colleague` | yes | Shared live `employments` row at the same `firm_id` | Either employment ends (converts to `acquaintance` at ×0.6 strength) | |
| `rival` | yes | Repeated negative interaction (§2.3) | Valence recovers above +0.1 for 30 sim-days | |
| `creditor` | **no** | `8010 LOAN_ORIGINATED` (lender = `a_id`) | Loan repaid or written off | Not a social tie; carried here so debt appears in network metrics |
| `acquaintance` | yes | Co-location, conversation, school cohort, institutional contact | Strength < 0.02 | The default entry point |

### 2.2 Formation

Formation is evaluated at the end of PHASE 5 slot 2, from the tick's committed contacts.

| Source | Predicate | Type | Initial `strength / valence / trust` |
|---|---|---|---|
| Co-location | ≥ `colocation_threshold` (default 6) joint place-ticks within 30 sim-days at a place with `capacity ≤ 40`, both awake | `acquaintance` | 0.05 / 0.00 / 0.50 |
| Conversation | Any exchanged turn pair in a conversation | `acquaintance` (or strengthen) | 0.10 / 0.05 / 0.50 |
| Direct message | First DM answered | `acquaintance` | 0.08 / 0.05 / 0.50 |
| Shared employer | `5010 HIRED` into a firm with existing staff | `colleague` | 0.15 / 0.00 / 0.55 |
| Shared school cohort | Concurrent `enrolments` at the same `school_id` | `acquaintance` | 0.12 / 0.05 / 0.55 |
| Shared household | `15010 HOUSEHOLD_FORMED` / joined | `kin` if related else `friend` | 0.40 / 0.30 / 0.70 |
| Shared institution | Same party, same court case as co-parties, same council | `acquaintance` | 0.10 / 0.05 / 0.50 |
| Loan | `8010 LOAN_ORIGINATED` | `creditor` | 0.20 / 0.00 / credit score |
| `BEFRIEND` accepted | Target's next-tick `BEFRIEND` back, or a `SAY` accepting | `friend` | 0.35 / 0.25 / 0.60 |
| Platform | Mutual `follows` sustained 14 sim-days | `acquaintance` | 0.06 / 0.00 / 0.45 |

`BEFRIEND` params: `{target_id, message}`. It is an offer, not a fiat: it creates or
strengthens an `acquaintance` immediately and upgrades to `friend` only on reciprocation
within `befriend_window` (default 14 sim-days).

### 2.3 Dynamics

Applied once per tick per live tie, in `stable()` order by `(a_id, b_id, type)`:

```
Δs_interaction = Σ over this tick's interactions:  ι(kind) · attention_or_weight
s' = clip( s + Δs_interaction - δ_s(type) · Δticks_since_last_interaction , 0, 1 )
     where δ_s(type) = ln 2 / (tie_halflife_sim_days[type] · ticks_per_sim_day)
     and δ_s = 0 for kin and partner

v' = clip( v + Σ ν(kind) · w , -1, 1 )
τ' = clip( τ + Δτ , 0, 1 )
```

| Interaction | `ι` (strength) | `ν` (valence) | `Δτ` (trust) |
|---|---|---|---|
| Conversation turn exchanged | +0.030 | +0.005 | 0 |
| DM exchanged | +0.020 | +0.005 | 0 |
| Co-location tick | +0.002 | 0 | 0 |
| Agreement expressed on a held proposition | +0.010 | +0.030 | +0.010 |
| Disagreement expressed | +0.010 | −0.025 | −0.005 |
| Gift / transfer received | +0.040 | +0.080 | +0.030 |
| Hired by / hired | +0.060 | +0.050 | +0.020 |
| Fired by | 0 | −0.250 | −0.120 |
| **Promise kept** (loan payment on time, delivery made, wage paid, settlement honoured) | +0.010 | +0.030 | **+0.060** |
| **Promise broken** (missed payment with capacity to pay, non-delivery, wage arrears) | 0 | −0.150 | **−0.220** |
| Victim of the other's crime (once detected) | 0 | −0.600 | −0.500 |
| Court adversary | 0 | −0.200 | −0.100 |
| Testimony against | 0 | −0.300 | −0.200 |

**Trust tracks realised, log-checkable behaviour.** That is the entire point: it makes the
trust index (§10.2) a measurement of the society rather than a restatement of its opinions.

**Type transitions**, evaluated after the update:

```
acquaintance → friend   iff  s ≥ 0.40 and v ≥ 0.20
friend → rival          iff  v ≤ -0.40
rival → acquaintance    iff  v ≥ 0.10 sustained 30 sim-days
colleague → acquaintance on employment end, s ← 0.6·s
any → ended             iff  s < 0.02 and type ∉ {kin, partner, creditor}
```

### 2.4 Kinds 10040–10059

| Kind | Name | Payload |
|---|---|---|
| 10040 | `TIE_FORMED` | `a_id, b_id, type, context (colocation\|conversation\|dm\|employer\|school\|household\|institution\|loan\|befriend\|platform), strength, valence, trust` |
| 10041 | `TIE_UPDATED` | `a_id, b_id, type, d_strength, d_valence, d_trust, drivers[{kind, weight}]` |
| 10042 | `TIE_ENDED` | `a_id, b_id, type, reason (decay\|death\|emigration\|conflict\|dissolution\|employment_end)`, `final_strength` |
| 10043 | `TIE_TYPE_CHANGED` | `a_id, b_id, from_type, to_type, trigger` |
| 10050 | `NETWORK_SNAPSHOT` | `n_nodes, n_edges, mean_degree, degree_gini, powerlaw_alpha, powerlaw_ks, clustering_global, clustering_avg_local, assortativity_degree, assortativity_wealth, assortativity_belief, assortativity_district, modularity, n_communities, largest_component_share, n_components` |

`10041 TIE_UPDATED` is written only when `|d_strength| + |d_valence| + |d_trust| ≥
tie_event_threshold` (default 0.02); sub-threshold drift is folded into the projection
without an event. Without this the graph would emit ~10⁵ events/tick.

### 2.5 Homophily

**Homophily is not imposed.** There is no formation bias on trait or belief similarity by
default (`society.homophily_bias: 0.0`). Observed homophily must therefore arise from:

- **spatial sorting** — districts differ in `land_value_cents`, so wealth sorts by rent
  affordability, so co-location sorts by wealth (`05-WORLD-SPEC.md`);
- **institutional sorting** — firms, schools, and parties concentrate similar people;
- **platform sorting** — the follow graph and the feed algorithm (§3), which is precisely
  research question B1.

**MECHANISM `graph_homophily: off`** —
`entails: "with homophily_bias = 0 the graph imposes no similarity preference, so measured assortativity is attributable to space, institutions, and the platform. Setting homophily_bias > 0 multiplies BEFRIEND acceptance and acquaintance→friend upgrade probability by exp(β · sim(i,j)) and thereby entails positive belief assortativity; any B1 result must be reported with the value used."`

### 2.6 Network statistics are reported, never perceived

**No agent's `Observation` ever contains a network statistic.** Degree, clustering,
centrality, and community membership are researcher-facing metrics computed in PHASE 7 and
written to `metrics` and `10050`. An agent knows who it knows; it does not know it is a
bridge. Violating this would let agents optimise a network position they have no realistic
way to observe, and would make every structural finding circular.

Community detection uses seeded Louvain over a node ordering sorted by `agent_id`, with
`seed = rng.get("metrics.louvain", "", tick).getrandbits(32)`. Power-law exponent by MLE with
a KS goodness-of-fit statistic; both are reported, and `powerlaw_ks` above 0.10 means the
degree distribution is not power-law and must not be described as scale-free.

---

## 3. Social media platform

`polis/society/media/platform.py` and `media/feed.py`. Tables: `posts`, `follows`,
`engagements` (`03-DATA-MODEL.md §8`).

### 3.1 Objects and actions

| Action | Params | Effect |
|---|---|---|
| `POST` | `{text, topic, stance_proposition, stance_value, claims[], in_reply_to: po_… \| null}` | New `posts` row. `in_reply_to` set ⇒ it is a reply; there is no separate reply action |
| `REPOST` | `{repost_of: po_…, comment: str \| null}` | New `posts` row with `repost_of` set; `text` is the comment or empty |
| `LIKE` | `{post_id}` | `engagements` row, type `like` |
| `COMMENT` | `{post_id, text, claims[]}` | Sugar for `POST{in_reply_to}` + `engagements` row type `comment`; kept because it is in the closed enum |
| `FOLLOW` / `UNFOLLOW` | `{followee_id}` | `follows` row opened/closed |
| `RETRACT` | `{post_id \| article_id, correction_text}` | §4.8 |

**Posting is LLM-only.** The reflex action set (`04-AGENT-SPEC.md §8`) excludes every media
action. Every post, repost, like, follow, and reply in the city is a deliberate choice by a
model. This is load-bearing for T9: whatever cascade dynamics appear are not a transmission
constant.

`claims[]` is populated by the same deliberate call that writes the post
(`04-AGENT-SPEC.md §9.2` output, extended with the action's own param schema), which is what
makes `posts.truthfulness` computable without a second LLM purpose (§4.5).

### 3.2 The feed: candidate pool

Built in PHASE 1 as part of perception, for every agent including reflex agents. Pure
function of last tick's committed state.

```
W          = society.feed_window_sim_hours converted to ticks
F_i        = {followees of i with follows.ended_tick IS NULL}
InNet_i(t) = {p : p.tick ∈ (t-W, t], author(p) ∈ F_i or (p.repost_of ≠ null and author(p) ∈ F_i)}
Out_i(t)   = {p : p.tick ∈ (t-W, t], author(p) ∉ F_i, author(p) alive, p.author ≠ i}

C_i(t) = InNet_i(t) ∪ sample(Out_i(t))        # sample only for engagement / adversarial
```

`C_i(t)` is truncated to `feed_candidate_cap` (300) by descending `(p.tick, hash(post_id))`,
then sorted by `post_id` before ranking so that ranking is order-independent. Out-of-network
candidates are drawn with `rng.get("feed.pool", agent_id, tick)` and capped so that
out-of-network posts may occupy at most `feed_out_of_network_quota` (0.30) of the 15 slots.

`chronological` and `random` do not use `Out_i` in the same way: `chronological` is
in-network only; `random` draws its pool from the whole city (§3.3).

Deduplication: at most one item per `root_post_id` per feed. Self-authored posts are
excluded. Already-engaged posts are excluded for `chronological`; for the other three they
are down-weighted by `repeat_penalty` (0.4) rather than excluded, because repeat exposure is
part of what the algorithms do.

### 3.3 The four ranking functions

This is **the single most important research lever in the system** (B1). All four take the
same candidate pool and return exactly `feed_slice = 15` items (`04-AGENT-SPEC.md §5`). The
feed is a pure deterministic function of committed state plus the seeded RNG, which is why
it need not be fully logged (§3.6).

Shared features, all computed from committed state, all in `[0,1]` unless noted:

```
rec(p)      = exp(-ln2 · (t - p.tick) / halflife_ticks)          halflife = 12 sim-hours
pop(p)      = log1p(engagements(p)) / log1p(pop_norm)             pop_norm = 200
tie(i,p)    = relationships.strength(i, author(p))  (0 if none)
aff(i,p)    = 0.5·tie(i,p) + 0.5·past_engagement_rate(i, author(p))
inf(p)      = log1p(followers(author(p))) / log1p(follower_norm)
cong(i,p)   = clip( b_i(prop_p) · p.stance_value , -1, 1 )        # +1 total agreement, -1 total opposition
ext(p)      = |p.stance_value|
dis(i,p)    = max(0, -cong(i,p))
agr(i,p)    = max(0,  cong(i,p))
conf(i,p)   = beliefs.confidence(i, prop_p)
```

#### `chronological`

```
score_chrono(i, p) = p.tick            (ties broken by post_id ascending)
pool               = InNet_i only, no out-of-network injection
```

| | |
|---|---|
| **Optimises** | Nothing. It is a pure recency ordering over the follow graph. |
| **Expected to do** | Exposure ∝ the posting rate of the agent's followees. Reach is bounded by the follow graph and by explicit reposting, so cascades are shallow and slow. Cross-cutting exposure equals whatever homophily the follow graph already contains — no more, no less. Popular posts get no amplification, so the reach distribution should be closer to log-normal than power-law. |
| **Role** | The **null arm** for B1. Any effect of the other three is measured against this. |

#### `engagement`

Optimises predicted engagement probability. The predictor is **fitted online to the
simulation's own realised engagements**, not hand-coded. This matters: if the coefficients
were set by hand, "the engagement feed polarises" would follow from the coefficients rather
than from the society (T6).

```
x(i,p) = [1, aff, tie, pop, rec, inf, ext, agr, dis, conf, repeat]
ê(i,p) = σ( β_t · x(i,p) )
score_engagement(i,p) = ê(i,p)
```

**Fitting.** `β` is updated once per sim-day in PHASE 7 by deterministic batch logistic
regression on the previous sim-day's `(agent, post)` impression records, label
`y = 1` iff the agent produced any of `like | repost | comment` on that post:

```
batch  = all (i,p) impressions of the previous sim-day, sorted by (tick, agent_id, post_id)
β ← β - η · Σ (σ(β·x) - y) · x / |batch|          η = 0.05, 20 passes, β rounded to 6 dp
β ← (n0·β_prior + n·β_fitted) / (n0 + n)          n0 = 5000 pseudo-observations
```

`β_prior` is the cold-start prior in config and decays in influence as impressions
accumulate. Fitting is deterministic (fixed order, fixed passes, fixed rounding), so replay
reproduces it exactly. `β` is written to `metrics` every sim-day; **the fitted coefficients
are themselves a research output** — whether outrage predicts engagement in an LLM society
becomes an empirical result rather than an assumption.

**MECHANISM `feed_engagement_prior: outrage_positive`** —
`entails: "the cold-start prior gives positive weight to disagreement-extremity for the first ~5000 impressions, so early-run polarisation under the engagement arm is partly seeded by the prior. Any B1 claim must be measured on a window after n >> n0, and must be re-run with a zero prior (β_prior = 0) to confirm the effect survives."`

| | |
|---|---|
| **Optimises** | `P(any engagement | impression)`, using in-sim revealed preference. |
| **Expected to do** | Concentrate attention on high-`pop`, high-`aff`, high-`ext` content, producing a heavy-tailed reach distribution and deeper cascades than `chronological`. Whether it reduces cross-cutting exposure depends on whether `agr` or `dis` carries more fitted weight — which is exactly what B1 is asking. If `dis` dominates, the arm produces *more* cross-cutting exposure but more hostile exposure, which is the affective-polarisation pathway; if `agr` dominates, it produces echo chambers. The design refuses to prejudge which. |

#### `random`

```
pool                = all posts in (t-W, t] by living authors, city-wide (no follow filter)
score_random(i, p)  = rng.get("feed.random", agent_id, tick).random()
```

| | |
|---|---|
| **Optimises** | Nothing, and deliberately severs both the follow graph and the ranker. |
| **Expected to do** | Maximal cross-cutting exposure — the feed becomes an unbiased sample of the city's discourse, so exposure is proportional to posting volume alone. Cascades collapse: reach is decoupled from popularity, so there is no rich-get-richer. Belief distributions should be the least bimodal of the four arms if exposure drives polarisation, and indistinguishable from `chronological` if it does not. |
| **Role** | The **control arm** that isolates the algorithm from the network. Comparing `random` to `chronological` measures the follow graph's contribution; comparing `engagement` to `random` measures the ranker's. |

#### `adversarial`

Deliberately polarising. It performs one step of gradient ascent on the population's belief
dispersion, using the platform's own forward model of belief updating (§5.4).

```
Δ̂(i,p)  = predicted belief update for i on prop_p under §5.4 given one impression of p
d_before = | b_i(prop_p) - μ(prop_p) |             μ = population mean on that proposition
d_after  = | b_i(prop_p) + Δ̂(i,p) - μ(prop_p) |

score_adv(i,p) = ê(i,p)^γ · ( d_after - d_before )        γ = 0.5
```

Note what this does: because the §5.4 update includes the **backfire branch**, the ranker
learns that showing a confidently-held agent extreme opposing content from a low-trust
source pushes them *further from* the mean, and it will do so. Confirming content is shown
to the already-extreme; hostile content is shown to the confident. Content predicted to move
an agent *toward* the mean scores negative and is never shown. The `ê^γ` factor exists
because content nobody looks at cannot polarise anyone.

**MECHANISM `feed_adversarial: polarisation_ascent`** —
`entails: "this arm maximises predicted divergence from the population mean by construction, so it necessarily increases the dispersion of any proposition the belief model is capable of moving. It is an upper bound on algorithmic harm, not a hypothesis. It is never used as evidence that feeds polarise; it calibrates how much polarisation the belief model can produce at all, which bounds the interpretation of the engagement arm."`

| | |
|---|---|
| **Optimises** | Predicted increase in `|b_i − μ|`, gated by predicted engagement. |
| **Expected to do** | Drive the bimodality coefficient toward its ceiling within a few sim-months; collapse cross-cutting *persuasive* exposure while raising cross-cutting *hostile* exposure; drive inter-cluster tie valence sharply negative. If it does not, the belief model is too rigid and §5 needs recalibration — which is the arm's diagnostic value. |

### 3.4 Reach, impressions, and virality

```
impressions(p) = |{(i, t) : p ∈ feed_i(t)}|                # agent-tick pairs
reach(p)       = |{i     : ∃t, p ∈ feed_i(t)}|             # unique agents → posts.reach
engagement_rate(p) = |engagements(p) of type like|repost|comment| / impressions(p)
```

**Cascades.** A cascade is the tree rooted at a post with `repost_of = null`, edges from
`repost_of`. Recorded per cascade at close (no repost for `cascade_idle_ticks`, default 24):

```
depth(p)               = max path length from root
breadth(p)             = max out-degree at any level
size(p)                = node count
structural_virality(p) = mean pairwise shortest-path distance over cascade nodes   (Wiener index / C(n,2))
```

`structural_virality` distinguishes *broadcast* (one influential author, depth 1, high
breadth) from *diffusion* (many short hops, high depth) — the same total reach can be either,
and they behave differently under intervention.

**There is no transmission probability.** A repost happens because an agent chose `REPOST`
in deliberate mode. Reach is a consequence of ranker exposure plus agent choice, never a
draw from a contagion distribution. This is the anti-T6 property that makes any virality
finding meaningful.

### 3.5 Ledger contact

The platform has no revenue and no cost. It is infrastructure, not a firm. The only money
that touches it is campaign advertising (§6.4), which is a purchase from an **outlet**, not
from the platform. If a future version monetises the platform it must be a `fm_` firm with a
ledger account like any other.

### 3.6 Kinds 11000–11029

| Kind | Name | Payload |
|---|---|---|
| 11010 | `POST_PUBLISHED` | `post_id, author_id, text, topic, stance_proposition, stance_value, in_reply_to, repost_of, root_post_id, claims[], follower_count_at_post` |
| 11011 | `POST_DELETED` | `post_id, author_id, reason (retracted\|author_death\|moderation)` |
| 11012 | `REPOST_MADE` | `post_id, repost_of, root_post_id, author_id, original_author_id, cascade_depth, comment` |
| 11020 | `POST_ENGAGED` | `post_id, agent_id, type (like\|repost\|comment\|report), author_id` |
| 11021 | `FEED_SERVED` | `agent_id, algorithm, post_ids[≤15], scores[], candidate_pool_size, out_of_network_count, cross_cutting_count, mean_extremity` |
| 11022 | `CASCADE_CLOSED` | `root_post_id, size, depth, breadth, structural_virality, reach, impressions, unique_reposters, lifetime_ticks` |
| 11040 | `FOLLOW_CREATED` | `follower_id, followee_id, context (feed\|colocation\|article\|campaign)` |
| 11041 | `FOLLOW_ENDED` | `follower_id, followee_id, reason (unfollow\|death\|emigration)` |

`11021 FEED_SERVED` is written under the **cognition-event sampling policy**
(`02-ARCHITECTURE.md §3.3`): always for agents routed `DELIBERATE` or `REFLECT`, and for a
seeded `cognition_sample_rate` of reflex agents. This is safe because the feed is a pure
deterministic function of committed state and the seeded RNG, so `polis rebuild`
(`03-DATA-MODEL.md §12`) recomputes every unlogged feed exactly. `engagements` rows of type
`view` are written for all agents from the recomputed feed; only the *event* is sampled.

---

## 4. News and journalism

`polis/society/media/news.py`, `media/checker.py`. Tables: `outlets`, `articles`
(`03-DATA-MODEL.md §8`). The news cycle is a **PHASE 7** scheduled step, cadence
`society.news_cycle` (default daily).

### 4.1 Outlets

`outlets(outlet_id ol_…, name, firm_id, slant ∈ [−1,1], rigour ∈ [0,1], reach)`.

`society.outlets` (default 4) are created at genesis with `slant` drawn on a spread of width
`outlet_slant_dispersion` and `rigour ~ U(0.35, 0.9)`. Agents found new outlets through the
ordinary economy: `FOUND_COMPANY{sector: "media"}` creates the `fm_`, and the firm's first
`POST_VACANCY` for occupation `reporter` registers the `ol_` and emits `11050`.

An outlet is a firm. It pays wages, pays rent, can be distressed, and can go bankrupt
through the normal path (`06-ECONOMY-SPEC.md`, kinds 9030). Newsroom staff are ordinary
employed agents: one `editor` (highest `writing` skill at the firm, ties broken by
`agent_id`) and *n* `reporter`s.

### 4.2 What a reporter can see

Reporters are **not omniscient**. Perception rule 4 (`04-AGENT-SPEC.md §5`) applies: if a
reporter can see it, a spec says so. An event *e* is available to reporter *r* iff any of:

| Channel | Condition |
|---|---|
| **Public record** | `e.kind ∈ PUBLIC_KINDS` — trades and OHLCV, IPOs, bankruptcy filings, election results, policy enactments, judgments, vacancies posted, births and deaths, published posts and articles, `POLICY_RATE_SET` |
| **Witness** | `r` holds a memory whose `source_event_seq = e.seq` (was present, was addressed, was told) |
| **Source** | Some agent *s* holds such a memory, `relationships(r, s).type ∈ {friend, colleague}` with `trust ≥ 0.5`, and `r` sent `s` a `DIRECT_MESSAGE` within `source_window` (14 sim-days) that `s` answered |
| **Document** | `e` is a `ledger_entries` row on an account the outlet's firm is a counterparty to |

The source channel is what makes investigative journalism real work: a scoop requires a
cultivated tie, a DM, and an answer, each costing action slots. `11035 SOURCE_CULTIVATED`
records it.

### 4.3 Newsworthiness and the story list

At each news cycle, per outlet, over available events since the last cycle:

```
N(e) = w_mag·magnitude(e) + w_prom·prominence(subjects(e)) + w_nov·novelty(e)
     + w_conf·conflict(e) + w_prox·proximity(e, outlet.district) + w_slant·align(e, outlet.slant)

magnitude   = normalised |amount_cents| or headcount or vote margin, by kind
prominence  = max over subjects of (followers + office + wealth percentile), normalised
novelty     = 1 - (count of same-kind, same-subject events in the last 30 sim-days)/n_norm
conflict    = 1 if the event has an adversarial pair (crime, suit, firing, election, default)
proximity   = 1 - district_distance/max_distance
align       = agreement between the event's implied stance and outlet.slant, in [0,1]
```

Top `stories_per_reporter_per_cycle × n_reporters` by `N(e)`, ties broken by `seq`, are
assigned to reporters by `stable()` order.

### 4.4 Writing

LLM purpose `NEWS_WRITE` (`02-ARCHITECTURE.md §8` routing table). Prompt receives: the source
events rendered as structured facts, the outlet's editorial line **rendered as narrative,
never as a number** (`04-AGENT-SPEC.md §9.1`), the reporter's own retrieved memories and
beliefs, and the outlet's rigour rendered as a sourcing standard. Output schema:

```json
{
  "headline": "string",
  "body": "string",
  "claims": [
    {"claim_id": "clm_…",
     "text": "as written in the body",
     "refers_to": {"entity_id": "fm_acme", "predicate": "firm.solvent", "value": false,
                   "as_of_tick": 41200},
     "sourced_to_event_seqs": [812441, 812502]}
  ],
  "confidence": 0.0
}
```

**Requiring explicit structured claims alongside the prose is the whole trick.** It is what
turns "misinformation" from a label into a measurement, and it costs nothing extra — it is
one more field in a call the outlet was making anyway.

### 4.5 The claim-checking procedure

`polis/society/media/checker.py`. Runs in PHASE 7 after publication, over article `claims[]`,
post `claims[]`, and utterance `claims[]`. **The event log is ground truth.**

```
for each claim k:
  1. RESOLVE. Parse (entity_id, predicate, value, as_of_tick).
     predicate must be in RESOLVERS. Unresolvable → verdict = unverifiable, excluded.
  2. GROUND TRUTH. truth = RESOLVERS[predicate](entity_id, as_of_tick, run)
     Every resolver is a PURE function over the committed log and its projections at
     as_of_tick. It may not read state after as_of_tick — a claim is judged against what
     was true when it was made, not against hindsight.
  3. COMPARE.
       categorical : supported iff claimed == truth, else contradicted
       boolean     : as categorical
       numeric     : rel = |claimed - truth| / max(|truth|, floor)
                     rel ≤ tol            → supported        (tol = claim_tolerance = 0.10)
                     tol < rel ≤ 3·tol    → imprecise
                     rel > 3·tol          → contradicted
       existential : supported iff a matching event exists in [as_of_tick - w, as_of_tick]
                     contradicted iff no such event AND the log is complete for that kind
                     (i.e. the kind is one that always emits when the fact obtains)
  4. SCORE.  supported = 1.0, imprecise = 0.5, contradicted = 0.0, unverifiable = excluded
  5. AGGREGATE.
       accuracy(article)     = Σ score / |verifiable claims|,  NULL if none
       truthfulness(post)    = same formula over the post's claims, NULL if none
  6. EMIT 11034 CLAIM_CHECKED per claim, with matched_event_seqs.
```

Starter `RESOLVERS` registry (closed, extended only by spec change):

| `predicate` | Ground truth source |
|---|---|
| `firm.solvent` | `firms.status ∉ {bankrupt, dissolved}` at `as_of_tick` |
| `firm.headcount` | count of `employments` open at `as_of_tick` |
| `firm.profitable` | last closed period net income sign |
| `agent.employed` | `employments` open at `as_of_tick` |
| `agent.convicted` | `∃ 13040` with `verdict = guilty` and `defendant_id = entity` |
| `agent.wealth_percentile` | `agents.wealth_cents` rank at `as_of_tick` |
| `agent.holds_office` | office register at `as_of_tick` |
| `crime.occurred` | `∃ 13010` matching `(type, perpetrator, victim)` in window |
| `price.close` | `ohlcv.close_cents` for the session containing `as_of_tick` |
| `policy.value` | `polis.config.runtime.get(param, as_of_tick)` |
| `election.winner` | `elections.winner_id` |
| `election.turnout` | `elections.turnout` |
| `macro.unemployment` | `metrics` series at `as_of_tick` |
| `macro.cpi` | `metrics` series at `as_of_tick` |
| `outlet.retracted` | `∃ 11033` for that outlet/article |

Three rules that keep this honest:

1. **The checker never blocks and never penalises.** It only measures. Nothing is deleted,
   demoted, or flagged to any agent.
2. **No agent is ever told its truthfulness or accuracy score**, and no score enters any
   `Observation`. If it did, the model would optimise the metric and the measurement would
   die.
3. **Omission audit.** A post that carries no claims gets `truthfulness = NULL`, which an
   agent could exploit by never annotating. A seeded `society.misinfo_audit_rate` (0.05) of
   claim-free posts is passed through the cheap `IMPORTANCE`-purpose model with a
   claim-extraction template — reusing an existing routing purpose, adding none — to
   estimate the share of unannotated checkable claims. That rate is reported per run as
   `claim_annotation_coverage`. It corrects the denominator; it never changes a post.

### 4.6 Editorial process

The editor gate runs after writing, before publication. Deterministic, with an optional
`NEWS_WRITE`-purpose rewrite pass.

| Gate | Test | Outcome |
|---|---|---|
| **Sourcing** | share of claims with empty `sourced_to_event_seqs` > `(1 − rigour)` | Return for one rewrite; on second failure `11031 ARTICLE_SPIKED{reason: thin_sourcing}` |
| **Line** | `|align(article, outlet.slant)| < line_threshold` (0.25) | Return for one rewrite; then spike, `reason: slant_mismatch` |
| **Legal** | a claim asserts `agent.convicted` or `crime.occurred` naming an agent with no supporting event, and `regulation.media.disclosure_required` is on | Spike, `reason: legal_risk`. If published anyway (rigour < 0.3 outlets skip this gate), the named agent may `FILE_SUIT` for defamation (§8.11) |
| **Budget** | reporter's outlet is `distressed` and the story requires source cultivation | Spike, `reason: budget` |

`slant_applied` is **measured, not injected**: after publication it is computed as the mean
signed deviation of the article's claim values from ground truth on stance-relevant
predicates, signed toward the outlet's slant direction. Slant enters as narrative in the
prompt and leaves as a number in the data. That separation is what allows "does a slanted
outlet produce less accurate copy" to be asked rather than assumed.

### 4.7 Distribution and reach

Articles fill the `Observation.news` slot, capped at 3 (`04-AGENT-SPEC.md §5`).

```
S(i, a) = w_t · trust_i(outlet(a))
        + w_m · topic_match(i, a)
        + w_p · proximity(i.district, a.subject_districts)
        + w_s · [i subscribes to outlet(a)]
        + w_r · outlet_reach_capacity(outlet(a))

trust_i(ol)             = beliefs[i, 'trust.outlet.<ol_id>'].value, default 0.5
topic_match(i,a)        = max over a's propositions of confidence_i(prop) · |b_i(prop)|
outlet_reach_capacity   = clip(log1p(outlet.reach)/log1p(reach_norm), 0.1, 1)
```

Top 3 by `S`, ties broken by `rng.get("media.impression", agent_id, tick)`. `articles.reach`
is the count of agents in whose `news` slot the article appeared at least once, and
`11032 ARTICLE_DISTRIBUTED` records the district breakdown.

### 4.8 Retraction and correction

`RETRACT{article_id | post_id, correction_text}`. Available to the outlet's editor for its
own articles and to any author for their own posts. Emits `11033`, sets
`articles.retracted_tick`, and distributes a correction item at
`correction_reach_multiplier` (default 0.6) of the original's reach — deliberately less than
the original, because that asymmetry is the thing B2 exists to quantify. Correction efficacy
is defined in §10.3.

### 4.9 Business model

Outlet revenue is booked at the outlet fiscal close (PHASE 7, weekly):

```
ad_revenue_cents     = round(impressions_week / 1000 · cpm_cents)          # from identified advertisers
subscription_cents   = subscribers · subscription_price_cents
campaign_cents       = Σ CAMPAIGN spend directed at this outlet (§6.4)
```

`ad_revenue_cents` is **allocated from real advertisers**: firms allocate
`ad_budget_share` of the previous period's revenue to advertising in PHASE 7
(`06-ECONOMY-SPEC.md` owns the firm-side decision), and that budget is split across outlets
in proportion to `outlet.reach`. Government public notices are a line in the government
budget. Each is a balanced ledger transaction with a named counterparty. If total advertiser
budget is zero, outlet ad revenue is zero — outlets can and should fail.

Outlet costs are ordinary: reporter and editor wages (labour, `06`), newsroom rent. Failure
runs through `9030 BANKRUPTCY_FILED` and emits `11052 OUTLET_CLOSED`. **Media concentration
is therefore an economic outcome, not a parameter** — a run in which three of four outlets
fail and the survivor's slant dominates the information environment is a legitimate,
measurable result about the economics of attention.

### 4.10 Kinds 11030–11069

| Kind | Name | Payload |
|---|---|---|
| 11030 | `ARTICLE_PUBLISHED` | `article_id, outlet_id, reporter_id, headline, body_hash, source_event_seqs[], claims[], slant_at_write, rigour_at_write, llm_call_id` |
| 11031 | `ARTICLE_SPIKED` | `draft_id, outlet_id, reporter_id, editor_id, reason (thin_sourcing\|slant_mismatch\|legal_risk\|budget), rewrite_attempts` |
| 11032 | `ARTICLE_DISTRIBUTED` | `article_id, reach, impressions, district_shares{}, subscriber_share` |
| 11033 | `ARTICLE_RETRACTED` | `article_id \| post_id, outlet_id, author_id, reason, correction_text, original_reach, correction_reach` |
| 11034 | `CLAIM_CHECKED` | `subject_kind (article\|post\|speech), subject_id, claim_id, predicate, entity_id, claimed_value, truth_value, verdict (supported\|imprecise\|contradicted\|unverifiable), matched_event_seqs[], score` |
| 11035 | `SOURCE_CULTIVATED` | `reporter_id, source_id, outlet_id, message_id, subject_event_seqs[]` |
| 11050 | `OUTLET_FOUNDED` | `outlet_id, firm_id, founder_id, slant, rigour, place_id` |
| 11051 | `OUTLET_REVENUE_BOOKED` | `outlet_id, period_start_tick, impressions, cpm_cents, ad_revenue_cents, subscription_cents, campaign_cents, advertisers[], txn_ids[]` |
| 11052 | `OUTLET_CLOSED` | `outlet_id, firm_id, reason, final_reach, staff_ids[]` |

Researcher-injected falsehoods do **not** get their own kind. They arrive as
`99001 SHOCK_INJECTED{kind: "falsehood", target_proposition, value, carrier}` and cause an
ordinary `11010 POST_PUBLISHED` or `11030 ARTICLE_PUBLISHED` whose `cause_seq` points at the
injection. The checker treats them like any other item, which is exactly what makes injected
and organic falsehoods comparable (B2).

---

## 5. Belief dynamics

`polis/society/beliefs.py`. Table: `beliefs` (`03-DATA-MODEL.md §2.4`), primary key
`(run_id, agent_id, proposition)`, `value NUMERIC(5,4)`, `confidence NUMERIC(5,4)`,
`source ∈ {inherited, experience, social, media, reflection}`.

### 5.1 Proposition vocabulary

`PROPOSITION_REGISTRY` in `beliefs.py`. Three classes with different value ranges. Policy
and trust propositions are a **closed list**; factual propositions are **templated** over
entity ids and validated by template plus existence of the entity.

**Policy stances — `value ∈ [−1, +1]`** (−1 = strongly against, +1 = strongly for). Each maps
to at least one policy parameter in §7.2, which is what closes the loop from opinion to law.

```
policy.tax.progressivity          policy.tax.rate_should_rise
policy.tax.corporate_should_rise  policy.tax.inheritance_should_rise
policy.welfare.generosity         policy.welfare.conditionality
policy.money.should_tighten       policy.money.independence
policy.education.spend            policy.education.compulsory_longer
policy.police.budget              policy.sentencing.severity
policy.labour.min_wage_should_rise policy.labour.protection
policy.regulation.finance         policy.regulation.media
policy.housing.rent_control       policy.migration.openness
policy.market.free_vs_managed     policy.debt.austerity
```

**Factual credences — `value ∈ [0, 1]`** (subjective probability the claim is true). Templated:

```
fact.firm.<fm_id>.solvent          fact.firm.<fm_id>.fraudulent
fact.agent.<ag_id>.corrupt         fact.agent.<ag_id>.competent
fact.market.<symbol>.overvalued    fact.economy.recession_now
fact.economy.prices_rising         fact.economy.jobs_scarce
fact.crime.rising                  fact.election.<el_id>.rigged
fact.outlet.<ol_id>.fabricates     fact.policy.<param>.caused_harm
fact.party.<pt_id>.corrupt
```

**Trust — `value ∈ [0, 1]`**:

```
trust.generalised                  trust.institution.court
trust.institution.police           trust.institution.government
trust.institution.bank             trust.institution.market
trust.institution.press            trust.outlet.<ol_id>
trust.party.<pt_id>                trust.agent.<ag_id>
```

`trust.generalised` is the "most people can be trusted" item and is the headline input to
the trust index (§10.2). `trust.agent.<ag_id>` is kept in `beliefs` only for agents outside
the holder's `relationships`; inside it, `relationships.trust` is authoritative and the
belief row is a projection of it.

Population initialisation at genesis: each policy proposition drawn from a mixture of two
Gaussians with configurable separation (default: unimodal, separation 0) so the run does not
start pre-polarised. Starting polarised would answer B1 by assumption.

### 5.2 The four channels

| Channel | `source` value | Fires when | May write |
|---|---|---|---|
| **Inherited** | `inherited` | Once, at birth (§9.6) | Policy stances + `trust.generalised` only |
| **Direct experience** | `experience` | An event with a direct observational referent lands in the agent's memory | **Factual credences only** |
| **Social contact** | `social` | An annotated `SAY`/`BROADCAST`/`MESSAGE` is heard, or an annotated post is impressed | Policy stances, factual credences, trust |
| **Media** | `media` | An article enters `Observation.news`, or a post from a followed outlet account is impressed | Policy stances, factual credences, trust |
| *(LLM-authored)* | `reflection` | `belief_updates[]` in any `DELIBERATE` or `REFLECT` output (`04-AGENT-SPEC.md §9.2`, §10) | Anything in the registry |

> **Normative rule.** The **direct-experience channel may never update a policy stance.**
> Being fired may update `fact.economy.jobs_scarce`; it may **not** mechanically move
> `policy.welfare.generosity`. If it did, research question B4 — does economic precarity
> cause political radicalisation — would be answered by the update rule rather than by the
> society. Experience reaches politics only through memory, retrieval, and the LLM's own
> `belief_updates`. This rule is worth more than any other line in this section.

Experience → factual mapping (closed table, extended only by spec change):

| Triggering event | Proposition | Target |
|---|---|---|
| Own `5011 FIRED`, or employer `9030 BANKRUPTCY_FILED` | `fact.firm.<employer>.solvent` | 0.0 |
| Observed missed payroll | `fact.firm.<fm>.solvent` | 0.15 |
| Own unemployment spell > 30 sim-days | `fact.economy.jobs_scarce` | 0.85 |
| Own basket price up > 5% over 30 sim-days | `fact.economy.prices_rising` | 0.90 |
| Victim of a detected crime | `fact.crime.rising` | +0.25 toward 1 |
| Defrauded by `ag_x` (detected) | `fact.agent.<x>.corrupt` | 0.95 |
| Own loan denied while solvent | `fact.institution.bank` trust | −0.15 |
| Court ruled against, with contradicting log evidence | `trust.institution.court` | −0.20 |

### 5.3 Source trust weighting

```
τ(i, src, channel) =
    channel = social : relationships.trust(i, src)  if a tie exists, else 0.35
    channel = media  : b_i('trust.outlet.<ol>')                       default 0.5
                       × (1 - b_i('fact.outlet.<ol>.fabricates'))
    channel = experience : 1.0                       (own senses)
    channel = inherited  : 1.0
```

### 5.4 The update rule

Applied in PHASE 5 slot 2 for social, PHASE 7 for media (after the news cycle), in
`stable()` order by `(agent_id, proposition, source_id)`.

```python
def update(agent, prop, target, channel, source_id):
    b, c = current(agent, prop)                 # value, confidence; defaults from registry
    τ    = trust(agent, source_id, channel)
    α    = ALPHA[channel]                       # experience .35, social .10, media .08
    λ    = 1 - c                                # confident beliefs are stickier
    d    = abs(target - b)

    entrenched = (channel in ("social", "media")
                  and d > θ_backfire            # 0.60
                  and c > θ_entrench            # 0.60
                  and τ < θ_trust)              # 0.40

    if entrenched:
        Δb = -β_backfire * (1 - τ) * sign(target - b) * min(d, 1.0)      # β = 0.05
        Δc = +δ_entrench                                                  # 0.03
        adjust_trust(agent, source_id, -η_trust)                          # 0.04
    else:
        Δb = α * τ * λ * (target - b)
        Δc = +γ_c * τ * (1 - d)                                           # γ_c = 0.02

    b = clip(b + Δb, *range_of(prop))
    c = clip(c + Δc, 0.0, 1.0)
    write(agent, prop, b, c, source=channel, source_ref=source_id, tick=tick)
```

Two mechanisms, both declared:

**MECHANISM `belief_social_influence: bounded_confidence`** —
`entails: "exposure to an annotated stance moves the receiver toward it in proportion to source trust and inversely to own confidence. Consensus formation within trusting clusters and separation between distrusting ones follow analytically. Therefore no B1 claim may rest on this rule alone: every headline B1 effect must be reproduced under --social-influence-off, where belief change is LLM-authored only, and the effect size under that ablation is the reported result."`

**MECHANISM `belief_backfire: on`** —
`entails: "cross-cutting exposure from a distrusted source to a confidently-held opposing belief moves the receiver AWAY from the source. Any finding that cross-cutting exposure increases polarisation is therefore partly entailed. Ablate with --backfire-off; the sign and magnitude of the cross-cutting effect must be reported under both settings."`

`ALPHA[experience] > ALPHA[social] > ALPHA[media]` is itself a substantive assumption
(direct evidence beats a friend beats a newspaper) and the three values are swept.

### 5.5 Validating LLM-authored belief updates

`belief_updates[]` arrives from `04-AGENT-SPEC.md §9.2` (deliberate) and §10 (reflect).
Gates, in order; each failure increments a counter reported per run.

| # | Gate | On failure |
|---|---|---|
| 1 | Proposition resolves against `PROPOSITION_REGISTRY` (templates expanded, entity must exist and be in-run) | Drop. `belief_update_unknown_proposition_rate` |
| 2 | `value` within the proposition class's range | Clamp. `belief_update_clamped_rate` |
| 3 | `confidence ∈ [0,1]` | Clamp |
| 4 | At most `max_belief_updates_per_call` (default 5) | Drop the excess in listed order. `belief_update_truncated_rate` |
| 5 | `|Δvalue| ≤ max_step` (default 0.35) | Clamp the step, keep the direction. Prevents one call flipping a lifetime stance |
| 6 | Not a `trust.outlet.<X>` update whose `source_ref` is an article from outlet X | Apply at half weight, flag `self_serving` |
| 7 | Sorted by `proposition` before application | — (determinism) |

Applied updates are written with `source = 'reflection'` (the enum in `03-DATA-MODEL.md §2.4`
has no `deliberate` value) and `source_ref = 'llm_call:<call_id>'`, so LLM-authored updates
remain separable from mechanical ones in analysis by joining `llm_calls.purpose`.

### 5.6 Trust tracks accuracy

Once per sim-week, per agent, per outlet the agent was exposed to:

```
realised_accuracy(i, ol) = mean(articles.accuracy) over articles of ol that entered i's news slot
                            and whose claims have since been checked
Δ b_i('trust.outlet.<ol>') = α_acc · (realised_accuracy - b_i('trust.outlet.<ol>'))     α_acc = 0.05
```

Only over articles the agent actually saw, and only after checking is possible. **Whether
trust converges on accuracy is then a measurable property of the run**, not an assumption —
it will not converge if agents mostly see outlets they already trust, which is precisely the
feedback the feed algorithm controls.

### 5.7 Measuring polarisation formally

For a policy proposition *p* over the living adult population, `x = {b_i(p)}`, `n = |x|`:

```
Bimodality coefficient
    BC(p) = (g² + 1) / ( κ + 3(n-1)² / ((n-2)(n-3)) )
    g = sample skewness, κ = sample excess kurtosis
    BC > 5/9 ≈ 0.5556  ⇒  the distribution is more bimodal than a uniform; report BC, not a verdict.
    Reported alongside Hartigan's dip statistic D and its p-value; a bimodality claim
    requires BC > 5/9 AND dip p < 0.05.

Cross-cutting exposure rate  (per agent, over a window W)
    CCE_i = ( Σ_{p ∈ impressions_i(W)} 1[ sign(p.stance_value) ≠ sign(b_i(prop_p)) ] · |p.stance_value| )
            / ( Σ_{p ∈ impressions_i(W)} |p.stance_value| )
    CCE   = mean_i CCE_i, over agents with ≥ 5 annotated impressions.
    Reported separately as CCE_persuasive (source trust ≥ 0.5) and CCE_hostile (< 0.5),
    because the adversarial arm raises the second while lowering the first.

Dispersion         σ²(p) = Var(x)
Distance-to-mean   D(p)  = mean_i |b_i(p) - mean(x)|
Affective polarisation
    AP = mean valence of ties to out-cluster - mean valence of ties to in-cluster,
         clusters from the Louvain partition of the belief-similarity graph (§2.6)
Time-to-consensus  first tick at which σ²(p) < consensus_floor (0.02) for 30 sim-days
```

### 5.8 Kinds 10060–10069 (declared deviation D-1)

| Kind | Name | Payload |
|---|---|---|
| 10060 | `BELIEF_UPDATED` | `agent_id, proposition, old_value, new_value, old_confidence, new_confidence, channel, source_id, source_ref, entrenched (bool), llm_call_id` |
| 10061 | `BELIEF_DRIFT_APPLIED` | `agent_id, channel, updates[{proposition, d_value, d_confidence}], n_sources` |
| 10062 | `BELIEF_UPDATE_REJECTED` | `agent_id, proposition, raw_value, gate (unknown\|range\|count\|step\|self_serving), llm_call_id` |
| 10063 | `BELIEF_PRIORS_SET` | `agent_id, source (genesis\|birth\|migration), propositions[{proposition, value, confidence}]` |

`10060` is written in full for the `experience` and `reflection` channels. High-volume
`social` and `media` nudges are aggregated per agent per tick into a single `10061`.

---

## 6. Politics

`polis/society/polity.py`. Resolves 8th in PHASE 5. Election day, council sessions, and
policy review are PHASE 7 scheduled steps. Tables: `parties`, `elections`, `candidacies`,
`votes` (`03-DATA-MODEL.md §8`).

### 6.1 Parties

Founded by agents via `FOUND_PARTY` (requested addition D-2). No parties exist at genesis —
whether a party system emerges at all is research question B3, and seeding one would answer
it by fiat.

| Rule | Detail |
|---|---|
| Founding | `FOUND_PARTY{name, platform: {proposition: stance}, founding_member_ids}`. Capability: age ≥ 18, ≥ 3 founding members who each submit `JOIN_PARTY` in the same or next tick, registration fee to government (`transfer`) |
| Platform | `parties.platform JSONB`, `{proposition: stance}` over the closed policy vocabulary (§5.1). At most `max_platform_planks` (default 8) |
| Membership | `JOIN_PARTY{party_id}` / `PARTY_LEFT` on `JOIN_PARTY` to another. One party at a time |
| Leader | The party's highest office-holder; else the member with the most votes received across all elections; ties by `agent_id` |
| Dissolution | Membership < 3 for 30 sim-days, or no candidacy for two consecutive election cycles → `12005 PARTY_DISSOLVED` |

**Platform drift.** Recomputed each sim-quarter in PHASE 7:

```
platform[p] ← (1 - ρ) · platform[p] + ρ · trimmed_mean_10%( {b_m(p) : m ∈ members} )   ρ = 0.25
```

**MECHANISM `party_platform_drift: member_mean`** —
`entails: "party platforms converge on their members' trimmed mean stance, so inter-party platform distance tracks inter-cluster belief distance by construction. Party polarisation is therefore NOT independent evidence of mass polarisation. Ablate with party_platform_drift: fixed (platform frozen at founding), under which platform-voter divergence becomes observable."`

### 6.2 Offices

| Office | Seats | Term | Selection | Powers |
|---|---|---|---|---|
| `president` | 1 | 4 sim-years, max 2 terms | City-wide election, `polity.election_method` | Proposes policy; appoints judges and police chief subject to council confirmation; veto (overridden by 5/7 council) |
| `council` | 7 | 2 sim-years | 6 district seats + 1 at-large, same method | Votes policy (simple majority); confirms appointments; may remove the central bank governor by 5/7 supermajority |
| `judge` | 2 | 6 sim-years | Appointed by president, confirmed by council. Requires `agent_skills.law ≥ 0.6` | Renders judgments (§8.8) |
| `police_chief` | 1 | Serves at pleasure | Appointed by president | Allocates the police budget across districts |
| `cb_governor` | 1 | 6 sim-years | Appointed by president, confirmed by council; removable only by 5/7 | **Sole** setter of `money.policy_rate` (§7.2) |

Succession: on death, incarceration, or emigration of the president, the council member with
the most votes at the last election assumes the office for the remainder of the term
(`12024` then `12023`). Vacant council seats are filled at the next scheduled election.

Holding office is an ordinary employment with a salary paid by government
(`wage`, `06-ECONOMY-SPEC.md`), which makes office capture economically motivated and makes
the government payroll a real fiscal line.

### 6.3 Candidacy

`ANNOUNCE_CANDIDACY{election_id, party_id | null, platform}`. Gates: age ≥ 18, alive, not
incarcerated, `criminal_record` below `polity.candidacy_record_bar` (default: no live
`guilty` verdict for `fraud`, `embezzlement`, or `perjury`), and a deposit of
`candidacy_deposit_cents` posted to government (`transfer`). The deposit is refunded if the
candidate takes ≥ `deposit_refund_share` (0.05) of the vote. Independent candidacy
(`party_id: null`) is legal and carries the candidate's own platform.

### 6.4 Campaigning

`CAMPAIGN{candidacy_id, amount_cents, channel, target}` where
`channel ∈ {ads, rally, canvass}`. **Money buys reach and the transaction is real.**

| Channel | Ledger | Reach |
|---|---|---|
| `ads` | debit candidate cash, credit `target` outlet's firm cash, `purchase` | `reach = min(outlet.reach, round(amount_cents / cpm_cents · 1000 · outlet_efficiency))` agents drawn top-down by `S(i, outlet)` from §4.7 |
| `rally` | debit candidate cash, credit venue owner, `rent` | Audience = all agents at the place that tick; a `BROADCAST` is emitted as the rally's content |
| `canvass` | none | Candidate and party members spend action slots on `DIRECT_MESSAGE`; reach = messages sent |

Exposure is recorded per exposed agent inside the `12012` payload (`reached_agent_ids`,
capped and sorted) rather than as one event per exposure. Exposure feeds the `media` term of
the vote model (§6.5) and decays with `exposure_halflife_sim_days` (default 14).

Campaign finance limits are a **policy parameter** (`polity.campaign_cap_cents`, §7.2), which
means the society can vote on how much money is allowed in its own politics. That is a
genuinely interesting endogenous loop and it costs nothing to allow.

### 6.5 The vote model

Voting happens on election day, a PHASE 7 step. Eligibility: alive, age ≥ 18, not
incarcerated (unless `polity.felon_franchise` policy flag is on), resident ≥ 90 sim-days.

Election day is a `MANDATORY` scheduled obligation, so eligible agents are force-routed
`DELIBERATE` before budget allocation (`04-AGENT-SPEC.md §7` step 2). At 1,000 agents and
`llm.budget.calls_per_tick = 90`, this is impossible for the whole electorate even with
`llm_election_multiplier: 6.0`. The design confronts that directly rather than pretending.

**Deliberate voters** (as many as the boosted budget allows, ranked by salience) cast a
`VOTE{election_id, candidacy_id | ranking[] | approvals[]}` action chosen by the LLM.

**Reflex voters** cast via a random-utility model whose coefficients are **fitted to the
deliberate voters' revealed choices in the same election**:

```
U_i(c) = ω_b·congruence_i(c) + ω_s·self_interest_i(c) + ω_n·social_i(c)
       + ω_m·media_i(c) + ω_p·party_id_i(c) + ω_inc·incumbency(c) + ε_ic

congruence_i(c)   = 1 - Σ_p conf_i(p)·|b_i(p) - platform_c(p)| / (2·Σ_p conf_i(p))
                    over p ∈ union of all candidate platforms
self_interest_i(c)= normalised first-order Δ in i's own annual disposable income under c's
                    platform, computed by applying c's proposed parameter values to i's
                    current income statement (wage, benefits, taxes, rent, debt service).
                    Deterministic, auditable, and stored in the 12020 payload.
social_i(c)       = Σ_{j ∈ ties(i)} strength(i,j)·valence(i,j)·expressed_stance_j(c)
                    / Σ strength, where expressed_stance_j(c) is derived ONLY from j's
                    observable posts and speech — never from j's actual vote.
media_i(c)        = Σ_{a ∈ exposures_i(campaign window)} trust_i(src(a))·align(a, c)·decay(Δt)
party_id_i(c)     = 1 if i ∈ members(party(c)); ρ (0.3) if formerly; 0 otherwise
incumbency(c)     = 1 if c holds the office
ε_ic              ~ Gumbel(0, σ) via rng.get("polity.vote", voter_id, tick)   ⇒ conditional logit

choice = argmax_c U_i(c)   if max_c U_i(c) ≥ abstain_threshold_i else ABSTAIN
abstain_threshold_i = θ_0 - θ_c·conscientiousness_i - θ_a·(civic engagement proxy)
```

**MECHANISM `vote_model: fitted_from_deliberate`** —
`entails: "reflex voters are an extrapolation of the deliberate voters in the same election, fitted by multinomial logit on the six utility terms with the deliberate choices as labels. This makes the reflex electorate a projection of LLM behaviour rather than an independent hard-coded theory of voting, but it also means reflex voters cannot exhibit a preference structure absent from the deliberate sample. Reported per election: n_deliberate, n_reflex, fitted ω vector, log-likelihood, and holdout accuracy on a 20% split of the deliberate voters. If holdout accuracy is below 0.5 above chance, the reflex vote is not usable and the election must be re-run with a larger LLM share. For the first election of a run, ω comes from the config prior, and that election is excluded from B-track analysis."`

Every vote — deliberate or reflex — writes its utility components into the `12020` payload.
The vote is fully auditable: you can always ask why an agent voted as it did, and for
deliberate voters compare the LLM's `reasoning` against the model's decomposition.

### 6.6 Election mechanics

| Method | Rule | Winner |
|---|---|---|
| `plurality` | One choice per voter | Highest count; tie broken by `rng.get("polity.vote", election_id, tick)` and logged as a coin-flip |
| `approval` | `approvals[]`, any subset | Most approvals |
| `irv` | `ranking[]` | Eliminate lowest, redistribute, until majority; every round recorded in `12022.rounds` |
| `proportional` | Party list, council only | D'Hondt over party vote shares; seats to each party's candidates by vote order |

`elections.turnout = votes_cast / eligible`. `elections.method` records the method used, so a
sweep over `polity.election_method` with everything else fixed is a clean institutional
experiment.

Cadence: `election_interval` expressed in sim-time (`02-ARCHITECTURE.md §5.2`). The election
is *called* `campaign_length` (default 30 sim-days) before `voting_tick`, which opens the
campaign window; candidacies close 7 sim-days before voting.

### 6.7 Kinds 12000–12029

| Kind | Name | Payload |
|---|---|---|
| 12001 | `PARTY_FOUNDED` | `party_id, founder_id, name, platform{}, founding_member_ids[], fee_cents, txn_id` |
| 12002 | `PARTY_JOINED` | `agent_id, party_id, alignment_score, prior_party_id` |
| 12003 | `PARTY_LEFT` | `agent_id, party_id, reason (switched\|expelled\|dissolved\|death)` |
| 12004 | `PARTY_PLATFORM_CHANGED` | `party_id, changes[{proposition, old, new}], driver (member_drift\|leader\|election_loss)` |
| 12005 | `PARTY_DISSOLVED` | `party_id, reason, final_membership, merged_into` |
| 12010 | `ELECTION_CALLED` | `election_id, office, seats, method, called_tick, voting_tick, campaign_ends_tick, electorate_size` |
| 12011 | `CANDIDACY_ANNOUNCED` | `candidacy_id, agent_id, election_id, party_id, platform{}, deposit_cents, txn_id` |
| 12012 | `CAMPAIGN_SPEND` | `candidacy_id, agent_id, amount_cents, channel, target_id, reached_agent_ids[], reach, txn_id` |
| 12020 | `VOTE_CAST` | `election_id, voter_id, candidacy_id \| ranking[] \| approvals[], origin (deliberate\|reflex), utility{congruence, self_interest, social, media, party_id, incumbency, epsilon}` |
| 12021 | `ABSTAINED` | `election_id, agent_id, reason (below_threshold\|incapacity\|absent\|ineligible), max_utility` |
| 12022 | `ELECTION_RESOLVED` | `election_id, method, tallies{}, winner_id(s), turnout, margin, rounds[], n_deliberate, n_reflex, fitted_omega{}, holdout_accuracy` |
| 12023 | `OFFICE_ASSUMED` | `office, agent_id, election_id \| appointment, term_start_tick, term_end_tick, salary_cents` |
| 12024 | `OFFICE_VACATED` | `office, agent_id, reason (term_end\|death\|resignation\|removal\|incarceration\|emigration), successor_id` |

---

## 7. Policy engine

**This is the critical loop.** An election that does not change a simulation parameter is
theatre, and a society whose politics cannot touch its economy is not worth simulating. This
section specifies how an enacted policy becomes a live change to the running configuration.

### 7.1 The runtime overlay

`polis/config/runtime.py` (requested addition D-4). A tick-keyed, append-only overlay on the
static YAML config.

```python
class RuntimeConfig:
    def get(self, parameter: str, tick: int) -> Any:
        """Value in force at `tick`. Static YAML value if no enactment precedes `tick`."""
    def enact(self, parameter: str, value: Any, effective_tick: int,
              policy_id: str, event_seq: int) -> None:
        """Append-only. Never mutates history. Called ONLY from the PHASE 7 policy step."""
```

**Normative rules:**

1. **Every institution reads policy-controllable parameters through
   `runtime.get(param, tick)`, never from the static config object.** An institution that
   caches a policy parameter across ticks is a bug. This single rule is what makes the loop
   closed; without it, the whole of §6 is decorative.
2. The overlay is a **projection of `12030 POLICY_ENACTED` events**, so `polis rebuild`
   (`03-DATA-MODEL.md §12`) reconstructs it exactly and `get` is a pure function.
3. `effective_tick > enacted_tick` always. Policy never applies retroactively; the lag is
   per-parameter (§7.2) and is itself a research object.
4. `society` writes the overlay; `economy` and `world` read it. Both may import `config`
   under `02-ARCHITECTURE.md §7.1`, so this creates no illegal dependency and no direct
   `society → economy` import.

### 7.2 The closed set of policy-controllable parameters

`POLICY_REGISTRY` in `polity.py`. **Closed.** A parameter not in this table cannot be changed
by any political process, and a `PROPOSE_POLICY` naming one is rejected at the capability
gate.

| Parameter | Type | Admissible range | Authority | Effect site | Lag |
|---|---|---|---|---|---|
| `tax.income.brackets` | `[(threshold_cents, rate)]` | rate ∈ [0, 0.75], monotone thresholds, ≤ 5 brackets | Council majority | Payroll withholding | 1 sim-month |
| `tax.corporate.rate` | float | [0, 0.60] | Council majority | Firm fiscal close | 1 sim-quarter |
| `tax.capital_gains.rate` | float | [0, 0.60] | Council majority | Exchange settlement | 1 sim-month |
| `tax.inheritance.rate` | float | [0, 0.90] | Council + president | Estate settlement (`04 §12.3`) | 1 sim-month |
| `tax.vat.rate` | float | [0, 0.35] | Council majority | Goods purchase | 1 sim-week |
| `money.policy_rate` | float | [−0.02, 0.25] | **`cb_governor` only** | Banking | 1 sim-week |
| `welfare.unemployment_benefit_cents` | int | [0, 2× median wage] | Council majority | PHASE 7 transfers | 1 sim-month |
| `welfare.benefit_duration_ticks` | int | [0, 2 sim-years] | Council majority | PHASE 7 transfers | 1 sim-month |
| `welfare.pension_cents` | int | [0, 2× median wage] | Council majority | PHASE 7 transfers | 1 sim-quarter |
| `welfare.child_benefit_cents` | int | [0, median wage] | Council majority | §9.5, §9.3 | 1 sim-month |
| `education.spend_cents_per_student` | int | ≥ 0, within budget | Council majority | `schools.quality` | 1 sim-term |
| `education.compulsory_until_age` | int | [10, 22] | Council majority | `agents.education_level` gating | 1 sim-term |
| `police.budget_cents` | int | ≥ 0, within budget | Council majority | §8.4 detection | 1 sim-month |
| `courts.budget_cents` | int | ≥ 0, within budget | Council majority | §8.7 throughput | 1 sim-month |
| `courts.loser_pays` | bool | — | Council majority | §8.11 | 1 sim-month |
| `prison.capacity` | int | ≥ 0, within budget | Council majority | §8.9 | 1 sim-quarter |
| `sentencing.multiplier` | float | [0.25, 4.0] | Council + president | §8.8 statutory ranges | Next judgment |
| `labour.minimum_wage_cents` | int | [0, 3× median wage] | Council majority | Labour market | 1 sim-month |
| `labour.max_hours_per_sim_week` | int | [20, 80] | Council majority | `WORK` validation | 1 sim-month |
| `regulation.finance.margin_allowed` | bool | — | Council majority | Exchange validation | 1 sim-week |
| `regulation.finance.short_selling_allowed` | bool | — | Council majority | `SHORT` validation | 1 sim-week |
| `regulation.finance.insider_trading_enforced` | bool | — | Council majority | §8.3 legality gate | Immediate |
| `regulation.labour.at_will_dismissal` | bool | — | Council majority | `FIRE_EMPLOYEE` validation | 1 sim-month |
| `regulation.media.disclosure_required` | bool | — | Council majority | §4.6 legal gate | 1 sim-week |
| `regulation.housing.rent_cap_pct` | float \| null | [0.0, 1.0] or null | Council majority | `places.rent_cents` | 1 sim-quarter |
| `migration.quota_per_sim_year` | int | [0, 0.2 × population] | Council majority | §9.7 | 1 sim-quarter |
| `polity.campaign_cap_cents` | int \| null | ≥ 0 or null | Council + president | §6.4 validation | Next election |
| `polity.felon_franchise` | bool | — | Council majority | §6.5 eligibility | Next election |
| `government.debt_ceiling_cents` | int | ≥ 0 | Council + president | §7.5 | Immediate |
| `society.feed_algorithm` | enum | the four of §3.3 | Council + president | §3.3 | 1 sim-week |

The last row is **off by default** (`polity.can_regulate_feed: false`). When enabled, the
society can legislate its own information environment, and whether it does — and whether
that changes anything — is a first-class experiment. When disabled, the parameter is absent
from `POLICY_REGISTRY` and a proposal naming it is rejected.

### 7.3 From proposal to enactment

```
1. PROPOSE_POLICY{parameter, new_value, rationale}          PHASE 5 slot 8
   Capability gate (PHASE 4): proposer holds council seat or presidency,
     OR the proposal carries ≥ initiative_signatures (default 50) co-signing agents
     collected via DIRECT_MESSAGE in the preceding 30 sim-days.
   → 12025 POLICY_PROPOSED

2. ADMISSIBILITY (PHASE 7, policy review, weekly)
   a. parameter ∈ POLICY_REGISTRY (and not gated off)                → else 12033 blocked
   b. type and range check                                            → else 12033 blocked
   c. authority: proposer's office satisfies the Authority column     → else 12033 blocked
   d. INVARIANT PRE-CHECK (§7.4)                                      → else 12033 blocked
   e. BUDGET IDENTITY (§7.5)                                          → else 12033 blocked

3. VOTE (PHASE 7, council session, weekly)
   Council members cast VOTE{motion_id, aye|nay|abstain}. Deliberate members decide by LLM;
   members below the salience cutoff vote by the §6.5 utility model restricted to the
   proposal's own propositions.
   → 12027 POLICY_VOTED{proposal_id, yeas, nays, abstentions, passed, margin}
   Presidential veto where the Authority column requires it; override at 5/7.

4. ENACT
   → 12030 POLICY_ENACTED{policy_id, parameter, old_value, new_value, effective_tick,
                          enacted_by, vote_margin, proposal_seq}
   → INSERT into policies(policy_id, parameter, old_value, new_value, enacted_tick,
                          enacted_by, vote_margin)
   → UPDATE the prior live row for that parameter: repealed_tick = enacted_tick
   → runtime.enact(parameter, new_value, effective_tick, policy_id, event_seq)

5. FAILURE → 12026 POLICY_REJECTED{proposal_id, yeas, nays}
```

Repeal is not a separate mechanism: enacting a parameter's previous value is a repeal, and
the `policies` row chain records it. `12032 POLICY_REPEALED` is emitted only for the
explicit case where a proposal's `new_value` equals the value in force before the most recent
enactment of that parameter, so that the intent is visible in analysis.

### 7.4 Policy cannot violate invariants

Before a vote is scheduled, the proposal is evaluated against a set of **hard admissibility
predicates**. Failure emits `12033 POLICY_BLOCKED` with the predicate id and the proposal
never reaches a vote. This is not censorship of ideas; it is the same class of check as the
resource gate on an individual action.

| Predicate | Statement |
|---|---|
| `P-RANGE` | The value lies in the parameter's admissible range |
| `P-MONEY` | The change cannot create or destroy money outside the issuance account. No policy may credit an account without a matching debit — this is `INV-MONEY` restated at the legislative level |
| `P-SOLVENCY` | Projected government balance over the next sim-year, holding all else fixed, stays above `−government.debt_ceiling_cents` |
| `P-NONNEGATIVE` | No parameter that indexes a count or a price may go negative |
| `P-MONOTONE` | Tax brackets remain monotone in threshold; benefit tapers remain monotone |
| `P-SCOPE` | The parameter is not in `run:`, `llm:`, `clock:`, `mechanisms:`, `ablations:`, `population:`, or `world:`. **Agents cannot legislate the simulation's own machinery.** |
| `P-SEPARATION` | `money.policy_rate` is not being set by anyone other than `cb_governor`. The council's only lever on monetary policy is removing the governor — which makes central bank independence a variable, not an assumption |

A blocked proposal is still visible to everyone next tick, so agents learn what is
constitutionally impossible, which is itself a thing to observe.

### 7.5 Fiscal identity

Government revenue and spending are ordinary ledger flows. Any proposal that raises spending
or cuts revenue must satisfy `P-SOLVENCY` against the projection

```
projected_balance(T) = current_balance
                     + Σ_{t<T} (projected_tax_revenue(t) - projected_outlays(t))
```

computed by applying the proposed parameter values to the **current** population and firm
distribution, held fixed (a static-scoring rule — deliberately simple and deliberately
stated, because dynamic scoring would embed a macro theory). If the projection breaches the
debt ceiling, the proposal is blocked and the council must raise the ceiling first, which is
itself a policy requiring a vote.

**MECHANISM `fiscal_scoring: static`** —
`entails: "proposals are scored against a frozen population, so behavioural responses to tax and benefit changes are never anticipated by the legislature. This makes fiscal policy systematically mis-calibrated in the direction of ignoring Laffer and labour-supply effects. Any A4 result about policy transmission must note that the enacting body did not anticipate the response it caused."`

### 7.6 Why the change is measurable

Because enactment is an event with a tick, a `cause_seq`, an old value, and a new value:

- **Impulse response.** Metric series are aligned to `effective_tick` and windowed; the
  pre-period is the counterfactual baseline (`10-RESEARCH-AND-OBSERVABILITY.md`).
- **Seed-matched counterfactual.** `policies.old_value` gives the exact config diff needed to
  re-run the same seed without the policy — the cleanest counterfactual any economist has
  ever had, and the direct answer to research question A4.
- **Attribution.** `12030.enacted_by` and `12027` tallies link the change to the coalition
  that produced it, so "who benefited from the policy they passed" is a query, not a study.

### 7.7 Kinds 12030–12049

| Kind | Name | Payload |
|---|---|---|
| 12025 | `POLICY_PROPOSED` | `proposal_id, proposer_id, parameter, old_value, proposed_value, rationale, cosigners[]` |
| 12026 | `POLICY_REJECTED` | `proposal_id, yeas, nays, abstentions` |
| 12027 | `POLICY_VOTED` | `proposal_id, chamber, votes[{agent_id, choice, origin}], yeas, nays, abstentions, passed, margin` |
| 12028 | `POLICY_VETOED` | `proposal_id, president_id, overridden (bool), override_margin` |
| 12030 | `POLICY_ENACTED` | `policy_id, parameter, old_value, new_value, effective_tick, enacted_by, vote_margin, proposal_seq` |
| 12032 | `POLICY_REPEALED` | `policy_id, parameter, restored_value, repealed_policy_id` |
| 12033 | `POLICY_BLOCKED` | `proposal_id, predicate (P-RANGE\|P-MONEY\|P-SOLVENCY\|P-NONNEGATIVE\|P-MONOTONE\|P-SCOPE\|P-SEPARATION), detail` |
| 12034 | `BUDGET_SET` | `period_start_tick, revenue_projection_cents, outlay_projection_cents, allocations{police, courts, education, welfare, prisons, public_notices}, debt_cents` |
| 12040 | `APPOINTMENT_MADE` | `office, appointee_id, appointed_by, confirmed (bool), confirm_margin` |
| 12041 | `OFFICER_REMOVED` | `office, agent_id, removed_by, margin, reason` |

---

## 8. Law and crime

`polis/society/law.py`. Flagging happens at the PHASE 4 legality gate; resolution is 9th in
PHASE 5; court sessions are a PHASE 7 scheduled step. Tables: `crimes`, `court_cases`
(`03-DATA-MODEL.md §8`).

### 8.1 The taxonomy

| `crimes.type` | Arises from | Victim | `amount_cents` | Statutory fine range (× `sentencing.multiplier`) | Custodial range |
|---|---|---|---|---|---|
| `theft` | Explicit `COMMIT_CRIME{type: theft, target_id, amount_cents}` at co-location | Agent or firm | Amount taken | 1×–3× amount | 0–90 sim-days |
| `assault` | Explicit `COMMIT_CRIME{type: assault, target_id}` at co-location | Agent | 0 | 5k–50k | 30–365 sim-days |
| `fraud` | **Derived**: a claim in a `PITCH`, loan application, `SET_PRICE`, or listing that the checker (§4.5) scores `contradicted`, where a counterparty relied on it | Counterparty | Value induced | 2×–5× amount | 90–1080 sim-days |
| `insider_trading` | **Derived**: `SUBMIT_ORDER` in symbol S while holding MNPI on S (§8.3) | Market (no named victim) | Profit realised | 3×–10× profit | 0–720 sim-days |
| `embezzlement` | **Derived**: a transfer from a firm account by an agent with firm authority to an account they control, outside payroll/dividend | Firm | Amount | 2×–5× amount | 180–1440 sim-days |
| `contract_breach` | **Derived**: non-performance of a logged obligation (loan payment, delivery, wage) while holding sufficient funds | Counterparty | Amount owed | 1×–2× amount | none (civil) |
| `perjury` | **Derived**: `TESTIFY` whose statement is scored `contradicted` by the checker on a matter the witness had first-hand memory of | Court | 0 | 10k–100k | 30–540 sim-days |

`contract_breach` is prosecuted civilly by default; `courts.loser_pays` and the plaintiff's
election determine the track.

### 8.2 The legality gate flags, it does not block

Per `04-AGENT-SPEC.md §11`, legality is the fifth and final validation gate and **does not
reject**. It emits `13001 LEGALITY_FLAGGED`, inserts a `crimes` row with `detected = false`,
and lets the action proceed to PHASE 5.

Two flagging paths, and the second is the more important one:

| Path | Description |
|---|---|
| **Explicit** | The agent chose `COMMIT_CRIME`. Covers `theft` and `assault`. Requires the model to knowingly select a criminal action |
| **Derived** | An ordinary action (`SUBMIT_ORDER`, `PITCH`, `APPLY_FOR_LOAN`, a transfer, `TESTIFY`) meets a deterministic predicate that makes it an offence. Covers `fraud`, `insider_trading`, `embezzlement`, `contract_breach`, `perjury` |

The derived path exists because a safety-trained model may decline to select an action
labelled `COMMIT_CRIME` (§11, failure mode F2) but will readily buy a stock it has private
information about, or default on a loan it could pay. **Five of the seven offence types
require no criminal intent to be expressed in text.** This is the structural mitigation that
keeps B5 answerable regardless of model refusal behaviour, and it is why the taxonomy is
weighted toward white-collar offences.

### 8.3 Material non-public information, defined deterministically

```
MNPI(i, S, t) ⟺ ∃ event e :
      e.subject_ids ∋ issuer(S)
  and e.kind ∈ MNPI_KINDS          # 9010 ROUND_CLOSED, 9030 BANKRUPTCY_FILED, earnings,
                                   # 6xxx production shocks, 5011 mass layoffs, M&A
  and i holds a memory with source_event_seq = e.seq
  and no public disclosure of e exists at t (no 11010/11030 citing e.seq,
      no PUBLIC_KINDS event carrying it)
  and t - e.tick ≤ mnpi_window     # default 14 sim-days

insider_trading flagged ⟺ MNPI(i, S, t) and action is SUBMIT_ORDER/SHORT in S
                          and regulation.finance.insider_trading_enforced
```

No LLM, no judgement call, fully replayable. Note the dependence on
`regulation.finance.insider_trading_enforced` — a policy parameter (§7.2), so the society can
legalise insider trading and the effect is directly measurable.

### 8.4 Detection

Detection is a per-tick hazard over a `detection_window` (default 180 sim-days), not a
one-shot draw at commission. That lag is essential: a fraud that is discovered eighteen
sim-months later is how bubbles end.

```
capacity_d(t)  = runtime.get("police.budget_cents", t) · district_share_d
                 / (population_d · cost_per_patrol_cents)

p_detect(c, t) = clip( base[c.type]
                     · capacity_d(t) ^ η                       # η = 0.6
                     · (1 + witness_bonus(c))                  # 0.4 per co-located non-accomplice, capped 1.2
                     · victim_awareness[c.type]                # theft 0.95, fraud 0.30, insider 0.05, embezzlement 0.15
                     · (1 - concealment(perpetrator)) , 0, 0.98 )

concealment(i) = clip(0.10 + 0.45·max(skill.law_i, skill.finance_i) + 0.15·(1 - honesty_i), 0, 0.85)

detected iff rng.get("law.detect", crime_id, tick).random() < p_detect(c, t) / window_ticks
```

`district_share_d` is set by the `police_chief` (an agent decision, one allocation per
sim-month), which makes discriminatory or self-serving policing possible and measurable.

**MECHANISM `crime_detection: budget_scaled`** —
`entails: "detection probability rises monotonically with police.budget_cents. Therefore the observation that 'more police means more crimes detected' is definitional and is NOT a finding. The studiable quantity for B5 is the elasticity of the COMMITTED crime rate — the count of 13010 events, detected or not — with respect to p_detect, which operates only through agents' own decisions. Every B5 result must be stated over committed crimes; any result stated over detected crimes is rejected by the reviewer checklist."`

The distinction between committed and detected crime is the single most important
measurement discipline in this section, and the simulation is one of the very few settings
where both numbers exist.

### 8.5 Reporting

| Route | Trigger |
|---|---|
| Automatic | `theft` and `assault` against a living agent: the victim knows immediately, and a `13012` is emitted with `reporter_id = victim` unless the victim chooses otherwise next tick |
| `REPORT_CRIME{crime_id \| description, suspect_id, evidence_event_seqs[]}` | Any agent who holds a memory of the crime. Deliberate action only |
| Audit | Detected via the §8.4 hazard with `detector = audit`, for derived offences with low `victim_awareness` |
| Whistleblower | An employee reporting their own firm. `altruism` and `trust.institution.*` condition the LLM's choice; there is no mechanical whistleblowing rule |

Reporting is not automatic for derived offences. **The gap between the committed rate and
the reported rate is a dark figure the simulation can actually measure**, which no real
criminology dataset can.

### 8.6 Investigation and arrest

Police capacity in PHASE 7 (daily): `investigation_slots = floor(police.budget_cents /
cost_per_investigation_cents)`. Open cases are queued by
`severity = w_amt·log1p(amount_cents) + w_type·type_weight + w_age·case_age`, ties by
`crime_id`.

```
evidence(c)          = events e where e.subject_ids ∩ {perpetrator, victim} ≠ ∅
                       and e.tick ∈ [crime.tick - w, crime.tick + w]
                       and e is admissible under §8.7
evidence_strength(c) = clip( Σ_e directness(e.kind) · corroboration(e) / strength_norm , 0, 1 )
    directness: ledger entry 1.0, testimony 0.6, co-location 0.3, inference 0.1
    corroboration: 1 + 0.2 × (number of independent events supporting the same fact)

charge iff evidence_strength ≥ charge_threshold (default 0.45)
       → 13015 ARREST_MADE → 13020 SUIT_FILED{type: criminal}
else   → 13014 INVESTIGATION_CLOSED{outcome: unsolved}
```

### 8.7 Court: filing, counsel, evidence

Court sessions run in PHASE 7 at `polity.court_session` cadence. Throughput is
`cases_per_session = floor(courts.budget_cents / cost_per_case_cents)` — an underfunded court
produces a backlog, and backlog length is a reported metric.

**Filing.** `FILE_SUIT{type, defendant_id, cause_of_action, claim_cents, evidence_event_seqs[]}`.
Criminal cases are filed by the prosecutor (the `police_chief`'s office) after arrest; civil
cases by any agent. Filing fee to government (`transfer`); waived below a wealth percentile.

**Counsel.** `RETAIN_COUNSEL{case_id, counsel_id, fee_cents}`.

| Rule | Detail |
|---|---|
| Capability | `agent_skills.law ≥ 0.5`, alive, not incarcerated, not a party to the case |
| Fee | Negotiated in the action; ledger legs client → lawyer, `purchase`. Lawyers set their own price and can price themselves out of the market |
| Public defender | A criminal defendant below `legal_aid_wealth_pct` (default 25th percentile) is assigned the available lawyer with the lowest fee; the government pays (`purchase`, from `courts.budget_cents`) |
| Effect | Counsel quality determines how much of the admissible record reaches the judge: `n_surfaced = round(base_evidence + k · skill.law_counsel)`, `base = 3`, `k = 8` |

**Admissibility.** An event `seq` is admissible iff its `subject_ids` include a party **and**
one of: (a) it is a `ledger_entries`-backed event, (b) a testifying witness holds a memory
with that `source_event_seq`, (c) it is a published post or article, (d) it is in
`PUBLIC_KINDS`. Everything else is excluded. Admitted and excluded counts are recorded in
`13022` — an evidentiary regime the researcher can vary.

**Testimony.** `TESTIFY{case_id, statement, claims[]}`. The claims are run through the §4.5
checker; `consistency_score` is the resulting accuracy, and a `contradicted` claim on a
matter the witness had first-hand memory of flags `perjury` at the legality gate. Lying under
oath is therefore mechanically detectable and mechanically prosecutable.

### 8.8 The judgment

LLM purpose `JUDGE` (`02-ARCHITECTURE.md §8`, temperature 0.2). The judge is an agent holding
the office (§6.2), with their own beliefs, ties, and memories — which is what makes judicial
bias measurable rather than assumed away.

Prompt: the admitted evidence rendered as a structured record, the charge, the statutory
range in force (after `sentencing.multiplier`), the counsel submissions, and the judge's own
retrieved memories. **Closed output schema:**

```json
{
  "verdict": "guilty | not_guilty | liable | not_liable | dismissed",
  "findings": ["short factual findings, each citing admitted event seqs"],
  "penalty": {
    "fine_cents": 0,
    "sentence_ticks": 0,
    "damages_cents": 0,
    "restitution_cents": 0,
    "disqualification_ticks": 0
  },
  "reasoning": "string"
}
```

Constraints applied after parsing, before effect:

| Constraint | Action on breach |
|---|---|
| `verdict` in the enum, and matching the case type (criminal → guilty/not_guilty/dismissed; civil → liable/not_liable/dismissed) | Repair loop (2 attempts), then bench rule |
| `fine_cents`, `sentence_ticks` within the statutory range × `sentencing.multiplier` | **Clamp**, record the clamp in `13040.payload.clamped[]` |
| `damages_cents ≤ claim_cents` | Clamp |
| `restitution_cents ≤ amount_cents` of the crime | Clamp |
| Zero penalty on a guilty/liable verdict | Legal; recorded as `nominal` |
| Findings cite only admitted seqs | Uncited findings dropped, counted |

**Bench-rule fallback** on LLM failure (`02-ARCHITECTURE.md §10`):

```
verdict = guilty/liable iff evidence_strength ≥ conviction_threshold (0.60) else not_guilty
penalty = range_low + (range_high - range_low) · (evidence_strength - threshold)/(1 - threshold)
```

**MECHANISM `bench_rule: evidence_threshold`** —
`entails: "when the JUDGE call fails, conviction is a monotone function of evidence_strength alone, with no consideration of the defendant's identity. The bench-rule share of judgments is reported per run; any finding about judicial bias must be computed over LLM-decided judgments only, and the bench-rule share is the ceiling on how much of the docket that excludes."`

Because judges are agents, these are queries rather than studies: correlation of verdict with
defendant wealth percentile, with shared party membership, with `relationships.strength`
between judge and party, with defendant district.

### 8.9 Penalties, ledger legs, and incarceration

| Penalty | Ledger |
|---|---|
| Fine | debit convict cash → credit government cash, `fine` |
| Damages | debit defendant cash → credit plaintiff cash, `transfer` |
| Restitution | debit convict cash → credit victim cash, `transfer` |
| Loser-pays costs (if on) | debit loser cash → credit winner cash, `transfer` |
| Insufficient funds | Shortfall becomes a government (or plaintiff) receivable; the convict's future income is garnished at `garnishment_rate` (0.20) until satisfied. **Never** written off silently — `INV-MONEY` must hold |

**Incarceration** (`13043`): the agent moves to a `prison` place (D-3), for
`sentence_ticks × sentencing.multiplier`, subject to `prison.capacity` — over capacity, the
sentence converts to a fine at `fine_per_tick_cents` and the conversion is logged.

| Effect | Rule |
|---|---|
| Action set | Restricted to `IDLE, SLEEP, EAT, SAY, STUDY, NULL_ACTION`. No labour, media, market, polity, or law actions |
| Employment | Terminated: `5011 FIRED{reason: incarceration}`; the firm gets a vacancy |
| Obligations | Rent, loans, and child costs continue to accrue. Default risk rises sharply — the debt consequences of a custodial sentence are real and are one of the more interesting things this layer produces |
| Skills | Decay at 2× the `04-AGENT-SPEC.md §3` rate |
| Ties | Decay at 2×; `partner` ties do not end automatically |
| Franchise | Ineligible to vote or stand unless `polity.felon_franchise` |
| Record | `agents.criminal_record += 1` |
| On release | `13044`; agent returns to their household's home place, or to a state household if none |

**MECHANISM `ex_offender_wage_penalty: on`** —
`entails: "released agents receive wage offers multiplied by (1 - penalty · criminal_record), so lower post-release earnings and any resulting recidivism follow partly from this rule rather than from agent choice. Ablate with --no-record-penalty; recidivism must be reported under both."`

### 8.10 Deterrence as the object of study

Research question B5 is answered by a sweep, not by a single run.

```
Detection elasticity   ε_p = ∂ ln(committed crimes per capita) / ∂ ln(p̄_detect)
   swept over police.budget_cents, all else fixed, ≥ 20 seeds per cell

Severity elasticity    ε_s = ∂ ln(committed crimes per capita) / ∂ ln(sentencing.multiplier)
   swept over sentencing.multiplier

Displacement           the change in the SHARE of each crime type as p_detect rises;
                       a fall in total crime that is entirely a shift from high-detection
                       (theft) to low-detection (insider_trading, embezzlement) types is
                       displacement, not deterrence, and is reported as such

Dark figure            committed / reported  and  committed / convicted, per type
```

Deterrence requires that agents *know* the enforcement regime. They learn it from perception
of the news (arrests and judgments are `PUBLIC_KINDS`), from memories of victimisation, and
from their social graph — **not** from a privileged read of `p_detect`. No agent's
`Observation` ever contains the detection probability. If deterrence appears, it appears
because agents inferred the regime from observable enforcement.

### 8.11 Civil suits

`cause_of_action ∈ {contract_breach, negligence, fraud, defamation, wrongful_dismissal}`.
Filed by any agent; `defamation` is the hook that makes §4.6's legal gate bite. `SETTLE{case_id,
amount_cents}` before judgment ends the case with a `13030` and a `transfer` leg; either party
may offer, and acceptance is the other party's next-tick action. Judgment awards
`damages_cents ≤ claim_cents`. `courts.loser_pays` (a policy parameter) determines cost
shifting, which changes the incentive to file at all — a clean institutional experiment.

### 8.12 Kinds 13000–13999

| Kind | Name | Payload |
|---|---|---|
| 13001 | `LEGALITY_FLAGGED` | `action_id, actor_id, action_type, offence_type, path (explicit\|derived), predicate_id, crime_id` |
| 13010 | `CRIME_COMMITTED` | `crime_id, type, perpetrator_id, victim_id, amount_cents, place_id, district_id, source_action_id, concealment, detected (at commission)` |
| 13011 | `CRIME_DETECTED` | `crime_id, detector (patrol\|audit\|victim\|witness\|counterparty\|whistleblower), p_detect, ticks_since_commission` |
| 13012 | `CRIME_REPORTED` | `crime_id, reporter_id, latency_ticks, evidence_event_seqs[]` |
| 13013 | `INVESTIGATION_OPENED` | `case_file_id, crime_id, officer_id, severity, queue_position` |
| 13014 | `INVESTIGATION_CLOSED` | `case_file_id, outcome (charged\|unsolved\|no_crime), evidence_strength, evidence_event_seqs[]` |
| 13015 | `ARREST_MADE` | `crime_id, suspect_id, officer_id, place_id, evidence_strength` |
| 13020 | `SUIT_FILED` | `case_id, type, plaintiff_id, defendant_id, crime_id, cause_of_action, claim_cents, filing_fee_cents, txn_id` |
| 13021 | `COUNSEL_RETAINED` | `case_id, party_id, counsel_id, fee_cents, counsel_skill_law, public_defender (bool), txn_id` |
| 13022 | `EVIDENCE_ADMITTED` | `case_id, admitted_seqs[], excluded_seqs[], excluded_reasons[], evidence_strength, surfaced_by_counsel` |
| 13023 | `TESTIMONY_GIVEN` | `case_id, witness_id, statement, claims[], consistency_score, perjury_flagged` |
| 13030 | `CASE_SETTLED` | `case_id, amount_cents, offered_by, txn_id` |
| 13031 | `TRIAL_HELD` | `case_id, judge_id, session_tick, plaintiff_counsel_id, defence_counsel_id, evidence_strength` |
| 13040 | `JUDGMENT_RENDERED` | `case_id, judge_id, verdict, findings[], fine_cents, sentence_ticks, damages_cents, restitution_cents, disqualification_ticks, clamped[], origin (llm\|bench), llm_call_id` |
| 13041 | `FINE_LEVIED` | `case_id, payer_id, amount_cents, txn_id, garnished (bool), shortfall_cents` |
| 13042 | `DAMAGES_AWARDED` | `case_id, from_id, to_id, amount_cents, txn_id` |
| 13043 | `INCARCERATION_STARTED` | `agent_id, case_id, ticks, place_id, converted_to_fine (bool), capacity_at_sentencing` |
| 13044 | `INCARCERATION_ENDED` | `agent_id, ticks_served, skill_delta, ties_lost, returns_to_household_id` |
| 13050 | `POLICE_BUDGET_ALLOCATED` | `total_cents, chief_id, district_shares{}, patrol_units, audit_units, investigation_slots` |

---

## 9. Demographics

`polis/agents/demography.py`. All of this resolves in **PHASE 8**, after commit and after
scheduled institutions, in the fixed order: partnering → household formation/dissolution →
conception → gestation advance → birth → child costs → migration in → migration out.

Ageing, the mortality hazard, birth mechanics, and the full death-settlement transaction are
specified in `04-AGENT-SPEC.md §12` and are **not duplicated here**. This section specifies
only what §12 defers to demography.

### 9.1 Courtship and partnering

Courtship is agent-driven, not a matching algorithm.

```
COURT{target_id, message}          # 'social' group action; LLM-only
  gates: both age ≥ 18, both alive, neither has a live `partner` tie,
         co-located this tick OR an existing relationship of any type
  effect: 15001 COURTSHIP_STARTED (first time), strengthens the tie

compatibility(a,b) = w_age·(1 - |age_a - age_b|/age_norm)
                   + w_tr ·(1 - ‖traits_a - traits_b‖₁/10)
                   + w_bel·(1 - mean_p |b_a(p) - b_b(p)|/2)
                   + w_tie· strength(a,b)
                   + w_eco·(1 - |wealth_pct_a - wealth_pct_b|)
```

`compatibility` is **not** a matching rule — it is a feature surfaced in the courting agent's
perception as narrative ("you have a lot in common with…", "you disagree about most things")
and it conditions the reflex `SAY` template choice. The decision to court, and the decision
to accept, are LLM actions.

Mutual courtship within `courtship_window` (default 60 sim-days) enables
`PROPOSE_UNION{partner_id}`; acceptance is the partner's next-tick `PROPOSE_UNION` back or an
accepting `SAY`. → `15003 UNION_FORMED`, `partner` tie created at `0.7 / 0.6 / 0.7`.

**Cost note.** Courtship is LLM-only and therefore competes for the same budget as everything
else. Courtship-eligible agents receive a `scheduled` salience term of 0.3 when a mutual
courtship is live, which force-boosts them without pinning them. In tight-budget runs,
partnering slows and the birth rate falls — a budget artefact that must be reported alongside
any demographic result (threat T8).

### 9.2 Households

| Transition | Rule |
|---|---|
| Formation | On `15003 UNION_FORMED`, if either partner is not a household head, a new `hh_` forms. Home chosen from available `places.type = 'home'` by `min(rent) s.t. rent ≤ housing_burden · combined income`; `tenure` own/rent per `06-ECONOMY-SPEC.md`. → `15010 HOUSEHOLD_FORMED` |
| Leaving home | An agent aged ≥ `leave_home_age` (18) with income ≥ `independence_threshold` may `PROPOSE_UNION`-independently form a single-person household via the same path → `15012 HOUSEHOLD_LEFT` + `15010` |
| Dissolution | `DISSOLVE_UNION{partner_id}` by either partner, unilateral. Jointly-acquired wealth (value accumulated since `formed_tick`) split 50/50 via balanced ledger legs; separately-held prior wealth untouched. Dependants to the higher-income parent by default. → `15004`, `15013` |
| Death of the head | `04-AGENT-SPEC.md §12.3` step 6. Demography only reassigns dependants and, if no adult remains, forms a state household |

**MECHANISM `custody_default: higher_income`** —
`entails: "dependants follow the higher-income parent on dissolution, so child outcomes correlate with parental income through the assignment rule as well as through investment. Ablate with custody_default: coin_flip, which uses rng.get('demog.courtship', household_id, tick)."`

### 9.3 The fertility hazard

**MECHANISM `fertility_hazard: income_conditional`** (declared in `02-ARCHITECTURE.md §8`).

```
h_fert(m, t) = base(age_m)
             · κ_inc( household_income_percentile )
             · κ_partner( 1.0 if partnered else φ_single = 0.15 )
             · κ_parity( existing_living_children )
             · κ_intent( 1 + ι · [HAVE_CHILD_INTENT within 90 sim-days] )     ι = 2.0
             · κ_policy( 1 + ψ · welfare.child_benefit_cents / median_wage )  ψ = 0.4
             · κ_health( health_m )
             · κ_hous( 1 if household has spare capacity else 0.4 )

base(age)  = bell curve, zero outside [16, 45], peak 28, scaled by demographic_acceleration
κ_inc(q)   = 0.6 + 0.8·q                     # monotone increasing in income percentile
κ_parity(n)= 1.0, 0.85, 0.6, 0.35, 0.15 for n = 0..4, 0.05 beyond
conception iff rng.get("demog.conception", mother_id, tick).random() < h_fert · Δt_sim_days
```

`entails: "the birth rate is increasing in household income and in the child benefit, and decreasing in parity. Therefore (a) any finding that redistribution raises fertility is entailed by κ_policy and κ_inc and is NOT evidence about agent motivation; (b) any finding that wealth and family size are positively associated is entailed by κ_inc. Ablate with fertility_hazard: uniform (κ_inc = κ_policy = 1), under which fertility varies only with age, partnership, parity, health, and expressed intent."`

`HAVE_CHILD_INTENT` (the closed-enum social action) is the agent's own channel into this: it
does not create a child, it multiplies the hazard. That keeps intention consequential without
making reproduction a deterministic consequence of a single decision.

### 9.4 Conception to birth

```
15020 CONCEPTION{mother_id, father_id, due_tick, hazard, draw}
   gestation_ticks = 270 sim-days / demographic_acceleration
   mother's health hazard modified during gestation (06/04 own the health model)
15021 PREGNANCY_ENDED{mother_id, outcome (birth|loss), child_id}
   loss probability = f(health, age) — a hazard, not an event agents choose
   on birth → 2001 AGENT_BORN, owned by 04-AGENT-SPEC §12.1. Do not duplicate.
```

Demography supplies §12.1's `belief priors` input via §9.6 and nothing else.

### 9.5 Child-rearing costs

Charged daily from the household head's account:

```
child_cost_cents_per_sim_day = base_child_cost
                             · (1 + age_multiplier[stage])       # infant 1.0, child 1.2, adolescent 1.6
                             · (1 + private_education_share)
                             - welfare.child_benefit_cents / ticks_per_sim_day
```

Ledger: debit household head cash → credit the supplying firms, `purchase` (it is real
consumption of real SKUs, not an abstraction). Shortfall for `arrears_tolerance_days` (30) →
child `health` declines; below `child_welfare_threshold` → state intervention, the child moves
to a state household and the government pays (`transfer`). That is a real fiscal cost of
child poverty, and it makes `welfare.child_benefit_cents` a parameter with an actual budget
consequence.

### 9.6 Inheritance of belief priors

Wealth inheritance is specified in `04-AGENT-SPEC.md §12.3` steps 3–5 and is **not repeated
here**. Demography adds two things §12.3 defers:

**Intestacy order** (applied by the estate settlement in §12.3 step 5): surviving `partner`
takes 50%; living children split the remainder equally; if none, parents; if none, siblings;
if none, escheat to government. `tax.inheritance.rate` (§7.2) is applied to the estate
*before* distribution, as a `tax` leg to government.

**Belief priors at birth** (`15030`, feeding `2001 AGENT_BORN`):

```
for p in POLICY_PROPOSITIONS ∪ {'trust.generalised'}:
    b_child(p) = clip( η_b · (w_m·b_m(p) + w_f·b_f(p))
                     + (1 - η_b) · population_mean(p)
                     + N(0, σ_belief),  range(p) )
    c_child(p) = mean(c_m(p), c_f(p)) · confidence_dilution        # 0.5
```

`η_b = heritability_beliefs` (default 0.4), the direct analogue of trait heritability in
`04-AGENT-SPEC.md §2.1`, and the knob for research question B6: sweeping it from 0 to 1
separates inherited worldview from lived experience cleanly. Draws use
`rng.get("beliefs.noise", child_id)`.

**Only policy stances and `trust.generalised` are inherited.** `fact.*` propositions about
specific firms, agents, or elections are not: a newborn has no view on whether `fm_acme` is
solvent, and manufacturing one would corrupt the misinformation measurements.

### 9.7 Migration

**In.** Scheduled in PHASE 8 at `migration_cadence` (monthly), up to
`runtime.get("migration.quota_per_sim_year", t) / 12`. Arrivals are generated with traits from
the population distribution shifted by `migration.origin_profile` (config: skill premium,
wealth offset, belief prior offsets), zero social ties, and a `home` place chosen by
affordability. Because arrivals have no ties, **assimilation is directly observable**: time
to first tie, time to first job, degree trajectory, and belief convergence toward the host
distribution are all measurable without any assimilation mechanism being coded.

**Out.**

```
h_emig(a, t) = base_emig
             · κ_unemp( 1 + 2.0 · min(unemployment_spell_sim_days / 180, 1) )
             · κ_wealth( 1.5 - wealth_percentile )
             · κ_ties( 1.5 - normalised_degree )
             · κ_crime( 1 + district.crime_rate / crime_norm )
             · κ_age( peak 22-35, low outside )
emigrates iff rng.get("demog.emigration", agent_id, tick).random() < h_emig · Δt_sim_days
```

**MECHANISM `emigration_hazard: precarity_conditional`** —
`entails: "emigration is increasing in unemployment duration, decreasing in wealth and in social connectedness. Selective out-migration of the poor and weakly-tied therefore mechanically improves the resident wealth distribution and mechanically raises mean tie density. Any A2 (inequality) or network-density result must be reported with the emigration rate, and re-run with base_emig = 0."`

On emigration, in one atomic step mirroring `04-AGENT-SPEC.md §12.3`: cancel resting orders,
terminate employment, settle or default debts, liquidate or transfer positions, leave or
dissolve the household, end all ties (`10042 reason: emigration`), and stop all obligations.

**Schema encoding.** `agents` has no departure column. An emigrant is recorded as
`died_at_tick = tick, death_cause = 'emigrated'`, and **every mortality metric filters
`death_cause <> 'emigrated'`**. This is a normative filter rule, not an optional convention.
If `03-DATA-MODEL.md §2.1` is ever revised, a dedicated `left_at_tick` column is preferable
and this encoding should be retired.

### 9.8 Kinds 15000–15999

| Kind | Name | Payload |
|---|---|---|
| 15001 | `COURTSHIP_STARTED` | `a_id, b_id, initiator_id, compatibility, place_id` |
| 15002 | `COURTSHIP_ENDED` | `a_id, b_id, outcome (union\|rejected\|drifted\|death), duration_ticks` |
| 15003 | `UNION_FORMED` | `partner_ids[], household_id, courtship_ticks` |
| 15004 | `UNION_DISSOLVED` | `partner_ids[], initiator_id, reason, split_txn_id, dependants[], custody{}` |
| 15010 | `HOUSEHOLD_FORMED` | `household_id, member_ids[], home_place_id, tenure, rent_cents, head_agent_id` |
| 15011 | `HOUSEHOLD_JOINED` | `agent_id, household_id, reason (birth\|union\|custody\|state_care)` |
| 15012 | `HOUSEHOLD_LEFT` | `agent_id, household_id, reason (independence\|union\|dissolution\|death\|emigration\|incarceration)` |
| 15013 | `HOUSEHOLD_DISSOLVED` | `household_id, reason, members_reassigned[]` |
| 15020 | `CONCEPTION` | `mother_id, father_id, due_tick, hazard, draw` |
| 15021 | `PREGNANCY_ENDED` | `mother_id, outcome, child_id, gestation_ticks` |
| 15022 | `CHILD_COST_CHARGED` | `household_id, child_ids[], amount_cents, benefit_offset_cents, txn_id, arrears_cents` |
| 15023 | `STATE_CARE_STARTED` | `child_id, from_household_id, to_household_id, reason, cost_cents` |
| 15030 | `BELIEF_PRIORS_INHERITED` | `child_id, mother_id, father_id, heritability_beliefs, propositions[{proposition, value, confidence}]` |
| 15040 | `MIGRATION_IN` | `agent_id, cohort_id, origin_profile, arrival_wealth_cents, skills{}, belief_priors[], home_place_id` |
| 15041 | `MIGRATION_OUT` | `agent_id, hazard_components{}, exit_wealth_cents, ties_severed, debts_settled_cents, debts_defaulted_cents` |

---

## 10. Social metrics

Formal definitions in **simulation-state terms only**. Real-world analogues are named
separately in §10.10 (threat T11). All are computed in PHASE 9 or PHASE 7 and written to
`metrics` (`03-DATA-MODEL.md §10`).

### 10.1 Polarisation

| Metric | Definition |
|---|---|
| `polarisation.bc.<prop>` | Bimodality coefficient `BC(p)` of `{b_i(p)}` over living adults, §5.7 |
| `polarisation.dip.<prop>` | Hartigan's dip statistic and p-value on the same sample |
| `polarisation.var.<prop>` | `Var({b_i(p)})` |
| `polarisation.index` | Mean `BC(p)` over the 20 policy propositions |
| `polarisation.affective` | `AP` from §5.7: mean out-cluster tie valence minus mean in-cluster tie valence |
| `polarisation.party_distance` | Mean pairwise L1 distance between party platforms, normalised by plank count |
| `exposure.crosscut` | `CCE` from §5.7, over a 7-sim-day window |
| `exposure.crosscut_persuasive` / `_hostile` | `CCE` split at source trust 0.5 |
| `consensus.time_to.<prop>` | First tick at which `Var < 0.02` sustained 30 sim-days; `null` if never |

### 10.2 Trust

| Metric | Definition |
|---|---|
| `trust.generalised` | Population mean of `beliefs[i, 'trust.generalised'].value` |
| `trust.institution.<k>` | Population mean of the corresponding trust proposition |
| `trust.dyadic` | Mean `relationships.trust` over live non-kin ties |
| `trust.behavioural` | `(count of transactions with counterparties having no prior relationship) / (all transactions)` over a 30-sim-day window — a revealed-preference measure independent of stated beliefs |
| `trust.promise_keeping` | `kept / (kept + broken)` over log-checkable obligations (§2.3) |
| `trust.calibration` | Correlation across agent-outlet pairs between `b_i('trust.outlet.X')` and the realised accuracy of X's articles that `i` saw |

`trust.behavioural` and `trust.calibration` exist because a trust index built only from
stated beliefs measures what agents say, and this system can measure what they do.

### 10.3 Misinformation

Let a **false item** be a post or article with `truthfulness < 0.5` or `accuracy < 0.5` and at
least one `contradicted` claim.

| Metric | Definition |
|---|---|
| `misinfo.exposure_reach(x)` | `|{i : x entered i's feed or news slot at least once}|` |
| `misinfo.adoption_reach(x)` | `|{i : ∃ 10060 for i on x's target proposition with source_ref tracing to x, moving i's value toward the false claim by ≥ 0.05}|` |
| `misinfo.believers(x, t)` | `|{i : b_i(target_prop) on the false side of the truth by ≥ 0.2 at tick t}|` |
| `misinfo.half_life(x)` | `min{Δt : believers(x, t_peak + Δt) ≤ believers(x, t_peak)/2}`. Also reported as `λ` from an exponential fit `believers(t) = A·e^{-λ(t-t_peak)}` with R²; if R² < 0.5 the decay is not exponential and only the empirical half-life is reported |
| `misinfo.correction_efficacy(x)` | `(believers(t_corr) − believers(t_corr + 14 sim-days)) / (believers(t_corr) − believers(t_0))`, defined only where a `11033` exists |
| `misinfo.share_of_impressions` | Impressions of false items / all impressions of checkable items |
| `misinfo.organic_share` | False items with no `cause_seq` chain to a `99001` injection, over all false items |
| `claim_annotation_coverage` | From the §4.5 omission audit |

Separating exposure from adoption is essential: an item everyone saw and nobody believed and
an item few saw and all believed are opposite phenomena with the same reach.

### 10.4 Social mobility

Requires the demographic layer (M5) and at least two generations.

| Metric | Definition |
|---|---|
| `mobility.iges` | Intergenerational elasticity: OLS slope `β` of `ln(child wealth_cents at age 40)` on `ln(parent wealth_cents at age 40)`, over child-parent pairs where both reached 40 |
| `mobility.rank_rank` | Slope of child's wealth percentile on parent's wealth percentile — preferred over IGE because it is robust to zero and negative wealth |
| `mobility.transition` | 5×5 quintile transition matrix, parent quintile → child quintile at age 40 |
| `mobility.upward_q1` | `P(child in top two quintiles | parent in bottom quintile)` |
| `mobility.belief_ige` | Slope of child's policy-stance vector on parent's, at age 30 — the direct measurement for B6, to be read against `mobility.rank_rank` for the same cohort |

### 10.5 Network segregation

| Metric | Definition |
|---|---|
| `network.assortativity.<attr>` | Newman assortativity coefficient `r` on `attr ∈ {wealth_quintile, belief_cluster, district, education_level, party}` over the undirected non-kin tie graph |
| `network.modularity` | Louvain modularity `Q` of the belief-cluster partition, §2.6 |
| `network.ei_index` | `(E − I)/(E + I)` where `E` = cross-attribute ties, `I` = within-attribute ties. `−1` fully segregated, `+1` fully integrated |
| `network.crosscut_tie_share` | Share of live ties joining agents on opposite sides of the median on ≥ 3 policy propositions |
| `network.degree_gini` | Gini of the degree distribution |
| `network.clustering` | Global transitivity and mean local clustering |
| `network.largest_component_share` | Fraction of living agents in the largest connected component |

### 10.6 Turnout

```
turnout(e)              = |votes where election_id = e| / |eligible at e.voting_tick|
turnout.deliberate(e)   = deliberate votes / deliberate-eligible        # T8 diagnostic
turnout.by_quintile(e)  = turnout within each wealth quintile
turnout.differential(e) = turnout(top quintile) - turnout(bottom quintile)
```

`turnout.deliberate` must be reported with any turnout claim: a turnout difference across
arms that is entirely a difference in how many voters got LLM cognition is a budget artefact,
not a political finding.

### 10.7 Crime

```
crime.committed_rate  = |13010 in window| / (living adults · window_sim_years)
crime.reported_rate   = |13012 in window| / (living adults · window_sim_years)
crime.detected_rate   = |13011 in window| / (living adults · window_sim_years)
crime.dark_figure     = crime.committed_rate / crime.reported_rate
crime.by_type.<t>     = the same four, per type
crime.mean_p_detect   = mean p_detect over crimes live in the window
crime.victimisation   = |distinct victim_id in 13010| / living adults
crime.recidivism      = P(new 13010 within 1 sim-year | prior 13044)
```

### 10.8 Courts

```
conviction.rate       = |13040 with verdict ∈ {guilty, liable}| / |13040|
conviction.per_crime  = |13040 guilty| / |13010 committed|      # the number that matters for B5
charge.rate           = |13020 criminal| / |13012 reported|
court.backlog         = open court_cases at tick / cases_per_session
court.time_to_verdict = mean (resolved_tick - filed_tick)
court.counsel_gap     = conviction rate of defendants with counsel skill above vs below median
court.bench_share     = |13040 with origin = bench| / |13040|
```

### 10.9 Incarceration

```
incarceration.rate      = |agents in a prison place at tick| / living adults
incarceration.admissions= |13043 in window| / (living adults · window_sim_years)
incarceration.mean_days = mean sentence_ticks served, in sim-days
incarceration.by_quintile = rate within each wealth quintile at time of offence
prison.utilisation      = occupants / prison.capacity
```

### 10.10 Real-world analogues, named separately (T11)

The left column is what the simulation computes. The right column is the human statistic it
resembles. **They are not the same thing, and a result statement uses only the left column.**

| Simulation metric | Named analogue | Principal difference |
|---|---|---|
| `polarisation.index` | Mass ideological polarisation (ANES/Pew) | Propositions are a closed 20-item vocabulary chosen by the modeller; humans have open-ended and multidimensional politics |
| `exposure.crosscut` | Cross-cutting media exposure | Measured over a fully observed 15-slot feed; human panel data are self-reported and partial |
| `trust.generalised` | WVS/GSS generalised trust item | A model-authored credence, not a survey response; no acquiescence or social-desirability bias, and no interviewer |
| `trust.behavioural` | Trust-game transfer rate | Arises in a live economy with repeated play, not a one-shot lab game |
| `misinfo.half_life` | Rumour decay in observational social-media studies | Ground truth is exactly known here and unknown there; this is the whole reason the measurement exists |
| `mobility.rank_rank` | Chetty-style rank-rank mobility | Two to three synthetic generations at N ≈ 1,000, with `demographic_acceleration` compressing lifespans; no schools, neighbourhoods, or labour markets resembling any real place |
| `network.ei_index` | Residential and social segregation indices | Ties are simulator-defined events, not survey-elicited or platform-observed relationships |
| `turnout` | Electoral turnout | Voting is costless here except for an action slot; a fraction of voters are a fitted extrapolation (§6.5) |
| `crime.committed_rate` | Offending rate | The committed rate is directly observed here and is fundamentally unobservable in any real society |
| `crime.dark_figure` | Victimisation-survey vs police-recorded gap | Here it is exact; there it is an estimate from two noisy instruments |
| `conviction.per_crime` | Clearance/conviction rate | Denominator is all committed crimes, not reported ones |
| `incarceration.rate` | Incarceration rate per 100,000 | A city of 1,000 with two judges and one prison; finite-size effects dominate (T7) |

---

## 11. Threats and failure modes

The things most likely to go wrong in this layer, how each is detected, and what it means.
Every detector is a computed metric with a stated threshold, evaluated in PHASE 9 or in the
per-run report; none relies on a human noticing.

### F1 — Opinion monoculture

**Symptom.** Every agent converges on the same stance on every proposition. The society has
no politics, B1 has no variance to explain, and V4 (behavioural diversity) fails.

| Detector | Threshold |
|---|---|
| `polarisation.var.<prop>` | `< 0.02` on ≥ 15 of 20 propositions for 30 consecutive sim-days |
| `polarisation.index` | `< 0.25` sustained |
| `INV-ENTROPY` (`02-ARCHITECTURE.md §9`) | Action-type entropy below floor → WARN |
| `parties` | Fewer than 2 live parties after the first election cycle |

**Likely causes, in order of probability.** (a) The base model has strong, uniform priors on
policy questions and trait conditioning is too weak to overcome them — check by comparing
genesis belief dispersion to steady-state dispersion; (b) `ALPHA[social]` and `ALPHA[media]`
are too high, so bounded-confidence dynamics drive consensus mechanically — check by running
`--social-influence-off`; (c) the feed shows everyone the same 15 posts because the follow
graph is near-complete — check `network.degree_gini` and feed overlap across agents.
**Mitigation before it happens:** initialise the population with a genuinely dispersed belief
prior (§5.1), keep trait-conditioned prompts, and report genesis dispersion in every run.

### F2 — Nobody commits crimes

**Symptom.** `crime.committed_rate` near zero. B5 is unanswerable, courts sit idle, the entire
law layer is dead weight.

| Detector | Threshold |
|---|---|
| `crime.committed_rate` | `< 0.005` per adult per sim-year |
| `crime_action_refusal_rate` | Share of deliberate calls whose response contains a refusal pattern **and** whose prompt included `COMMIT_CRIME` in the legal action set. Reported every run |
| `13001 LEGALITY_FLAGGED` by path | If `explicit` is ~0 but `derived` is healthy, the model is refusing the label, not the behaviour |
| `insider_trading` count while `MNPI` holders trade | If agents holding MNPI never trade in that symbol, either the window is wrong or refusal is operating |

**Why the design already anticipates this.** Five of seven offence types are **derived**
(§8.2) and require no criminal intent in text. An agent that defaults on a payable loan, buys
a stock it heard about from a friend at the firm, or moves money out of a company it controls
has committed an offence without ever selecting an action called `COMMIT_CRIME`. If explicit
crime is zero and derived crime is healthy, that is a *finding about model refusal
behaviour*, reported as such, and B5 proceeds on the derived types. If both are zero, the
enforcement sweep is not runnable and the run is not usable for B5.

### F3 — Elections that change nothing

**Symptom.** Power alternates but no parameter moves. The polity is theatre and A4 has no
shocks to trace.

| Detector | Threshold |
|---|---|
| `policy.enactments_per_sim_year` | `< 1` |
| `policy.parameter_drift` | L1 distance between the runtime overlay vector at the start and end of an administration; `≈ 0` is the failure |
| `policy.blocked_rate` | `|12033| / |12025| > 0.7` means admissibility is eating everything — usually `P-SOLVENCY` with a debt ceiling set too low |
| `policy.platform_delivery` | Correlation between the winner's platform stances and the sign of enacted changes in the following sim-year; `≈ 0` means winning does not translate into governing |
| Runtime-read audit | A CI test asserts that every parameter in `POLICY_REGISTRY` is read through `runtime.get` at least once during a 500-tick smoke run. A parameter nobody reads cannot have an effect no matter how often it is enacted |

The last detector is the important one: the most likely concrete cause of F3 is not political,
it is an institution that read the static config once at startup.

### F4 — The model will not generate a falsehood

**Symptom.** `misinfo.share_of_impressions` ≈ 0. Every post and article checks out. B2 has
nothing to measure.

| Detector | Threshold |
|---|---|
| `misinfo.share_of_impressions` | `< 0.01` |
| Distribution of `posts.truthfulness` | Mass concentrated at 1.0 with no left tail |
| `misinfo.organic_share` | `< 0.1` means only injected falsehoods exist |
| Refusal patterns in `llm_calls` for `POST_WRITE` / `NEWS_WRITE` | Reported per purpose |

**The design's answer, which matters more than the detector.** *The system does not need
agents to lie. It needs agents to be wrong.* The primary designed channel for false content
is **belief error transmitted honestly**: an agent whose `fact.firm.fm_acme.solvent` credence
is 0.2 because it heard a rumour from a trusted colleague will sincerely post that Acme is
failing, and the checker will score that claim `contradicted` against the log. No deception is
required, no prompt asks for a lie, and no model needs to be jailbroken. Deliberate deception
— conditioned on the `honesty` trait — is a bonus channel, not a requirement.

Three consequences: (a) misinformation volume scales with belief error, so a run where beliefs
track reality too well produces no misinformation and that is itself the finding; (b)
researcher-injected falsehoods (`99001`) exist to provide a *controlled* item with known
ground truth and known injection tick, not to make up the volume; (c) the reported statistic
is `misinfo.organic_share`, and an organic share near zero means B2 is answerable only for
injected items, which must be stated in any result.

### F5 — News that just restates the event log

**Symptom.** Every outlet writes the same true article. `articles.accuracy` ≈ 1.0 everywhere,
`slant_applied` ≈ 0 everywhere. The media layer is a formatter, not an institution, and B1's
media channel carries no signal.

| Detector | Threshold |
|---|---|
| `news.editorial_divergence` | Mean pairwise L1 distance between claim values across outlets covering the same `source_event_seq`; `< 0.05` is the failure |
| `slant_applied` dispersion | Std across outlets `< 0.1` despite `outlet_slant_dispersion` > 0.4 at genesis |
| `articles.accuracy` distribution | Variance `≈ 0` |
| `news.claims_per_source_event` | Distinct claim predicates per source event; `≈ 1` means outlets are transcribing, not interpreting |
| `news.selection_divergence` | Jaccard distance between outlets' published story sets; `≈ 0` means the newsworthiness function dominates the editorial line |

**Likely causes.** The `NEWS_WRITE` prompt renders slant as an instruction the model politely
ignores; the reporter's own beliefs and memories are not in the prompt; `stories_per_cycle` is
so small that all outlets cover the same one story. **Fixes are prompt-side and config-side,
and each is testable:** raise `w_slant` in the newsworthiness function so outlets select
differently, ensure the writing prompt carries the reporter's retrieved memories, widen
`outlet_slant_dispersion`, and verify the editor's `line` gate is actually spiking
off-line copy (`11031` count by reason).

### F6 — The feed algorithm has no effect (a B1 null)

**Symptom.** `exposure.crosscut` differs sharply across the four arms, exactly as designed,
but `polarisation.index` does not.

| Detector | Threshold |
|---|---|
| Arm contrast | `|Δ exposure.crosscut| > 0.2` across arms while `|Δ polarisation.index| < 0.02`, at ≥ 20 seeds |
| Adversarial arm | If even `adversarial` fails to move polarisation, the belief model is too rigid — this is the arm's diagnostic purpose (§3.3) |
| Channel share | Share of belief movement attributable to `media` and `social` channels vs `reflection`, from `10060.channel` counts |

A genuine null here is a **publishable result** and is one of the more interesting things this
platform could produce. It becomes a bug only if the adversarial arm also fails, which means
`ALPHA[media]` is too small or too few agents ever reach deliberate cognition to act on what
they saw.

### F7 — Degenerate network

| Symptom | Detector |
|---|---|
| Everyone follows everyone | `mean_degree > 0.3 · n` or `network.clustering > 0.8` |
| Nobody knows anybody | `network.largest_component_share < 0.5` or `mean_degree < 2` |
| One super-influencer | Top-1 follower share `> 0.4` |

Causes are usually mechanical: `colocation_threshold` too low (everyone at the same park
becomes acquainted), or tie decay half-lives too long relative to `demographic_acceleration`.

### F8 — Single-party or no-party system

| Detector | Threshold |
|---|---|
| Live parties | `< 2` after two election cycles, or one party holding `> 6/7` council seats for two consecutive terms |
| `FOUND_PARTY` attempts | Zero attempts means B3 is answered trivially and the founding fee or the capability gate is likely too strict |

### F9 — Courts that always or never convict

| Detector | Threshold |
|---|---|
| `conviction.rate` | `> 0.95` or `< 0.05` |
| `court.bench_share` | `> 0.3` means the `JUDGE` call is failing and the bench rule is deciding the docket |
| Verdict–evidence correlation | Correlation between `verdict` and `evidence_strength` near zero means the judge is not reading the record |
| Clamp rate | `|13040 with clamped[] non-empty| / |13040| > 0.5` means the model cannot hold the statutory range and the prompt needs the range stated more prominently |

### F10 — Budget-induced demographic collapse

**Symptom.** Because courtship, partnering, and voting are LLM-only, a tight budget suppresses
family formation and turnout, and the population falls.

| Detector | Threshold |
|---|---|
| `INV-POP` (`02-ARCHITECTURE.md §9`) | Population outside `[0.2×, 5×]` initial → WARN |
| Births per sim-year vs deliberate-call share | Correlation across the sweep; a strong positive correlation is a budget artefact, not a demographic finding |
| `turnout.deliberate` | Reported with every turnout number (§10.6) |

This is threat T8 (budget-induced selection) expressed in the demographic layer, and it is the
reason `llm_election_multiplier` and the courtship salience boost exist.

---

## 12. Scheduled steps and their phases

Consolidated so that no chunk has to infer a cadence.

| Step | Phase | Cadence (sim-time) | Section |
|---|---|---|---|
| Speech, DM, broadcast resolution | 5 (slot 2) | every tick | §1 |
| Tie formation and dynamics | 5 (slot 2) | every tick | §2.2–2.3 |
| Social/media belief updates | 5 (slot 2) / 7 | every tick / news cycle | §5.4 |
| Post, repost, like, follow resolution | 5 (slot 2) | every tick | §3.1 |
| Feed construction | 1 | every tick | §3.2–3.3 |
| Polity actions (candidacy, campaign, proposals) | 5 (slot 8) | every tick | §6 |
| Law actions (crime, report, filing, counsel, testimony) | 5 (slot 9) | every tick | §8 |
| Legality flagging | 4 | every tick | §8.2 |
| **News cycle** (story selection, writing, editing, distribution) | **7** | `society.news_cycle` (daily) | §4.3–4.7 |
| **Claim checking** | **7** | daily, after publication | §4.5 |
| Engagement-ranker refit | 7 | daily | §3.3 |
| Cascade closure and reach computation | 7 | daily | §3.4 |
| Outlet fiscal close and revenue booking | 7 | weekly | §4.9 |
| Trust-tracks-accuracy update | 7 | weekly | §5.6 |
| **Council session** (policy votes) | **7** | `polity.council_session` (weekly) | §7.3 |
| **Policy review** (admissibility) | **7** | `polity.policy_review` (weekly) | §7.3 |
| Party platform drift | 7 | quarterly | §6.1 |
| **Election day** | **7** | `election_interval` per office | §6.5–6.6 |
| Police budget allocation | 7 | monthly | §8.4 |
| Investigation queue processing | 7 | daily | §8.6 |
| **Court sessions** | **7** | `polity.court_session` | §8.7–8.8 |
| Crime detection hazard | 7 | daily, over the detection window | §8.4 |
| Network snapshot | 7 | weekly | §2.4 |
| Partnering and household formation | **8** | every tick | §9.1–9.2 |
| Conception hazard | **8** | daily | §9.3 |
| Gestation advance and birth | **8** | every tick | §9.4 |
| Child cost charging | **8** | daily | §9.5 |
| Migration in | **8** | monthly | §9.7 |
| Migration out hazard | **8** | daily | §9.7 |
| Social metric snapshot | 9 | every tick (cheap) / weekly (network) | §10 |

---

## 13. Implementation checklist

A chunk implementing any part of this document is not done until:

- [ ] Every money movement goes through `polis.economy.ledger.post_transaction`, and
      `INV-MONEY` holds across an election, a judgment with a fine, an inheritance, and an
      outlet revenue close.
- [ ] Every random draw uses `rng.get` with a namespace from §0.6, and two runs of the same
      `(config, seed, cache)` produce identical hash chains over 200 ticks including a
      contested election and a trial.
- [ ] No module in `polis/society/` imports anything from `polis.agents.cognition`.
      Verified by `import-linter`.
- [ ] Every `MECHANISM` in this document has a `@mechanism(id, entails=...)` decorator whose
      `entails` string matches the text here, and is ablatable from config.
- [ ] Every kind in §1.5, §2.4, §3.6, §4.10, §5.8, §6.7, §7.7, §8.12, §9.8 exists in
      `polis/events/kinds.py` with a JSON Schema for its payload.
- [ ] `polis rebuild` reproduces `relationships`, `beliefs`, `posts`, `engagements`,
      `articles`, `parties`, `votes`, `policies`, `crimes`, `court_cases`, and `households`
      exactly, including feeds recomputed from the seeded RNG.
- [ ] `runtime.get` is the only read path for every parameter in `POLICY_REGISTRY`, verified
      by the CI audit in §11 F3.
- [ ] The claim checker's `RESOLVERS` are pure functions of state at `as_of_tick` and never
      read later state — verified by a test that checks a claim twice, once at the true tick
      and once after the fact has changed, and asserts the same verdict.
- [ ] No agent `Observation` contains a network statistic, a detection probability, a
      truthfulness score, an accuracy score, or a polarisation metric.
- [ ] The four feed algorithms produce measurably different `exposure.crosscut` on the same
      seed, and `chronological` produces a strictly recency-ordered slice.

---

*Next: `08-EXTERNAL-AGENT-PROTOCOL.md`.*
