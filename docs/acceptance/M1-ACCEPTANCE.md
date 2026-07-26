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
| Quality gates | Ruff lint/format, mypy strict, import-linter, determinism lint, prompt lint, 69 pytest tests and the production frontend build pass |
| Clean Compose infrastructure | Postgres 17.10 with pgvector 0.8.5, Redis and MinIO healthy |
| M0 determinism | Repeated 200-tick chains are byte-identical; 100-tick checkpoint plus resume equals the continuous 200-tick run |
| Event verification and rebuild | Persistent demo verifies all 10,741 events; offline replay and projection rebuild reproduce the terminal hash |
| Frozen M1 golden run | 50 agents, 100 ticks, 11,706 hashed events; hash `cc544aae606d0a900baaf879bd15ab7432803c98eb1a619f6b3cf06543bb7582` |
| M1 integration smoke | 50 agents and 500 ticks complete with population, action-diversity, cognition and wellbeing assertions |
| Formal M1 calibration | 1,000 agents, 2,000 ticks, 5,065,238 hashed events, 1.234 ticks/s; all gates passed |
| Deliberate cognition | Mean/p05/p50/p95 all 700 basis points (7.00%) |
| V4 diversity | Normalized action entropy mean 0.864100, minimum 0.806881, 100% of measured ticks above the 0.35 floor; all six legal action types present every tick |
| Calibration invariants | Population remained 1,000, halt reason was null and wellbeing remained positive |
| Five Observatory clients | Three paired 300-tick trials; 1,516 frames, explicit bounded-lag frames, no client disconnects; median regression -4.324%, passing the no-more-than-3% regression gate |
| Live map database isolation | Per-tick state follows engine to bounded Redis publisher to WebSocket hub; no Postgres query exists on the tick fan-out path |
| Read-only enforcement | `polis_reader` receives `permission denied` on direct `UPDATE`; API mutation verb returns HTTP 405 |
| Real provider and offline replay | One bounded MiniMax M2.7 call cost $0.001321 under the $0.05 cap; replay matched call ID and response hash with zero provider lanes |
| Frontend | TypeScript and Vite production build pass; live Map, Charts, Agents and Inspector browser smoke has no console errors |

Formal artifacts:

- [`m1-calibration.json`](../../artifacts/acceptance/m1-calibration.json)
- [`observatory-five-clients.json`](../../artifacts/acceptance/observatory-five-clients.json)
- [`live-provider-smoke.json`](../../artifacts/acceptance/live-provider-smoke.json)

The calibration run ID is `48d40d5e-a56b-5658-8850-e0932ca1e625` and its terminal
hash is `84b1e31e6641f533506beade224fb4107b74072838bec1d71657f737a7468af1`.
The live persistent demo is run `4376fbba-bf5c-556b-97b2-62e09918d418`; its
10,741 stored events terminate at
`33b768a3ec019afaf691e70741aa821db5514fd522ee9a86743eeacab07fe292`.

## Calibration decisions

1. The M1 deliberate target remains 7%. A deterministic reserve admits 70 of 1,000
   agents per tick while event-triggered reflections use only the remaining call budget.
2. Trait-conditioned prompt variation passes V4 without per-agent templates.
3. Global retrieval weights remain fixed for M1. This is an operational acceptance
   decision, not evidence that the weights are scientifically identified.
4. Reflection remains importance/life-event triggered with a 24-tick cooldown.
   Threshold crossings are queued deterministically rather than converted to a schedule.

The event log and cache remain authoritative for replay. The Observatory is a read-only
derived view, and coalesced live frames can be recovered from projections or exact replay.
