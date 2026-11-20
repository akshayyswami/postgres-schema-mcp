# postgres-schema-mcp

[![CI](https://github.com/akshayyswami/postgres-schema-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/akshayyswami/postgres-schema-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP 2026-07-28](https://img.shields.io/badge/MCP-2026--07--28-blue.svg)](https://modelcontextprotocol.io)

**Schema access for an agent is a retrieval problem, not a dump.**

An MCP server that gives an AI agent the relevant slice of a wide Postgres schema instead of all of it, and enforces read-only in three layers rather than annotating it.

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

stdio by default. `postgres-schema-mcp --http` serves Streamable HTTP on port 3000 instead. The credentials above belong to a local throwaway container holding public sample data. There are no real credentials in this repo.

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

The server authenticates as `scout_ro`, which is granted `SELECT` and nothing else, with `TEMPORARY` revoked on the database and no `CREATE` on any schema. Sessions run with `default_transaction_read_only=on`, `statement_timeout` and `idle_in_transaction_session_timeout`, and every query runs in an explicit read-only transaction with a row cap.

[`tests/integration/test_readonly_role.py`](tests/integration/test_readonly_role.py) sends `DELETE`, `UPDATE`, `INSERT`, `TRUNCATE`, `DROP`, `ALTER`, `CREATE TABLE`, `CREATE TEMP TABLE`, `CREATE INDEX` and `GRANT` straight to Postgres, bypassing the guard, and asserts the database refuses each one. That test is the proof the bottom layer holds without the parser.

---

## Evals

Everything below comes from `make evals` (offline) and `make evals-db` (against the seeded database), and both run in CI.

**Fault matrix: <!-- eval:faults.must_block_caught -->12<!-- /eval -->/<!-- eval:faults.must_block_total -->12<!-- /eval --> blocked, <!-- eval:faults.must_allow_passed -->9<!-- /eval -->/<!-- eval:faults.must_allow_total -->9<!-- /eval --> allowed.**

Two-sided on purpose. The `must_allow` half is the half people skip, and it proves the guard is a guard and not a policy that refuses everything. `make evals-db` also executes all <!-- eval:faults.must_allow_total -->9<!-- /eval --> allowed queries against the database, so "allowed" means it actually runs.

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
