#!/usr/bin/env python3
"""Fail when a README figure differs from evals/results/latest.json.

A figure in the README is wrapped in comment markers that GitHub does not show:

    <!-- eval:reduction_x:.1f -->6.0<!-- /eval -->

`reduction_x` is a key in evals/results/latest.json. A dotted key walks into
nested objects and lists (`slices.0.tokens`). The part after the second colon is
a Python format spec applied to the value (`.1f`, `,d`, `.0%`). Ints and strings
need no spec; floats must have one, so a rounding choice is never implicit.

The text between the markers has to equal the formatted value exactly. Hand-editing
the number, or the JSON, makes this fail. A README with no markers also fails, so
the check can never pass because nothing was checked.

Usage:
    python scripts/check-readme-numbers.py [--readme PATH] [--results PATH]

Defaults: --readme README.md, --results evals/results/latest.json

Exit codes: 0 all figures match, 1 a figure is wrong, 2 a file is missing or unreadable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

OPEN = re.compile(r"<!--\s*eval:(?P<key>[A-Za-z0-9_.-]+)(?::(?P<fmt>[^\s>]+))?\s*-->")
CLOSE = re.compile(r"<!--\s*/eval\s*-->")


class CheckError(Exception):
    """A problem with the inputs, not with a figure."""


def lookup(data: Any, key: str) -> Any:
    """Walk a dotted key through nested dicts and lists."""
    node = data
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise KeyError(key)
    return node


def render(value: Any, fmt: str | None) -> str:
    """Format a JSON value the way the README is expected to show it."""
    if isinstance(value, (dict, list)) or value is None:
        raise ValueError("value is not a scalar")
    if fmt:
        return format(value, fmt)
    if isinstance(value, float):
        raise ValueError("float needs a format spec, for example eval:KEY:.1f")
    return str(value)


def check_readme(
    text: str, results: dict[str, Any], name: str = "README.md"
) -> tuple[int, list[str]]:
    """Return (figures checked, problems)."""
    problems: list[str] = []
    checked = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        position = 0
        for match in OPEN.finditer(line):
            if match.start() < position:
                continue
            close = CLOSE.search(line, match.end())
            if close is None:
                problems.append(
                    f"{name}:{lineno}: eval:{match['key']} is not closed on the same line"
                )
                continue
            position = close.end()
            shown = line[match.end() : close.start()]
            checked += 1
            try:
                expected = render(lookup(results, match["key"]), match["fmt"])
            except KeyError:
                problems.append(f"{name}:{lineno}: eval:{match['key']} is not in the results file")
                continue
            except (ValueError, TypeError) as exc:
                problems.append(f"{name}:{lineno}: eval:{match['key']}: {exc}")
                continue
            if shown != expected:
                key = match["key"]
                problems.append(
                    f'{name}:{lineno}: eval:{key} shows "{shown}", results say "{expected}"'
                )
        stray = CLOSE.search(line, position) if position else CLOSE.search(line)
        if stray and not OPEN.search(line):
            problems.append(f"{name}:{lineno}: closing marker with no opening marker")
    if checked == 0:
        problems.append(
            f"{name}: no <!-- eval:KEY --> markers found, so no figure is being checked"
        )
    return checked, problems


def load_results(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CheckError(f"{path}: not found. Run `make evals` first.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckError(f"{path}: cannot read as JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise CheckError(f"{path}: expected one JSON object at the top level")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--results", type=Path, default=Path("evals/results/latest.json"))
    args = parser.parse_args(argv)

    try:
        results = load_results(args.results)
        text = args.readme.read_text(encoding="utf-8")
    except CheckError as exc:
        print(f"check-readme-numbers: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"check-readme-numbers: {args.readme}: {exc}", file=sys.stderr)
        return 2

    checked, problems = check_readme(text, results, name=str(args.readme))
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"check-readme-numbers: {checked} figure(s) match {args.results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
