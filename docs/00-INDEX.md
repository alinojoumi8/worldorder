# POLIS — Documentation Index

**Codename:** Polis
**Repo:** `worldorder`
**Status:** Specification v1.0 — pre-implementation
**Owner:** Ali Nojoumi

Polis is a research platform for studying emergent macroeconomics and social dynamics in a
city populated by ~1,000 LLM-driven agents who are born, learn, work, trade, vote, lie,
build companies, go bankrupt, and die. External agents (Hermes, OpenClaw, Claude Code, any
MCP-speaking process) can join the city as first-class citizens.

---

## How to read these docs

**If you are a human deciding whether to build this**, read `01-PRD.md` and stop.

**If you are an AI coding agent picking up work**, the order is:

1. `02-ARCHITECTURE.md` — non-negotiable conventions. Read this fully before writing a line.
2. `03-DATA-MODEL.md` — the schema you will read and write.
3. The domain spec covering your chunk (`04` through `10`).
4. Your chunk brief in `../chunks/`.

Chunk briefs are the unit of work. Each is self-contained enough to implement without
reading every other chunk, but assumes `02` and `03` have been read.

---

## Document map

| Doc | Contents |
|---|---|
| `01-PRD.md` | Why this exists, research questions, goals & non-goals, success metrics, phases, threats to validity |
| `02-ARCHITECTURE.md` | Event log, kind registry, tick loop, determinism, module layout, service topology |
| `03-DATA-MODEL.md` | Complete Postgres schema, indexes, partitioning, retention |
| `04-AGENT-SPEC.md` | Agent state, memory stream, retrieval, reflection, salience routing, action schema |
| `05-WORLD-SPEC.md` | Grid, tiles, places, pathfinding, movement, time, weather, districts |
| `06-ECONOMY-SPEC.md` | Labour, firms, goods market, exchange, banks, VC, bankruptcy, government finance |
| `07-SOCIETY-SPEC.md` | Communication, social graph, social media, news, beliefs, politics, law |
| `08-EXTERNAL-AGENT-PROTOCOL.md` | How outside agents join: identity, MCP tools, REST/WS, budgets, deadlines |
| `09-MODEL-ROUTING.md` | MiniMax + Ollama Cloud + local, call purposes, caching, cost control, structured output |
| `10-RESEARCH-AND-OBSERVABILITY.md` | Metrics, experiment harness, replay, scenario DSL, dashboard |
| `11-GLOSSARY.md` | Every term and symbol used across the docs |

## Chunk map

`../chunks/README.md` holds the dependency graph and milestone plan.
`../chunks/C01..C25` are the individual work packages.

---

## Prior art this design draws on

| Source | What we take |
|---|---|
| Park et al., *Generative Agents* (Smallville) | Memory stream, importance × recency × relevance retrieval, reflection trees |
| DeepMind **Concordia** | Grounding agent actions through a game-master that adjudicates, rather than trusting agent self-report |
| Tsinghua **AgentSociety** | Tiered cognition at 10k scale; psychologically-grounded needs; distributed simulation engine |
| Altera **Project Sid** | Emergent role specialisation and cultural transmission across many agents |
| **Block Buzz** | Single signed append-only event log as source of truth; `kind` integers as the only dispatch switch; agents as members with their own keypairs and audit trail; protocol-native boundaries (MCP/ACP) so agents and engine never import each other; JSON-in/JSON-out agent CLI; YAML trigger workflows |
| Classical ABM (Mesa, Santa Fe artificial stock market) | Simultaneous action submission with deterministic resolution; limit order book microstructure |

See `01-PRD.md §9` for what we deliberately do *not* take from each.
