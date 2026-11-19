# Decisions

One entry per decision that changes architecture or scope. Newest first.

## 2026-11-19: Rename to postgres-schema-mcp, keep the PGSS_ prefix and the scout_ro role

- **Decision:** the repo, distribution, package, console script and MCP server name are `postgres-schema-mcp`. Environment variables keep the `PGSS_` prefix and the database role stays `scout_ro`.
- **Why:** the repo name is what search reads, and a generic name finds more users than a clever one. The prefix and the role name live in the seed SQL and in existing volumes, and renaming them gains nothing for search.
- **Alternatives:** rename everything including the prefix and role (churn in the database init and every config, for no search benefit); keep the old name (it does not say what the tool is).

## 2026-11-19: Freeze the schema as a fixture so the numbers are measured offline

- **Decision:** `evals/fixtures/catalog.json` holds the seeded database's table metadata. `make evals` measures from it with no database. CI separately compares it with a freshly seeded database (`make evals-db`).
- **Why:** every figure in the README must be re-measured by CI, and doing that against a live Postgres made the measurement depend on a four-minute seed. The fixture is names, comments and keys only, no rows. Tables are sorted in Python and `row_estimate` is dropped, so the file is the same on every machine.
- **Alternatives:** measure against the live database in `make check` (slow, and `make test-fast` could not check the figures); store only the final numbers (nothing to re-measure).

## 2026-11-19: `make check` and CI need the seeded database, and integration tests fail instead of skipping

- **Decision:** the integration tests run in `make check`. If Postgres is not reachable they fail with a message pointing at `make db`. `make test-fast` is the loop without a database.
- **Why:** a skipped test reports green. A check that is green without ever touching the database says nothing about the part that enforces read-only. Coverage is 80 or more only with the adapter tests included.
- **Alternatives:** skip when the database is absent (silent gaps); lower the coverage floor (hides the untested adapter); fake the connection pool (tests the fake).

## 2026-11-19: Split core and adapters, move the Bedrock backend out of the index

- **Decision:** guard, retrieval with BM25, types and DDL rendering are in `core/` and do no I/O. Postgres and the Titan backend are in `adapters/`. `SchemaIndex` takes a backend factory, and `adapters.backends.make_index` picks the backend from `PGSS_RETRIEVAL_BACKEND`.
- **Why:** the standard keeps `core/` free of I/O, which keeps unit tests and the offline evals cheap. The index used to import the Bedrock client.
- **Alternatives:** leave the layout flat (the retrieval module made network calls when Titan was selected).

## 2026-11-19: Leave mypy's python_version unpinned

- **Decision:** `[tool.mypy]` does not set `python_version`, unlike the standard's template.
- **Why:** pinning it makes mypy parse installed third-party stubs, such as numpy's via `rank_bm25`, under the older grammar, and fail on syntax it cannot know about. The comment in `pyproject.toml` says the same.
- **Alternatives:** pin it and exclude the stubs (hides real errors).

## 2026-08-07: BM25 is the default retrieval backend

- **Decision:** the lexical backend stays the default and Titan embeddings are optional.
- **Why:** Titan was measured on the same questions and did not win, and BM25 also runs offline with no credentials and no per-query cost. See the README for the figures and the two caveats.
- **Alternatives:** make embeddings the default (measured worse); drop the option (it is cheap to keep and the comparison is part of the argument).

## 2026-08-07: Rank with one damped round of foreign-key propagation

- **Decision:** a table's score adds a share, 0.35, of its best neighbour's score.
- **Why:** purely lexical ranking missed join hubs such as `production.product`, and a slice without the table a foreign key points at cannot be joined. The weight was chosen on the same questions it is measured on, so the recall is optimistic, and the README says so.
- **Alternatives:** two rounds (starts returning the whole schema); a larger weight (the graph term swamps the lexical signal).

## 2026-08-07: Four tools, not fourteen

- **Decision:** `search_schema`, `describe_table`, `explain_query`, `run_query`.
- **Why:** every tool description is paid for on every request, and these four match the loop an agent runs.
- **Alternatives:** one tool per REST-style operation (a per-call tax).

## 2026-08-07: A blocked query is a tool error, not a protocol error

- **Decision:** a rejected query returns a result with `isError: true` and a message the model can act on.
- **Why:** nothing about the protocol failed, and a JSON-RPC error would hide the reason from the model.
- **Alternatives:** raise a protocol error (the model cannot read it and retry).

## 2026-08-07: Choices in the schema and result handling

- **The five shorthand view schemas are excluded from the index.** The upstream loader also creates `pe`, `hr`, `pr`, `pu` and `sa`, convenience views over the same tables. Indexing them would double every table and inflate the baseline the README compares against. Excluding them makes the headline ratio harder to reach, not easier.
- **Foreign-key edges that leave the slice are dropped** from `SchemaSlice.foreign_keys`. The model cannot join to a table whose columns it has not been shown.
- **DDL is rendered, not taken from `pg_dump`.** No storage parameters, tablespaces or ownership: they cost tokens and answer no question a query author has.
- **`Decimal` is returned as a string.** These are money and quantity columns, and a silent rounding is worse than a visible type change.
- **Sample data is downloaded at image build time**, not vendored, so no 40 MB blob lives in git. The loader scripts are pinned by commit SHA and the data to Microsoft's release asset.
- **`server.py` uses absolute imports.** `mcp dev` loads the file by path, and relative imports fail under that loader.
