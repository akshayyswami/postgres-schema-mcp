#!/bin/sh
# Strip AI attribution lines from every commit message.
# Runs as the pre-commit commit-msg hook (see .pre-commit-config.yaml).
msg_file="$1"
tmp_file="$msg_file.tmp"

grep -v -i -E \
  -e '^Co-Authored-By:.*(claude|anthropic\.com)' \
  -e '^Claude-Session:' \
  -e 'Generated with \[?Claude Code' \
  "$msg_file" > "$tmp_file" || true

mv "$tmp_file" "$msg_file"
