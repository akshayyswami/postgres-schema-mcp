# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- When the database cannot be reached, a tool now fails with "Could not connect to the database", which names `PGSS_DSN`, instead of raising the driver's exception as it was. The `mcp` SDK from 2.3.0 shows the model only "Error executing tool X" for such an exception, so the model would have had no hint. A schema read that fails gets the same treatment ("Could not read the schema from the database"). Both messages carry the exception's class name and never its text, because libpq repeats part of a malformed connection string, password included. Neither failure is remembered, so the next call connects again.

## [0.1.1] - 2026-11-23

### Added

- README sections for adding the server to Claude Code, Claude Desktop, Codex, Cursor and VS Code, for using it on your own database (with the SQL for a read-only role), a Configuration table with every setting, Troubleshooting and Privacy. Only Claude Code was run. The other clients follow their own documentation as read on 2026-11-22.
- A test that fails when an environment variable the code reads has no row in the README's Configuration table.
- A pull request template, and Dependabot for GitHub Actions and uv.

### Fixed

- On a database whose schemas are not the AdventureWorks default, `search_schema` returned `"tables": []` with no error. It now fails with a message that lists the schemas the role can read and the `PGSS_SCHEMAS` value to set, and the empty result is not remembered, so a restart or a grant is picked up. A misspelt schema in `PGSS_SCHEMAS` logs a warning.
- That message repeats only schema names made of plain identifier characters, lists at most 20 and counts the rest, because a schema name can hold any text and the message goes to the model. If listing the schemas fails, the message says so.
- The instructions sent to the model name schema, table and column names as untrusted text, and say so for error messages that list them.
- README: the role SQL says it needs a superuser, not the database owner, and that `GRANT SELECT ON ALL TABLES` exposes every row to the model provider.
- The instructions sent to the model said the database was AdventureWorks with 68 tables, whatever it was connected to.

## [0.1.0] - 2026-11-20

First release.

### Added

- The `mcp-name` line the MCP Registry reads in the README, and a `uvx postgres-schema-mcp` client config next to the clone-based one.
- Result caps: rows are read from a server-side cursor in batches, with a row cap, a per-cell cap (`PGSS_MAX_CELL_CHARS`) and a total size cap (`PGSS_MAX_RESULT_BYTES`). Each cut is reported to the model.
- A tighter database role, the third layer, in `init/20-grants.sql`: `EXECUTE` revoked from `PUBLIC` on the functions the guard refuses, and a connection limit and temp-file limit on `scout_ro`.
- Nine planted faults and three clean inputs for the guard bypasses below. The matrix is 21 must-block and 12 must-allow.
- The release workflow checks that the tag matches `pyproject.toml` and `server.json` and is on `main`, and every action is pinned by commit SHA.
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

### Security

A review before this release found and fixed these. They are recorded in `docs/DECISIONS.md`.

- A double-quoted function name (`"pg_sleep"(1)`) or a `U&"..."` escape bypassed the function denylist. Names are now compared unquoted, and a name that is not a plain identifier is refused.
- Side-effect functions outside the list ran inside a SELECT: advisory locks, `nextval`, `pg_notify`, large objects and `crosstab`. The denylist now covers whole families by prefix.
- Transactions were not explicitly read-only, so `set_config('default_transaction_read_only', 'off', false)` could change the session default. Every transaction is now `BEGIN READ ONLY`, and a connection is reset when it returns to the pool.
- The HTTP transport refuses a non-loopback bind unless `PGSS_ALLOW_REMOTE=1`, because it has no authentication.
- Database comments shown to the model are joined onto one line and capped, and the instructions call comments and rows untrusted.

### Changed

- Renamed from `pg-schema-scout` to `postgres-schema-mcp`. Nothing had been published under the old name.
- Code is split into `core/` (no I/O) and `adapters/` (Postgres, Bedrock).
- Python 3.12 or newer. CI runs 3.12 and 3.13 through `make check`.
- `BlockedQuery` is now `BlockedQueryError`.
