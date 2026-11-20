# Security policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting: open the Security tab of this repository and choose "Report a vulnerability".

You will get a reply within 7 days.

## What is in scope

This server executes SQL that a model writes, so the read-only guarantee is the thing to attack. In scope:

- A statement that gets past `core/guard.py` and changes data or calls a function that reaches outside the database.
- Anything that lets the `scout_ro` role write, create objects, or read a file or the network. That role is the layer that actually holds, so a bypass there is the most serious kind.
- A path from a tool argument to something other than a single read-only query.

A query that gets past the parser but is stopped by the database role is still a bug in the parser. Please report it, with the statement.

## What is not a security boundary

- `PGSS_SCHEMAS` chooses which schemas are indexed and shown to the model. It does not limit what the role can read. A query can still `SELECT` from any table the role has been granted. Limit that with the role's grants.
- The HTTP transport (`--http`) has no authentication. It binds to loopback and refuses any other address unless `PGSS_ALLOW_REMOTE=1` is set, which means something in front of the port does the authentication.
- Table and column comments, defaults and query results are data from the database. The server tells the model to treat them as untrusted, and collapses and shortens comments, but it cannot stop a model from following text it is shown.

## Supported versions

Only the latest release receives fixes.
