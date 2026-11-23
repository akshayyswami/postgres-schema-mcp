# postgres-schema-mcp

[![CI](https://github.com/akshayyswami/postgres-schema-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/akshayyswami/postgres-schema-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP 2026-07-28](https://img.shields.io/badge/MCP-2026--07--28-blue.svg)](https://modelcontextprotocol.io)

**Schema access for an agent is a retrieval problem, not a dump.**

An MCP server that gives an AI agent the relevant slice of a wide Postgres schema instead of all of it, and enforces read-only in three layers rather than annotating it.

<!-- mcp-name: io.github.akshayyswami/postgres-schema-mcp -->

![Terminal demo: one question retrieves a small slice of the schema, and the read-only guard blocks a DELETE hidden inside a CTE.](docs/demo.gif)

---

## The number

Measured on the seeded AdventureWorks database in this repo: <!-- eval:context.tables -->68<!-- /eval --> tables across <!-- eval:context.schemas -->5<!-- /eval --> schemas, over the <!-- eval:context.questions -->26<!-- /eval --> questions in [`evals/questions.yaml`](evals/questions.yaml).

| | Tokens |
|---|---|
| Baseline: the full schema DDL, dumped in one blob | **<!-- eval:context.full_schema_tokens:,d -->10,927<!-- /eval -->** |
| Retrieved slice, mean over the questions (k=<!-- eval:context.k -->8<!-- /eval -->) | **<!-- eval:retrieval.slice_tokens_mean:,.0f -->1,816<!-- /eval -->** |
| | **<!-- eval:context.reduction_x:.1f -->6.0<!-- /eval -->× less schema context (<!-- eval:context.fewer_tokens:.0% -->83%<!-- /eval --> fewer tokens)** |

Median slice <!-- eval:retrieval.slice_tokens_median:,.0f -->1,858<!-- /eval -->, range <!-- eval:retrieval.slice_tokens_min:,d -->1,071<!-- /eval -->–<!-- eval:retrieval.slice_tokens_max:,d -->2,368<!-- /eval -->. Tokenizer: <!-- eval:context.tokenizer -->tiktoken/cl100k_base<!-- /eval -->. Ranking backend: <!-- eval:context.backend -->bm25<!-- /eval -->.

Reproduce: `make evals` prints the numbers as one JSON object. It needs no database: it reads the frozen schema in `evals/fixtures/catalog.json`, which is names, comments and keys only, no rows. CI re-measures on every push and fails if any figure in this README differs from the measurement, and checks the frozen schema against a freshly seeded database.

What the number leaves out: it counts schema context only, the DDL a model is given before it writes SQL. It excludes tool descriptions and the rows a query returns.

The ratio is the boring half of the argument. The interesting half is a schema too wide to dump at all. A dump-everything server truncates, and if the table you need falls outside the cutoff, the model has no way to ask for it. `describe_table`, below, is this server's answer.

---

## Quickstart

```bash
git clone https://github.com/akshayyswami/postgres-schema-mcp && cd postgres-schema-mcp
docker compose up -d --wait
uv sync
```

You need Docker and [uv](https://docs.astral.sh/uv/). The first `docker compose up` builds Postgres, downloads the AdventureWorks OLTP sample, seeds <!-- eval:context.tables -->68<!-- /eval --> tables and creates both database roles. It takes a few minutes; afterwards it is instant.

Then point your MCP client at it:

```json
{
  "mcpServers": {
    "postgres-schema-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/postgres-schema-mcp", "postgres-schema-mcp"],
      "env": {
        "PGSS_DSN": "postgresql://scout_ro:scout_ro_pw@localhost:55432/adventureworks"
      }
    }
  }
}
```

The package is also on PyPI, so a client can run it without a clone. Set `"command": "uvx"` and `"args": ["postgres-schema-mcp"]`, with the same `env`, and point `PGSS_DSN` at your own database through a role that can only `SELECT`, and set `PGSS_SCHEMAS`. [Add it to your client](#add-it-to-your-client) and [Your own database](#your-own-database) have the steps.

stdio by default. `postgres-schema-mcp --http` serves Streamable HTTP on port 3000 instead. The credentials above belong to a local throwaway container holding public sample data. There are no real credentials in this repo.

---

## Add it to your client

The server is a local command that talks over stdio, so every client needs the same three things: the command `uvx postgres-schema-mcp`, the environment variable `PGSS_DSN`, and on your own database `PGSS_SCHEMAS` (see [Your own database](#your-own-database)). Only Claude Code was run by me. The other snippets follow each client's own documentation as I read it on 2026-11-22, and each has a link, so check the page if a client has changed. Any other client that can start a stdio MCP server takes the same command, `args` and `env`.

The DSN below is the demo database from the Quickstart. Swap in your own.

### Claude Code

```bash
claude mcp add postgres-schema-mcp \
  -e PGSS_DSN=postgresql://scout_ro:scout_ro_pw@localhost:55432/adventureworks \
  -- uvx postgres-schema-mcp
```

The server is added for you in the current project only. Add `--scope user` to have it in every project. Do not use `--scope project` with a password in the DSN: it writes `.mcp.json`, which is meant to be committed. `claude mcp list` should show `✔ Connected`. The first start downloads the package. One cold check I ran reported a 30 second timeout and the next one connected, so run it again if the first fails.

### Claude Desktop

Open Settings, Developer, Edit Config. That opens `claude_desktop_config.json`: `~/Library/Application Support/Claude/` on macOS, `%APPDATA%\Claude\` on Windows, `~/.config/Claude/` on Linux. Add the server, then quit and restart Claude Desktop ([docs](https://modelcontextprotocol.io/docs/develop/connect-local-servers)).

```json
{
  "mcpServers": {
    "postgres-schema-mcp": {
      "command": "uvx",
      "args": ["postgres-schema-mcp"],
      "env": {
        "PGSS_DSN": "postgresql://scout_ro:scout_ro_pw@localhost:55432/adventureworks"
      }
    }
  }
}
```

### Codex (CLI, desktop app and IDE extension)

```bash
codex mcp add postgres-schema-mcp \
  --env PGSS_DSN=postgresql://scout_ro:scout_ro_pw@localhost:55432/adventureworks \
  -- uvx postgres-schema-mcp
```

Or put it in `~/.codex/config.toml`, or in `.codex/config.toml` for a trusted project ([docs](https://developers.openai.com/codex/mcp)). Codex waits 10 seconds for a server to start by default, and a first `uvx` run can take longer, so the example raises it:

```toml
[mcp_servers.postgres-schema-mcp]
command = "uvx"
args = ["postgres-schema-mcp"]
startup_timeout_sec = 60

[mcp_servers.postgres-schema-mcp.env]
PGSS_DSN = "postgresql://scout_ro:scout_ro_pw@localhost:55432/adventureworks"
```

Check it with `codex mcp list`, or `/mcp` inside Codex.

### Cursor

Put this in `~/.cursor/mcp.json` for every project, or `.cursor/mcp.json` for one ([docs](https://cursor.com/docs/context/mcp)):

```json
{
  "mcpServers": {
    "postgres-schema-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["postgres-schema-mcp"],
      "env": {
        "PGSS_DSN": "postgresql://scout_ro:scout_ro_pw@localhost:55432/adventureworks"
      }
    }
  }
}
```

Cursor's docs let you write `"${env:PGSS_DSN}"` in `env` so the password stays out of the file. Server output is in the Output panel under "MCP Logs".

### VS Code (GitHub Copilot)

Put this in `.vscode/mcp.json` in the workspace, or run "MCP: Add Server" from the Command Palette ([docs](https://code.visualstudio.com/docs/copilot/customization/mcp-servers)). The top-level key here is `servers`, not `mcpServers`:

```json
{
  "servers": {
    "postgres-schema-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": ["postgres-schema-mcp"],
      "env": {
        "PGSS_DSN": "postgresql://scout_ro:scout_ro_pw@localhost:55432/adventureworks"
      }
    }
  }
}
```

"MCP: List Servers" shows whether it connected. VS Code's docs also describe a newer Copilot location, `.mcp.json` at the workspace root with the `mcpServers` key, so try that if `.vscode/mcp.json` is not picked up.

---

## Use it

### Your own database

Give the server a role that can read and nothing else. It is the layer that holds, so do not skip it. Run this as a superuser, with your own names. Creating a role needs `CREATEROLE` or superuser, and only a superuser (or, from PostgreSQL 15, a role granted `SET` on the setting) can set `temp_file_limit`, so the database owner is not enough. Run it with `psql -v ON_ERROR_STOP=1` so one failed line stops the rest instead of leaving a half-made role:

```sql
CREATE ROLE scout_ro LOGIN PASSWORD 'change-me' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT CONNECT ON DATABASE shop TO scout_ro;
GRANT USAGE ON SCHEMA public, billing TO scout_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public, billing TO scout_ro;
-- Tables that app_owner creates later are readable too:
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public, billing GRANT SELECT ON TABLES TO scout_ro;
-- What one connection can cost:
ALTER ROLE scout_ro CONNECTION LIMIT 8;
ALTER ROLE scout_ro SET temp_file_limit = '256MB';
ALTER ROLE scout_ro SET work_mem = '16MB';
```

`GRANT SELECT ON ALL TABLES` lets the model read every row of every table in those schemas, and the rows go to the model provider (see Privacy). If a schema also holds password hashes, tokens or other secrets, grant `SELECT` on the tables you want to expose instead, one by one.

What this does not block: on a default Postgres any role can create temp tables and call `pg_sleep`, and before PostgreSQL 15 any role can create tables in the `public` schema. The AST guard refuses the first two. `init/20-grants.sql` shows how the demo database refuses them at the database too, but those revokes apply to `PUBLIC`, so they change what every role on that database can do. Use them only on a database that is dedicated to this.

Set `PGSS_SCHEMAS` to the schemas to index, comma separated, for example `PGSS_SCHEMAS=public,billing`. The default is the five AdventureWorks schemas, so on any other database the server finds no tables until you set it. It chooses what is indexed, not what the role can read.

The server reads the schema once at start-up, so restart it after a migration.

### Each time

There is nothing to type. Ask the question in plain words and Claude uses the tools in the same loop every time: `search_schema` for the relevant tables, `describe_table` for one it needs by name, `explain_query` for anything large, then `run_query`. On the demo database, try:

- Which sales orders had the highest total due in 2013?
- What products were on each sales order, with quantities and unit prices?
- Which employees have had their pay rate changed, and when?

Your client may ask you to approve the tools the first time. Ask it to change data and `run_query` refuses with a message saying the server is read-only.

---

## Configuration

Every setting is an environment variable. The audit and a test in this repo fail if one the code reads is missing from this table.

| Variable | Default | What it changes |
|---|---|---|
| `PGSS_DSN` | the demo database, `postgresql://scout_ro:scout_ro_pw@localhost:55432/adventureworks` | The database to connect to. Use a role that can only `SELECT` |
| `PGSS_SCHEMAS` | `person,humanresources,production,purchasing,sales` | The schemas to index. It chooses what is indexed, not what the role can read |
| `PGSS_STATEMENT_TIMEOUT_MS` | `10000` | Cancels a query that runs longer than this |
| `PGSS_IDLE_TX_TIMEOUT_MS` | `10000` | Ends a session left idle inside a transaction |
| `PGSS_MAX_ROWS_CAP` | `1000` | The most rows `run_query` returns, whatever `max_rows` asks for |
| `PGSS_MAX_CELL_CHARS` | `2000` | A longer text value is cut, with a note of how much was dropped |
| `PGSS_MAX_RESULT_BYTES` | `200000` | Cap on the JSON size of all rows from one query |
| `PGSS_POOL_MIN` | `1` | Connections kept open |
| `PGSS_POOL_MAX` | `4` | The most connections open at once |
| `PGSS_TRANSPORT` | `stdio` | `stdio`, or `streamable-http` (the same as the `--http` flag) |
| `PGSS_HOST` | `127.0.0.1` | The address the HTTP transport binds. A non-loopback address is refused unless `PGSS_ALLOW_REMOTE=1` |
| `PGSS_PORT_HTTP` | `3000` | The port for the HTTP transport |
| `PGSS_ALLOW_REMOTE` | unset | Set to `1` to allow a non-loopback bind, only behind a proxy that does the authentication |
| `PGSS_RETRIEVAL_BACKEND` | `bm25` | `bm25`, or `titan` (needs the `bedrock` extra) |
| `PGSS_TITAN_MODEL_ID` | `amazon.titan-embed-text-v2:0` | The embedding model when the backend is `titan` |
| `AWS_REGION` | `us-east-1` | The Bedrock region when the backend is `titan` |

---

## Troubleshooting

| What you see | Why | Fix |
|---|---|---|
| `search_schema` fails with "No tables were found in the indexed schemas" (version 0.1.0 returns `"tables": []` instead) | The default schemas are the AdventureWorks ones, and your database holds none of them | Set `PGSS_SCHEMAS` to the schemas that hold your tables. The message lists the ones the role can read |
| The client says the server failed to connect, right after you added it | The first `uvx` run downloads the package and can take longer than the client waits | Run the check again. In Codex raise `startup_timeout_sec` |
| `permission denied for table ...` | The role has no `SELECT` on that table, or no `USAGE` on its schema | Grant both, as in [Your own database](#your-own-database) |
| `run_query` returns "Blocked: ..." | The guard refused a statement that is not a single read-only `SELECT` | Rewrite it as one `SELECT`. See the safety model below |
| Tables you added are missing from the results | The schema is read once at start-up | Restart the server |

---

## Privacy

The server connects to the database you give it, and it sends no telemetry. Table and column names, comments and the rows a query returns go to the model through your client, so they leave your machine for whatever model that client uses.

Two downloads happen on first use, and neither carries anything from your database. `uvx` fetches the package from PyPI. The first time the server counts tokens, the `tiktoken` library downloads its `cl100k_base` vocabulary file from `openaipublic.blob.core.windows.net` and keeps it in the system temp folder (`TIKTOKEN_CACHE_DIR` changes where). If that download fails, token counts fall back to a rough estimate, and the tokenizer is named as approximate in the result.

With `PGSS_RETRIEVAL_BACKEND=titan`, the text describing each table is also sent to Amazon Bedrock. The default, `bm25`, sends nothing.

---

## How it works

```mermaid
flowchart LR
    Q[question] --> S[search_schema]
    S -->|"BM25 over one document per table"| R[ranked tables]
    R -->|"plus a share of the best foreign-key neighbour"| T["slice: top tables, DDL, joins"]
    T --> M[model writes SQL]
    M --> G["run_query: AST guard"]
    G --> D[("scout_ro role: SELECT only")]
```

At start-up the server reads the schema once and builds one document per table: its name, comments, column names and types, and the tables it joins to. `search_schema` ranks those documents against the question with BM25, which needs no credentials and no per-query cost.

Ranking alone returns slices you cannot join, so it adds one damped round of score propagation across foreign keys: a table next to a strong match is likely to be needed, because you cannot join to what you have not been shown. The share is <!-- eval:fk_sweep.2.neighbour_weight:.2f -->0.35<!-- /eval -->. Retrieval can still rank a needed table below the cutoff, so `describe_table` fetches any table by name without using the ranking.

### The four tools

Four, not fourteen. Every tool description is paid for on every request, so mirroring a REST surface into tools taxes each call. These four match the loop an agent actually runs.

| Tool | Signature | Annotations |
|---|---|---|
| `search_schema` | `(question: str, limit: int = 8) -> SchemaSlice` | `readOnlyHint=true`, `idempotentHint=true`, `openWorldHint=false`, `destructiveHint=false` |
| `describe_table` | `(name: str) -> TableDetail` | same |
| `explain_query` | `(sql: str) -> QueryPlan` | same |
| `run_query` | `(sql: str, max_rows: int = 200) -> QueryResult` | same |

All four return Pydantic models, so `outputSchema` and `structuredContent` are generated, not hand-written.

The spec's annotation defaults are pessimistic: omitting them declares a tool destructive, open-world and non-idempotent. So all four set them explicitly, in a fixed order, because the spec cites prompt-cache hit rate as the reason to keep the tool list stable. `tools/list` carries `ttlMs: 300000` and `cacheScope: "public"`. The list comes from this server's own code, so it is the same for every caller, but a redeploy can change it, so it is not cached longer.

### Safety model

Three layers, weakest first.

| Layer | What it does | Why it is not enough alone |
|---|---|---|
| `readOnlyHint=true` | Tells the client the tool is safe to auto-approve | **An untrusted hint.** Decorative |
| AST policy (`core/guard.py`) | Parses with sqlglot and rejects anything that is not a single read-only SELECT | A parser bug is a bypass |
| `scout_ro` database role | Holds `SELECT` and nothing else, anywhere | This is the layer that actually holds |

**The annotation is the weakest layer, on purpose.** The specification says clients must treat tool annotations from an untrusted server as untrusted. A malicious server can set `readOnlyHint=true` and delete your data. It is documentation, not a control. Anyone who says their database MCP server is read-only because of the annotation has misread the spec.

#### The query a regex guard misses

```sql
WITH gone AS (DELETE FROM customer RETURNING *) SELECT * FROM gone;
```

It begins with `WITH`. It contains `SELECT`. It empties the table. A regex allowlist that checks the statement's prefix passes it straight through. sqlglot parses it to a top-level `Select` whose tree contains a `Delete` node, so walking the AST rejects it.

The same argument runs the other way. A text denylist wrongly rejects

```sql
SELECT 'DROP TABLE customer' AS note FROM person.person;
```

which is an ordinary read-only query. Both directions are in the fault matrix.

The guard fails closed: unparseable input, empty input, statement stacking, and anything sqlglot can only represent as an opaque `Command` are all refused. It also blocks `SELECT ... FOR UPDATE` (row locks are not read-only), `SELECT ... INTO` (DDL wearing a SELECT costume), and a denylist of filesystem, network, administrative and denial-of-service functions, checked against every function node in the tree rather than the raw string.

#### Blocked is a tool error, not a protocol error

A rejected query returns HTTP 200 with a JSON-RPC result carrying `isError: true` and a message the model can act on. Only real protocol faults become JSON-RPC errors. Verified on the wire:

```json
{"jsonrpc":"2.0","id":1,"result":{
  "content":[{"type":"text","text":"Error executing tool run_query: Blocked: the statement contains a DELETE nested inside the query (in a CTE or subquery), which a prefix check would miss. This server is read-only. Rewrite the request as a plain SELECT."}],
  "isError":true,"resultType":"complete"}}
```

#### Below the parser

The server authenticates as `scout_ro`, which is granted `SELECT` and nothing else, with `TEMPORARY` revoked on the database and no `CREATE` on any schema. Every transaction starts as `BEGIN READ ONLY`, so a query that flips `default_transaction_read_only` for the session changes nothing. A connection is reset with `DISCARD ALL` when it goes back to the pool, so a lock or setting one request leaves behind is gone before the next. Sessions run with `statement_timeout` and `idle_in_transaction_session_timeout`. Rows are read from a server-side cursor in batches, with a row cap, a per-cell cap and a total size cap, and each cut is reported to the model.

The seeded database goes one step further. `init/20-grants.sql` revokes `EXECUTE` from `PUBLIC` on the functions the guard refuses (`pg_sleep`, `set_config`, advisory locks, `nextval`, large objects, `crosstab`), and gives `scout_ro` a connection limit and a temp-file limit. A name the guard missed is then refused by the database. Point `PGSS_DSN` at your own database and you need to give that role the same limits yourself.

[`tests/integration/test_readonly_role.py`](tests/integration/test_readonly_role.py) sends `DELETE`, `UPDATE`, `INSERT`, `TRUNCATE`, `DROP`, `ALTER`, `CREATE TABLE`, `CREATE TEMP TABLE`, `CREATE INDEX` and `GRANT` straight to Postgres, bypassing the guard, and asserts the database refuses each one. That test is the proof the bottom layer holds without the parser.

---

## Evals

Everything below comes from `make evals` (offline) and `make evals-db` (against the seeded database), and both run in CI.

**Fault matrix: <!-- eval:faults.must_block_caught -->21<!-- /eval -->/<!-- eval:faults.must_block_total -->21<!-- /eval --> blocked, <!-- eval:faults.must_allow_passed -->12<!-- /eval -->/<!-- eval:faults.must_allow_total -->12<!-- /eval --> allowed.**

Two-sided on purpose. The `must_allow` half is the half people skip, and it proves the guard is a guard and not a policy that refuses everything. `make evals-db` also executes all <!-- eval:faults.must_allow_total -->12<!-- /eval --> allowed queries against the database, so "allowed" means it actually runs.

**Retrieval: recall@<!-- eval:context.k -->8<!-- /eval --> <!-- eval:retrieval.recall_at_k:.3f -->0.955<!-- /eval -->, precision@<!-- eval:context.k -->8<!-- /eval --> <!-- eval:retrieval.precision_at_k:.3f -->0.260<!-- /eval -->, <!-- eval:retrieval.complete -->24<!-- /eval -->/<!-- eval:retrieval.questions -->26<!-- /eval --> questions with complete recall.**

Precision is structurally capped: a question needs 1 to 4 tables but <!-- eval:context.k -->8<!-- /eval --> slots come back. Recall is the number that matters, because a missing table breaks the query and a spare one costs a few tokens.

### Foreign-key propagation, and what it bought

The first run failed. Purely lexical ranking scored recall@<!-- eval:context.k -->8<!-- /eval --> <!-- eval:fk_sweep.0.recall_at_k:.3f -->0.840<!-- /eval -->, and <!-- eval:fk_sweep.0.incomplete -->9<!-- /eval --> questions were missing a required table. The misses had a shape: `production.product` was missed <!-- eval:fk_sweep.0.missed.0.count -->4<!-- /eval --> times and `person.person` <!-- eval:fk_sweep.0.missed.1.count -->2<!-- /eval --> times. These are join hubs. A slice containing `salesorderdetail` without `product` names a `productid` the model cannot resolve, so it is not a usable slice.

Ranking now adds one damped round of score propagation across foreign keys. Measured over the <!-- eval:context.questions -->26<!-- /eval --> questions:

| neighbour weight | recall@8 | complete recall | mean slice tokens |
|---|---|---|---|
| <!-- eval:fk_sweep.0.neighbour_weight:.2f -->0.00<!-- /eval --> (off) | <!-- eval:fk_sweep.0.recall_at_k:.3f -->0.840<!-- /eval --> | <!-- eval:fk_sweep.0.complete -->17<!-- /eval -->/<!-- eval:context.questions -->26<!-- /eval --> | <!-- eval:fk_sweep.0.slice_tokens_mean:,.0f -->1,684<!-- /eval --> |
| <!-- eval:fk_sweep.1.neighbour_weight:.2f -->0.25<!-- /eval --> | <!-- eval:fk_sweep.1.recall_at_k:.3f -->0.955<!-- /eval --> | <!-- eval:fk_sweep.1.complete -->24<!-- /eval -->/<!-- eval:context.questions -->26<!-- /eval --> | <!-- eval:fk_sweep.1.slice_tokens_mean:,.0f -->1,800<!-- /eval --> |
| **<!-- eval:fk_sweep.2.neighbour_weight:.2f -->0.35<!-- /eval --> (shipped)** | **<!-- eval:fk_sweep.2.recall_at_k:.3f -->0.955<!-- /eval -->** | **<!-- eval:fk_sweep.2.complete -->24<!-- /eval -->/<!-- eval:context.questions -->26<!-- /eval -->** | **<!-- eval:fk_sweep.2.slice_tokens_mean:,.0f -->1,816<!-- /eval -->** |
| <!-- eval:fk_sweep.3.neighbour_weight:.2f -->0.50<!-- /eval --> | <!-- eval:fk_sweep.3.recall_at_k:.3f -->0.965<!-- /eval --> | <!-- eval:fk_sweep.3.complete -->24<!-- /eval -->/<!-- eval:context.questions -->26<!-- /eval --> | <!-- eval:fk_sweep.3.slice_tokens_mean:,.0f -->1,864<!-- /eval --> |

0.35 takes nearly all the gain. Past 0.5 the graph term starts to swamp the lexical signal, which is how you end up returning the whole schema again.

**Two honest caveats.** The weight was chosen on the same <!-- eval:context.questions -->26<!-- /eval --> questions that recall is measured on, so <!-- eval:fk_sweep.2.recall_at_k:.3f -->0.955<!-- /eval --> is optimistic: it is not a held-out score. And better recall costs tokens. The mean slice grew from <!-- eval:fk_sweep.0.slice_tokens_mean:,.0f -->1,684<!-- /eval --> to <!-- eval:fk_sweep.2.slice_tokens_mean:,.0f -->1,816<!-- /eval -->, which moved the headline ratio from <!-- eval:fk_sweep.0.reduction_x:.1f -->6.5<!-- /eval -->× to <!-- eval:fk_sweep.2.reduction_x:.1f -->6.0<!-- /eval -->×. Recall was judged worth more than the ratio.

### BM25 vs Titan embeddings: the lexical backend wins

Both backends were measured on the same <!-- eval:context.questions -->26<!-- /eval --> questions. The Titan figures are **recorded, not re-measured here**: they come from a run on <!-- eval:baseline.titan.recorded -->2026-08-07<!-- /eval --> against the real Bedrock service (<!-- eval:baseline.titan.model_id -->amazon.titan-embed-text-v2:0<!-- /eval -->, <!-- eval:baseline.titan.invocations -->94<!-- /eval --> invocations), and are kept with that date in [`evals/baseline/titan-bedrock.json`](evals/baseline/titan-bedrock.json). CI has no AWS credentials, so it does not re-run them. The BM25 rows are measured on every push.

| Backend | FK propagation | recall@8 | precision@8 | complete recall |
|---|---|---|---|---|
| **BM25 (default)** | **0.35** | **<!-- eval:fk_sweep.2.recall_at_k:.3f -->0.955<!-- /eval -->** | **<!-- eval:fk_sweep.2.precision_at_k:.3f -->0.260<!-- /eval -->** | **<!-- eval:fk_sweep.2.complete -->24<!-- /eval -->/<!-- eval:context.questions -->26<!-- /eval -->** |
| Titan V2 | 0.35 | <!-- eval:baseline.titan.with_fk.recall:.3f -->0.926<!-- /eval --> | <!-- eval:baseline.titan.with_fk.precision:.3f -->0.255<!-- /eval --> | <!-- eval:baseline.titan.with_fk.complete -->22<!-- /eval -->/<!-- eval:context.questions -->26<!-- /eval --> |
| BM25 | off | <!-- eval:fk_sweep.0.recall_at_k:.3f -->0.840<!-- /eval --> | <!-- eval:fk_sweep.0.precision_at_k:.3f -->0.226<!-- /eval --> | <!-- eval:fk_sweep.0.complete -->17<!-- /eval -->/<!-- eval:context.questions -->26<!-- /eval --> |
| Titan V2 | off | <!-- eval:baseline.titan.without_fk.recall:.3f -->0.833<!-- /eval --> | <!-- eval:baseline.titan.without_fk.precision:.3f -->0.226<!-- /eval --> | <!-- eval:baseline.titan.without_fk.complete -->17<!-- /eval -->/<!-- eval:context.questions -->26<!-- /eval --> |

**The embeddings lose.** Before propagation the two are indistinguishable, so the embeddings were not finding anything the lexical path missed. After propagation BM25 pulls ahead. That is a reasonable outcome, not a surprising one: a table's document here is mostly identifiers and a one-line comment, not prose, which is the regime BM25 is built for. Foreign-key structure mattered far more than semantic similarity.

Two caveats cut against the winner. Titan was handed the same identifier-heavy text as BM25, and a prose rendering of each table might suit it better. And the lexicon and stemmer were written with BM25 in mind, so the comparison is not perfectly even.

BM25 stays the default, which also means the repo runs offline with no credentials and no per-query cost. To try Titan, install the `bedrock` extra and set `PGSS_RETRIEVAL_BACKEND=titan`.

---

## Limits

- **Retrieval has a recall ceiling, and this is the design's real failure mode.** At k=<!-- eval:context.k -->8<!-- /eval --> it misses a required table on <!-- eval:retrieval.incomplete -->2<!-- /eval --> of <!-- eval:retrieval.questions -->26<!-- /eval --> questions: `revenue-by-category` (a four-table join through the category hierarchy) and `reseller-locations` (the store-to-address path runs through a cross-reference table that matches nothing in the question's wording). When it misses, the table is absent and the slice alone cannot recover.
- **`describe_table` is the escape hatch for exactly that.** It does not use the ranking, so a model that knows or guesses a table name can always reach it. Every `SchemaSlice` carries a note telling the model to do this.
- **The lexical backend cannot do irregular morphology.** Stemming reduces `employees` to `employee`, but `paid` does not reduce to `pay`. A test asserts the limit instead of hiding it. Embeddings were the obvious fix, they were measured, and they did not help overall.
- **The identifier lexicon is specific to this schema.** AdventureWorks names are lowercase concatenations (`salesorderheader`), and splitting them needs a word list. The one in `core/retrieval.py` covers this schema's vocabulary. Another database would want its own.
- **The Titan backend is measured but not the default**, because it measured worse. It has only ever been exercised over 68 documents and 26 questions, with no retry, batching or rate-limit handling.
- **Not implemented, on purpose:** no prompts, no resources, no write path of any kind, no `EXPLAIN ANALYZE` (it would execute the query), no cross-database or multi-tenant support, and no auth. The server is expected to run locally over stdio, and the security boundary is the database role, not the transport.
- **The headline number is for one schema.** It is measured on AdventureWorks, a sample with <!-- eval:context.tables -->68<!-- /eval --> tables. A different schema will give different figures, and the lexicon above is part of why.

### Conformance: does not pass, and here is why

`npx @modelcontextprotocol/conformance@`<!-- eval:baseline.conformance.version -->0.1.16<!-- /eval --> `server --url http://localhost:3000/mcp` reported **<!-- eval:baseline.conformance.passed -->11<!-- /eval --> passed, <!-- eval:baseline.conformance.failed -->21<!-- /eval --> failed** on <!-- eval:baseline.conformance.recorded -->2026-08-07<!-- /eval -->. That is not a green check, and it is not a defect in this server:

1. **The tool has no knowledge of protocol revision 2026-07-28.** `--spec-version` accepts only `2025-03-26`, `2025-06-18`, `2025-11-25`, `draft` and `extension`. So it exercised the legacy handshake path the SDK still supports for older clients, which is why `server-initialize` and `ping` appear in the passing column although both were removed in the revision this server targets.
2. **Its scenarios assert against a reference fixture server.** The failures are things like `Unknown resource: test://static-text`, `Unknown prompt: test_simple_prompt` and `No image content found`. A server with four tools and no prompts or resources cannot satisfy them. `logging-set-level` fails with `Method not found`, which is correct behaviour for this revision.

Every scenario that tests protocol behaviour rather than fixture presence passes: `tools-list`, `tools-call-simple-text`, `tools-call-error`, `resources-list`, `prompts-list`, `ping`, `server-initialize`, `server-sse-multiple-streams` and `dns-rebinding-protection`.

This result is recorded in [`evals/baseline/conformance.json`](evals/baseline/conformance.json), not re-run in CI. Wiring it in would mean checking in a baseline of <!-- eval:baseline.conformance.failed -->21<!-- /eval --> expected failures, and a baseline that large hides regressions instead of catching them.

### Spec version

Built against MCP protocol revision **2026-07-28** on the official Python SDK `mcp==2.0.0`, pinned exactly because the v1 line continues in parallel and the two APIs are not compatible. That revision made the protocol stateless, so there is no session handling anywhere in this codebase. Details, and what changed in the SDK, are in [`docs/architecture.md`](docs/architecture.md).

---

## Development

```bash
make setup      # uv sync, install the git hooks
make db         # start the seeded Postgres (first run takes a few minutes)
make check      # exactly what CI runs: hooks, mypy, tests with coverage, evals
make test-fast  # no database: unit tests and the offline evals
make evals      # re-measure and rewrite evals/results/latest.json
make fixtures   # re-freeze the schema from the running database
```

Inspect the server with `mcp dev src/postgres_schema_mcp/server.py`. Design decisions, with dates and the alternatives considered, are in [`docs/DECISIONS.md`](docs/DECISIONS.md). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. The AdventureWorks sample data is Microsoft's, downloaded at build time and not redistributed here. The frozen schema in `evals/fixtures/` holds table and column metadata, not rows.

Not affiliated with the PostgreSQL project, Microsoft or the Model Context Protocol maintainers.
