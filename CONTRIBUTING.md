# Contributing

Issues and pull requests are welcome.

## Set up

You need [uv](https://docs.astral.sh/uv/), `make` and Docker.

```bash
make setup
make db
```

`make setup` installs the dependencies and the git hooks. `make db` starts the seeded Postgres that the integration tests run against. The first run builds the image and downloads the AdventureWorks sample, which takes a few minutes.

## Before you open a pull request

- Run `make check` with the database up. It runs the same checks as CI: hooks, types, tests with coverage, and the evals. Integration tests fail, not skip, when the database is down.
- `make test-fast` is a quicker loop with no database. It is not what CI runs.
- Add a test for what you changed.
- If your change moves a number the README quotes, run `make evals` and commit `evals/results/latest.json`. CI fails when a README figure and the measured result disagree.
- If you change the schema the evals run on, run `make fixtures` and review the diff of `evals/fixtures/catalog.json`.
- Keep writing plain and specific. A pre-commit hook rejects em dashes and a short list of marketing words.

## Reporting bugs

Use the bug report template. For security problems, see [SECURITY.md](SECURITY.md) instead of opening an issue.
