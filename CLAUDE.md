# postgres-schema-mcp: notes for Claude Code

Thesis: Schema access for an agent is a retrieval problem, not a dump.

Read first: `docs/SPEC.md` for scope and milestones, `docs/architecture.md` for the layout.

## House rules (same in every repo)

1. Evals before features. The number in `docs/SPEC.md` must be measurable from the first milestone, even if it starts out bad.
2. Every figure in the README comes from a script in `evals/` and CI re-measures it on every push. No estimated numbers.
3. Ship planted faults: known-bad inputs the tool must catch, run in CI.
4. Fail closed. When the tool can't verify something it says "not checked". It never guesses.
5. One milestone per session. End each session with tests green and a `CHANGELOG.md` entry.
6. Use plan mode before changing architecture. Record decisions in `docs/DECISIONS.md` (date, decision, why, alternatives).
7. No company code, data, client names or screenshots. Public or fabricated sample data only.
8. Writing: plain and specific. No em dashes, no marketing words.
9. Run `/security-review` before tagging a release.
10. Python 3.12+, uv, ruff, mypy strict, pytest, src layout, `core/` free of I/O. Follow the oss-kit skills (oss-new-repo, oss-evals, oss-docs, oss-release, oss-review).
11. Finish every session with `make check` green.
12. No AI attribution in commits or PRs: no `Co-Authored-By`, no `Claude-Session`, no "Generated with Claude Code".

## This repo

- `make check` needs the seeded database: run `make db` first. Integration tests fail, not skip, when it is down. `make test-fast` is the loop without it.
- `core/` has no I/O and never imports from `adapters/`. Postgres and the Bedrock backend live in `adapters/`.
- README figures sit in eval markers. After a change that moves a number, run `make evals` and commit `evals/results/latest.json`.
- `evals/fixtures/catalog.json` is the frozen schema. Re-freeze it with `make fixtures` and review the diff.
- Titan and conformance figures are recorded in `evals/baseline/` with a date. They are not re-measured in CI, so do not edit them without re-running the real tool.

## Current milestone

M4: v0.1.0. The tooling and docs are done. The tag, the GitHub Release and the MCP Registry entry are not.
