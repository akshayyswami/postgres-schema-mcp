# postgres-schema-mcp: spec

## Thesis

Schema access for an agent is a retrieval problem, not a dump.

## Problem

An agent that writes SQL needs the schema in its context. The usual MCP database server either lists table names and makes the agent ask for each one, or dumps the whole schema. On a wide schema the dump costs most of the context window and truncates when it does not fit, and a table that falls outside the cutoff cannot be asked for. Read-only is usually claimed through a tool annotation, which a client is told to treat as untrusted.

## Who uses it

Developers wiring an LLM agent to a Postgres database with dozens of tables, who want the agent to find the relevant tables from a plain-language question and want a read-only guarantee that does not depend on the model or the client behaving.

## v0.1 scope

- Four tools: `search_schema`, `describe_table`, `explain_query`, `run_query`.
- Retrieval: BM25 over one document per table, plus one damped round of score propagation across foreign keys.
- Read-only enforced in three layers: an untrusted annotation, an AST policy, and a database role that holds only SELECT.
- Runs offline. An optional Bedrock embedding backend exists and is not the default, because it measured worse.
- Seeded AdventureWorks sample database in Docker, so every number is reproducible.

## Non-goals

- No write path of any kind, no prompts, no resources.
- No `EXPLAIN ANALYZE`, because it would execute the query.
- No cross-database or multi-tenant support, and no auth. The server runs locally over stdio, and the security boundary is the database role.
- No schema-agnostic claims. The identifier lexicon is specific to the sample schema.

## Architecture

See [architecture.md](architecture.md). `core/` is pure: the read-only guard, the retrieval index and the types. `adapters/` does the I/O: Postgres and the optional Bedrock backend. `server.py` is the thin MCP entry point.

## The number

Schema context sent to the model, in tokens, on the seeded AdventureWorks database over 26 questions. Baseline: the full schema DDL in one blob. Measured with `tiktoken/cl100k_base`. Reproduce with `make evals`. The headline and its caveats are in the README.

Retrieval quality is measured alongside it as recall@8 and the fraction of questions with every needed table present, because a smaller slice is only worth having if it still contains what the query needs.

## Planted faults

Named known-bad inputs the guard must block, and clean inputs it must allow, in `evals/faults.yaml`. It is two-sided on purpose: a guard that blocks everything passes a one-sided test. Every allowed query is also executed against the database by `make evals-db`.

## Milestones

Vertical slices. Each one has an acceptance test that proves it.

- M1: the four tools, the AST guard, and the read-only role, with the fault matrix. Done.
- M2: retrieval with an eval set, then foreign-key propagation after the first run missed join hubs. Done.
- M3: README with measured numbers, the Titan comparison and the conformance result. Done.
- M4: standard tooling, figures re-measured offline in CI, v0.1.0 tag and GitHub Release, MCP Registry entry. Tooling done, release not yet.

## Distribution

- GitHub: `akshayyswami/postgres-schema-mcp`.
- PyPI: not published. The package name `postgres-schema-mcp` was free on 2026-11-19.
- MCP Registry: `server.json` is written, name `io.github.akshayyswami/postgres-schema-mcp`. Not published.

## GitHub

- Description (under about 120 characters, with the search phrase in it): An MCP server that gives an AI agent the relevant slice of a Postgres schema, with read-only enforced in three layers
- Topics (up to 20): mcp, model-context-protocol, postgres, postgresql, text-to-sql, ai-agents, llm, schema-retrieval, python
