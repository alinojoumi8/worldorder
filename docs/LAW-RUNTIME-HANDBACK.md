# C19 law runtime handback

## Composition and coordination

The M4 baseline selects `actions.legality.oracle: law`. The composition swap is effective at
tick 0 when `SocietyRuntime` is constructed with the C19 oracle and law resolver. The
implementation commit that introduced the swap is
`cc8bdce3ae94e474f1f36f891d5afcd6655b5558`.

- C11 payroll now accepts `GarnishmentProtocol` and `WagePenaltyProtocol`. Garnishment posts
  a real ledger transfer before reducing the receivable; an unpaid penalty is never silently
  written off.
- C13 can call `backfill_insider_profit(repo, crime_id, realised_profit_cents)` when a flagged
  position realizes its profit. The existing crime row is updated.
- Core world generation always supplies a prison while preserving `places_per_district`.
  At scales that would omit the rare facility, the final core allocation becomes the prison.
  Detection population counts exclude custodial occupancy.
- PHASE 7 runs detection, the investigation queue, and releases daily; allocates the police
  budget monthly; and exposes an async twice-weekly court phase on sim-days 0 and 3.

## Deterministic calibration

The 400-tick mechanism smoke used two police-budget cells, three seeds per cell, and 20
seeded committed crimes per seed (half theft, half insider trading). It is a deterministic
mechanism calibration, not a scientific agent-behaviour acceptance run.

| Measure | Low budget | High budget |
|---|---:|---:|
| Seeded committed rate | 1.0000 | 1.0000 |
| Mean detected rate | 0.0333 | 0.0667 |
| Mean dark figure | 0.9667 | 0.9333 |
| Type share: theft | 0.5000 | 0.5000 |
| Type share: insider trading | 0.5000 | 0.5000 |
| Seeded path split (proxy; no `13001` in this sweep) | 100% derived | 100% derived |

The end-to-end stub court fixture produced one conviction from one charged case, so
`conviction.rate = 1.0` and `court.bench_share = 1.0`. Those values deliberately fail the F9
research thresholds because the fixture exercises deterministic fallback with no live judge
provider. They prove plumbing and replay only. A live 400-tick M4 calibration remains required
before making deterrence or judicial-behaviour claims.

The separate legality acceptance fixture emitted six `13001` events: five derived and one
explicit (`derived = 0.8333`, `explicit = 0.1667`).

## Decisions not fixed by the spec

- The twice-weekly court cadence is sim-day 0 and sim-day 3 of each seven-day week.
- If prison capacity is exhausted, the conviction record still increments even though custody
  converts to a fine; the record belongs to the conviction, not the placement.
- Findings that cite any non-admitted event sequence are dropped in full and recorded as
  `finding_non_admitted`.
- The judge route retries an invalid or mismatched structured response three times, then uses
  the bench rule and records `origin: bench`.
