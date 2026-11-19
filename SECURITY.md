# Security policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting: open the Security tab of this repository and choose "Report a vulnerability".

You will get a reply within 7 days.

## What is in scope

This server executes SQL that a model writes, so the read-only guarantee is the thing to attack. In scope:

- A statement that gets past `core/guard.py` and changes data or reads outside the indexed schemas.
- Anything that lets the `scout_ro` role write, create objects, or read a file or the network. That role is the layer that actually holds, so a bypass there is the most serious kind.
- A path from a tool argument to something other than a single read-only query.

A query that gets past the parser but is stopped by the database role is still a bug in the parser. Please report it, with the statement.

## Supported versions

Only the latest release receives fixes.
