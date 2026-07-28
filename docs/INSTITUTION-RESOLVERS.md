# Institution resolver implementation note

`InstitutionResolver` is the stable boundary between agent decisions and mechanical
institutions. C11-C19 implementations import the contract from
`polis.agents.actions`; they must not import agent cognition or memory.

## Contract

Each resolver declares one fixed `InstitutionSlot` and a disjoint `handles` set. Registering
the same action type twice is an error, as is registering two resolvers in the same slot.
The registry is intentionally explicit: registration order never determines simulation
order.

Validation calls the resolver gates in this exact order:

1. `check_capability` — standing or authority, such as ownership or a licence.
2. `check_locality` — physical or relational ability, using the committed Observation in
   `ValidationContext`, never mutable PHASE 5 state.
3. `check_resources` — funds, shares, inventory, or institutional capacity.

Return `None` to pass or `GateFailure` to reject. C10 owns schema parsing and action-slot
accounting. Resolvers must use `ValidatedAction.validated_params`; they must not independently
re-parse the raw mapping.

The legality oracle runs last. A crime verdict is classification only: the action always
continues to resolution and emits `ACTION_FLAGGED_ILLEGAL` with `proceeded=true`. Detection,
prosecution, and punishment belong to C19.

## Context and ordering guarantees

`ValidationContext` is frozen and contains the actor's committed Observation, agent state,
tick, runtime overlay, and read-only repository handles. `ResolutionContext` contains the
event emitter, runtime overlay, and read-only repository handles.

The dispatcher walks the literal slot order:

`MOVEMENT, COMMUNICATION, LABOUR, GOODS, EXCHANGE, BANKING, VENTURES, POLITY, LAW, MISC`.

Within a resolver, actions are pre-sorted by `(actor_id, action_id)`. The dispatcher calls
every registered resolver once per tick, including with an empty batch, and concatenates
events exactly as returned. A resolver must therefore tolerate empty input and must be
order-independent unless its documented mechanism is explicitly price-time-priority.

## Migration and C19 handoff

The existing M1-M3 engine remains on its compatibility validator while resolvers migrate
slot by slot. The import-linter contract records the ten existing economy-to-agent-state
imports as named grandfathered edges; new institutional imports are not exempt.

`PermissiveLegalityOracle` is the run default during this migration. C19 must replace it with
the law oracle at the tick boundary where the LAW resolver and crime repository become active;
the replacement must occur before PHASE 4 validation for that tick.
