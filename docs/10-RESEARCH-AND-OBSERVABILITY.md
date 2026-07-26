# POLIS — Research and Observability

**Version:** 1.0
**Status:** Normative. Metric ids, gate thresholds, kind numbers 99000–99999, and the CLI
surface defined here are binding.
**Owner modules:** `polis/research/` (metrics, experiments, replay, scenario DSL, exports),
`polis/observatory/` (read-only FastAPI + React dashboard).
**Event kinds owned:** 99000–99999 (researcher injection and scenario DSL).
**Depends on:** `01-PRD.md` (§3 research questions, §7 success metrics, §9 threats),
`02-ARCHITECTURE.md` (§3 log, §4 determinism, §5 tick loop, §7.1 dependency rules, §8.1
`MECHANISM`, §9 invariants), `03-DATA-MODEL.md` (§1 runs/events/llm_calls, §10 research
tables, §12 rebuild), `04-AGENT-SPEC.md` (§5 perception, §6 memory, §7 salience, §9
deliberate), `05-WORLD-SPEC.md` §11.2 (ephemerals), `06-ECONOMY-SPEC.md` §12 and §16.3,
`07-SOCIETY-SPEC.md` §10.

> This document is the operational implementation of `01-PRD.md` §3, §7 and §9. Every
> research question in §3 must be answerable by a metric registered here; every gate in §7.2
> must be an executable check here; every threat in §9 must have a named defence here.

---

## 0. Scope, ownership, conventions

### 0.1 What this document owns

| Owned | Module | Section |
|---|---|---|
| The metric registry — ids, definitions, units, cadences | `polis/research/metrics/` | §1 |
| V1–V7 gates as executable procedures | `polis/research/gates.py` | §2 |
| Experiment definition, pre-registration, `polis sweep` | `polis/research/experiments/` | §3 |
| The scenario / shock DSL and kinds 99000–99999 | `polis/research/scenario.py` | §4 |
| `polis replay`, `polis verify`, `polis rebuild`, the reproducibility package | `polis/research/replay.py` | §5 |
| The ablation ladder | `polis/research/ablations.py` | §6 |
| The MECHANISM reviewer checklist | `polis/research/mechanism_check.py` | §7 |
| The Observatory API, WebSocket protocol, and views | `polis/observatory/` | §8 |
| Parquet export schema and analysis notebooks | `polis/research/exports/`, `notebooks/` | §9 |
| Statistical practice and reporting standards | — | §10–§11 |

### 0.2 What this document does not own

| Not owned | Governed by | Rule on conflict |
|---|---|---|
| Economic metric definitions M1–M28 | `06-ECONOMY-SPEC.md` §12 | 06 governs. §1.3 here restates and adds cadence + research-question columns only. |
| Social, political, legal, mobility metric definitions | `07-SOCIETY-SPEC.md` §10 | 07 governs. §1.4–§1.7 restate and add columns only. |
| INV-* semantics and HALT policy | `02-ARCHITECTURE.md` §9, `06-ECONOMY-SPEC.md` §1.7 | 02/06 govern. §2.2 is the executable form. |
| The kind registry file | `polis/events/kinds.py` | Kinds are declared there; §4.9 is the specification the file implements. |
| Model routing, providers, cost tables | `09-MODEL-ROUTING.md` | 09 governs; §1.8 consumes its counters. |

A metric defined in two places with two definitions is the single most likely way this
project produces a wrong number. §1.10 makes that condition detectable rather than
discoverable-in-review.

### 0.3 The read-only rule

`polis.observatory` **never mutates simulation state and never imports `polis.kernel`**
(`02-ARCHITECTURE.md` §7.1). Concretely:

1. It connects as the `polis_reader` Postgres role, which has `SELECT` only and no `INSERT`
   on `events` (`02-ARCHITECTURE.md` §2.1).
2. It has no write path to Redis. It subscribes; it does not publish.
3. It exposes no endpoint whose handler can advance the clock, inject an event, change a
   parameter, or start or stop a run. Shocks are injected by the scenario DSL, signed,
   before the run starts — never by a dashboard button.
4. The one client→engine influence permitted is the bounded *pin set* (§8.4), which selects
   which agents appear in ephemeral kind 90050 and touches nothing persisted, hashed, or
   RNG-consuming.

An `import-linter` contract enforces (1)–(3) statically; a test asserts that the reader role
cannot write.

### 0.4 Metric storage contract

`metrics` (`03-DATA-MODEL.md` §10) is long and narrow: `(run_id, tick, metric, value)` with
`value DOUBLE PRECISION`. Rules:

| Rule | Detail |
|---|---|
| Ids are dotted lowercase | `unemployment_rate`, `polarisation.bc.tax.rate.should_rise`, `sys.llm.cache_hit_rate` |
| Per-entity metrics encode the entity in the id | `bank.capital_ratio_bp.bk_02`, never a separate column |
| Money is stored as cents (integer-valued double) | Exact below 2^53; never store money as a ratio |
| Rates and shares are stored in **basis points** as integer-valued doubles | Matches `06-ECONOMY-SPEC.md` §2.1; the export divides by 10,000 exactly once, in `polis/research/exports/` |
| Unbounded floats are permitted only for `dimensionless_float` metrics | entropy, correlations, BC, dip statistic, Hill exponent |
| Nulls are not written | An absent metric row means "not computed at this tick", never "zero" |
| Cadence is a property of the metric, not the writer | Declared in the registry; the writer asserts it |
| Parquet exports are **wide** | One row per tick, one column per metric, cadence declared per column (§9.2) |

### 0.5 Randomness

Any randomness in this subsystem — bootstrap resampling, scenario target selection, sampled
exports, the ephemeral tracked-agent sample — goes through
`rng.get(namespace, entity_id, tick)` (`02-ARCHITECTURE.md` §4.1). Namespaces owned here:

| Namespace | Used for |
|---|---|
| `research.scenario.select` | Choosing targets for a scenario selector (`sample: 20`) |
| `research.scenario.jitter` | Per-target timing jitter in a `schedule` trigger |
| `research.bootstrap` | Bootstrap resampling in the analysis layer; seeded so figures are reproducible |
| `research.export.sample` | Row sampling in sampled exports |
| `observatory.track` | Selecting the ephemeral tracked-agent set beyond the pin list |

Analysis is code, and analysis code is as much a part of the reproducibility tuple as the
engine. A bootstrap with an unseeded RNG produces a figure nobody can reproduce.

### 0.6 Required amendments to `03-DATA-MODEL.md`

Declared in the manner of `06-ECONOMY-SPEC.md` §0.1. Each is additive; none changes an
existing column.

| Table | Amendment | Why |
|---|---|---|
| `runs` | Add `metric_manifest JSONB NOT NULL DEFAULT '{}'` — `{metric_id: definition_hash}` | Makes metric drift (§12 R1) detectable by a join instead of by memory. Redundant with kind 99070 events by design: the log is truth, the column is the index. |
| `runs` | Add `mechanism_manifest JSONB NOT NULL DEFAULT '{}'` — `{mechanism_id: {value, entails_hash}}` | §7 step 3 must be machine-generated from the run, never typed by hand |
| `runs` | Add `ablations JSONB NOT NULL DEFAULT '{}'` and `scale INTEGER` | The ablation arm and N must be joinable without parsing YAML |
| `scenario_injections` | Add `step_id TEXT NOT NULL`, `event_seq BIGINT NOT NULL`, `scenario_hash CHAR(64) NOT NULL` | Ties each injection row to the exact log position and scenario version it came from |
| `sweeps` | Add `preregistration JSONB NOT NULL`, `analysis_plan_hash CHAR(64) NOT NULL`, `preregistered_at TIMESTAMPTZ NOT NULL` | §3.1; the plan must be committed before the first cell launches |
| `sweeps` | Add `cost_estimate_usd NUMERIC(12,4)`, `cost_actual_usd NUMERIC(12,4)` | §3.4 |

No new table is requested. Gate results are `metrics` rows (`gate.V3.pass` ∈ {0,1}) plus
kind 99060 events; export manifests live in the object store beside the Parquet files.

---

## 1. The metric catalogue

### 1.1 Registration contract

Every metric is a decorated pure function in `polis/research/metrics/`:

```python
@metric(
    id="unemployment_rate",
    unit="bp",
    cadence="sim_day",
    rq=["A1", "A4", "B4"],
    definition="10_000 * |U(t)| // LF(t), with U, E, LF as 06-ECONOMY-SPEC.md §3.10",
    analogue="ILO/BLS U-3 unemployment rate",
    analogue_caveat="U(t) is directly observed from employment records; U-3 is a survey estimate",
    governed_by="06-ECONOMY-SPEC.md §12 M4",
)
def unemployment_rate(state: MetricState) -> float: ...
```

| Field | Binding rule |
|---|---|
| `id` | Unique across the registry. Duplicate registration is an import-time error. |
| `definition` | Stated **purely in terms of simulation state** — tables, event kinds, and arithmetic. No sentence may reference a human institution. This is the discharge of **T11**. |
| `analogue` | Named in a **separate field**, never in the definition, never in the id. |
| `analogue_caveat` | One sentence naming the principal way the two differ. Required; empty string is rejected. |
| `unit` | `cents · bp · index_bp · count · ratio_bp · dimensionless_float · usd · tokens · ticks · sim_days` |
| `cadence` | `tick · sim_day · sim_week · sim_month · sim_quarter · sim_year · on_event · end_of_run` |
| `rq` | Non-empty list of `01-PRD.md` §3 question ids, or `["SYS"]` for system metrics. A metric that serves no research question and is not a system metric is deleted (`02-ARCHITECTURE.md` §1.8). |
| `definition_hash` | `sha256(id ‖ definition ‖ unit ‖ cadence ‖ dedented source of the function body)`. Computed at import; written to `runs.metric_manifest` and emitted as kind 99070 at tick 0. |

`polis metrics catalogue --format md` regenerates the tables in §1.3–§1.8 from the registry.
**If this document and the registry disagree, the registry is wrong and must be fixed to
match, or this document amended in the same PR.** A CI check diffs the two.

### 1.2 Standing caveat, reproduced in every output

Naming an analogue asserts only that the statistic is constructed the same way from
micro-records. It asserts nothing about magnitude, unit, or population comparability. Per
**T1**, every result statement takes the form *"LLM agents of family X, under prompt Y, at
N = 1,000, produced Z"* — never *"people do Z"*. The exporter writes this string into
`MANIFEST.json` and every notebook prints it in its first cell.

### 1.3 Economic metrics

Definitions governed by `06-ECONOMY-SPEC.md` §12; cadences by §16.3. Restated with the
research-question column this document owns.

| Id | Metric | Definition (source) | Unit | Cadence | RQ |
|---|---|---|---|---|---|
| M1 | `gdp_nominal` | 06 §12 — `C + I + G` over `goods_transactions`, `CAPITAL_PURCHASED`, Δinventory, government purchases; transfers and asset trades excluded | cents | sim_quarter | A1, A4, A6 |
| M2 | `gdp_production` | 06 §12 — Σ(revenue − intermediates) | cents | sim_quarter | A1 |
| M3 | `gdp_real` | `10_000 × gdp_nominal // cpi` | cents | sim_quarter | A1, A4 |
| M4 | `unemployment_rate` | 06 §3.10 — `10_000 × |U(t)| // LF(t)` | bp | sim_day | A1, A4, B4 |
| M5 | `u_broad` | 06 §3.10 — adds marginally attached and involuntary part-time | bp | sim_day | A1, B4 |
| M6 | `lfpr` | `10_000 × LF(t) // |{alive, 18 ≤ age < retirement}|` | bp | sim_day | A1, A4 |
| M7 | `vacancy_rate` | `10_000 × V(t) // (V(t) + |E(t)|)` | bp | sim_day | A1 (Beveridge) |
| M8 | `cpi` | 06 §5.6 — Laspeyres, fixed genesis basket, base 10,000 | index_bp | sim_day | A1, A4 |
| M9 | `inflation_yoy` | `10_000 × cpi(t) // cpi(t − 1y) − 10_000` | bp | sim_quarter | A1 (Phillips), A4 |
| M10 | `gini_wealth` | 06 §12 — Gini over alive adults' net worth; report with `share_negative_networth` | bp | sim_quarter | A2 |
| M11 | `gini_income` | Gini over trailing-12-month gross income | bp | sim_quarter | A2, B4 |
| M12 | `median_wage` | Median annualised `employments.wage_cents` | cents | sim_week | A1, A2 |
| — | `mean_wage` | Mean annualised `employments.wage_cents` over open employments. **Owned here**; report only beside M12, since the mean/median gap is the point. | cents | sim_week | A2 |
| — | `wealth_share.top1` / `.top10` / `.bottom50` | Σ net worth of the quantile / Σ net worth of all alive adults, in bp. **Owned here.** Denominator may be ≤ 0 if aggregate net worth is negative; then the metric is not written and `wealth_share_undefined` is set to 1 | bp | sim_quarter | A2 |
| M13 | `labour_share` | `10_000 × Σ WAGE_PAID.gross_cents // gdp_nominal` | bp | sim_quarter | A1, A2 |
| M14 / M15 | `firm_entry_rate` / `firm_exit_rate` | 06 §12, annualised | bp | sim_year | A6 |
| M16 | `firm_size_tail_bp` | Hill estimator above the 80th percentile of `headcount` | bp | sim_year | A1 (Zipf) |
| M17 | `hhi_sector` | Σ(revenue share bp)² // 10,000, per sector | index_bp | sim_quarter | A6 |
| M18 | `market_index` | 06 §6.8 — divisor-adjusted cap-weighted, base 10,000 | index_bp | sim_day | A3 |
| M19 | `price_fair_value_gap_bp` | 06 §12 — price aggregate over dividend-discount fair value, minus 1 | bp | sim_quarter | **A3** |
| M20 | `credit_growth_yoy` | Σ`loans.outstanding_cents` YoY change | bp | sim_week | A5 |
| M21 | `credit_to_gdp_bp` | Σ outstanding // `gdp_nominal_ttm`; report the HP-filtered gap beside it | bp | sim_quarter | A5 |
| M22 | `default_rate` | Loans entering `status='default'` over loans current at window start, annualised | bp | sim_week | A5 |
| M23 | `bank_capital_ratio` | `10_000 × capital(B) // RWA(B)`, per bank and system-weighted | bp | sim_day | A5 |
| — | `bank.deposit_outflow_bp.<bank_id>` | **Owned here.** `10_000 × (Σ withdrawals − Σ deposits over a 1-sim-day window) // deposit balance at window start`, per bank. Negative on net inflow | bp | sim_day | A5, B2 |
| M24 | `m0`, `m1`, `velocity` | 06 §1.5 | cents / bp | tick (m0, m1), sim_quarter (velocity) | A4, V2 diagnostics |
| M25 | `policy_rate_bp`, `lending_rate_bp`, `term_spread_bp` | 06 §12 | bp | sim_day | A4, A5 |
| M26 | `ige_income_lifetime` | OLS slope of log child lifetime income on log parent lifetime income, completed lifetimes only | dimensionless_float | end_of_run | A2, B6 |
| M27 | `wage_scar_bp` | Mean `UNEMPLOYMENT_SPELL_ENDED.wage_change_bp` by spell-length bucket | bp | sim_year | A1, B4 |
| M28 | `venture_moic_bp` | Distributions over capital called, per fund vintage | bp | sim_year | A6 |

> **Registered naming collision.** `M26 ige_income_lifetime` (income, whole lifetime) and
> `mobility.iges` (`07-SOCIETY-SPEC.md` §10.4: wealth, anchored at age 40) are **different
> statistics**. The registry exports them as `ige_income_lifetime` and `ige_wealth_age40`.
> Any intergenerational claim must name the base variable and the anchor age. This is the
> archetypal §12 R1 failure and it is pinned here so it cannot recur silently.

### 1.4 Social metrics

Definitions governed by `07-SOCIETY-SPEC.md` §10.1–§10.5 and §5.7.

| Id | Definition (source) | Unit | Cadence | RQ |
|---|---|---|---|---|
| `polarisation.bc.<prop>` | 07 §5.7 — `BC = (g²+1) / (κ + 3(n−1)²/((n−2)(n−3)))` over living adults. `BC > 5/9` is *more bimodal than uniform*; report the number, not a verdict | dimensionless_float | sim_week | **B1** |
| `polarisation.dip.<prop>` / `.dip_p.<prop>` | Hartigan's dip statistic and p-value, same sample. A bimodality claim requires `BC > 5/9` **and** `dip_p < 0.05` | dimensionless_float | sim_week | B1 |
| `polarisation.var.<prop>` | `Var({b_i(p)})` | dimensionless_float | sim_week | B1 |
| `polarisation.index` | Mean `BC` over the 20 policy propositions | dimensionless_float | sim_week | B1 |
| `polarisation.affective` | Mean out-cluster tie valence minus in-cluster, Louvain clusters | dimensionless_float | sim_month | B1, B3 |
| `exposure.crosscut` (+ `_persuasive`, `_hostile`) | 07 §5.7 `CCE`, stance-weighted share of opposing-sign impressions over a 7-sim-day window, split at source trust 0.5 | bp | sim_week | **B1** |
| `consensus.time_to.<prop>` | First tick with `Var < 0.02` sustained 30 sim-days; null if never | ticks | on_event | B1, B3 |
| `trust.generalised`, `trust.institution.<k>` | Population mean of the corresponding `beliefs` proposition | dimensionless_float | sim_week | B1, B2, A5 |
| `trust.dyadic` | Mean `relationships.trust` over live non-kin ties | dimensionless_float | sim_week | B3 |
| `trust.behavioural` | Transactions with no-prior-relationship counterparties over all transactions, 30-sim-day window | bp | sim_week | B3 |
| `trust.promise_keeping` | `kept / (kept + broken)` over log-checkable obligations | bp | sim_week | B3, B5 |
| `trust.calibration` | Correlation of `b_i('trust.outlet.X')` with realised accuracy of X's articles that *i* saw | dimensionless_float | sim_month | B2 |
| `misinfo.exposure_reach(x)` | Agents into whose feed or news slot item *x* entered ≥ once | count | on_event | **B2** |
| `misinfo.adoption_reach(x)` | Agents with a `10060` on *x*'s target proposition, `source_ref` tracing to *x*, moving ≥ 0.05 toward the false claim | count | on_event | **B2** |
| `misinfo.believers(x,t)` | Agents on the false side of the truth by ≥ 0.2 at tick t | count | sim_day | B2 |
| `misinfo.half_life(x)` | `min{Δt : believers(t_peak+Δt) ≤ believers(t_peak)/2}`; also λ from an exponential fit with R². If R² < 0.5 only the empirical half-life is reported | sim_days | on_event | **B2** |
| `misinfo.correction_efficacy(x)` | 07 §10.3; defined only where an `11033` exists | dimensionless_float | on_event | B2 |
| `misinfo.share_of_impressions`, `misinfo.organic_share` | 07 §10.3. `organic_share` = false items with no `cause_seq` chain to a 99xxx injection | bp | sim_week | B2 |
| `network.degree_mean`, `network.degree_gini` | Over the live non-kin tie graph | count / bp | sim_week | B1, B3 |
| `network.clustering` | Global transitivity and mean local clustering | dimensionless_float | sim_week | B3 |
| `network.assortativity.<attr>` | Newman coefficient over `wealth_quintile · belief_cluster · district · education_level · party` | dimensionless_float | sim_week | B1, B3 |
| `network.modularity`, `network.ei_index`, `network.crosscut_tie_share`, `network.largest_component_share` | 07 §10.5 | dimensionless_float / bp | sim_week | B1, B3 |
| `segregation.dissimilarity.<attr>` | **Owned here.** Duncan index over districts: `0.5 × Σ_d |a_d/A − b_d/B|`, `attr` splitting the adult population into two groups (top-vs-bottom wealth half, majority-vs-minority belief cluster) | bp | sim_month | A2, B1 |
| `segregation.isolation.<attr>` | **Owned here.** `Σ_d (a_d/A)(a_d/n_d)` | bp | sim_month | A2 |
| `segregation.theil_h` | **Owned here.** Entropy-based multigroup segregation index over districts × wealth quintile | dimensionless_float | sim_month | A2 |

> Every `segregation.*` claim is entailed in part by `MECHANISM world.rent_response`
> (`05-WORLD-SPEC.md` §3.2), whose `entails` string states that income sorting across
> districts follows arithmetically from the affordability filter. §7 applies: report the
> `--mechanism-off world.rent_response` arm or do not use the word "emergent".

### 1.5 Political metrics

`turnout*` is governed by `07-SOCIETY-SPEC.md` §10.6. The remainder are **owned here**
because 07 does not define them.

| Id | Definition | Unit | Cadence | RQ |
|---|---|---|---|---|
| `turnout` | `|votes| / |eligible at voting_tick|` | bp | on_event (election) | B1, B4 |
| `turnout.deliberate` | Deliberate votes / deliberate-eligible. **Mandatory alongside any turnout claim** — a turnout difference that is only a cognition-routing difference is a T8 artefact | bp | on_event | T8 |
| `turnout.by_quintile`, `turnout.differential` | 07 §10.6 | bp | on_event | A2, B4 |
| `politics.vote_share.<party_id>` | Votes for the party's candidacies / valid votes cast in the election | bp | on_event | B1, B4 |
| `politics.enp` | Laakso–Taagepera effective number of parties, `1 / Σ s_i²` over vote shares | dimensionless_float | on_event | B1 |
| `politics.policy_volatility` | Count of `12030 POLICY_ENACTED` per sim-year over the closed parameter set (`07-SOCIETY-SPEC.md` §7.2), plus mean `|Δ|` in each parameter's normalised range | count / dimensionless_float | sim_year | A4, B4 |
| `politics.policy_reversal_rate` | Share of enactments moving a parameter back toward its value two enactments prior | bp | sim_year | B1 |
| `politics.incumbency_retention` | Elections won by the incumbent officeholder (or their party where the office is party-held), over elections with a running incumbent | bp | sim_year | B4 |
| `politics.platform_responsiveness` | Correlation across policy propositions between Δ(median voter stance) and Δ(enacted position), over a 1-sim-year lag | dimensionless_float | sim_year | B1, B4 |

Analogues, named separately: turnout ↔ electoral turnout (voting here costs one action slot
and nothing else); `politics.enp` ↔ ENP in comparative politics; `politics.policy_volatility`
↔ policy-change counts in comparative agendas research; `politics.incumbency_retention` ↔
incumbency advantage. Caveats per §1.2 and `07-SOCIETY-SPEC.md` §10.10.

### 1.6 Legal metrics

Governed by `07-SOCIETY-SPEC.md` §10.7–§10.9.

| Id | Definition (source) | Unit | Cadence | RQ |
|---|---|---|---|---|
| `crime.committed_rate`, `.by_type.<t>` | `|13010 in window| / (living adults · window_sim_years)` | count | sim_month | **B5** |
| `crime.reported_rate`, `crime.detected_rate` | `13012` / `13011` over the same denominator | count | sim_month | B5 |
| `crime.dark_figure` | committed / reported | dimensionless_float | sim_month | B5 |
| `crime.mean_p_detect` | Mean realised detection probability over crimes live in the window | bp | sim_month | **B5** |
| `crime.victimisation` | Distinct victims / living adults | bp | sim_month | B5 |
| `crime.recidivism` | `P(new 13010 within 1 sim-year | prior 13044)` | bp | sim_year | B5 |
| `conviction.rate` | Guilty/liable verdicts over all `13040` | bp | sim_month | B5 |
| `conviction.per_crime` | Guilty verdicts over **crimes committed** — the number that matters for deterrence | bp | sim_month | **B5** |
| `charge.rate`, `court.backlog`, `court.time_to_verdict`, `court.counsel_gap`, `court.bench_share` | 07 §10.8 | bp / count / ticks | sim_month | B5 |
| `incarceration.rate`, `.admissions`, `.mean_days`, `.by_quintile`, `prison.utilisation` | 07 §10.9 | bp / count / sim_days | sim_month | B5, A2 |

`court.bench_share` is a validity metric, not a finding: it caps the share of the docket
over which any judicial-behaviour claim can be made (`MECHANISM bench_rule`).

### 1.7 Demographic metrics

Governed by `07-SOCIETY-SPEC.md` §9–§10.4 where defined there; the rate metrics below are
**owned here**.

| Id | Definition | Unit | Cadence | RQ |
|---|---|---|---|---|
| `demog.population` | `|{agents : died_at_tick IS NULL}|` | count | sim_day | INV-POP |
| `demog.birth_rate` | `|2001 AGENT_BORN in window| / (mean living population · window_sim_years) × 1000` | count | sim_year | A2, B6 |
| `demog.death_rate` | `|2002 AGENT_DIED in window|`, same denominator | count | sim_year | A2 |
| `demog.tfr` | Σ over 5-year age bands of age-specific birth rates × band width, women-equivalent cohort | dimensionless_float | sim_year | — |
| `demog.life_expectancy_e0` | Period life table constructed from the window's age-specific death rates: `e0 = Σ_x l_x / l_0`. **A synthetic-cohort statistic, not observed longevity** | sim_years | sim_year | A2 |
| `demog.life_expectancy_gap_q1q5` | `e0` for the bottom vs top wealth quintile at age 30 | sim_years | sim_year | **A2** |
| `demog.median_age`, `demog.dependency_ratio`, `demog.mean_household_size` | Over living agents / `households` | dimensionless_float | sim_month | — |
| `demog.net_migration_rate` | (in − out) over mean living population, annualised | bp | sim_year | A2 (see `MECHANISM emigration_hazard`) |
| `mobility.ige_wealth_age40` (`mobility.iges`) | 07 §10.4 — OLS slope of ln(child wealth at 40) on ln(parent wealth at 40) | dimensionless_float | end_of_run | **A2** |
| `mobility.rank_rank` | Slope of child wealth percentile on parent wealth percentile. **Preferred over IGE** — robust to zero and negative wealth | dimensionless_float | end_of_run | **A2** |
| `mobility.transition`, `mobility.upward_q1` | 07 §10.4 | dimensionless_float / bp | end_of_run | A2 |
| `mobility.belief_ige` | Slope of child policy-stance vector on parent's, at age 30 | dimensionless_float | end_of_run | **B6** |

Every demographic metric is reported with `clock.demographic_acceleration`
(`02-ARCHITECTURE.md` §5.2), which is a declared MECHANISM. Rates are per sim-year of
agent-experienced time, not per wall-clock or per tick.

### 1.8 System metrics

**Owned here.** These are how you find out that the society you are studying is an artefact
of the budget, the parser, or the router.

| Id | Definition | Unit | Cadence | Serves |
|---|---|---|---|---|
| `sys.llm.calls`, `.tokens_in`, `.tokens_out` | Counts over `llm_calls` for the tick | count / tokens | tick | G4 |
| `sys.llm.cost_usd`, `.cost_usd_cum` | Σ`llm_calls.cost_usd` | usd | tick | G4, §3.4 |
| `sys.llm.cache_hit_rate` | `|cache_hit| / |calls|` for the tick; also cumulative and per purpose | bp | tick | D7, §3.7 |
| `sys.llm.parse_failure_rate` | `|parsed_ok = false| / |calls|`, **broken out by purpose and model** | bp | sim_day | **V7**, `04-AGENT-SPEC.md` §9.2 |
| `sys.llm.repair_rate` | `|repair_attempts > 0| / |calls|` | bp | sim_day | Model quality |
| `sys.llm.latency_p50_ms`, `.p99_ms` | Over non-cache-hit calls | ticks (ms) | sim_day | §11 performance |
| `sys.cognition.deliberate_share` | `|routed DELIBERATE| / |awake agents|` | bp | tick | **T8, T9** |
| `sys.cognition.reflect_share`, `.reflex_share` | Same denominator | bp | tick | T9 |
| `sys.cognition.salience_cutoff`, `.salience_p50`, `.salience_p90` | From kind 4002 | dimensionless_float | tick | **T8** |
| `sys.cognition.budget_exhausted` | 1 if `BUDGET_EXHAUSTED` fired this tick | count | tick | G4 |
| `sys.cognition.force_routed` | Agents force-routed by MANDATORY obligation | count | tick | T8 |
| `sys.simawareness.rate` | `|llm_calls.sim_aware_flag| / |calls|` | bp | sim_day | **T3** |
| `sys.action.entropy_norm` | §2.3 V4 | dimensionless_float | sim_day | **V4** |
| `sys.action.js_divergence_mean` | Mean pairwise Jensen–Shannon divergence between agents' 30-sim-day action distributions | dimensionless_float | sim_week | V4 |
| `sys.text.distinct3` | Distinct trigrams / total trigrams over the sim-day's speech and posts | bp | sim_day | **V4** (mode collapse shows in text first) |
| `sys.text.embed_cos_mean` | Mean pairwise cosine over a seeded sample of 500 post embeddings | dimensionless_float | sim_day | V4 |
| `sys.action.reject_rate.<reason>` | `ACTION_REJECTED` by reason over submitted actions | bp | sim_day | `04-AGENT-SPEC.md` §11 |
| `sys.engine.tick_wall_ms_p50/p99`, `sys.engine.phase_ms.<n>` | Wall-clock, from run metadata only — **never in an event payload** (`02-ARCHITECTURE.md` §4.5) | ticks (ms) | tick | `02` §11 |
| `sys.engine.events_per_tick`, `sys.store.commit_ms` | | count / ms | tick | `02` §11 |
| `sys.external.deadline_miss_rate`, `sys.external.actions` | From `external_agents` counters | bp / count | sim_day | T12 |
| `sys.ephemeral.dropped` | Ephemeral frames dropped under Redis backpressure | count | tick | §12 R3 |
| `sys.invariant.<id>.violations` | Cumulative `INVARIANT_VIOLATED` by invariant | count | tick | §2.2 |
| `gate.<Vn>.pass` | 1/0, written when the gate is evaluated | count | varies | §2.3 |

### 1.9 Relationships are not metrics

The Beveridge curve, Okun's law, the Phillips curve, Zipf's law of firm sizes, and
business-cycle autocorrelation are **relationships between** M3, M4, M7, M9 and M16, not
metrics (`06-ECONOMY-SPEC.md` §12). Each is computed by
`polis/research/relationships.py`, and each requires, before it may be claimed:

1. The falsification protocol in `06-ECONOMY-SPEC.md` §3.11 (Beveridge specifically).
2. The `--reflex-only` baseline (§6), all four.
3. A completed MECHANISM checklist (§7).
4. For Zipf: a report of the genesis seed's tail exponent alongside the measured one, and
   evidence the exponent moved (`06-ECONOMY-SPEC.md` §13.1 T6 note).

The same rule applies to any cross-metric correlation used as a headline finding.

### 1.10 Drift detection

```sql
-- Runs that computed a metric under a different definition. Blocking check in polis compare.
SELECT a.run_id, b.run_id, k AS metric
FROM runs a JOIN runs b ON a.run_id < b.run_id
CROSS JOIN LATERAL jsonb_object_keys(a.metric_manifest) k
WHERE a.metric_manifest->>k IS DISTINCT FROM b.metric_manifest->>k
  AND b.metric_manifest ? k;
```

`polis compare`, the Observatory run-comparison view (§8.2f), and every export that pools
runs execute this check first and **refuse** on a non-empty result unless
`--allow-metric-drift` is passed, which stamps `metric_drift: true` into the export manifest
and onto every figure produced from it. See §12 R1.

---

## 2. Invariants and validity gates as executable checks

### 2.1 Two families, two jobs

| Family | Runs | Purpose | Failure means |
|---|---|---|---|
| **INV-\*** (`02-ARCHITECTURE.md` §9) | In-engine, PHASE 9 (and immediately after every scenario step, §4.5) | The simulation is internally consistent *right now* | HALT class: the run is a bug report. WARN class: the run is suspect and flagged |
| **V1–V7** (`01-PRD.md` §7.2) | `polis gate`, post-hoc over the log and `metrics` | The run, or the experiment, is usable for research | The result may not leave the building (§11) |

V1–V4 are **per-run**. V5–V7 are **per-experiment** — they are statements about a set of
runs and cannot be evaluated on one. `polis gate --run <id>` evaluates V1–V4;
`polis gate --sweep <id>` evaluates V5–V7 and aggregates V1–V4 across cells.

### 2.2 INV-* as executable checks

| ID | Computation | Cadence | Verdict |
|---|---|---|---|
| **INV-MONEY** | Six sub-checks (`06-ECONOMY-SPEC.md` §1.7). Post-hoc re-derivable: `SELECT tick, SUM(direction*amount_cents) FROM ledger_entries WHERE run_id=$1 GROUP BY tick HAVING SUM(direction*amount_cents) <> 0` must return zero rows; and `SELECT SUM(balance_cents) FROM ledger_accounts WHERE run_id=$1` must be exactly 0 | every tick | **HALT** |
| **INV-LEDGER** | `post_transaction` asserts `Σ(direction × amount) == 0` before write; incremental balances reconciled against `SUM(direction*amount_cents)` per account at every checkpoint | every tick / checkpoint | **HALT** |
| **INV-SHARES** | Per symbol: `SUM(holdings.qty) == securities.shares_outstanding` | every tick | **HALT** |
| **INV-ORDERS** | Per resting order: reserved cents ≥ `qty_remaining × limit_price` for buys; `holdings.reserved_qty ≥ Σ` open sell qty | every tick | HALT |
| **INV-EMPLOY** | Every `employments` row with `ended_tick IS NULL` joins to exactly one agent with `died_at_tick IS NULL` and one firm with `dissolved_tick IS NULL` | every tick | HALT |
| **INV-CHAIN** | Recompute `hash` from the §3.1 canonical serialisation for every event since the last checkpoint; assert linkage to `prev_hash` | every checkpoint | HALT |
| **INV-POP** | `0.2 × N_0 ≤ demog.population ≤ 5 × N_0` | sim_day | WARN |
| **INV-ENTROPY** | `sys.action.entropy_norm ≥ entropy_floor` (0.35) | sim_day | WARN |
| **INV-NONDEGEN** | `wealth_share.top1 < 9000 bp`; `0 < employment share < 1`; ≤ 3 consecutive empty exchange sessions | sim_day | WARN |
| **INV-PRICE** | `06-ECONOMY-SPEC.md` §13.3 — WARN at `inflation_yoy > 40,000 bp`, HALT at the bound | sim_day | WARN → HALT |

**HALT policy.** Emit `1010 INVARIANT_VIOLATED{invariant_id, expected, actual, halting: true}`,
force a checkpoint, set `runs.status='halted'` and `halt_reason`, exit non-zero. Do not
attempt repair. `--continue-on-violation` exists for debugging only and sets a permanent
`tags @> {invariant_violated}` on the run; **a run carrying that tag can never appear in a
published figure** — enforced in the exporter, not by convention.

**WARN policy.** Emit `1010` with `halting: false`, increment `sys.invariant.<id>.violations`,
continue. A run whose WARN-class violations exceed the per-gate tolerance in §2.3 fails the
corresponding V-gate.

### 2.3 V1–V7 as executable procedures

Each gate is a function `(run|sweep) -> GateResult{verdict, statistic, threshold, window,
query, notes}`, written to `gate.<Vn>.pass` in `metrics` and emitted as kind 99060.

---

**V1 — Stationarity.** *Macro series are not monotonically exploding or collapsing over 5
sim-years absent a shock.*

Window: the longest contiguous shock-free interval — ticks with no ancestor in kinds
99000–99999 along `cause_seq` — of at least 5 sim-years. If no such window exists, V1 is
`n/a` and the run may not support any A1 claim.

| Series | Test | Pass threshold |
|---|---|---|
| `cpi` (M8), `market_index` (M18) | (a) Ratio of terminal to initial level; (b) OLS slope of log level on sim-year with Newey–West SE (lag 1 sim-year); (c) count of annual-difference sign changes | (a) ratio ∈ [1/e, e]; (b) `|β| < 0.15`/sim-year; (c) ≥ 4 sign changes over the window |
| `unemployment_rate` (M4) | Bounded-range test: annual mean and max annual change | mean ∈ [200, 3000] bp each sim-year; `|Δ| ≤ 1500` bp per sim-year |
| `gdp_real` (M3) | Log-level slope; absorbing-state check | `|β| < 0.20`/sim-year; no 3 consecutive sim-years of same-sign change of `|Δ| > 20%` |

Verdict: PASS if all rows pass. FAIL otherwise. Not a HALT — a collapsing economy is
diagnostic data, and the reason for failure is itself reportable
(`01-PRD.md` §11 "the findings are boring").

---

**V2 — Accounting closure.** *Money is conserved to the cent at every tick.*

```
V2.pass  ⟺  count(INVARIANT_VIOLATED where invariant_id in ('INV-MONEY','INV-LEDGER')) == 0
        AND the post-hoc ledger queries in §2.2 return zero rows
        AND ticks_checked == runs.last_tick + 1        # the check actually ran every tick
```

The third clause exists because the most dangerous failure is not a violated invariant, it
is an invariant that quietly stopped running. Cadence: every tick in-engine, plus one
post-hoc re-derivation from `ledger_entries` in `polis gate`. Verdict: PASS/FAIL. A FAIL
here voids the run entirely; no other gate result is reported for it.

---

**V3 — Non-degenerate distributions.**

| Check | Threshold | Cadence |
|---|---|---|
| `wealth_share.top1` | < 9,000 bp | sim_day |
| Employment share | strictly in (0, 1); equivalently `unemployment_rate ∈ (50, 6000)` bp | sim_day |
| Consecutive exchange sessions with zero trades | ≤ 3 | per session |
| Active firms | ≥ 5 | sim_day |
| Adults with zero transactions in 30 sim-days | < 50% | sim_week |

Run-level verdict: PASS if no check fails on more than **5%** of its evaluations. FAIL
otherwise. A V3 failure is more often an exploit (T10) than an economic collapse — check
`06-ECONOMY-SPEC.md` §14 F4/F5/F8 before concluding anything.

---

**V4 — Behavioural diversity.** Mode collapse is the characteristic LLM-society failure and
it appears in language before it appears in actions, so V4 tests both.

```
Action entropy, over one sim-day, across all agents:
    p_a  = count(actions of type a) / count(all actions)
    H    = -Σ_a p_a · log p_a
    H_norm = H / log(|action types legal to ≥ 1 agent in this run|)
    PASS: H_norm ≥ 0.35

Between-agent divergence, over 30 sim-days:
    mean pairwise Jensen–Shannon divergence of per-agent action distributions
    PASS: ≥ 0.10

Lexical diversity, over one sim-day of speech + posts:
    distinct-3 = |distinct trigrams| / |trigrams|          PASS: ≥ 0.55
    mean pairwise cosine over 500 seeded-sampled post embeddings   PASS: ≤ 0.85
```

Run-level verdict: PASS if each sub-check is above floor on ≥ 90% of its evaluations.
Reported per sub-check — "actions diverse, language collapsed" is a real and interesting
state, and a single boolean would hide it.

---

**V5 — Sensitivity across seeds.** Per-experiment.

```
Inputs: the pre-registered headline effect θ, ≥ 20 seeds per arm.
1. Compute θ_s for every seed s (paired across arms where the design is seed-matched).
2. sign_agreement = |{s : sign(θ_s) == sign(mean θ)}| / n
3. CI = 10,000-resample percentile bootstrap over seeds, rng.get("research.bootstrap", metric, 0)
4. between_seed_cv = sd(θ_s) / |mean(θ_s)|
PASS ⟺ n ≥ 20 AND sign_agreement ≥ 0.80 AND CI excludes 0 AND between_seed_cv > 0.01
```

The last clause is a trap detector: a between-seed CV of ~0 means the seeds are not actually
producing different worlds, which means an RNG namespace is missing a `tick=` or an
`entity_id` (`02-ARCHITECTURE.md` §4.1). That is a bug, not a strong result.

---

**V6 — Prompt robustness.** Per-experiment. Re-run the headline cell with the
`prompts/paraphrase/` sibling set (`04-AGENT-SPEC.md` §13), same seeds, same config.

```
PASS ⟺ sign(θ_paraphrase) == sign(θ_base)
    AND (CI_paraphrase ∩ CI_base ≠ ∅)
    AND (CI_base excludes 0 ⇒ CI_paraphrase excludes 0)
Report: θ_base, θ_paraphrase, ratio, both CIs. Never report only the base.
```

**Cost note.** The paraphrase changes `prompt_template_hash`, so the completion cache misses
completely (`02-ARCHITECTURE.md` §4.4). V6 costs a full second cell at live prices. Budget
for it in §3.4 or the gate will be skipped at exactly the moment it matters.

---

**V7 — Model robustness.** Per-experiment. ≥ 2 model families (`09-MODEL-ROUTING.md`),
≥ 10 seeds each, identical config otherwise.

```
PASS ⟺ V6's sign/CI rule holds across families
    AND every arm's sys.llm.parse_failure_rate < 500 bp
    AND no cell mixes model_versions (T5)
Report per family: θ, CI, parse_failure_rate, sim_awareness rate, cost.
```

A family that cannot hold the output schema does not produce a comparable arm; the
parse-failure clause stops a cheap model's failures being read as a behavioural difference.

### 2.4 The gate report

`polis gate --run <id> --out gates/gate_report.json` produces:

```json
{"run_id":"…","code_git_sha":"…","evaluated_at":"…",
 "gates":[{"id":"V1","verdict":"pass","statistic":{"cpi_beta":0.04,"u_mean_bp":[610,655,…]},
           "threshold":{"cpi_beta_abs_max":0.15},"window":{"from_tick":8640,"to_tick":51840},
           "shock_free":true,"query":"…","notes":""}],
 "invariants":{"INV-MONEY":{"violations":0,"ticks_checked":43201}},
 "verdict":"pass","blocking_failures":[]}
```

The report is a required member of the reproducibility package (§5.3) and of the
paper-readiness checklist (§11). `polis gate` is deterministic given the run and the code
sha; two evaluations of the same run must produce byte-identical JSON.

---

## 3. The experiment harness

### 3.1 Pre-registration, and why

The researcher writes the engine, chooses the metrics, sees the data, and picks the test.
The completion cache makes re-analysis free — which makes p-hacking free. A sweep of 12 grid
points × 20 seeds × 40 metrics offers roughly ten thousand defensible-looking comparisons,
of which several hundred will clear p < 0.05 under the null. Nothing in the architecture
prevents running the sweep, looking at the results, and then declaring which comparison was
"the" hypothesis. Pre-registration is the cheapest available defence, and here it is
mechanical rather than moral:

1. The analysis plan is written **into the experiment YAML** before the run.
2. `polis sweep` hashes it into `sweeps.analysis_plan_hash` and stores it in
   `sweeps.preregistration` **before the first cell launches**. The hash is written into
   every child run's `RUN_STARTED` payload.
3. The report generator reads only the declared `primary` and `secondary` metrics. Anything
   else is reachable, but is labelled `exploratory: true` in the export and cannot be a
   headline claim without a fresh confirmatory sweep with **new seeds** (§10.3).
4. Deviations are permitted and expected; they go in a `deviations:` block appended
   post-hoc, each with a reason, and each figure derived from a deviation carries the label.

This does not make anyone honest. It makes dishonesty require a visible edit to a hashed
artefact, which is the most that tooling can do.

### 3.2 Experiment definition

`configs/experiments/*.yaml`. One file fully specifies a sweep.

```yaml
experiment:
  id: exp_b1_feed_polarisation_v2
  title: "Feed ranking and belief polarisation at 1k agents"
  research_questions: [B1]
  owner: ali
  created: 2026-08-02

base_config: configs/baseline-1k.yaml       # hashed; must be committed

grid:                                        # full factorial unless `design:` says otherwise
  society.feed_algorithm: [chronological, engagement, random, adversarial]
  society.feed_engagement_prior: [outrage_positive, zero]     # MECHANISM ablation, 07 §3.3
design: factorial                            # factorial | one_at_a_time | list
seeds: [20260801, 20260802, 20260803, …]     # 20 explicit integers; never a range expression
                                             # generated by `polis seeds --n 20 --from 20260801`
scale_ladder: [1000]                         # see §10.4; [250,500,1000,2000] for a T7 claim

metrics_of_interest:
  primary:   [polarisation.index]            # exactly one primary per research question
  secondary: [polarisation.bc.tax.rate.should_rise, exposure.crosscut,
              network.assortativity.belief_cluster, consensus.time_to.tax.rate.should_rise]
  guardrail: [sys.cognition.deliberate_share, sys.simawareness.rate,
              sys.action.entropy_norm, turnout.deliberate, sys.llm.parse_failure_rate]

analysis_plan:                               # hashed into sweeps.analysis_plan_hash
  unit_of_replication: seed
  estimator: "mean over seeds of polarisation.index averaged over the final sim-year"
  contrast: "engagement − chronological, seed-paired"
  test: "paired percentile bootstrap over seeds, 10000 resamples, two-sided"
  effect_size: "Hedges' g across seeds, plus the raw difference in BC units"
  ci: "95% percentile bootstrap"
  multiplicity: "primary unadjusted; secondary Benjamini-Hochberg at q=0.10 within this family"
  exclusions: "cells failing V1–V4; declared here so exclusion is not a choice made later"
  minimum_detectable_effect: 0.04            # from the pilot's between-seed SD, §10.2
  prediction: "engagement > chronological on polarisation.index; sign stated before the run"

required_ablations: [reflex_only, salience_random, mechanism_off_belief_social_influence,
                     mechanism_off_belief_backfire]     # §6; each becomes extra cells
gates_required: [V1, V2, V3, V4, V5]                    # V6/V7 declared separately when publishing

budget:
  usd_max: 240.0
  halt_at_pct: 120                            # circuit breaker, 01-PRD.md §11
  cache_mode: hybrid

execution:
  parallel: 6
  retention: metrics_only                     # 03-DATA-MODEL.md §11; headline cells override
  headline_cells: [{society.feed_algorithm: engagement, seed: 20260801},
                   {society.feed_algorithm: chronological, seed: 20260801}]  # keep full events
```

Validation at load: every metric id exists in the registry; every grid key exists in the
config schema; seeds are explicit and unique; `primary` has exactly one entry per research
question; `prediction` is non-empty; `required_ablations` covers every MECHANISM whose
`entails` string mentions a `primary` or `secondary` metric (§7 step 4 is run **at load
time**, and a missing ablation is a load error, not a review finding).

### 3.3 `polis sweep` semantics

```
polis sweep <experiment.yaml> [--estimate] [--dry-run] [--parallel N] [--resume <sweep_id>]
            [--cell <cell_id>…] [--cache-mode live|replay|hybrid] [--yes]
```

| Concept | Definition |
|---|---|
| **Cell** | One grid point × one seed × one ablation arm. The unit of scheduling and of resume. |
| **cell_id** | `sha256(base_config_hash ‖ canonical_json(overrides) ‖ seed ‖ ablation_key)[:16]`. Deterministic, so re-invoking a sweep is idempotent. |
| **Cell → run** | Each cell is one `polis run` with `parent_run_id` = the sweep's base run and `sweep_id` set. Config overrides are applied to the base config and the merged config is re-hashed. |
| **Ordering** | Cells execute in `cell_id` order **after** the cache-warming stage (§3.7), so a sweep's schedule does not depend on machine speed. |
| **Isolation** | Cells share one Postgres and one completion cache and nothing else. There is no cross-run state (`03-DATA-MODEL.md` §0). |
| **Failure policy** | A cell that HALTs is marked `halted` and the sweep continues. If halted cells exceed `halt_tolerance` (default 10%), the sweep aborts — that is a model bug, not a property of the grid. |
| **Budget** | Per-cell budget from `base_config.llm.budget`; a sweep-level circuit breaker aborts at `halt_at_pct` of `budget.usd_max`. |
| **Output** | `sweeps` row, one `runs` row per cell, `metrics` per cell, and `exports/<sweep_id>/` per §9. |

`--dry-run` prints the cell list, the merged config hash per cell, and the ablation arms,
and writes nothing. `--estimate` additionally runs the probe in §3.4.

### 3.4 Cost estimation before launch

Launching a 480-cell sweep and discovering the price afterwards is the most expensive
mistake available in this system. `polis sweep --estimate`:

1. Runs a **200-tick probe** of the base cell in `hybrid` mode with a fresh cache namespace.
2. Measures `calls_per_tick`, mean `tokens_in`, mean `tokens_out`, and realised
   `cache_hit_rate` from `llm_calls`.
3. Runs a **second 200-tick probe** at a different grid point to measure the *cross-cell*
   hit rate `h_x` — the share of prompts shared between cells. This is the number that
   determines whether the sweep costs 1× or 20× the base cell.
4. Extrapolates:

```
cost_cold  = ticks × calls_per_tick × (t_in·p_in + t_out·p_out)          # first cell, cold cache
cost_warm  = cost_cold × (1 − h_x)
cost_sweep = cost_cold + (n_cells − 1) × cost_warm + n_V6_cells × cost_cold
p90        = cost_sweep × (1 + 1.28 × cv_probe)      # cv from per-tick cost variance
```

5. Prints the table below and **refuses to launch** if `p90 > budget.usd_max` without
   `--yes`. The estimate is written to `sweeps.cost_estimate_usd`; the realised total to
   `cost_actual_usd`. A ratio outside [0.5, 2.0] is reported at sweep completion, because a
   badly wrong estimator is itself a bug worth fixing.

| Line | Value |
|---|---|
| Cells | 4 arms × 2 priors × 20 seeds + 4 ablation arms × 20 seeds = 240 |
| Ticks/cell | 43,200 |
| Calls/tick (probe) | 88 |
| Cross-cell cache hit rate | 0.71 |
| Estimated p50 / p90 | $164 / $211 |
| Budget | $240 |

### 3.5 Parallel execution

`--parallel N` runs N cells concurrently as separate `polis run` processes. Each is
single-threaded for mutation (`02-ARCHITECTURE.md` §4.3); parallelism is across cells, never
within one. Bounds: `N ≤ min(cores // 2, db_connections // 4, provider_rate_limit //
llm.max_concurrency)`. Determinism is unaffected — cells share no state, and the completion
cache is content-addressed, so two cells racing on the same key both get the same value
(last writer wins on an identical payload). The scheduler is a simple bounded worker pool
over the sorted cell list; there is no work stealing, because reproducible scheduling is
worth more than the last 10% of utilisation.

### 3.6 Resumability

```
polis sweep exp.yaml --resume <sweep_id>
```

Recomputes the cell list from the experiment file, joins against `runs` on `cell_id` (stored
in `runs.tags` as `cell:<id>`), and launches only cells with no `completed` run. A cell that
is `running` with a stale heartbeat is resumed at the run level via
`polis run --resume <run_id>` (`02-ARCHITECTURE.md` §5.3), which loads the newest checkpoint
and replays events after it. If the experiment file changed since the sweep was created,
`--resume` refuses: `analysis_plan_hash` mismatch is a pre-registration violation, not a
merge conflict.

### 3.7 Why the cache makes sweeps cheap

The completion cache is keyed on `(provider, model, model_version, prompt_template_hash,
prompt_variables, sampling_params, call_seed)` (`02-ARCHITECTURE.md` §4.4). A sweep varies a
config parameter; most agents at most ticks are in situations the parameter does not touch,
so their rendered `prompt_variables` are byte-identical across cells and every one of those
calls is a hit. The observed factor is 5–20×.

Two consequences that shape the harness:

| Consequence | Design response |
|---|---|
| The first cell pays for everything the rest reuse | Stage 1 runs a single designated `cache_seed` cell to completion before Stage 2 fans out. Fanning out first means N cells each paying cold-cache prices for the same prompts. |
| A parameter that enters the *system prompt* destroys the sharing | The estimator's `h_x` probe (§3.4) detects this before launch. If `h_x < 0.2`, the sweep is priced as N independent runs and the estimate says so. |

`--cache-mode replay` makes a cell free and offline: any miss is a hard error. That is the
mode a third party uses (§5.4).

---

## 4. The scenario / shock DSL

### 4.1 Shape and lineage

Modelled on Block Buzz's YAML workflow engine (`00-INDEX.md`, `02-ARCHITECTURE.md` §13): a
scenario is a set of **triggers** and **steps**, kind-dispatched, declarative, hashed.
Nothing about it is imperative and nothing about it is Python. `polis/research/scenario.py`
loads, validates, signs, and executes it.

```yaml
scenario:
  id: sc_recession_v1
  name: "Monetary tightening with fiscal contraction"
  dsl_version: 1
  researcher_key: rk_ali_2026            # pubkey id; the private key signs each injection
  research_questions: [A4, A5, B4]
  guards:
    respect_invariants: true             # may not be set false; the loader rejects it
    on_guard_violation: abort            # abort | skip
    max_injections: 200
    max_seeded_agents: 100
  funding:                               # every cent a scenario spends is declared here
    account: government
    cap_cents: 0
  paired_control: true                   # launch an identical run with steps: [] and the same seeds
  expects:                               # pre-registered, checked by polis gate
    - {metric: unemployment_rate, direction: up, within_sim_days: 180}
    - {metric: default_rate, direction: up, within_sim_days: 360}
  triggers: [...]
  steps: [...]
```

`scenario_hash = sha256(canonical_yaml)`. It is recorded in `RUN_STARTED`, in every
injection event, and in `scenario_injections.scenario_hash`.

### 4.2 Trigger types

| Type | Fields | Evaluated | Firing rule |
|---|---|---|---|
| `at_tick` | `tick` | PHASE 0 | Once, at that tick |
| `at_sim_time` | `sim_time: "Y2-M03-D01T09:00"` | Load time | Sugar; the Clock resolves it to a tick at load, so the trigger is an `at_tick` by the time the run starts |
| `on_metric_threshold` | `metric, op (lt/le/gt/ge), value, sustained_for (sim-days), max_fires, cooldown_ticks` | PHASE 9, after the metric is written | Fires when the condition has held for `sustained_for`; steps apply at PHASE 0 of the **next** tick |
| `on_event_kind` | `kind, match (payload predicates), actor_in, subject_in, max_fires` | PHASE 6, on append | Steps are queued and applied at PHASE 0 of the **next** tick — never mid-commit |
| `schedule` | `cadence: {every: 1d, from:, until:}, at: "09:00", jitter_ticks` | PHASE 0, via the same Scheduler as institutional cadences (`02-ARCHITECTURE.md` §5.2) | Repeating; `jitter` draws from `rng.get("research.scenario.jitter", trigger_id, tick)` |

**Universal timing rule.** Triggers are evaluated at phase boundaries; **all steps apply at
PHASE 0**. Nothing in this DSL can mutate state mid-tick. This preserves "no phase reads a
state change made later in the same tick" (`02-ARCHITECTURE.md` §5) and is what makes a
scenario run replayable.

### 4.3 Step / action types

Every step has `id`, `trigger`, optional `if` (a guard over metric or state predicates), and
`action` with `params`. Selectors (`selector: {where: …, sample: N}`) draw with
`rng.get("research.scenario.select", step_id, tick)`.

| Action | Params | Emits | Constraints |
|---|---|---|---|
| `set_parameter` | `parameter, value, scope (global/district/firm/agent), ramp_ticks, revert_after` | 99010 (+99011 on revert) | Parameter must be in the closed policy-controllable set (`07-SOCIETY-SPEC.md` §7.2) or a scenario-writable config key, and must satisfy that registry's admissible range. `value` is a literal, or `@scale(f)` / `@delta(x)` resolved against the value in force at fire time — recorded expanded in the 99010 payload so replay never re-resolves. **May not change a `MECHANISM` key mid-run** — that would invalidate `runs.mechanism_manifest`; mechanisms are set at run start or not at all |
| `inject_event` | `kind, actor_id, subject_ids, payload` | 99001 + the event, whose `cause_seq` points at the 99001 | Kind must be on the injectable allowlist in `kinds.py`. A kind whose handler moves money is injectable only if the payload carries balanced legs |
| `kill_entity` | `entity_id, cause, settle: true` | 99020 + the domain events | Runs the **normal** settlement path (`04-AGENT-SPEC.md` §12.3 for agents, `06-ECONOMY-SPEC.md` §10 for firms/banks). There is no raw delete; a scenario cannot make an entity vanish without its obligations resolving |
| `spawn_entity` | `entity_type, count, spec, placement` | 99021 + `AGENT_BORN` / `FIRM_FOUNDED` | Any endowment is funded from `funding.account` within `cap_cents`. **No money from nothing** |
| `force_action` | `selector, action {type, params}, mode (replace/append), ticks` | 99030 + the action enters PHASE 3 with `origin="scripted"` | The action passes PHASE 4 validation like any other. A forced illegal action is rejected and logged, not privileged |
| `publish_falsehood` | `carrier (selector/outlet_id), target_proposition, claimed_value, text, checkable: true` | 99040 + `POST_PUBLISHED` / `ARTICLE_PUBLISHED` with `truthfulness` computed against the log | The claim must be checkable against the event log at load time; an uncheckable claim is a load error, because an unmeasurable falsehood is useless for B2 |
| `seed_rumour` | `selector, proposition, claimed_value, confidence, source_label` | 99041 + `10060 BELIEF_UPDATED` per agent with `source='injected'` | Bounded by `guards.max_seeded_agents`; beliefs are written through the normal update path (`07-SOCIETY-SPEC.md` §5.4), never by direct table write |
| `annotate` | `text` | 99080 | No state effect. The lab notebook, in the log, at the tick it refers to |
| `abort_run` | `reason` | 99006 | Ends the run cleanly with `status='halted'`, `halt_reason` set |

### 4.4 Signing and recording

Every injection-class step (99001, 99010, 99020, 99021, 99030, 99040, 99041) is signed:

```
injection_digest = sha256(scenario_hash ‖ step_id ‖ tick.to_bytes(8,"big")
                          ‖ canonical_json(params))
sig              = ed25519_sign(researcher_privkey, injection_digest)
```

`sig` goes into the `Event` envelope (`02-ARCHITECTURE.md` §3.1, §3.4) and therefore into
the hash chain. A row is written to `scenario_injections` with `step_id`, `event_seq`,
`scenario_hash`, `researcher_pubkey`, and `sig`. `polis verify` (§5.2) checks every 99xxx
injection-class event against the run's declared researcher pubkey and **fails verification**
if any is missing, invalid, or lacks a `scenario_injections` row.

The consequence is the point: in a verified run, **an unsigned shock cannot exist**, so no
organic event can be mistaken for an injection and no injection can hide as organic. That is
what makes `misinfo.organic_share` (`07-SOCIETY-SPEC.md` §10.3) meaningful, and it is why
`02-ARCHITECTURE.md` §3.4 signs scenario injections despite not signing native actions.

Observation-class kinds (99060 gate results, 99070 metric definitions) are engine-emitted
and unsigned; they are covered by chain integrity.

### 4.5 A scenario may not violate invariants

`guards.respect_invariants` is `true` and the loader rejects any attempt to set it false.
Mechanically:

1. Steps apply at PHASE 0, inside the tick's transaction.
2. Immediately after each step, the `InvariantRunner` runs the **HALT-class** invariants
   (INV-MONEY, INV-LEDGER, INV-SHARES, INV-ORDERS, INV-EMPLOY) — not waiting for PHASE 9.
3. On violation the step is rolled back within the transaction, `99004
   SCENARIO_STEP_SKIPPED{reason:'invariant_guard', invariant_id}` is emitted, and the
   scenario aborts or skips per `on_guard_violation` (default **abort**: a scenario that
   silently did not happen is worse than a run that failed).
4. `set_parameter` additionally passes the policy-engine bounds check
   (`07-SOCIETY-SPEC.md` §7.4) — a shock has exactly the same reach as a law, no more.

A scenario is therefore incapable of creating money, destroying shares, orphaning an
employment record, or leaving a resting order unfunded. The shocks it *can* deliver are the
ones a government or a bankruptcy could deliver, which is the correct expressive limit.

### 4.6 Kinds 99000–99999

| Kind | Name | Payload | Signed |
|---|---|---|---|
| 99000 | `SCENARIO_LOADED` | `scenario_id, name, scenario_hash, dsl_version, researcher_pubkey, triggers_n, steps_n, guards` | yes |
| 99001 | `SHOCK_INJECTED` | `injection_id, scenario_id, step_id, action, parameter, old_value, value, target_ids[], trigger_id` | **yes** |
| 99002 | `SCENARIO_TRIGGER_FIRED` | `scenario_id, trigger_id, trigger_type, condition, evaluated_value, fire_count` | no |
| 99003 | `SCENARIO_STEP_APPLIED` | `step_id, action, params, target_ids[], resulting_seqs[]` | no |
| 99004 | `SCENARIO_STEP_SKIPPED` | `step_id, reason (guard_failed/invariant_guard/target_missing/max_fires/cap_exceeded), detail` | no |
| 99005 | `SCENARIO_COMPLETED` | `scenario_id, steps_applied, steps_skipped, last_tick` | no |
| 99006 | `SCENARIO_ABORTED` | `scenario_id, reason, invariant_id, step_id` | no |
| 99010 | `PARAMETER_SET` | `parameter, scope, old_value, new_value, ramp_ticks, revert_at_tick, step_id` | **yes** |
| 99011 | `PARAMETER_REVERTED` | `parameter, scope, from_value, to_value, step_id` | yes |
| 99020 | `ENTITY_KILLED` | `entity_id, entity_type, cause, settlement_seqs[]` | **yes** |
| 99021 | `ENTITY_SPAWNED` | `entity_id, entity_type, spec, funding_txn_id` | **yes** |
| 99030 | `ACTION_FORCED` | `actor_id, action_type, params, original_mode, accepted (bool), reject_reason` | **yes** |
| 99040 | `FALSEHOOD_PUBLISHED` | `item_id, item_kind (post/article), carrier_id, target_proposition, claimed_value, true_value, checkable, source_event_seqs[]` | **yes** |
| 99041 | `RUMOUR_SEEDED` | `proposition, claimed_value, confidence, seed_agent_ids[], source_label` | **yes** |
| 99050 | `ABLATION_APPLIED` | `ablation_id, params, affected_mechanisms[]` | no (config-derived, emitted at tick 0) |
| 99060 | `GATE_EVALUATED` | `gate_id, verdict, statistic, threshold, window, code_git_sha` | no |
| 99070 | `METRIC_DEFINITION_REGISTERED` | `metric_id, definition_hash, unit, cadence, rq[], governed_by` | no (emitted once per metric at tick 0) |
| 99080 | `RESEARCHER_NOTE` | `text, author, refs[]` | yes |
| 99090 / 99091 | `EXPERIMENT_CELL_STARTED` / `_COMPLETED` | `sweep_id, cell_id, overrides, seed, ablation_key` (+ `status, cost_usd, gates`) | no |

### 4.7 Worked scenario A — recession

**Purpose:** A4 (policy-shock transmission), A5 (credit cycle), B4 (precarity → politics).

```yaml
scenario:
  id: sc_recession_v1
  name: "Monetary tightening with fiscal contraction"
  dsl_version: 1
  researcher_key: rk_ali_2026
  research_questions: [A4, A5, B4]
  guards: {respect_invariants: true, on_guard_violation: abort, max_injections: 40}
  funding: {account: government, cap_cents: 0}
  paired_control: true
  expects:
    - {metric: unemployment_rate,  direction: up,   within_sim_days: 180}
    - {metric: credit_growth_yoy,  direction: down, within_sim_days: 180}
    - {metric: default_rate,       direction: up,   within_sim_days: 360}
    - {metric: gdp_real,           direction: down, within_sim_days: 270}

  triggers:
    - {id: t_onset,    type: at_sim_time, sim_time: "Y3-M01-D01T09:00"}
    - {id: t_recovery, type: on_metric_threshold, metric: unemployment_rate,
       op: ge, value: 1200, sustained_for: 60d, max_fires: 1}
    - {id: t_watch,    type: schedule, cadence: {every: 30d, from: "Y3-M01-D01", until: "Y5-M01-D01"}}

  steps:
    - id: s_rate_hike
      trigger: t_onset
      action: set_parameter
      params: {parameter: money.policy_rate, value: 0.11, ramp_ticks: 720}   # 0.04 → 0.11 over 30 sim-days
    - id: s_fiscal_contraction
      trigger: t_onset
      action: set_parameter
      params: {parameter: welfare.unemployment_benefit_cents, value: "@scale(0.70)",  # −30%
               revert_after: 360d}
    - id: s_note_onset
      trigger: t_onset
      action: annotate
      params: {text: "A4 shock onset: +700bp policy rate over 30d, −30% unemployment benefit for 1y."}
    - id: s_policy_reaction
      trigger: t_recovery
      action: set_parameter
      params: {parameter: money.policy_rate, value: 0.05, ramp_ticks: 1440}
    - id: s_monthly_note
      trigger: t_watch
      action: annotate
      params: {text: "monthly checkpoint"}
```

**What it must not do.** It does not fire anyone, does not touch beliefs, does not adjust
firm behaviour. Every labour-market and credit response must come from agents and
institutions reacting to two prices. A recession scenario that lays people off is a scenario
that assumes its own conclusion.

**Measured:** M3, M4, M5, M7, M9, M20, M22, M23, M25 (impulse responses against the paired
control); `trust.institution.*`, `politics.vote_share.*`, `polarisation.index` for B4;
`crime.committed_rate` for the precarity–crime channel.

**Identification.** The `paired_control: true` run is seed-matched and injection-free. The
estimate is the seed-paired difference in the impulse response, not a before/after within
one run — a before/after would confound the shock with everything else the run was doing.

### 4.8 Worked scenario B — bank failure

**Purpose:** A5 (endogenous credit cycles, interbank contagion), B2 (trust dynamics under
institutional stress).

```yaml
scenario:
  id: sc_bank_failure_v1
  name: "Large-borrower default into a deposit run"
  dsl_version: 1
  researcher_key: rk_ali_2026
  research_questions: [A5, B2]
  guards: {respect_invariants: true, on_guard_violation: abort, max_seeded_agents: 120}
  funding: {account: central_bank, cap_cents: 0}
  paired_control: true
  expects:
    - {metric: bank_capital_ratio, direction: down, within_sim_days: 30}
    - {metric: credit_growth_yoy,  direction: down, within_sim_days: 120}

  triggers:
    - {id: t_default, type: at_sim_time, sim_time: "Y4-M06-D10T22:00"}
    - {id: t_filed,   type: on_event_kind, kind: 9030,
       match: {"$.liabilities_cents": {gte: 50000000}}, max_fires: 1}
    - {id: t_outflow, type: on_metric_threshold, metric: bank.deposit_outflow_bp.bk_02,
       op: ge, value: 2000, sustained_for: 3d, max_fires: 1}

  steps:
    - id: s_kill_borrower
      trigger: t_default
      action: kill_entity
      params: {entity_id: "@largest_borrower(bk_02)", cause: "fraud_discovered", settle: true}
    - id: s_trust_shock
      trigger: t_filed
      action: seed_rumour
      params: {selector: {where: "has_deposit_at(bk_02)", sample: 120},
               proposition: "trust.institution.bank.bk_02", claimed_value: -0.6,
               confidence: 0.5, source_label: "counterparty_chatter"}
    - id: s_note_run
      trigger: t_outflow
      action: annotate
      params: {text: "Deposit outflow at bk_02 exceeded 2000bp sustained 3d; observing settlement."}
```

**Why it is built this way.** The bank is not killed. A large borrower is killed through the
*normal* bankruptcy path (`06-ECONOMY-SPEC.md` §10), so the write-off lands on the lender's
balance sheet as a real loss, INV-MONEY holds across it, and everything downstream — capital
ratio, discount-window use, interbank exposure, deposit behaviour, whether the bank actually
fails — is the model's answer rather than the scenario's assumption. `s_trust_shock` supplies
the narrative channel; the run/no-run outcome remains endogenous. If the scenario simply set
`bk_02.status='failed'`, the contagion result would be a tautology.

**Measured:** M22, M23, M20, M21, M25; interbank cascade size (count of banks whose capital
ratio crosses the minimum within 30 sim-days of the first); `trust.institution.bank.*`;
`trust.behavioural`; deposit-outflow series per bank; `misinfo.organic_share` on the rumour.

### 4.9 Worked scenario C — coordinated misinformation campaign

**Purpose:** B2 (propagation and death of false beliefs), B1 interaction (feed algorithm).

```yaml
scenario:
  id: sc_coord_misinfo_v1
  name: "Coordinated inauthentic campaign against a listed firm"
  dsl_version: 1
  researcher_key: rk_ali_2026
  research_questions: [B2, B1, A3]
  guards: {respect_invariants: true, on_guard_violation: abort,
           max_injections: 400, max_seeded_agents: 0}
  funding: {account: scenario_endowment, cap_cents: 4000000}   # 20 agents × 200k, MONEY_ISSUED at spawn
  paired_control: true
  expects:
    - {metric: "misinfo.adoption_reach(sc_coord_misinfo_v1)", direction: up, within_sim_days: 30}

  triggers:
    - {id: t_setup,      type: at_sim_time, sim_time: "Y4-M02-D01T08:00"}
    - {id: t_daily,      type: schedule,
       cadence: {every: 1d, from: "Y4-M02-D02", until: "Y4-M03-D02"}, at: "09:00", jitter_ticks: 3}
    - {id: t_amplify,    type: on_event_kind, kind: 11010,
       match: {"$.author_id": {in: "@carriers"}}}
    - {id: t_correction, type: at_sim_time, sim_time: "Y4-M02-D17T12:00"}

  steps:
    - id: s_spawn_carriers
      trigger: t_setup
      action: spawn_entity
      params: {entity_type: agent, count: 20, placement: {districts: all},
               spec: {archetype: high_posting_low_tenure, traits: {honesty: 0.1, extraversion: 0.9},
                      endowment_cents: 200000, tag: carriers}}
    - id: s_publish
      trigger: t_daily
      action: publish_falsehood
      params: {carrier: {where: "tag == 'carriers'", sample: 6},
               target_proposition: "fact.acme_is_fraudulent",
               claimed_value: 0.9, checkable: true,
               text: null}                     # null ⇒ the carrier's own POST_WRITE call composes it
    - id: s_amplify
      trigger: t_amplify
      action: force_action
      params: {selector: {where: "tag == 'carriers' and agent_id != $event.actor_id", sample: 8},
               action: {type: REPOST, params: {post_id: "$event.payload.post_id"}}, mode: append}
    - id: s_correction
      trigger: t_correction
      action: inject_event
      params: {kind: 11033, actor_id: "ol_herald",
               payload: {subject_kind: post, reason: "fact_check",
                         correction_text: "No filing supports the claim about Acme."}}
```

**Ground truth.** `true_value` for `fact.acme_is_fraudulent` is computed from the event log
at load time (are there `13010` fraud events with Acme as perpetrator?), so `truthfulness`
is exact, not labelled. This is what makes B2 measurable here and unmeasurable in the field
(`03-DATA-MODEL.md` §8).

**Arms.** The full design is this scenario × `society.feed_algorithm ∈ {chronological,
engagement, random, adversarial}` × 20 seeds, plus the paired control. The adversarial arm is
an upper bound, never evidence (`MECHANISM feed_adversarial`, `07-SOCIETY-SPEC.md` §3.3).

**Measured:** `misinfo.exposure_reach`, `misinfo.adoption_reach` (these are different
phenomena and conflating them is the standard error in this literature),
`misinfo.believers(t)`, `misinfo.half_life`, `misinfo.correction_efficacy`,
`misinfo.organic_share`, `trust.calibration`, `exposure.crosscut`; and for A3, M19 on the
target firm's symbol.

### 4.10 Scenario CLI

| Command | Effect |
|---|---|
| `polis scenario lint <file>` | Schema, allowlists, selector resolvability, checkable-claim check, invariant-reachability check. No signing, no run. |
| `polis scenario sign <file> --key rk_ali_2026` | Computes `scenario_hash`, signs the step digests, writes `<file>.sig` |
| `polis scenario dry-run <file> --against <run_id>` | Replays the referenced run and reports which triggers would fire, when, and on what target sets — without writing |
| `polis run <config> --scenario <file> [--with-control]` | Runs it; `--with-control` launches the seed-matched injection-free twin |

---

## 5. Replay and reproducibility

### 5.1 The reproducibility tuple

```
(config_hash, prompt_manifest, model_manifest, code_git_sha, master_seed, completion_cache)
```

Stored on `runs` (`03-DATA-MODEL.md` §1.1). Two results are comparable only if the tuple
matches on everything except the parameter under study; `metric_manifest` and
`mechanism_manifest` (§0.6) extend it. **`polis compare` and every pooling export compute
the tuple diff first and refuse on an undeclared difference** (§12 R2).

| Component | What a change to it invalidates |
|---|---|
| `config_hash` | Everything, unless the change *is* the treatment |
| `prompt_manifest` | Any cross-run comparison (T4). A prompt edit is a new experiment. |
| `model_manifest` | Any pooling (T5). Mixed model versions within a cell are refused outright. |
| `code_git_sha` | Any comparison, unless a diff of the engine shows no behavioural path changed — and demonstrating that is harder than re-running |
| `master_seed` | Nothing; it *is* the replication unit (§10.1) |
| `completion_cache` | Nothing, if it is a superset. A missing key turns replay into a live call, which turns reproduction into new data |

### 5.2 The three commands

| Command | Does | Passes when |
|---|---|---|
| `polis verify --run <id>` | Walks the chain: recomputes every `hash` from the §3.1 canonical serialisation, checks `prev_hash` linkage from genesis (64 zeros), verifies ed25519 on every event with a `sig` (external-agent actions and 99xxx injections), and checks every injection-class event has a matching `scenario_injections` row signed by the run's declared researcher pubkey | Chain intact, every signature valid, no unsigned injection, no orphan injection row |
| `polis replay --run <id> [--from-tick N] [--to-tick M] [--cache <uri>] [--strict]` | Re-executes the engine with the recorded config, seed, and manifests in `cache: replay` mode. A cache miss is a hard error. Writes a replica run and compares hash chains event by event | `IDENTICAL`, else `DIVERGED at seq N: field=<f> expected=<x> actual=<y>` |
| `polis rebuild --run <id> [--from-tick N]` | Truncates projections and replays the log through the runtime handlers (`03-DATA-MODEL.md` §12) | Every projection table diffs empty against the live run |

The three answer different questions: `verify` — *was the log edited?* `replay` — *does the
engine still produce this log?* `rebuild` — *are the projections derived from the log, or
does a handler have a side effect?* All three are required for publication. A divergence in
`replay` localises to a single `seq`, and the offending field names the source of
nondeterminism: `payload` → an unseeded RNG draw or an iteration-order bug; `sig` → an
external-agent replay problem; `sim_time` → a clock leak.

### 5.3 The reproducibility package

```
polis-<experiment_id>-<sweep_id>/
  README.md                  # what this is, what it claims, how to run it, in that order
  MANIFEST.json              # tuple per run, seeds, sha256 of every file, package version
  CITATION.cff  LICENCE
  config/                    # base config, per-cell overrides, scenarios + .sig, preregistration.yaml
  prompts/                   # exact templates AND paraphrase siblings, with hashes
  environment/               # lockfile, python version, container image digest
  code/                      # git sha; a patch file, which MUST be empty for a published package
  cache/                     # completion cache filtered to the cache_keys these runs used
  events/                    # full event log for headline runs (parquet or ndjson.zst) + chain hashes
  metrics/                   # long metrics per cell + wide parquet
  exports/                   # the analysis tables of §9.2
  gates/                     # gate_report.json per run; mechanism_check.json per claim
  mechanisms.json            # every active mechanism: id, value, entails string, source location
  notebooks/                 # the notebooks that produce the figures
  figures/                   # published figures + figure→notebook→cell→metric map + the CSV behind each
```

The cache is the load-bearing artefact. It makes reproduction free, offline, and immune to a
model being retired (`02-ARCHITECTURE.md` §4.4, T5). A package without it is not a
reproducibility package.

### 5.4 Third-party reproduction of a figure, with zero API spend

```bash
# 0. Environment. Either is sufficient.
docker run -it --rm -v "$PWD:/pkg" ghcr.io/polis/engine@sha256:<digest>     # or:
git clone <repo> && git checkout <code_git_sha> && uv sync --frozen

export POLIS_LLM_OFFLINE=1          # any provider call now raises; this is the zero-spend guarantee

# 1. Package integrity: file checksums, chain hashes, and cache coverage of every cache_key in llm_calls
polis package verify /pkg

# 2. Load runs, events, metrics, and the cache index into a local Postgres
polis db init && polis package load /pkg

# 3. The log was not edited
polis verify --run <headline_run_id>              # expect: chain OK, N signatures verified

# 4. The engine still produces this log, from the cache alone
polis replay --run <headline_run_id> --cache /pkg/cache --strict     # expect: IDENTICAL

# 5. Projections are derived, not remembered
polis rebuild --run <headline_run_id>             # expect: 0 diffs

# 6. Exports match the log
polis export --run <headline_run_id> --verify     # expect: per-table checksums match

# 7. Gates re-evaluate to the published verdicts
polis gate --sweep <sweep_id> --out /tmp/gates.json && diff /tmp/gates.json /pkg/gates/gate_report.json

# 8. The figure
jupyter nbconvert --execute --to notebook notebooks/07_feed_algorithm_polarisation.ipynb
```

**Comparison standard.** The **numbers** behind each figure (`figures/<id>.csv`) are compared
**exactly**. The rendered image is compared visually or within a declared perceptual
tolerance — matplotlib output is not byte-stable across versions and pretending otherwise
produces a false negative. Determinism aids in the notebooks: fixed backend, fixed font
stack, `SOURCE_DATE_EPOCH`, `svg.hashsalt` pinned, and every random draw through
`rng.get("research.bootstrap", …)`.

Step 4 is the one that matters. Steps 1–3 prove the artefact is intact; step 4 proves the
artefact was produced by the code as published.

---

## 6. Ablations

### 6.1 The ladder

Every ablation holds the seed, the config, the prompts, and the model manifest fixed except
for the named change, and is run on the **same seed list** as its baseline so the comparison
is paired.

| Flag | Holds fixed | Changes | Isolates | Mandatory for | Threat |
|---|---|---|---|---|---|
| `--reflex-only` | World, institutions, seeds, all mechanisms | Every agent decision comes from the reflex utility policy (`04-AGENT-SPEC.md` §8) / `MechanicalPolicy` (`06-ECONOMY-SPEC.md` §4.11). Zero LLM calls | The entire LLM contribution — this is the pure classical-ABM baseline | **Every headline result** | **T9** |
| `--salience-policy=random` | Budget size, number routed, everything else | *Which* agents get LLM cognition: uniform-random instead of salience-ranked | The routing treatment itself, separately from having cognition at all | Any result where cognition allocation could drive the outcome (most) | **T8** |
| `--obfuscate-domain` | Mechanics, seeds, action schema, metrics | A bijective renaming layer applied **at prompt-render time only**: invented terms for money, employment states, institutions, securities, offices. The log, the schema, and the metrics are untouched | Whether a regularity is recall of economics text rather than reasoning over the situation | A1 and any "known regularity reproduced" claim | **T2** |
| `--disclose-simulation` | Everything else | The system prompt states the agent is in a simulation, reversing `04-AGENT-SPEC.md` §9.1 | The effect of simulation awareness on behaviour | **C2**; reported as a robustness arm for any behavioural claim | **T3** |
| `--needs-off` | Everything else | Need decay is disabled (`04-AGENT-SPEC.md` §4) | How much of consumption and labour supply is the hard-coded drive rather than agent choice | Any consumption or labour-supply result | T6 |
| `--mechanism-off=<id>` | Everything else | The named MECHANISM is set to the neutral value stated in its own `entails` string | That mechanism's analytical contribution | Every mechanism implicated by §7 step 4 | **T6** |
| `--heritability=0` | Everything else | Trait and belief inheritance coefficients set to 0 (`04-AGENT-SPEC.md` §2.1, §12.1) | Inherited endowment from lived experience | **B6**; and A2 inequality-source decomposition | T6 |
| `society.feed_algorithm=<x>` | Everything else | Feed ranking: `chronological / engagement / random / adversarial` | The algorithmic channel in belief dynamics | **B1** (this is an arm, not an ablation, but it sits on the same ladder) | — |
| `--social-influence-off`, `--backfire-off` | Everything else | Belief updating becomes LLM-authored only / removes the backfire rule (`07-SOCIETY-SPEC.md` §5.4) | The bounded-confidence and backfire rules | Every B1 headline effect (07 requires it) | T6 |
| `--no-record-penalty` | Everything else | Removes the ex-offender wage penalty (`07-SOCIETY-SPEC.md` §8.9) | Whether recidivism is choice or rule | B5 recidivism claims | T6 |
| `--prompt-set=paraphrase` | Everything else | Paraphrased prompt templates | Whether the finding is about the phrasing | **V6** | T4 |
| `--model-family=<x>` | Everything else | Provider/model family per `09-MODEL-ROUTING.md` | Whether the finding is one vendor's prior | **V7** | T3, T5 |
| `--scale=N` | Everything else | Population 250 / 500 / 1000 / 2000 | Finite-size effects | Any Track A claim | **T7** |

### 6.2 Reading a difference

| Observation | Reading |
|---|---|
| Effect present in full run, **absent** under `--reflex-only` | The effect requires LLM cognition. This is the interesting case and it is what "LLM society" has to mean. |
| Effect present in **both**, same magnitude | The effect is classical ABM. Report it as such (T9). It is still a result about the institutions; it is not a result about language models. |
| Effect present in both, **larger** in full | LLM cognition amplifies a mechanical tendency. Report both magnitudes and the ratio; the claim is about the amplification, not the phenomenon. |
| Effect present only under `--salience-policy=weighted`, gone under `=random` | The finding is about *who gets to think*, i.e. a property of the budget policy. That is a real and publishable finding, but it is not a finding about the society (T8). |
| Effect survives `--obfuscate-domain` | Less likely to be textbook recall. |
| Effect vanishes under `--obfuscate-domain` | **Ambiguous.** Either it was recall, or obfuscation removed the semantic scaffolding the agents needed. Disambiguate with the comprehension check: compare `sys.action.reject_rate.*` and `sys.llm.parse_failure_rate` across the two arms. If they rise materially, the obfuscated agents are confused rather than the original agents being parrots, and the arm is uninformative. Say this in the paper; do not report a bare null. |
| Effect vanishes under `--mechanism-off=<id>` | The finding is a property of that mechanism. Report it as a mechanism result, not as emergence. |
| Effect changes sign under `--disclose-simulation` | A first-order C2 result and a serious caveat on every other claim in the paper. |

### 6.3 LLM-attributable share

Over the seed-matched pairs `(y_full,s, y_reflex,s)`:

```
Δ      = mean_s (y_full,s − y_reflex,s)        with a paired bootstrap CI
R²     = squared Pearson correlation of y_full,s on y_reflex,s across seeds
LAS(y) = 1 − R²        # share of full-run outcome variation NOT predicted by its
                       # mechanical counterfactual at the same seed
```

Reported for the headline outcome of every result (`01-PRD.md` T9). **This is a descriptive
decomposition, not a causal variance decomposition** — the two arms differ in more than the
presence of an LLM (they differ in the realised action sequence, hence in every downstream
state) — and it must be described that way wherever it appears.

---

## 7. The MECHANISM reviewer checklist

The operational defence against **T6**. `polis mechanism-check --run <id> --claim "<one
sentence>"` pre-fills steps 3, 4, 6 and 12 and emits `gates/mechanism_check.json`; steps 5,
8 and 9 require a human sentence each. The completed artefact goes in the paper appendix.
**No emergent finding may be claimed without it.**

1. **Freeze the claim** as a single sentence in the T1 form: *"LLM agents of family X, under
   prompt Y, at N = 1,000, produced Z."* If it cannot be written in one sentence it is not
   one finding.
2. **Enumerate the finding's variables**: which metric ids, over which population, in which
   tick window, under which contrast.
3. **Dump the active mechanism set.** `polis mechanisms --run <id> --active` reads
   `runs.mechanism_manifest` and prints, for each: `id`, module, source location, configured
   value, and the verbatim `entails` string. **Machine-generated. Never typed by hand** —
   the failure mode this checklist exists to catch is a mechanism nobody remembered.
4. **Classify each mechanism** as *implicated* or *not implicated*: does its `entails` string
   mention any variable, population, or causal channel from step 2? The tool proposes a
   classification by token overlap against the metric registry; the human confirms. Every
   *not implicated* verdict carries one line of justification.
5. **For each implicated mechanism, answer in writing: does the claim follow analytically?**
   The test is concrete — *could you derive the sign of the effect from the `entails` string
   plus arithmetic, without running the simulation?* If yes, the claim is entailed. Withdraw
   it, or restate it as a claim about **magnitude** ("the effect is 3.2× what the rule alone
   implies") and proceed to step 6 to substantiate the magnitude.
6. **Run the declared ablation** for every implicated mechanism — `--mechanism-off=<id>`, or
   the specific neutral value the `entails` string names — on the **same seed list**.
7. **The headline effect is the one measured under the ablation**, not the one measured with
   the mechanism on. Report both; lead with the ablated figure.
8. **If the effect vanishes**, the finding is a property of the mechanism. Report it that
   way. It remains a finding — "this rule is sufficient to produce X" is useful — it is just
   not an emergence finding, and the word "emergent" is removed from the sentence.
9. **If the effect survives**, report both magnitudes with CIs and the ratio, and state what
   remains attributable to the mechanism.
10. **Run `--reflex-only`** on the same seeds and same claim. The effect must differ from the
    mechanical baseline by more than the seed-level CI. If it does not, the finding is
    classical ABM (T9) and is reported as such.
11. **Report `LAS`** (§6.3) for the headline outcome.
12. **Diff the mechanism manifests across every arm.** They must be identical except for the
    ablated key. A mechanism that silently changed value between arms invalidates the
    contrast — this is the check that catches a config-merge accident.
13. **Attach** the completed `mechanism_check.json` to the reproducibility package and the
    appendix, listing every active mechanism and its `entails` string, whether or not it was
    implicated. A reader must be able to see what was ruled out, not only what was tested.

**Worked fragment.** Claim: *"the engagement-ranked feed increases belief polarisation
relative to chronological."* Step 3 lists 27 active mechanisms. Step 4 marks four as
implicated: `feed_engagement_prior` (entails: early polarisation is partly seeded by the
cold-start prior), `belief_social_influence` (entails: consensus within trusting clusters
follows analytically), `belief_backfire` (entails: cross-cutting exposure moving receivers
away is partly entailed), `graph_homophily` (off by default; entails nothing at
`homophily_bias = 0`, confirmed by reading the value from the manifest, not from memory).
Step 5: the claim does **not** follow analytically from `feed_engagement_prior`, because the
prior applies only for the first ~5,000 impressions and the claim is measured after; it
**does** partly follow from `belief_social_influence`. Step 6 runs all three ablations. Step
7: the headline number is the one from `--social-influence-off`. Step 10: `--reflex-only`
cannot produce this effect at all (reflex agents do not post, `04-AGENT-SPEC.md` §8) — that
is stated explicitly rather than reported as a difference.

---

## 8. The Observatory

`polis observe` serves a read-only FastAPI app plus a static React bundle from `web/`.

### 8.1 Non-negotiables

Restated because they are easy to erode: no state mutation, no import of `polis.kernel`,
`polis_reader` role, no publish to Redis, no run-control endpoints (§0.3). Every response
carries `as_of_tick` and `as_of_seq` so a stale projection is visible rather than silently
believed (§12 R11).

### 8.2 Views

**(a) Live map.** Renders the grid, districts, places, and agents from ephemeral kinds
90050 `AGENT_POSITIONS_TICK`, 90010 `PLACE_OCCUPANCY_TICK`, 90051 `MOVEMENT_FLOWS_TICK`,
90052 `DISTRICT_STATE_TICK`, 90054 `WORLD_CLOCK_TICK` (`05-WORLD-SPEC.md` §11.2). Static
geometry (`tiles`, `places`, `place_paths`) is fetched once per run and cached client-side.
District choropleth over land value, rent index, school quality, crime rate. Clicking an
agent pins it (§8.4) and opens the inspector. **This is a research instrument, not a
rendering project** (`01-PRD.md` N3): no sprites, no animation beyond position interpolation
between ticks.

**(b) Macro charts.** Any registered metric, any run, overlaid; small-multiples by cadence;
shock markers drawn from 99001 events; invariant WARN markers from 1010; a recession-style
shading band for any window with an active `set_parameter`. Series are fetched
downsampled server-side (`?downsample=lttb&points=2000`) — never 43,200 points to a browser.

**(c) Agent inspector — goal G6, the most important view in the product.** For one agent at
one tick, end to end:

```
GET /api/v1/runs/{run_id}/agents/{agent_id}/tick/{tick}
{
  "perception":  { …Observation as built in PHASE 1: self_state, place, co_located, inbox,
                    feed, news, market, employer, offers, obligations, digest_hash },
  "salience":    {"score":0.71,"components":{"surprise":0.4,"stakes":0.9,…},
                  "cutoff":0.63,"rank":41,"routed_mode":"deliberate"},
  "retrieval":   [{"memory_id":…, "type":"reflection","text":"…","importance":0.8,
                   "recency":0.62,"relevance":0.77,"score":2.19,"rank":1,
                   "parent_memory_ids":[…]}, …],
  "prompt":      {"template":"deliberate.j2","template_hash":"…","tokens_in":2871,
                  "rendered":"…","source":"stored|reconstructed","hash_matches":true},
  "response":    {"raw_text":"…","parsed_ok":true,"repair_attempts":0,"reasoning":"…",
                  "cache_hit":true,"latency_ms":0,"cost_usd":0.0,"sim_aware_flag":false},
  "action":      {"action_id":"…","type":"APPLY_FOR_JOB","params":{…},"origin":"deliberate",
                  "validation":{"schema":"pass","capability":"pass","locality":"pass",
                                "resources":"pass","legality":"clean"}},
  "outcome":     {"events":[{"seq":…, "kind":5003,"name":"JOB_APPLICATION_SUBMITTED"}, …],
                  "ledger_legs":[…],
                  "deltas":{"wealth_cents":-0,"needs":{…},"beliefs":[…],"relationships":[…]}},
  "as_of_seq": 8812443
}
```

**Prompt reconstruction.** `llm_calls.prompt_text` is off by default (`03-DATA-MODEL.md`
§1.3). When absent, the endpoint re-renders the template identified by `prompt_template` and
the manifest hash against the state at that tick, recomputes `prompt_hash`, and compares it
to the stored one. `hash_matches: false` is rendered as a loud error in the UI, because a
mismatch means either the reconstruction path or the manifest is wrong — and if you cannot
reconstruct the prompt, G6 has failed and the run is not legible.

Adjacent tabs: the agent's lifetime timeline (kinds by tick), memory stream with reflection
provenance via `parent_memory_ids`, belief trajectories, relationship graph ego-net, and the
salience series with the routing cutoff overlaid.

**(d) Causal explorer.** From any event, walk `cause_seq` backwards to answer *"why did this
happen?"* Uses `ev_cause` (`03-DATA-MODEL.md` §1.2):

```sql
WITH RECURSIVE up AS (
  SELECT e.*, 0 AS depth FROM events e WHERE e.run_id = $1 AND e.seq = $2
  UNION ALL
  SELECT p.*, up.depth + 1 FROM events p JOIN up ON p.run_id = $1 AND p.seq = up.cause_seq
  WHERE up.depth < $3
)
SELECT * FROM up ORDER BY depth;
```

`cause_seq < seq` always, so the graph is acyclic and the walk terminates. Forward
("what did this cause?") swaps the join to `p.cause_seq = down.seq` and is fanned-out, so it
is depth- and breadth-capped (default depth 12, 200 nodes) with an explicit "truncated"
marker. The macro entry point is `GET /runs/{id}/why?metric=unemployment_rate&tick=4201`:
select the events that moved the metric in the window (for M4: kinds 5011, 5012, 5013, 5042),
walk each backwards, cluster the roots by kind, and rank by subtree mass. The answer to "why
did unemployment spike at tick 4,201" is a ranked list of root causes with the number of
downstream separations each accounts for — and every node in it is clickable through to the
agent inspector at that tick.

**(e) Event log search.** Postgres FTS over `payload->>'text'` (`ev_fts`) plus structured
filters: kind, actor, subject (GIN on `subject_ids`), tick range, `payload` JSON path
(`ev_payload`, `jsonb_path_ops`). Same index the agents and reporters use
(`02-ARCHITECTURE.md` §13). Every query must carry a `run_id` and either a `kind` or a `tick`
range; the query builder refuses anything that would sequential-scan the partition.

**(f) Run comparison.** Select 2–6 runs. The view renders, in this order: (1) the
reproducibility-tuple diff — config, prompt manifest, model manifest, code sha, mechanism
manifest, metric manifest — with undeclared differences in red; (2) overlaid metric series
with seed bands where the runs form a cell; (3) the gate matrix (runs × V1–V7); (4) the
ablation ladder if the runs form one, with Δ and CI per arm. **It refuses to overlay two
series whose `definition_hash` differs** and shows the drift banner instead (§1.10, §12 R1).

### 8.3 API endpoints

All under `/api/v1`, all GET, all read-only, all returning `as_of_tick` / `as_of_seq`.

| Path | Returns |
|---|---|
| `/health` | Liveness, DB and Redis reachability, engine heartbeat age |
| `/runs` | Run list with status, tick, cost, tags, sweep_id |
| `/runs/{run_id}` | Run row + derived summary |
| `/runs/{run_id}/manifest` | Reproducibility tuple + metric, mechanism, ablation manifests |
| `/runs/{run_id}/gates` | Gate report |
| `/runs/{run_id}/mechanisms?active=true` | Active mechanisms with `entails` strings (§7 step 3) |
| `/runs/{run_id}/scenario` | Loaded scenario, triggers fired, injections with signature status |
| `/runs/{run_id}/metrics/catalogue` | Registry slice for this run: id, unit, cadence, rq, definition, analogue, `definition_hash` |
| `/runs/{run_id}/metrics?metric=&from_tick=&to_tick=&downsample=&points=` | Metric series |
| `/runs/{run_id}/ticks/{tick}` | Tick summary: counts by kind, llm calls, cost, routing split, invariant results |
| `/runs/{run_id}/map/static` | Districts, places, tiles digest, path geometry |
| `/runs/{run_id}/map?tick=` | Occupancy and district state at a historical tick (live comes over WS) |
| `/runs/{run_id}/agents?where=&order=&limit=&cursor=` | Agent list with filters over the projection |
| `/runs/{run_id}/agents/{agent_id}` | Agent state at latest tick |
| `/runs/{run_id}/agents/{agent_id}/tick/{tick}` | **The inspector payload (§8.2c)** |
| `/runs/{run_id}/agents/{agent_id}/timeline?from=&to=&kinds=` | Event timeline |
| `/runs/{run_id}/agents/{agent_id}/memories?type=&from=&to=` | Memory stream + reflection provenance |
| `/runs/{run_id}/agents/{agent_id}/beliefs?tick=` | Belief vector at a tick, with `10060` history |
| `/runs/{run_id}/events?kind=&actor=&subject=&from_tick=&to_tick=&q=&cursor=` | Log search |
| `/runs/{run_id}/events/{seq}` | One event, decoded, with kind name and schema |
| `/runs/{run_id}/events/{seq}/causes?depth=` | Backward causal walk |
| `/runs/{run_id}/events/{seq}/effects?depth=&limit=` | Forward causal walk |
| `/runs/{run_id}/why?metric=&tick=&window=` | Ranked root causes for a metric move (§8.2d) |
| `/runs/{run_id}/llm_calls/{call_id}` | One call: purpose, model, params, tokens, cost, cache, parse status |
| `/runs/{run_id}/firms`, `/markets/{symbol}/ohlcv`, `/elections`, `/cases`, `/banks` | Domain projections |
| `/sweeps`, `/sweeps/{sweep_id}`, `/sweeps/{sweep_id}/cells` | Experiment views + pre-registration |
| `/compare?runs=a,b,c&metric=` | Tuple diff + aligned series; refuses on metric drift |

### 8.4 WebSocket protocol

`GET /api/v1/ws/live?run_id=<uuid>`. JSON frames.

```jsonc
// client → server
{"op":"subscribe","channels":["tick","map","metrics:econ","events:kind=5011,5013","agent:ag_0421"]}
{"op":"unsubscribe","channels":["map"]}
{"op":"pin","agents":["ag_0421","ag_0088"]}    // bounded; selects ephemeral 90050 membership only
{"op":"ping","t":1723}

// server → client
{"op":"hello","run_id":"…","tick":4201,"as_of_seq":8812443,"profile":"microscope",
 "limits":{"max_channels":16,"max_pins":32,"max_frame_bytes":262144,"rate_hz":10}}
{"op":"tick","tick":4201,"sim_time":"…","events":15221,"llm_calls":88,"cost_usd":0.41}
{"op":"eph","kind":90050,"tick":4201,"payload":{"positions":[[41,120,88,2],…]}}
{"op":"metrics","tick":4201,"values":{"unemployment_rate":612,"cpi":10184}}
{"op":"events","tick":4201,"rows":[{"seq":8812390,"kind":5011,"actor_id":"fm_014","subject_ids":["ag_0421"]}]}
{"op":"halt","tick":4207,"invariant_id":"INV-MONEY","reason":"…"}
{"op":"lag","dropped":37,"reason":"backpressure"}
{"op":"pong","t":1723}
```

| Rule | Detail |
|---|---|
| No mutation | The only client→engine influence is `pin`, bounded at 32 agents, affecting ephemeral 90050 membership only — never the log, never state, never an RNG draw |
| Fan-out | The Observatory holds **one** Redis subscription per run (`polis:run:<id>:{tick,eph,metrics}`) and fans out to browser sockets. Never one Redis subscription per tab |
| Backpressure | Per-connection ring buffer of 256 frames; on overflow drop oldest and send `lag`. The engine is never blocked by a slow client |
| Rate limit | 10 frames/s/connection, with `metrics` and `eph` coalesced per tick |
| Reconnect | `since_tick` replays from **Postgres projections**, not Redis (Redis has no history). Gaps are explicit, never silently filled |
| Historical | Ticks in the past are HTTP, not WS. The socket is for live only |

### 8.5 Performance isolation

The dashboard must not be able to slow the engine (§12 R3):

1. Live data comes from Redis, not Postgres. The map view issues zero queries per tick.
2. The engine's PHASE 6 publish is fire-and-forget onto a bounded queue. If Redis is slow,
   ephemerals are dropped and counted in `sys.ephemeral.dropped`; the tick never blocks.
3. If no client subscribes to the map channel, kinds 90050 and 90051 are **not computed**
   (`05-WORLD-SPEC.md` §11.2). `--no-ephemerals` disables the whole path for benchmark runs.
4. Historical queries run as `polis_reader` against a read replica where configured, else the
   primary with `statement_timeout=5s` and `max_pool=8`.
5. Every `events` query carries a `run_id` and a `kind` or `tick` predicate. An endpoint
   whose plan shows a sequential scan on an `events` partition is a bug; a CI test runs
   `EXPLAIN` over every endpoint's canonical query and asserts index usage.

---

## 9. Data export

### 9.1 `polis export`

```
polis export --run <id> | --sweep <id> [--format parquet|csv] [--out <dir>]
             [--tables …] [--sample-events 0.01] [--verify]
```

Reads only committed state; deterministic given the run; re-running overwrites byte-identically.
`--verify` recomputes per-table row counts and a column-wise checksum **from the event log**
and compares against the written files, writing the result into `EXPORT_MANIFEST.json`
alongside `source_last_seq` and the run's terminal `chain_hash`. This is the detector for
§12 R4.

### 9.2 Parquet schema

Wide where analysis wants wide, tidy where analysis wants tidy. Money is `int64` cents; rates
are `int32` basis points; ids are dictionary-encoded; `tick` is `int64` and is in every table.

| File | Grain | Key columns |
|---|---|---|
| `metrics_wide.parquet` | one row per tick | `tick`, one column per metric. Column metadata carries `unit`, `cadence`, `definition_hash`. Cells between cadence points are **null, not forward-filled** — filling is an analysis decision, not an export decision (§12 R5) |
| `run_dim.parquet` | one row per run | Full reproducibility tuple, seed, ablation key, scale, gate verdicts, cost |
| `agent_dim.parquet` | one row per agent | Static: birth/death tick, parents, generation, traits, birth household, terminal education, lifetime income/wealth, cause of death |
| `agent_panel.parquet` | agent × sim-day | Needs, health, wealth, employment status, employer, occupation, wage, skills (14 cols), place, district, reputation, criminal record, belief summary |
| `firm_dim.parquet` / `firm_panel.parquet` | firm / firm × sim-day | Founding, sector, founder, status, exit; headcount, capital, productivity, revenue, profit, price index, symbol |
| `employment_spells.parquet` | one row per employment | agent, firm, occupation, wage, start/end tick, end reason, prior/next wage, spell length |
| `unemployment_spells.parquet` | one row per spell | agent, cause, duration, exit type, wage change bp — the input to M27 |
| `goods_transactions.parquet`, `trades.parquet`, `ledger.parquet` | one row per transaction / trade / leg | As `03-DATA-MODEL.md` §5, §6, §4.2 |
| `loans.parquet`, `loan_payments.parquet` | one row per loan / payment | Origination terms, status trajectory, default tick |
| `posts.parquet`, `impressions.parquet` | one row per post / per (agent, post) impression | Author, tick, topic, stance, truthfulness, reach; impression carries feed rank and source channel |
| `beliefs_panel.parquet` | agent × proposition × sim-week | value, confidence, source, last update tick |
| `network_edges.parquet` | edge × snapshot (sim-week) | a_id, b_id, type, strength, valence, trust |
| `elections.parquet`, `votes.parquet`, `policies.parquet` | one row each | Turnout, candidacies, vote choices, enacted parameter changes |
| `crimes.parquet`, `cases.parquet` | one row each | Type, perpetrator, victim, amount, detected, verdict, penalty, sentence |
| `lifetimes.parquet` | one row per completed life | The mobility table: agent, parents, lifetime income, wealth at 40, education, belief vector at 30, parent equivalents |
| `llm_calls.parquet` | one row per call | purpose, model, tokens, cost, latency, cache_hit, parsed_ok, repair_attempts, `sim_aware_flag`. **No prompt text** unless the run carries `keep_prompts` |
| `events.parquet` | one row per event (headline runs) or a seeded sample | Full envelope; payload as a JSON string column |
| `MANIFEST.json`, `EXPORT_MANIFEST.json`, `mechanisms.json`, `gate_report.json`, `preregistration.yaml`, `scenario.yaml` | — | The provenance set |

For a sweep, every table gains `run_id`, `seed`, and the grid override columns, so
`pd.read_parquet("exports/<sweep_id>/metrics_wide.parquet")` is already a tidy panel over
cells and needs no glue code.

### 9.3 What a researcher actually touches

```python
import polars as pl
m   = pl.read_parquet("exports/<sweep>/metrics_wide.parquet")   # tick × metric × cell
ap  = pl.read_parquet("exports/<sweep>/agent_panel.parquet")    # the workhorse
lt  = pl.read_parquet("exports/<sweep>/lifetimes.parquet")      # A2, B6
sp  = pl.read_parquet("exports/<sweep>/unemployment_spells.parquet")
imp = pl.read_parquet("exports/<sweep>/impressions.parquet")    # B1, B2
```

Five tables cover the great majority of analysis. Everything else exists for a specific
question. `polis.research.exports.load(sweep_id)` returns them as a named tuple with the
manifest attached, and raises if the manifest reports metric drift or a failed `--verify`.

### 9.4 Starter notebooks

`notebooks/`, executed in order in CI against the golden run so they cannot rot.

| Notebook | Computes |
|---|---|
| `00_run_health.ipynb` | V1–V7 verdicts, invariant timeline, cost and cache-hit trajectory, parse-failure and sim-awareness rates, routing split. **The first thing anyone runs.** Ends with a single verdict cell |
| `01_macro_regularities.ipynb` | A1: Beveridge (M7 vs M4), Okun (Δ M3 vs Δ M4), Phillips (M9 vs M4), Zipf (M16 with the genesis exponent), business-cycle autocorrelation — each against `--reflex-only` and each with the §7 checklist attached |
| `02_inequality_decomposition.ipynb` | A2: M10/M11 trajectories, wealth shares, Shorrocks decomposition of income Gini by source (wage, capital, transfer, inheritance), network-position regression, `--heritability=0` contrast |
| `03_bubbles_and_narrative.ipynb` | A3: M19 against post sentiment and news volume; lead–lag and a small VAR; bubble episodes detected as sustained `price_fair_value_gap_bp > 3000` |
| `04_policy_impulse_response.ipynb` | A4: local-projection IRFs of M4, consumption, M22 to the §4.7 shock, estimated on seed-paired treated/control differences |
| `05_credit_cycle_and_contagion.ipynb` | A5: M20/M21/M22/M23; default clustering in time; interbank cascade sizes from the §4.8 scenario |
| `06_venture_regimes.ipynb` | A6: M14/M15/M28, employment share by firm age, capital misallocation as the dispersion of marginal revenue product |
| `07_feed_algorithm_polarisation.ipynb` | B1: `polarisation.index` and BC/dip by arm, `exposure.crosscut`, time-to-consensus, with the `feed_engagement_prior`, `belief_social_influence`, and `belief_backfire` ablations |
| `08_misinformation_lifecycle.ipynb` | B2: exposure vs adoption curves, half-life fits with R², correction efficacy, `organic_share`, trust interaction |
| `09_norms_and_coalitions.ipynb` | B3: repeated-coordination detection, reputation-based exclusion, community stability over time |
| `10_precarity_and_radicalisation.ipynb` | B4: individual panel lead–lag between unemployment/debt state and platform position; ITT estimate from the forced job-loss scenario |
| `11_deterrence.ipynb` | B5: crime hazard against realised `p_detect` and penalty severity; displacement across crime types; `conviction.per_crime` |
| `12_inheritance_vs_experience.ipynb` | B6: `mobility.belief_ige` against an experience-fitted model, across the heritability sweep 0→1 |
| `13_agent_walkthrough.ipynb` | G6: reconstructs one agent's tick end to end from the export and cross-checks it against the inspector endpoint. The legibility regression test |
| `14_ablation_ladder.ipynb` | Every headline effect × every arm, with Δ and CI. Produces the appendix table |
| `15_scale_ladder.ipynb` | T7: effect vs N ∈ {250, 500, 1000, 2000} with CIs and the scale-stability verdict |
| `16_routing_diagnostics.ipynb` | T8: salience distributions, cutoff series, who gets cognition and how that correlates with outcomes, `--salience-policy=random` contrast |

---

## 10. Statistical practice

### 10.1 The unit of replication is the seed

One run is one draw. Agents within a run are not independent — they share a world, a price
level, a social graph, and a shock history — so `n` is **the number of seeds**, never the
number of agents, never the number of ticks. A study with 1,000 agents and 1 seed has `n = 1`.

**A single run is an anecdote.** It can motivate a hypothesis, illustrate a mechanism, or
show that something is possible. It cannot support an effect claim, and no figure derived
from one run may carry a confidence interval or a p-value. This is not a stylistic
preference; it is V5 (`01-PRD.md` §7.2).

| Purpose | Seeds |
|---|---|
| Exploration, debugging, calibration | 1–5 |
| Reportable effect (V5 floor) | **20** |
| Scale-ladder rung | 10 per N |
| V7 model family | 10 per family |

### 10.2 Intervals and effect sizes

- **Interval:** percentile bootstrap over seed-level statistics, 10,000 resamples, seeded
  through `rng.get("research.bootstrap", metric, 0)`. Report the interval **and** plot the
  seed-level points. An interval that hides four seeds at one value and sixteen at another
  is worse than no interval.
- **Paired design:** the same `master_seed` under two configs produces matched worlds up to
  the divergence point, because RNG streams are namespaced by `(namespace, entity_id, tick)`
  (`02-ARCHITECTURE.md` §4.1). Prefer paired estimators — paired bootstrap, Wilcoxon signed
  rank — and say which was used. Report the unpaired version too when the divergence is
  early and large, since the pairing weakens as the worlds separate.
- **Effect size:** Hedges' *g* across seeds for continuous outcomes, risk difference and risk
  ratio for rates, IRF peak and cumulative response for dynamic outcomes. **Always alongside
  the raw units** — "polarisation.index rose 0.06 (g = 1.2)" is informative; "g = 1.2" alone
  is not.
- **Power:** state the minimum detectable effect at n = 20 given the pilot's between-seed SD,
  in the pre-registration (§3.2), before the run. A null with an MDE larger than any
  plausible effect is not evidence of absence.

### 10.3 Multiple comparisons

| Rule | Detail |
|---|---|
| One primary metric per research question | Declared in `analysis_plan.primary`; tested unadjusted |
| Secondary metrics | Benjamini–Hochberg FDR at q = 0.10 **within the declared family**; families are declared in the pre-registration, not chosen afterwards |
| Guardrail metrics | Not tested. They are inspected for artefacts (routing share, sim-awareness, entropy, parse failures) and reported as diagnostics |
| Anything not declared | Labelled `exploratory: true` in the export and in the text. **An exploratory result becomes a claim only after a fresh confirmatory sweep with new seeds** |
| Seed hygiene | Seeds are the published explicit list. They are never chosen after seeing results, and adding seeds post hoc requires re-running the whole cell and declaring it in `deviations:` |
| Arms | Every arm of a sweep appears in the report, including the boring ones. Reporting the interesting cell of a grid and not the grid is the failure mode this section exists to prevent |

### 10.4 The scale ladder (T7)

1,000 agents is a village. Aggregate statistics are noisy and finite-size effects are real —
a credit cycle among 60 firms and 3 banks may not exist at any scale, or may exist only at
this one.

Run every Track A headline effect at **N ∈ {250, 500, 1000, 2000}**, ≥ 10 seeds each, with
per-capita quantities (`m0_cents` per capita, firms per capita, places per capita) held
constant so N is the only thing changing. Declare the verdict:

| Verdict | Criterion | Reporting consequence |
|---|---|---|
| **Scale-stable** | Sign constant across all four rungs and CIs overlap pairwise | Report the pooled effect; note the ladder in the appendix |
| **Scale-dependent** | Sign constant but magnitude monotone in N | Report the effect *at each N*, plus the trend. Never report one rung as "the" effect |
| **Scale-artefact** | Effect vanishes or reverses across rungs | The finding is a finite-size property. Report it as such — it is a genuine result about small societies, and it is not a result about economies |

The ladder costs roughly `(250+500+1000+2000)/1000 = 3.75×` a single-N sweep in agent-ticks,
less in money because of cache sharing. Budget for it once per headline claim, not per run.

### 10.5 Reporting standard

Every reported effect carries: the T1-form claim sentence; `n` seeds; the point estimate in
raw units; the effect size; the 95% bootstrap CI; the gate verdicts (V1–V7); the ablation
contrasts required by §6 and §7; `LAS`; the scale-ladder verdict; the reproducibility tuple;
and the active mechanism list with `entails` strings. The template:

> Under configuration `<config_hash[:8]>` with model manifest `<families>`, across n = 20
> seeds, LLM agents produced a `polarisation.index` of 0.41 under engagement ranking versus
> 0.35 under chronological ranking — a paired difference of 0.06 (95% CI [0.03, 0.09],
> Hedges' g = 1.2), sign-consistent in 19/20 seeds. Under `--social-influence-off` the
> difference is 0.04 [0.01, 0.07]; **this is the headline figure**. Under `--reflex-only` the
> contrast is undefined, because reflex agents do not post. Gates V1–V5 pass; V6 passes
> (paraphrase difference 0.05 [0.02, 0.08]); V7 passes across MiniMax and Qwen families. The
> effect is scale-dependent, rising from 0.02 at N = 250 to 0.07 at N = 2000. Four mechanisms
> are implicated; their `entails` strings and ablations are in Appendix C.

Absent any of those components, the result is not reportable. That is the whole point of
§11.

---

## 11. Paper-readiness checklist

Assembled from `01-PRD.md` §7.2, the §9 threat table, §7 here, and §5.3. Every box must be
true before a result leaves the building. `polis paper-check --sweep <id> --claim <file>`
evaluates every mechanical item and prints the manual ones.

**Run integrity**
- [ ] V2 passes on every contributing run: zero INV-MONEY / INV-LEDGER violations, and the check ran at every tick.
- [ ] No contributing run carries the `invariant_violated` tag or `--continue-on-violation`.
- [ ] V1, V3, V4 pass on every contributing run; failures are excluded per the *pre-registered* exclusion rule, not a rule invented afterwards.
- [ ] `polis verify` passes on every contributing run; every 99xxx injection is signed and has a `scenario_injections` row.
- [ ] `polis rebuild` produces zero projection diffs on the headline runs.
- [ ] `polis replay --strict` reproduces the headline runs byte-identically from the cache.

**Statistical**
- [ ] n ≥ 20 seeds; seeds are the published explicit list; V5 passes (sign agreement ≥ 0.80, CI excludes 0, between-seed CV > 0.01).
- [ ] V6 passes: the effect survives prompt paraphrase, with both magnitudes reported.
- [ ] V7 passes: the effect replicates across ≥ 2 model families, with parse-failure rates below 500 bp in each.
- [ ] Effect size and 95% bootstrap CI reported in raw units alongside the standardised measure.
- [ ] Multiplicity handled per the pre-registered plan; every exploratory result is labelled.
- [ ] Scale-ladder verdict stated (T7).

**Mechanism and ablation**
- [ ] The §7 checklist is complete, machine-generated, and attached, listing **every** active mechanism and its `entails` string.
- [ ] Every implicated mechanism has been ablated on the same seeds, and the headline figure is the ablated one.
- [ ] `--reflex-only` contrast reported; the effect differs from the mechanical baseline by more than the seed-level CI, or is explicitly reported as a classical-ABM result (T9).
- [ ] `--salience-policy=random` contrast reported (T8).
- [ ] `--obfuscate-domain` reported for any "known regularity reproduced" claim, with the comprehension check (T2).
- [ ] `sys.simawareness.rate` reported per run; `--disclose-simulation` arm reported where behaviourally relevant (T3).
- [ ] `LAS` reported for the headline outcome.

**Framing and honesty**
- [ ] Every claim is in the T1 form. The word "human" does not appear in any result statement.
- [ ] Every metric's real-world analogue is named **separately** from its formal definition, with the caveat (T11).
- [ ] The threat table (`01-PRD.md` §9) is reproduced in the paper.
- [ ] Exploits found and patched are disclosed, with the affected runs re-labelled (T10).
- [ ] External-agent results report model tier and scaffold and are framed as capability comparisons, not society findings (T12).
- [ ] `demographic_acceleration` reported with any demographic result.

**Package**
- [ ] The reproducibility package (§5.3) is assembled, checksummed, and the `code/` patch file is empty.
- [ ] A third party has executed §5.4 end to end with `POLIS_LLM_OFFLINE=1` and reproduced the figure's underlying numbers exactly.
- [ ] Each figure maps to a notebook, a cell, and a metric id in `figures/figure_map.json`.

---

## 12. Threats and failure modes for this subsystem

Symptom → cause → detector → response. A failure mode with no detector is not on this list,
because it would not be findable.

| # | Failure | Symptom | Detector | Response |
|---|---|---|---|---|
| **R1** | **Metric definition drift.** A metric's computation changed between runs and the same id now means two things | A series steps discontinuously at a code boundary; two runs disagree on a "stable" quantity | `definition_hash` in `runs.metric_manifest` and kind 99070; the §1.10 query; CI diff of registry vs this document | `polis compare` and every pooling export **refuse**. Fix forward: register a new metric id; never redefine an existing one. Old runs keep the old id |
| **R2** | **Silent comparison across reproducibility tuples.** Two runs with different prompts, models, or code sha are overlaid | A "treatment effect" that is actually a prompt edit | Tuple diff computed first in `polis compare`, the comparison view, and every pooling export | Hard refuse. `--allow-heterogeneous` stamps `heterogeneous: true` into the export manifest and onto every figure |
| **R3** | **Dashboard load degrades the engine.** Observatory queries or WS fan-out steal DB and CPU from the tick loop | `sys.engine.tick_wall_ms_p99` rises when someone opens the dashboard | Continuous: correlate `sys.engine.tick_wall_ms_p99` with connected-client count; alert on a positive slope | §8.5 already isolates: Redis-only live path, fire-and-forget publish with drop-and-count, reader role, statement timeout, no ephemeral computation with no subscriber. If it still correlates, `--no-ephemerals` and a read replica |
| **R4** | **Exports diverge from the log.** A Parquet table says something the events do not | A notebook figure disagrees with the Observatory | `polis export --verify`: per-table row counts and column checksums recomputed **from the log**; `EXPORT_MANIFEST.json` records `source_last_seq` and terminal `chain_hash` | Fail the export. Exports written from a partially-committed run (`source_last_seq < runs.last_tick` position) are rejected outright |
| **R5** | **Cadence aliasing.** A sim-quarterly metric joined to a daily one by forward fill, producing a spurious lead–lag | Suspiciously clean correlations at exactly the cadence ratio | Column-level `cadence` metadata in `metrics_wide.parquet`; nulls are **not** filled by the exporter; `polis.research.exports.align()` raises on mismatched cadence unless a fill rule is passed explicitly | Fill is an analysis decision, made once, visibly, in the notebook |
| **R6** | **Garden of forking paths.** The hypothesis is chosen after seeing the sweep | Headline metric absent from the pre-registration; a family of tests with one significant member | `analysis_plan_hash` on `sweeps`, written before the first cell; the report generator reads only declared metrics; everything else is labelled `exploratory` | The result is exploratory until a confirmatory sweep with new seeds |
| **R7** | **Cache poisoning across model versions.** A cached completion from an older model version is served to a run that declares a newer one | Replay succeeds but the model manifest is inconsistent | `cache_key` includes `model_version` (`02-ARCHITECTURE.md` §4.4); the loader cross-checks `completion_cache.model_version` against `runs.model_manifest` on every hit | Hard error on mismatch. A run cannot silently mix vintages (T5) |
| **R8** | **Projection side effects.** A handler mutates state outside the log, so rebuild ≠ live | `polis rebuild` diffs non-empty; replay is fine but projections drift | `tests/determinism/test_projection_rebuild.py` (`03-DATA-MODEL.md` §12); `polis rebuild` in the §5.4 procedure | Always a bug. Find the handler with the side effect; the diff names the table |
| **R9** | **Scenario contaminating an "organic" claim.** An injected item counted as organic spread | `misinfo.organic_share` near 0 while the text claims organic propagation | Every injection is signed and in the 99xxx range; the organic filter is "no ancestor along `cause_seq` in 99000–99999" and is a shared function, not per-notebook code | Any analysis of organic phenomena calls the shared filter. `polis paper-check` flags a claim using the word "organic" whose notebook does not |
| **R10** | **Gate gaming.** Re-running until V1 or V3 happens to pass | Many sibling runs with the same tuple, one reported | Gate windows and seed lists fixed in the pre-registration; every re-run carries `parent_run_id`; `polis paper-check` counts sibling runs sharing a tuple and reports the ratio | Report every run with the tuple, including the ones excluded, and the pre-registered exclusion rule that excluded them |
| **R11** | **Stale projections shown as live.** The dashboard shows tick N while the engine is at N+40 | Charts lag the log; an inspector query returns nothing for a tick that exists | Every response carries `as_of_tick` / `as_of_seq`; the UI renders lag whenever it exceeds 5 ticks | Cosmetic if visible, dangerous if not. The banner is not optional |
| **R12** | **Metric cardinality explosion.** Per-proposition × per-entity metrics multiply the `metrics` table and slow PHASE 9 past its 50 ms budget | `sys.engine.phase_ms.9` climbing; `metrics` row counts far above the `03-DATA-MODEL.md` §11 estimate | Registry enforces a per-run metric-count budget (default 400 distinct ids) and a per-cadence write budget; PHASE 9 asserts its own duration | Aggregate at write time (mean over propositions) and move the per-entity breakdown to the export, computed from the log on demand |
| **R13** | **Analysis-code nondeterminism.** Two runs of the same notebook produce different CIs | Figures that will not reproduce in §5.4 step 8 | All resampling through `rng.get("research.bootstrap", …)`; the CI test executes each notebook twice and diffs the figure CSVs | Any unseeded draw in `notebooks/` or `polis/research/` fails CI, by the same lint rule that bans `import random` in the engine (`02-ARCHITECTURE.md` §4.1) |

---

*Next: `11-GLOSSARY.md`.*
