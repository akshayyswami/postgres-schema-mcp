"""Every environment variable the code reads is named in the README.

The README's Configuration table is how a user finds a setting, and an undocumented one is
a setting nobody knows to change. This reads the source, so a new variable fails CI until it
has a row.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# os.environ.get("X"), os.environ["X"], os.getenv("X"), a helper such as _env_int("X", 3).
ENV_READ = re.compile(
    r"""(?:environ(?:\.get)?\s*[(\[]|getenv\s*\(|\b_?env\w*\s*\()\s*["']([A-Z][A-Z0-9_]{2,})["']"""
)


def variables_read() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted((ROOT / "src").rglob("*.py")):
        body = path.read_text(encoding="utf-8")
        for match in ENV_READ.finditer(body):
            line = body.count("\n", 0, match.start()) + 1
            found.setdefault(match.group(1), f"{path.relative_to(ROOT)}:{line}")
    return found


def test_the_scan_finds_the_variables_we_know_about() -> None:
    names = variables_read()
    assert {"PGSS_DSN", "PGSS_SCHEMAS", "PGSS_STATEMENT_TIMEOUT_MS", "AWS_REGION"} <= set(names)


def test_every_variable_the_code_reads_has_a_row_in_the_readme() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = {
        name: where
        for name, where in variables_read().items()
        if not re.search(rf"\|\s*`{re.escape(name)}`\s*\|", readme)
    }
    assert not missing, f"add a row to the Configuration table for: {missing}"
