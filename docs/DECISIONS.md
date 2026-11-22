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

## 2026-11-20: Pre-release security review, and what it changed

- **Decision:** before tagging v0.1.0, a reviewer read the whole code base with no database and no network. Findings that were reproduced against the real code were fixed and given planted faults. The rest were fixed from reading, or recorded here.
- **Found and fixed, reproduced:**
  - A double-quoted function name (`"pg_sleep"(1)`, `pg_catalog."set_config"(...)`) bypassed the denylist, because the name was compared as a node, not as text. All 42 denylisted names were allowed when quoted. The `U&"..."` escape form did too. Names are now compared unquoted and any name that is not a plain identifier is refused.
  - Side-effect functions outside the list ran inside a SELECT: advisory locks, `nextval`, `pg_notify`, large objects, and `crosstab`, which runs an SQL string outside the policy. The list is now exact names plus prefix families, so a function this list missed or a later PostgreSQL release adds is still caught. The cost is that a user function with such a prefix is refused too.
  - The "explicit read-only transaction" claim was false: psycopg sent a plain `BEGIN`, so only the session default applied, and a query could flip that with `set_config(..., false)`. Each connection is now configured so every transaction is `BEGIN READ ONLY`, and a connection is reset with `DISCARD ALL` on return to the pool.
  - Row and size caps applied after the result was in memory. Rows now come from a server-side cursor in batches, with a row cap, a per-cell cap and a total size cap. The cursor also forces the extended protocol, where PostgreSQL itself refuses a second statement.
  - HTTP mode had no Host or Origin checks off loopback and no authentication. A non-loopback bind is now refused unless `PGSS_ALLOW_REMOTE=1`.
  - The release workflow did not check the tag against the version or that it was on `main`, and pinned actions by mutable tag. It now checks both, and every action is pinned by commit SHA.
- **Database role tightened:** `init/20-grants.sql` revokes `EXECUTE` on the same functions from `PUBLIC`, and gives `scout_ro` a connection limit and a temp-file limit. A name the guard missed is then refused by the database, and the tests check that.
- **Smaller fixes:** the DSN is hidden from `repr(Settings())`; database comments are joined onto one line and capped at 300 characters, and the instructions tell the model that comments and rows are untrusted; the demo database port binds to 127.0.0.1; the sdist leaves out `CLAUDE.md`, `.claude/` and `.github/`.
- **Accepted, not fixed:**
  - `PGSS_SCHEMAS` limits what is indexed and shown, not what the role can read. SECURITY.md now says so. A role with narrower grants is the control.
  - A single very large cell is still read into memory before it is cut, because the cut is applied to the fetched row. The statement timeout and the per-batch fetch bound it, and the output is cut either way.
  - A query that materializes a huge set in a `FROM` clause, such as `SELECT * FROM generate_series(1, 1000000000)`, spills to disk until the shipped role's `temp_file_limit` stops it. On a role without that limit it runs until the statement timeout.
  - An unset `PGSS_DSN` still falls back to the local demo database. The README Quickstart sets it explicitly.
  - The demo database image downloads its sample data at build time with no checksum, and the base image is pinned by tag, not digest.
  - Local refs `backup/pre-rewrite` and `refs/original/refs/heads/main` hold commits with attribution trailers from an earlier history rewrite. They are not on any remote. Pushes in this repo name their refs, never `--all` or `--mirror`.
- **Alternatives:** an allowlist of known-safe functions instead of a denylist (stronger, but it refuses a lot of ordinary SQL, and a general query tool needs those functions); dropping the denylist and relying on the role alone (the role is the layer that holds, but a parser layer gives the model a clear error and stops a bad query before the database sees it).

## 2026-11-22: An empty index is an error, not an empty answer

- **Decision:** when the configured schemas hold no tables, `get_index` raises a tool error that names the schemas the role can read and the `PGSS_SCHEMAS` value to set, and does not keep the empty result. A configured schema that holds nothing while others do is logged as a warning.
- **Why:** an own-data run against a scratch PostgreSQL 16 showed that the default schemas are the five AdventureWorks ones, so on any other database `search_schema` returned `"tables": []` with no error while `run_query` worked. Nothing told the user or the model why. Fail closed: an empty list reads as "no relevant tables", which is a claim. The empty result is not cached, so a grant or a restart that fixes the cause is picked up on the next call.
- **The instructions** no longer say the database is AdventureWorks with 68 tables. They were sent to the model whatever the server was connected to.
- **Alternatives:** default `PGSS_SCHEMAS` to every schema the role can read (rejected: it widens what gets indexed and shown on a real database without being asked, and `PGSS_SCHEMAS` is already documented as the thing that chooses it); exit at start-up (rejected: a client shows only "failed to connect", which hides the message, whereas a tool error reaches the model and the user in the conversation); a startup probe that always connects first (rejected: the server connects lazily on purpose).
