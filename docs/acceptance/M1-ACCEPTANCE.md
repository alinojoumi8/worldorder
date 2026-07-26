# M1 Living City acceptance

**Status:** Passed on 2026-07-26  
**Delivery branch:** `build/m1-living-city`  
**M0 branch point:** `build/m0-kernel` at `235a59f`  
**Scope boundary:** M2 economy work has not started.

## Delivered

- Deterministic Python 3.12 kernel, typed configuration and the `run`, `resume`,
  `verify`, `rebuild`, `replay`, and `observe` commands.
- Hash-chained events, Postgres 17/pgvector projections, Redis live state,
  checkpoints, deterministic replay and a durable completion cache.
- Grid world, population genesis, six typed M1 actions, needs, memory retrieval,
  citation-grounded reflection, salience routing, reflex/deliberate cognition,
  education and research metrics.
- Read-only `/api/v1` Observatory routes and bounded WebSocket state. Live state is
  coalesced to the configured 10 Hz rate and never writes simulation truth.
- React Map, Charts, Agents and Inspector views connected to the API. Fixture data is
  available only through the explicitly labelled `?demo=1` path.
- Causal, Search, Compare and Arena remain visibly unavailable until M6. Economy,
  unemployment and CPI are not represented as M1 results.

## Acceptance evidence

| Gate | Result |
|---|---|
| Quality gates | Ruff lint/format, mypy strict, import-linter, determinism lint, prompt lint, 70 pytest tests and the production frontend build pass |
| Clean Compose infrastructure | Postgres 17.10 with pgvector 0.8.5, Redis and MinIO healthy |
| M0 determinism | Repeated 200-tick chains are byte-identical; 100-tick checkpoint plus resume equals the continuous 200-tick run |
| Event verification and rebuild | Stabilized persistent demo verifies all 9,673 events; offline replay and projection rebuild reproduce the terminal hash |
| Frozen M1 golden run | 50 agents, 100 ticks, 10,404 hashed events; cross-platform hash `fb583b89c6d0a34155c3ac422a2c0ed6c0216025fab03c82a40e02113aac9844` |
| M1 integration smoke | 50 agents and 500 ticks complete with population, action-diversity, cognition and wellbeing assertions |
| Formal M1 calibration | 1,000 agents, 2,000 ticks, 4,736,532 hashed events, 3.088 ticks/s under concurrent validation load; all gates passed |
| Three-seed stability | All three 1,000-agent/2,000-tick seeds passed; throughput 3.088–3.131 ticks/s and late/early ratio 0.932–0.941 |
| Deliberate cognition | Mean/p05/p50/p95 all 700 basis points (7.00%) |
| V4 diversity | Normalized action entropy mean 0.854824–0.864050 across seeds, minimum 0.782199, 100% of measured ticks above the 0.35 floor; all six legal action types present every tick |
| Calibration invariants | Population remained 1,000, halt reason was null and wellbeing remained positive |
| Memory stability | Mean 2.256–2.486 memories per agent after 2,000 ticks, maximum 20 of 3,000; write rate at most 0.001243 per agent-tick |
| Wellbeing stability | Final cross-seed range 44.31561–44.75657; last-200-tick slope -1.806715 to -1.254904 per 1,000 ticks |
| Five Observatory clients | Three paired 300-tick trials; 1,516 frames, explicit bounded-lag frames, no client disconnects; median regression -4.324%, passing the no-more-than-3% regression gate |
| Live map database isolation | Per-tick state follows engine to bounded Redis publisher to WebSocket hub; no Postgres query exists on the tick fan-out path |
| Read-only enforcement | `polis_reader` receives `permission denied` on direct `UPDATE`; API mutation verb returns HTTP 405 |
| Real provider and offline replay | One bounded MiniMax M2.7 call cost $0.001321 under the $0.05 cap; replay matched call ID and response hash with zero provider lanes |
| Frontend | TypeScript and Vite production build pass; live Map, Charts, Agents and Inspector browser smoke has no console errors |

Formal artifacts:

- [`m1-calibration.json`](../../artifacts/acceptance/m1-calibration.json)
- [`m1-multiseed.json`](../../artifacts/validation/m1-multiseed.json)
- [`observatory-five-clients.json`](../../artifacts/acceptance/observatory-five-clients.json)
- [`live-provider-smoke.json`](../../artifacts/acceptance/live-provider-smoke.json)

The promoted calibration run ID is `50d51fb0-50db-5a28-8353-58cf667d6f20` and its
terminal hash is
`cc43e04ed23e0c50ad9232c049ebcdaaf990cf33e0351089b4475d71ada23458`.
The stabilized live demo is run `672e6468-c53d-5e1f-bed4-e6b4e1ac2e89`; its 9,673
stored events terminate at
`b1af934edcc4a9ed019d71b3eddacf8ee3ebee9045d0e8e35066a8cb868d3ca5`.

## Calibration decisions

1. The M1 deliberate target remains 7%. A deterministic reserve admits 70 of 1,000
   agents per tick while event-triggered reflections use only the remaining call budget.
2. Trait-conditioned prompt variation passes V4 without per-agent templates.
3. Global retrieval weights remain fixed for M1. This is an operational acceptance
   decision, not evidence that the weights are scientifically identified.
4. Reflection remains importance/life-event triggered with a 24-tick cooldown.
   Threshold crossings are queued deterministically rather than converted to a schedule.
5. Chronic unmet needs are not event stakes. M1 sets event stakes to zero until the
   owning institutional milestones can supply auditable wealth, health, employment,
   relationship and legal deltas.

The event log and cache remain authoritative for replay. The Observatory is a read-only
derived view, and coalesced live frames can be recovered from projections or exact replay.

### Cross-platform portability update

On 2026-07-26, public Linux CI exposed final-bit differences in NumPy's
multivariate-normal trait generation relative to Windows. Agent traits are now quantised
to 12 decimal places at genesis. All 10,404 events in the frozen run were compared
between Windows Python 3.12 and Linux Python 3.12 and matched exactly, producing the
cross-platform hash recorded above.
