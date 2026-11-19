.PHONY: setup db fmt check test-fast evals evals-check evals-db fixtures

setup:
	uv sync
	uv run pre-commit install

# The seeded Postgres the integration tests and the live evals run against. The first
# run builds the image and seeds AdventureWorks, which takes a few minutes.
db:
	docker compose up -d --wait --wait-timeout 900

fmt:
	uv run ruff format .
	uv run ruff check --fix .

# Runs exactly what CI runs. Needs `make db` first: the integration tests fail, not
# skip, when the database is down, so a green check always touched Postgres.
check:
	uv run pre-commit run --all-files
	uv run mypy
	uv run pytest --cov
	$(MAKE) evals-check
	$(MAKE) evals-db

# Everything that needs no database. A quick loop while editing; not what CI runs.
test-fast:
	uv run pytest -m "not db"
	$(MAKE) evals-check

# Re-measure and overwrite evals/results/latest.json. Commit the result.
evals:
	uv run python evals/measure.py > evals/results/latest.json.tmp
	mv evals/results/latest.json.tmp evals/results/latest.json

# Fail if measure.py disagrees with the committed latest.json, or a README figure disagrees with it.
evals-check:
	@tmp="$$(mktemp)"; \
	uv run python evals/measure.py > "$$tmp"; rc=$$?; \
	if [ $$rc -eq 0 ]; then diff -u evals/results/latest.json "$$tmp"; rc=$$?; fi; \
	rm -f "$$tmp"; \
	if [ $$rc -ne 0 ]; then echo "evals-check: measure.py output differs from evals/results/latest.json (run make evals)"; exit $$rc; fi
	uv run python scripts/check-readme-numbers.py

# The checks that need the seeded Postgres: every must_allow query really executes, and the
# frozen catalog still matches the database.
evals-db:
	uv run python evals/check_live.py
	uv run python evals/capture_catalog.py --check

# Re-freeze the catalog from the running database. Review the diff before committing.
fixtures:
	uv run python evals/capture_catalog.py
