# M3 Capital acceptance

**Status:** Engineering milestone and Stage 0 mechanical calibration complete; scientific
acceptance remains blocked by the real-provider research gates

**Engineering implementation:** `ad7a1751add70ef8ce478dc3a3f5eb9b805b11a4`

**Validation harness:** `c6a1aaa46a87b1954c09a0c5e31847284e35e3b1`

**Scope boundary:** The deterministic capital layer and its executable V1-V3 evaluator are
operational. The five-seed, 1,000-agent Stage 0 calibration passes V2 and V3 without a
fixture, but it is reflex-only and uses the stub provider. It proves a viable mechanical
baseline, not scientific acceptance of cognition or five-year stationarity.

## Delivered

- A deterministic limit-order book with call auctions, continuous matching, escrow,
  cancellation, price-time priority, commissions, circuit breakers, market data, IPOs,
  short positions, margin calls, borrow fees, and forced covering.
- Startup formation, VC funds, pitch evaluation through the cached LLM router, term sheets,
  funding rounds, cap tables, dividends, and cent-exact venture waterfalls.
- Tender acceptance, drag-along and squeeze-out completion, cash/stock/mixed consideration,
  and absorption, subsidiary, and asset-sale integration modes.
- Deterministic overlap redundancies and severance, inventory and capital transfer,
  productivity blending, loan-obligor transfer, and insolvent asset-sale shells.
- Automatic stays, creditor claims, strict-priority monotone pro-rata distributions, loan
  write-offs, bank-failure cascade coverage, and liquidation discharge.
- Explicit mechanism declarations and runtime ablations for venture valuation, acquisition
  valuation anchors, and integration synergy.
- Checkpoint-safe capital state, Postgres projections through Alembic revision
  `0012_m3_capital`, M3 research metrics, and read-only capital Observatory routes.
- Executable V1, V2, and V3 procedures plus a reproducible five-seed/five-year runner that
  derives gate inputs from the event log and independently checks every ledger tick.

## Engineering evidence

| Gate | Result |
|---|---|
| Quality gates | Ruff lint/format, determinism lint, prompt lint, mypy strict over 115 source files, four import-linter contracts, and 250 non-live pytest tests pass |
| Frontend | TypeScript and Vite production build pass; 1,580 modules transformed |
| Exchange properties | P1-P12 cover crossed books, share conservation, reservations, ledger closure, price bands, replay, self-trades, price-time priority, short caps, and exact cancellation release |
| Capital properties | Strict priority, exact cents, order independence, monotone estate recoveries, valuation bounds, positive acquisition anchors, mechanism ablations, stay enforcement, and bank-failure cascade closure are executable tests |
| Frozen capital run | The 50-agent/100-tick run completes 28,332 events with terminal hash `79137a7b7d481d0ff56efd0a0ac61a6f68567121e56c27fdb36b802237af0659` |
| Economy activity | The frozen run includes 21 wage payments, 4,235 goods purchases, one exchange trade, 12 loan payments, and four loan originations |
| Persistent verification | Run `d0b842a7-929b-5b54-a20c-7b6f498208b2` verifies all 4,177 stored events |
| Exact replay and rebuild | Replay and projection rebuild reproduce terminal hash `fe7c2cf41dcc2dc0d0f83ea93a5314ac85c7b62c3861023fbd08ee822c18079d` exactly |
| Checkpoint resume | A dedicated M3 checkpoint test resumes without event or state divergence |
| Live Observatory | Capital catalogue and securities, trades, market-index, startups, acquisitions, and bankruptcies routes return projection data with tick/sequence freshness; mutation verbs return HTTP 405 |

Machine-readable engineering evidence is in
[`m3-acceptance.json`](../../artifacts/acceptance/m3-acceptance.json).

## Stage 0 mechanical calibration

The frozen `m3-stage0-mechanical-v1` protocol ran seeds `2026072701` through
`2026072705`, each with 1,000 agents for 720 ticks (two simulated years). All runs
completed the exact duration, passed V2 accounting closure and V3 non-degeneracy, and
recorded no invariant violations. The five runs emitted 17,039,602 hash-chained events at
0.534-0.564 ticks per second.

| Seed | Ticks | Events | V2 | V3 | Terminal hash |
|---|---:|---:|---|---|---|
| 2026072701 | 720 | 3,446,608 | PASS | PASS | `b0d2850ffeb878732864d39409192f5a419be671f3b649c76910e27b5b9b7140` |
| 2026072702 | 720 | 3,411,847 | PASS | PASS | `dc61346d134c2495ac74d6305ef3d3033e70ed03bc45e6c713c41710e4ef4c2e` |
| 2026072703 | 720 | 3,392,714 | PASS | PASS | `8ae3c81f9195ff858d832fb4086ef4434bf4c1c8f2ef2c7ad6e6ebd37ad385ba` |
| 2026072704 | 720 | 3,400,543 | PASS | PASS | `4e1146ce410e741c9e68ff34bd32103f5e52c28b23325d52b59d8b06d33ccee3` |
| 2026072705 | 720 | 3,387,890 | PASS | PASS | `bcd125e90ac9cf113a72820cc1cb36422a56f23f80c377904c81d4363a0c895d` |

The baseline uses a declared founder-owned genesis listing plus seeded,
resource-constrained reservation orders. It adds no market maker, synthetic consumer
demand, guaranteed fills, wash trades, or post-genesis subsidy. The formal run exposed an
interest-classification defect at tick 421; capitalized interest is now reconciled exactly
through both repayment and write-off paths, after which all five frozen runs completed.

Full reports, configuration hashes, code SHA, mechanism manifest, terminal hashes, and
series summaries are in
[`m3-stage0-multiseed.json`](../../artifacts/validation/m3-stage0-multiseed.json).

## Stage 3 diagnostic

The committed runner executed seeds `2026072701` through `2026072705` for 1,800 ticks each,
covering five simulated years per seed and 2,134,714 total events. The full per-seed gate
records and reproducibility hashes are in
[`m3-stage3-multiseed.json`](../../artifacts/validation/m3-stage3-multiseed.json).

| Gate | Five-seed result | Finding |
|---|---|---|
| V1 stationarity | FAIL × 5 | CPI and real GDP pass on all seeds. The fixture exposes only five market-index observations instead of a five-year series. Annual unemployment is below the 200 bp floor on four seeds. |
| V2 accounting closure | PASS × 5 | Zero runtime money/ledger violations, zero post-hoc closure failures, and exactly 1,801 tick boundaries checked per seed. |
| V3 non-degeneracy | FAIL × 5 | Wealth concentration passes. Active firms, zero-trade streaks, and 30-day transaction participation fail on every seed; unemployment also fails the daily band. |

The diagnostic is intentionally classified `engineering_diagnostic_failed`. It cannot be
promoted to research evidence because the configuration uses 50 agents, the development
acceptance fixture, the reflex-only ablation, and the stub provider.

## Gates still open

1. Run a bounded Stage 1 MiniMax M3 calibration over three predeclared seeds for one
   simulated year, with deliberate cognition enabled, V2/V3/V4 evaluation, a hard quota
   and cost ceiling, and exact offline cache replay.
2. If Stage 1 passes, schedule the final five-seed/five-year real-provider V1-V3 gate
   across provider quota windows.
3. Integrate antitrust decisions when polity lands in M4.
4. Integrate the four bankruptcy/death orderings when C20 lands in M5.

Until the first two gates pass, M3 is an accepted engineering and mechanical-calibration
milestone, not an accepted research milestone.

## Live-provider pilot completed

The bounded stage used `MiniMax-M3` under two independent circuit breakers:

- persistent provider scope: at most 10,000 calls in any 18,000-second window;
- pilot run: at most 8,000 provider wire attempts across cached resumes and at most USD 25
  at configured list prices.

The 80-tick pilot is intentionally shorter than a five-year research seed. At the accepted
7% deliberate share, a five-year/1,000-agent seed needs roughly 126,000 deliberate calls
before reflections. The pilot first validates schema repair, action diversity, throughput,
quota enforcement, cache persistence, and exact offline replay. Full five-seed execution
must then be scheduled across provider windows.

Codex and Grok CLI integrations are limited to one-call compatibility smokes. They execute
outside the repository with bounded output and no simulation mutation authority. Their
high harness overhead makes them inappropriate for population-scale cognition until a
separate cost and privacy review promotes them.

### Live calibration evidence

| Gate | Result |
|---|---|
| MiniMax M3 one-call smoke | PASS: schema-valid live response, USD 0.00019716, exact offline replay with no provider lane |
| Codex CLI one-call smoke | PASS: schema-valid response, bounded process, exact offline replay; 21,147 input and 23 output tokens |
| Grok CLI one-call smoke | PASS: clean-profile schema-valid response and exact offline replay; 15,349 input and 23 output tokens |
| 1,000-agent preflight | PASS: 70/70 schema-valid calls, 71 wire attempts, four action types, zero null actions, USD 0.04597452 |
| Preflight replay | PASS: terminal hash `04a8aca030f2a52cb4183ee27f2f88dd978532522df7de87afc9e00dcc6be702` reproduced exactly offline |
| 80-tick/1,000-agent pilot | PASS: 5,600 completed calls, 30 repair attempts, 6,072 provider wire attempts, 100% schema-valid responses, five action types, two null actions (0.035714%), and USD 2.98517640 |
| Pilot replay | PASS: terminal hash `b4fecd7855530a19f96d70618ffda3545b9a65d1e44099362e41566d79b371c4` reproduced exactly offline with no provider access |
| Full offline gate | PASS: 250 non-live tests, Ruff lint/format, mypy strict over 115 source files, four import contracts, determinism and prompt linters, and the production frontend build |

The completed run remained below both hard limits: 6,072 of 8,000 permitted wire attempts
and USD 2.98517640 of the USD 25 ceiling. Its run-UUID-scoped persistent wire-attempt
ledger included retries, while the completion cache preserved the 5,600 logical
completions across bounded restarts. Transport concurrency remained a runtime control, so
lowering burst pressure preserved the same run ID, completion cache, event chain, and quota
scope.
