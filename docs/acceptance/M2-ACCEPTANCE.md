# M2 Economy acceptance

**Status:** Passed on 2026-07-26

**Delivery branch:** `build/m2-economy`

**Accepted code:** `0b5a9266467b9061304381202ee9ebd59a70c9aa`

**Scope boundary:** Exchange, ventures, households, polity, and causal-comparison
research tooling remain assigned to M3-M6.

## Delivered

- Cent-exact double-entry money, deterministic genesis, labour matching, employment,
  payroll, firms, production, inventory, goods purchases, rationing, consumption,
  fixed-basket CPI, commercial banking, credit, central-bank settlement, taxes,
  transfers, arrears, treasury bonds, and both configured bank-resolution modes.
- M2 research metrics at their declared tick, day, week, and quarter cadences.
- Read-only Observatory exposure for unemployment, CPI, wages, bank capital, M1, and
  nominal GDP. Later-milestone views and `market_index` remain visibly unavailable.
- Rolling goods-market indexes that bound seller, sales-history, and CPI scans without
  changing deterministic output.
- Postgres projections and migrations through Alembic revision
  `0011_loan_close_tick`.

## Acceptance evidence

| Gate | Result |
|---|---|
| Quality gates | Ruff lint, scoped source format, mypy strict over 105 source files, four import-linter contracts, and 126 pytest tests pass |
| Frontend | TypeScript and Vite production build pass |
| Live Observatory | M2 charts render real projection data over the WebSocket with no browser console errors; mutation verbs remain HTTP 405 |
| Deterministic repeat | Two 50-agent/100-tick runs have identical events, complete economy state, metrics, and terminal hash `81c7051233293a2b10c71deba3128a38ea11cf00e3d679cdf37703900ff1c7e7` |
| Scale determinism | Repeated 1,000-agent/100-tick microscope runs produce hash `226bdfee73d34c17c4447774949033527cd8674f7a96b132692694d401ab6978` |
| Scale throughput | 1,000 agents complete 100 microscope ticks at 2.313 ticks/s, above the 1 tick/s target, with zero-cent global closure |
| Five simulated years | 50 agents complete 1,800 chronicle ticks (5.0 years) in 110.244 s at 16.327 ticks/s |
| Five-year accounting | 400,568 tick events and 404,465 ledger entries finish with global, materialisation, and maximum per-bank deposit imbalance all exactly 0 cents |
| Five-year economy | 39,530 goods transactions, 10 repaid loans, 46 paid tax assessments, unemployment 4.76%, CPI 14,524, nominal GDP 9,169,825 cents, and M1 45,125,403 cents |
| Performance stabilization | The 50-agent/800-tick economy improved from 82.41 s to 38.475 s while preserving exact closure |
| Persistent event verification | Run `84e520a2-1a28-5dc5-aa8f-35434bd1ef22` verifies all 29,616 stored events with no failures or unknown kinds |
| Exact replay and rebuild | Replay and projection rebuild both reproduce terminal hash `44cfda657b3552938a5fa342ad4ccaf9628e9087bbd8a28a2c273eef189326bf` exactly |
| Checkpoint restore | Restoring the tick-100 checkpoint appends zero events and preserves the terminal hash |

Machine-readable evidence is in
[`m2-acceptance.json`](../../artifacts/acceptance/m2-acceptance.json).

## Scientific boundary

The M2 baseline is deliberately mechanical and runs with the reflex-only ablation for
calibration. The scorecard, vacancy fallback, search slice, Cobb-Douglas production,
inventory markup rule, benefits, and policy-rate rule are declared mechanisms, not
empirical findings. The first A1 experiments become technically runnable at M2, but no
headline causal claim is accepted without the preregistered multi-seed and scale-ladder
design.

Finance and media firms may exist as labour-market entities without a goods SKU. They do
not receive synthetic inventory. M2 treasury bonds use the documented deterministic
primary-allocation bridge until M3 provides the exchange venue.
