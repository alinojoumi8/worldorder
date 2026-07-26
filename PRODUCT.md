# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- The primary user is the founder-researcher defining experiments, monitoring live runs, inspecting agents, comparing outcomes, and preparing reproducible research.
- Collaborating researchers and reviewers use the Observatory to verify claims, trace macro outcomes to individual events, and inspect the integrity of completed runs.
- External-agent operators use the Observatory to monitor participating agents and read capability scorecards.

## Product Purpose

POLIS is a persistent, tick-based city simulation where roughly one thousand AI agents live, work, learn, earn, spend, borrow, invest, build relationships, form institutions, and age across generations. Its Observatory makes the living city legible: researchers can move from live city-scale conditions to a specific agent, inspect what that agent perceived and remembered, and trace the resulting action and consequences.

Success means the interface helps a researcher answer not only what changed, but why it changed, without obscuring sampling gaps, stale projections, metric drift, or threats to reproducibility.

## Positioning

POLIS combines a living city of generative AI agents with a rigorous institutional economy and an append-only causal event log. Its distinguishing mechanism is end-to-end research traceability: city-scale statistics, shocks, and comparisons can be drilled down through causal events to an individual agent's perception, memory retrieval, prompt, response, action, and outcome.

## Operating Context

- The frontend is the read-only Observatory defined by `docs/10-RESEARCH-AND-OBSERVABILITY.md` and `chunks/C23-observatory.md`.
- Researchers work across live and completed runs, macro metric series, a 2D city map, agent records, causal graphs, event search, run comparison, scenarios, sweeps, and external-agent scorecards.
- Live state may arrive up to ten times per second; historical state is queried separately.
- Most agent-tick cognition details are intentionally unsampled. The interface must distinguish “not recorded” from “nothing happened.”
- Charts may be exported or captured for research communication, so warnings about drift, stale data, or overrides must remain visible in the rendered artefact.

## Capabilities and Constraints

- The frontend stack is React, TypeScript, and Vite.
- The Observatory is strictly read-only. It has no run control, parameter mutation, shock injection, or clock controls.
- Core views are Map, Charts, Agents, Inspector, Causal, Search, Compare, and Arena.
- The map is a research visualization of grid geometry, districts, places, movement, occupancy, and agent positions. It is not a game and does not use sprites or decorative simulation animation.
- The Agent Inspector is the primary product surface and must make perception, salience, retrieval, prompt, response, action, and outcome understandable end to end.
- Every data surface must expose its freshness using `as_of_tick`, `as_of_seq`, and persistent lag warnings when applicable.
- Run comparison must place reproducibility differences before charts and refuse incompatible metric overlays unless an explicit drift override is present and visibly stamped.
- The initial frontend may use realistic synthetic fixtures and local interactions while backend APIs are not yet implemented; mock data must be unmistakably demo data rather than a scientific claim.

## Brand Commitments

- The confirmed product name is **POLIS**.
- The product voice is precise, candid, research-oriented, and non-anthropomorphic.

## Evidence on Hand

- Product and system requirements are present in `docs/`.
- Implementation contracts and acceptance criteria are present in `chunks/`.
- No existing logo, design system, production UI, customer testimonials, published findings, or approved visual assets are present. The frontend must not fabricate them.

## Product Principles

1. Legibility over spectacle: every macro signal should lead toward an inspectable cause.
2. Scientific honesty is interface behavior: gaps, drift, lag, uncertainty, and invalid comparisons stay visible.
3. Dense does not mean confusing: progressive disclosure keeps the main research question prominent.
4. Read-only means demonstrably read-only: observation and explanation are never mixed with simulation control.
5. The city is an analytical object, not a game world.
