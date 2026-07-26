# M3 Capital acceptance

**Status:** Engineering slice passed on 2026-07-26; full research acceptance pending

**Delivery branch:** `codex/build/m3-capital`

**Accepted implementation:** `f5438fd7ccfb541034db34fa8f035b894a68abf8`

**Scope boundary:** This delivery makes the deterministic capital layer operational and
observable. It does not claim the full C15 research exit gate or a publishable A3, A5, or A6
result.

## Delivered

- A deterministic limit-order book with call auctions, continuous matching, escrow,
  cancellation, price-time priority, commissions, circuit breakers, market data, IPOs,
  short positions, margin calls, borrow fees, and forced covering.
- Startup formation, VC funds, pitch evaluation through the cached LLM router, term sheets,
  funding rounds, cap tables, dividends, acquisitions, automatic stays, creditor claims,
  priority distributions, loan write-offs, and liquidation discharge.
- Seeded liquidation slices that sell listed holdings through the real order book, plus
  in-world sales of private stakes, inventory, and capital to solvent buyers. Unsold
  non-listed assets are explicitly written off without a ledger transaction.
- Checkpoint-safe capital state, Postgres projections through Alembic revision
  `0012_m3_capital`, M3 research metrics, and read-only capital Observatory routes with
  freshness metadata.
- A scripted M3 smoke fixture that is explicitly development-only and does not masquerade as
  an emergent research result.

## Engineering evidence

| Gate | Result |
|---|---|
| Quality gates | Ruff lint/format, determinism lint, prompt lint, mypy strict over 111 source files, four import-linter contracts, and 155 pytest tests pass |
| Frontend | TypeScript and Vite production build pass; 1,580 modules transformed |
| Exchange properties | P1-P12 cover crossed books, share conservation, reservations, ledger closure, price bands, replay, self-trades, price-time priority, short caps, and exact cancellation release |
| Frozen capital run | The 50-agent/100-tick run has terminal hash `6e374b1f1731a7fc98b8d1d77f6d44cc23355177799a4b16d985308269fd4c53` |
| Liquidation conformance | Locked listed shares sell through seeded market-order slices with realised book impact and commissions; private stakes, inventory, and capital move only after a solvent in-world buyer posts exact cash |
| Economy activity | The frozen run includes 20 wage payments, 4,256 goods purchases, one exchange trade, 12 loan payments, and four loan originations |
| Integration smoke | The 50-agent/500-tick run completes 125,363 events with hash `a615919ea6a53788cc4d07b38f024aa5c2538bea9fd0d68d7925b34c09ee2b3b` and no halt |
| Long-run stability | The 50-agent/5,000-tick run completes 966,539 events with hash `982883938043edb71f014e4f50d04f46569f53d6b77190fc71438ae5de4ba129` and no halt |
| Persistent verification | Run `d0b842a7-929b-5b54-a20c-7b6f498208b2` verifies all 4,177 stored events |
| Exact replay and rebuild | Replay and projection rebuild reproduce terminal hash `fe7c2cf41dcc2dc0d0f83ea93a5314ac85c7b62c3861023fbd08ee822c18079d` exactly |
| Checkpoint resume | A dedicated M3 checkpoint test resumes without event or state divergence |
| Live Observatory | Capital catalogue and securities, trades, market-index, startups, acquisitions, and bankruptcies routes return projection data with tick/sequence freshness; mutation verbs return HTTP 405 |

Machine-readable evidence is in
[`m3-acceptance.json`](../../artifacts/acceptance/m3-acceptance.json).

## Gates still open

This merge must not be cited as full M3 scientific acceptance until the following are closed:

1. Complete C15 acquisition integration: overlap redundancies and severance, loan-obligor
   transfer, and the asset-sale shell insolvency path.
2. Register and test the three declared M3 mechanisms and their ablations.
3. Add the complete C15 property/invariant matrix, including strict priority-waterfall
   properties and bank-failure cascade coverage.
4. Integrate the four bankruptcy/death orderings when C20 lands in M5, and antitrust decisions
   when polity lands in M4.
5. Run calibration Stage 3: five seeds for five simulated years with V1-V3 holding while the
   capital layer is active.

Until those gates pass, this is an accepted engineering foundation, not an accepted research
milestone.
