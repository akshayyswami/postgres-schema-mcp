# pg-schema-scout

[![CI](https://github.com/AkshaySwami14/pg-schema-scout/actions/workflows/ci.yml/badge.svg)](https://github.com/AkshaySwami14/pg-schema-scout/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP 2026-07-28](https://img.shields.io/badge/MCP-2026--07--28-blue.svg)](https://modelcontextprotocol.io)

**Schema access for an agent is a retrieval problem, not a dump.**

An MCP server that hands a model only the tables relevant to its question, and enforces
read-only in three layers rather than annotating it.

---

## The number

Measured against the seeded AdventureWorks database in this repo — 68 tables across five
schemas — over the 26 questions in [`evals/questions.yaml`](evals/questions.yaml):

| | Tokens |
|---|---|
| Full schema DDL, dumped in one blob | **10,927** |
| Retrieved slice, mean over 26 questions (k=8) | **1,816** |
| | **6.0× less schema context (83% fewer tokens)** |

Median slice 1,858; range 1,071–2,368. Tokenizer: `tiktoken/cl100k_base`. Backend: BM25.

Reproduce with `python evals/measure_context.py`. Every figure in this README comes from a
real run against the seeded database; none is estimated. CI re-measures all of them on every
push from a clean machine, and reproduces them exactly — so they are properties of the
schema, not of the laptop they were first taken on.

That ratio is the boring half of the argument. The interesting half is what happens on a
schema too wide to dump at all: a dump-everything server does not degrade gracefully, it
truncates, and if the relevant table falls outside the cutoff it is gone and the model has
no way to ask for it. This server's answer to that is `describe_table`, below.

---

## Quickstart

```bash
docker compose up -d --wait
```

That builds Postgres, downloads the AdventureWorks OLTP sample, seeds 68 tables across five
schemas, and creates both roles. First run takes a few minutes; afterwards it is instant.

```bash
pip install -e .
```

Then point your MCP client at it:

```json
{
  "mcpServers": {
    "pg-schema-scout": {
      "command": "pg-schema-scout",
      "env": {
        "PGSS_DSN": "postgresql://scout_ro:scout_ro_pw@localhost:55432/adventureworks"
      }
    }
  }
}
```

stdio by default. `pg-schema-scout --http` serves Streamable HTTP on port 3000 instead.

The credentials above are for a local throwaway container holding public sample data. There
are no real credentials in this repo.

---

## The four tools

Four, not fourteen. Every tool description is paid for on every request, so mirroring a REST
surface into tools is a tax on each call. These four match the loop an agent actually runs.

| Tool | Signature | Annotations |
|---|---|---|
| `search_schema` | `(question: str, limit: int = 8) -> SchemaSlice` | `readOnlyHint=true`, `idempotentHint=true`, `openWorldHint=false`, `destructiveHint=false` |
| `describe_table` | `(name: str) -> TableDetail` | same |
| `explain_query` | `(sql: str) -> QueryPlan` | same |
| `run_query` | `(sql: str, max_rows: int = 200) -> QueryResult` | same |

All four return Pydantic models, so `outputSchema` and `structuredContent` are generated
rather than hand-written.

Annotation defaults in the spec are pessimistic — omitting them declares a tool destructive,
open-world and non-idempotent — so all four are set explicitly. They are returned in a fixed
order, because the SDK preserves registration order and the spec cites prompt-cache hit rate
as the reason to keep it stable. `tools/list` carries `ttlMs: 300000` and
`cacheScope: "public"`: the tool list is derived from this server's own code, so it is
identical for every caller and stable for the life of the process, but a redeploy can change
it, so it is not cached longer.

---

## Safety model

Three layers, weakest first.

| Layer | What it does | Why it is not enough alone |
|---|---|---|
| `readOnlyHint=true` | Tells the client the tool is safe to auto-approve | **An untrusted hint.** Decorative |
| AST policy (`guard.py`) | Parses with sqlglot and rejects anything that is not a single read-only SELECT | A parser bug is a bypass |
| `scout_ro` database role | Holds `SELECT` and nothing else, anywhere | This is the layer that actually holds |

**The annotation is the weakest layer, and deliberately so.** The specification states that
clients must treat tool annotations from an untrusted server as untrusted — the SDK's own
`ToolAnnotations` docstring says clients should never make tool-use decisions based on
annotations received from untrusted servers. A malicious server can set `readOnlyHint=true`
and delete your data. It is documentation, not a control. Anyone who claims their database
MCP server is read-only *because* of the annotation has misread the spec.

### The query a regex guard misses

```sql
WITH gone AS (DELETE FROM customer RETURNING *) SELECT * FROM gone;
```

It begins with `WITH`. It contains `SELECT`. It empties the table. A regex allowlist that
checks the statement's prefix passes it straight through.

sqlglot parses it to a top-level `Select` whose tree contains a `Delete` node, so walking the
AST rejects it. The same argument runs the other way — a text denylist wrongly rejects

```sql
SELECT 'DROP TABLE customer' AS note FROM person.person;
```

which is an ordinary read-only query. Both directions are in the fault matrix.

The guard fails closed: unparseable input, empty input, statement stacking, and anything
sqlglot can only represent as an opaque `Command` are all refused. It also blocks `SELECT …
FOR UPDATE` (row locks are not read-only), `SELECT … INTO` (DDL wearing a SELECT costume),
and a denylist of filesystem, network, administrative and denial-of-service functions checked
against every function node in the tree rather than against the raw string.

### Blocked is a tool error, not a protocol error

A rejected query returns HTTP 200 with a JSON-RPC **result** carrying `isError: true` and a
message the model can act on. Only genuine protocol faults become JSON-RPC errors. Verified
on the wire:

```json
{"jsonrpc":"2.0","id":1,"result":{
  "content":[{"type":"text","text":"Error executing tool run_query: Blocked: the statement contains a DELETE nested inside the query (in a CTE or subquery), which a prefix check would miss. This server is read-only. Rewrite the request as a plain SELECT."}],
  "isError":true,"resultType":"complete"}}
```

### Below the parser

The server authenticates as `scout_ro`, which is granted `SELECT` and nothing else, with
`TEMPORARY` revoked on the database and no `CREATE` on any schema. Sessions run with
`default_transaction_read_only=on`, `statement_timeout` and
`idle_in_transaction_session_timeout`, and every query runs in an explicit read-only
transaction with a row cap.

[`tests/test_readonly_role.py`](tests/test_readonly_role.py) sends `DELETE`, `UPDATE`,
`INSERT`, `TRUNCATE`, `DROP`, `ALTER`, `CREATE TABLE`, `CREATE TEMP TABLE`, `CREATE INDEX`
and `GRANT` **straight to Postgres, bypassing the guard entirely**, and asserts the database
refuses each one. That test is the proof the bottom layer holds independently of the parser.

---

## Evals

Both figures below are from `python evals/run_evals.py` against the seeded database. It
exits non-zero on any regression and runs in CI.

**Fault matrix — 12/12 blocked, 9/9 allowed, 9/9 executed.**

Two-sided on purpose. The `must_allow` half is the half people skip, and it is the half that
proves the guard is a guard rather than a policy that refuses everything. Every `must_allow`
query is *executed* against the database, not merely parsed, so "allowed" means it actually
runs.

**Retrieval — recall@8 0.955, precision@8 0.260, 24/26 questions with complete recall.**

`precision@8` is structurally capped: a question needs 1–4 tables but 8 slots come back, so
the ceiling is around 0.5. Recall is the number that matters here — a missing table breaks
the query, a spare one costs a few tokens.

### Foreign-key propagation, and what it bought

The first eval run failed. Purely lexical ranking scored recall@8 0.840, with nine questions
missing a required table, and the misses had a shape: `production.product` missed four times,
`person.person` twice. These are join hubs. A slice containing `salesorderdetail` without
`product` names a `productid` the model cannot resolve, so it is not a usable slice.

So ranking adds one damped round of score propagation across foreign keys: a table adjacent
to a strong match is likely to be needed, precisely because you cannot join to what you have
not been shown. Measured over the 26 questions:

| neighbour weight | recall@8 | complete recall |
|---|---|---|
| 0.00 (off) | 0.840 | 17/26 |
| 0.25 | 0.955 | 24/26 |
| **0.35 (shipped)** | **0.955** | **24/26** |
| 0.50 | 0.965 | 24/26 |

0.35 takes nearly all the gain; past 0.5 the curve flattens because the graph term starts
swamping the lexical signal, which is how you end up returning the whole schema again.

**Two honest caveats.** The weight was chosen on the same 26 questions the recall is measured
on, so 0.955 is optimistic — it is not a held-out score. And better recall costs tokens: the
mean slice grew from 1,684 to 1,816, moving the headline ratio from 6.5× to 6.0×. Recall was
judged worth more than the ratio.

### BM25 vs Titan embeddings: the lexical backend wins

Both backends were measured against the same 26 questions. Titan Text Embeddings V2 ran
against the real Bedrock service — 94 invocations, 68 table documents plus 26 queries.

| Backend | FK propagation | recall@8 | precision@8 | complete recall |
|---|---|---|---|---|
| **BM25 (default)** | **0.35** | **0.955** | **0.260** | **24/26** |
| Titan V2 | 0.35 | 0.926 | 0.255 | 22/26 |
| BM25 | off | 0.840 | 0.226 | 17/26 |
| Titan V2 | off | 0.833 | 0.226 | 17/26 |

**The embeddings lose.** Before propagation the two are indistinguishable (0.840 vs 0.833),
so the embeddings were not finding anything the lexical path missed; after propagation BM25
pulls ahead. This is a reasonable outcome rather than a surprising one: a table's document
here is mostly identifiers and a one-line comment, not prose, which is the regime BM25 is
built for. Foreign-key structure turned out to matter far more than semantic similarity.

Two caveats that cut against the winner. Titan was handed the same identifier-heavy document
text as BM25, and a prose rendering of each table might suit it better. And the lexicon and
stemmer were written with BM25 in mind, so the comparison is not perfectly even-handed.

BM25 stays the default — which also means the repo runs offline, with no credentials and no
per-query cost. That was already the plan; it is now the plan *and* the better-measuring
option. Reproduce with `PGSS_RETRIEVAL_BACKEND=titan`.

---

## Known limits

- **Retrieval has a recall ceiling, and this is the design's real failure mode.** At k=8 it
  misses a required table on 2 of 26 questions: `revenue-by-category` (needs a four-table
  join through the category hierarchy) and `reseller-locations` (the store→address path runs
  through a cross-reference table that matches nothing in the question's wording). When it
  misses, the table is simply absent and the slice alone cannot recover.
- **`describe_table` is the escape hatch for exactly that.** It does not use the ranking, so
  a model that knows or guesses a table name can always reach it. Every `SchemaSlice` carries
  a note telling the model to do this, and the failure is named in the `search_schema`
  docstring the model reads.
- **The lexical backend cannot do irregular morphology.** Stemming reduces `employees` to
  `employee`, but `paid` does not reduce to `pay`. There is a test asserting the limit rather
  than hiding it. Embeddings were the obvious fix and were measured; they did not help
  overall (see above), so this ceiling stands.
- **The identifier lexicon is schema-specific.** AdventureWorks names are lowercase
  concatenations (`salesorderheader`), and splitting them needs a word list. The one in
  `retrieval.py` covers this schema's vocabulary; another database would want its own.
- **The Titan/Bedrock backend is measured but not the default**, because it measured worse.
  It has been run against the real service, so the numbers above are real; it is not a code
  path to trust blindly at scale, since it has only ever been exercised over 68 documents and
  26 queries with no retry, batching or rate-limit handling.
- **Not implemented, deliberately:** no prompts, no resources, no write path of any kind, no
  `EXPLAIN ANALYZE` (it would execute the query), no cross-database or multi-tenant support,
  no auth — the server is expected to run locally over stdio, and the security boundary is
  the database role, not the transport.

### Conformance: does not pass, and here is why

`npx @modelcontextprotocol/conformance@0.1.16 server --url http://localhost:3000/mcp` reports
**11 passed, 21 failed**. That is not a green check, and it is not a defect in this server:

1. **The tool has no knowledge of protocol revision 2026-07-28.** `--spec-version` accepts
   only `2025-03-26`, `2025-06-18`, `2025-11-25`, `draft` and `extension`. It therefore
   exercised the *legacy handshake* path that the SDK still supports for older clients, which
   is why `server-initialize` and `ping` appear in the passing column despite both being
   removed in the revision this server targets.
2. **Its scenarios assert against a reference fixture server.** The failures are things like
   `Unknown resource: test://static-text`, `Unknown prompt: test_simple_prompt` and `No image
   content found` — a server with four tools and no prompts or resources cannot satisfy them.
   `logging-set-level` fails with `Method not found`, which is correct behaviour for this
   revision.

Every scenario that tests protocol behaviour rather than fixture presence passes:
`tools-list`, `tools-call-simple-text`, `tools-call-error`, `resources-list`, `prompts-list`,
`ping`, `server-initialize`, `server-sse-multiple-streams`, `dns-rebinding-protection`.

It is not wired into CI, because doing so would mean checking in a baseline of 21 expected
failures, and a baseline that large hides regressions rather than catching them.

---

## Spec version

Built against MCP protocol revision **2026-07-28**, on the official Python SDK `mcp==2.0.0`
(pinned exactly; the v1 line continues in parallel and the APIs are not compatible).

That revision made the protocol **stateless**: the `initialize` handshake,
`notifications/initialized`, sessions and `Mcp-Session-Id` are gone, replaced by
`server/discover` plus a per-request `_meta` envelope carrying the protocol version and the
client's capabilities. There is no session handling anywhere in this codebase for that
reason. Every result now carries a `resultType`, cacheable methods carry `ttlMs`/`cacheScope`,
HTTP+SSE is deprecated in favour of stdio and Streamable HTTP, and sampling, roots and
logging are deprecated — none of which this server builds on.

The v2 SDK also removed `FastMCP`: `from mcp.server.fastmcp import FastMCP` raises
`ModuleNotFoundError`, and the entry point is `MCPServer` from `mcp.server`. (The `fastmcp`
package on PyPI is a separate, independent project.)

Two notes for anyone reading the code against the docs:

- `mcp.types` is now a mirror of a standalone `mcp-types` package. Both spellings work.
- `ToolAnnotations` fields are **snake_case in Python** (`read_only_hint`) and camelCase on
  the wire (`readOnlyHint`). Both are accepted at runtime, but only snake_case type-checks
  under mypy, so the source uses snake_case and this README quotes the wire form.

---

## Design decisions not dictated by the brief

- **The five shorthand view schemas are excluded from the index.** The upstream loader also
  creates `pe`/`hr`/`pr`/`pu`/`sa`, 68 convenience *views* over the same 68 tables. Indexing
  them would double every table and inflate the full-schema baseline this README compares
  against. Excluding them makes the headline ratio harder to hit, not easier.
- **Foreign-key edges leaving the slice are dropped** from `SchemaSlice.foreign_keys`. The
  model cannot join to a table whose columns it has not been shown, so listing those edges
  spends tokens on an unusable hint.
- **DDL is rendered, not `pg_dump`ed.** No storage parameters, tablespaces or ownership:
  they cost tokens and answer no question a query author has.
- **`Decimal` is returned as a string**, not a float. These are money and quantity columns,
  and a silent rounding is worse than a visible type change.
- **Sample data is downloaded at image build time, not vendored**, so no 40 MB blob lives in
  git. Both sources are pinned — the loader scripts by commit SHA, the data to Microsoft's
  immutable release asset.
- **`server.py` uses absolute imports** while the rest of the package uses relative ones.
  `mcp dev` loads the file by path rather than as a package member, and relative imports fail
  under that loader.

---

## Development

```bash
pip install -e ".[dev,bedrock]"
docker compose up -d --wait

pytest                      # 100 tests
pytest -m "not db"          # skip anything needing Postgres
python evals/run_evals.py   # fault matrix + retrieval, non-zero exit on regression
python evals/measure_context.py
ruff check . && mypy
mcp dev src/pg_schema_scout/server.py   # Inspector
```

CI runs ruff, ruff format, mypy, pytest and the evals, with the seeded database as a compose
service.

## License

MIT. The AdventureWorks sample data is Microsoft's, downloaded at build time and not
redistributed here.
