# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

First release, not yet tagged.

### Added

- Four MCP tools: `search_schema`, `describe_table`, `explain_query` and `run_query`.
- Retrieval over one document per table with BM25, plus one damped round of score propagation across foreign keys.
- Read-only enforced in three layers: tool annotations, an AST policy, and a `scout_ro` database role that holds only SELECT.
- An optional Amazon Titan embedding backend through Bedrock, not the default.
- A seeded AdventureWorks Postgres in Docker, so every number is reproducible.
- Evals: a two-sided fault matrix, retrieval quality, and the schema-context measurement, all printed as one JSON object by `make evals` and re-measured in CI.
- A frozen copy of the schema in `evals/fixtures/catalog.json`, so figures are measured without a database and checked against a freshly seeded one.
- README figures wrapped in eval markers that CI checks against `evals/results/latest.json`.
- `server.json` for the MCP Registry.
- A terminal demo in the README. `docs/demo.tape` records it with `vhs`, and `evals/demo.py` is what it runs, offline.

### Changed

- Renamed from `pg-schema-scout` to `postgres-schema-mcp`. Nothing had been published under the old name.
- Code is split into `core/` (no I/O) and `adapters/` (Postgres, Bedrock).
- Python 3.12 or newer. CI runs 3.12 and 3.13 through `make check`.
- `BlockedQuery` is now `BlockedQueryError`.
