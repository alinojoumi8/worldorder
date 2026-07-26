# M3 Capital acceptance

**Status:** Engineering milestone complete on 2026-07-26; scientific acceptance blocked by
the Stage 3 research gates

**Engineering implementation:** `ad7a1751add70ef8ce478dc3a3f5eb9b805b11a4`

**Validation harness:** `c6a1aaa46a87b1954c09a0c5e31847284e35e3b1`

**Scope boundary:** The deterministic capital layer and its executable V1-V3 evaluator are
operational. The five-seed offline diagnostic is not a scientific pass: it deliberately uses
the 50-agent stub fixture and fails V1 and V3 on every seed.

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
| Quality gates | Ruff lint/format, determinism lint, prompt lint, mypy strict over 112 source files, four import-linter contracts, and 175 non-live pytest tests pass |
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

1. Replace the scripted capital fixture with a non-fixture 1,000-agent calibration
   configuration that sustains at least five active firms and a continuous listed market.
2. Calibrate labour participation and recurring transaction activity until V1 and V3 pass
   on all five predeclared seeds.
3. Run the same five-seed/five-year gate with a bounded real provider and offline cache
   replay after an explicit cost limit is approved.
4. Integrate antitrust decisions when polity lands in M4.
5. Integrate the four bankruptcy/death orderings when C20 lands in M5.

Until the first three gates pass, M3 is an accepted engineering milestone, not an accepted
research milestone.
