#!/usr/bin/env bash
# Writing rules for markdown: no em dashes, none of the banned words.
#
# Usage:
#   check-writing.sh [file.md ...]     check the files given (pre-commit passes them)
#   check-writing.sh                   no arguments: check every *.md under the current directory
#
# The banned words live in banned-words.txt next to this script, one per line,
# so a doc that talks about the rule does not trip the rule. Words match whole
# words only, any case.
#
# Exit 0 when clean, 1 when anything is found. Output is "file:line: reason".
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
words_file="$here/banned-words.txt"
em_dash=$'\xe2\x80\x94'

if [ ! -f "$words_file" ]; then
  echo "check-writing: missing $words_file" >&2
  exit 2
fi

files=()
if [ "$#" -gt 0 ]; then
  files=("$@")
else
  while IFS= read -r f; do
    files+=("$f")
  done < <(find . -type f -name '*.md' \
    -not -path './.git/*' -not -path './.venv/*' -not -path './node_modules/*' | sort)
fi

words=()
while IFS= read -r line; do
  case "$line" in
    '' | '#'*) continue ;;
  esac
  words+=("$line")
done < "$words_file"

status=0
for f in "${files[@]+"${files[@]}"}"; do
  [ -f "$f" ] || continue
  case "$f" in
    *.md) ;;
    *) continue ;;
  esac

  if hits="$(grep -n -F -- "$em_dash" "$f")"; then
    while IFS= read -r hit; do
      echo "$f:${hit%%:*}: em dash (use a colon, comma or full stop)"
    done <<< "$hits"
    status=1
  fi

  for w in "${words[@]+"${words[@]}"}"; do
    if hits="$(grep -n -i -w -F -- "$w" "$f")"; then
      while IFS= read -r hit; do
        echo "$f:${hit%%:*}: banned word \"$w\""
      done <<< "$hits"
      status=1
    fi
  done
done

exit "$status"
