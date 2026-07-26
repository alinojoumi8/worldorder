# POLIS implementation notes

This file records implementation findings that amend or narrow a binding specification.
No finding here changes scientific behaviour silently.

## M0 / C03

### Nested event partition primary key

PostgreSQL 17 rejects the documented combination of:

- `events PARTITION BY LIST (run_id)`;
- each run partition further `PARTITION BY RANGE (tick)`; and
- primary key `(run_id, seq)`.

A unique constraint on a subpartitioned table must contain every partition key, including
`tick`. The M0/M1 migration therefore uses primary key `(run_id, tick, seq)` and a separate
non-unique lookup index on `(run_id, seq)`. Global sequence uniqueness within a run remains
enforced by the single engine writer and verified by the hash-chain verifier.

### Milestone-scoped schema

C03's full migration plan names M2–M6 economy and society tables. The initial implemented
schema contains only M0/M1 core, agent, world, and research tables. Later tables will be
introduced and exercised by their owning milestones rather than remaining unvalidated,
unused schema.

## M1 / C23a

### Inspector trace projection

`03-DATA-MODEL.md` defines the authoritative event, LLM-call, agent, memory and world
tables but does not name a materialized inspector table. M1 adds `cognition_traces` as a
strictly derived read model so C23a can answer the end-to-end inspector route without
reconstructing every phase on each request. It is not simulation truth and may be deleted
and rebuilt from deterministic replay.

### Bounded reflection backlog

C09 says reflection force-routes may exceed the cognition call budget. In the M1 one-call
reflection implementation, synchronized threshold crossings caused hundreds of agents to
enter REFLECT together and suppressed the intended 7% DELIBERATE lane for long intervals.
M1 deterministically queues due reflections by accumulated importance and agent id, using
only the call reserve above the deliberate target. The importance/life-event trigger and
cooldown are unchanged; overflow remains armed for the next tick.
