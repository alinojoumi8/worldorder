# M1 stabilization and multi-seed validation

**Status:** Passed on 2026-07-26  
**Branch:** `stabilize/m1-validation`  
**Scope:** M1 validation and correction only; no M2 economy behavior was introduced.

## Finding and correction

The accepted implementation classified chronic unmet need pressure as the salience
`stakes` component. That contradicted `04-AGENT-SPEC.md`, which defines stakes as
event deltas in wealth, health, employment, relationships or legal jeopardy.

A three-seed, 100-tick preflight measured:

- 101.635–101.659 memories per agent;
- 1.01635–1.01659 writes per agent-tick;
- projected mean collision with the 3,000-memory cap near tick 2,951; and
- throughput of 2.435–2.467 ticks/s.

Once a non-restored M1 need reached zero, routine observations remained permanently
high-stakes. This created a positive feedback loop: chronic need pressure increased
salience, almost every tick became a memory, retrieval work grew, and throughput fell.

M1 observations now carry explicit event stakes of zero. Later milestones own calculation
of their event deltas. Needs still affect reflex decisions and wellbeing, but do not
masquerade as events. A regression test enforces this boundary.

## Formal validation

Three seeds ran concurrently at 1,000 agents and 2,000 ticks each. Concurrent measurements
are conservative because they include worker contention.

| Metric | Minimum | Mean | Maximum |
|---|---:|---:|---:|
| Throughput (ticks/s) | 3.088 | 3.106667 | 3.131 |
| Late/early throughput ratio | 0.932164 | 0.936651 | 0.940836 |
| Memories per agent | 2.256 | 2.357 | 2.486 |
| Memory writes per agent-tick | 0.001128 | 0.001178 | 0.001243 |
| Projected mean memory-cap tick | 2,413,515.7 | 2,549,767.7 | 2,659,574.5 |
| Final wellbeing | 44.31561 | 44.541957 | 44.75657 |
| Last-200 wellbeing slope per 1,000 ticks | -1.806715 | -1.464052 | -1.254904 |
| Normalized action entropy | 0.854824 | 0.860591 | 0.864050 |

Every seed:

- completed without halt or population loss;
- preserved exactly 7.00% deliberate cognition;
- used all six M1 action types every measured tick;
- passed the entropy floor on every measured tick;
- retained at least 90% of early throughput in its last 500 ticks; and
- remained far below the configured 3,000-memory cap.

The formal machine-readable report is
[`m1-multiseed.json`](../../artifacts/validation/m1-multiseed.json).

## Wellbeing interpretation

The decline from about 76.9 to 44.3–44.8 is reproducible, narrow across seeds and nearly
flat by the final 200 ticks. Its final need decomposition is also consistent:

- energy averages 0.804920–0.814230;
- hunger averages 0.817463–0.830644;
- social averages 0.003833–0.004350; and
- security, esteem and purpose are zero.

This is not treated as a healthy-city result. It is an explicit M1 milestone boundary.
M1 lacks the institutions named by the specification as restoring those needs:
employment, savings and housing arrive in M2; firms and founding expand in M2/M3; social
systems, media, elections and office arrive in M4. Adding artificial restoration in M1
would pre-state the effects those milestones are intended to create.

M2 must add tests showing that its employment, savings and housing mechanisms restore
security without breaking accounting closure. Later milestones must rebaseline social,
esteem and purpose as their owning systems land.

## Reproducibility

- Frozen 50-agent/100-tick cross-platform hash:
  `fb583b89c6d0a34155c3ac422a2c0ed6c0216025fab03c82a40e02113aac9844`.
- Stabilized persistent demo: `672e6468-c53d-5e1f-bed4-e6b4e1ac2e89`.
- Stored demo events: 9,673.
- Demo terminal hash:
  `b1af934edcc4a9ed019d71b3eddacf8ee3ebee9045d0e8e35066a8cb868d3ca5`.
- `verify`, offline `replay`, projection `rebuild` and completed-run `resume` all match;
  resume appends zero events.

