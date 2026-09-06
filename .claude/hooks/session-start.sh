#!/usr/bin/env bash
# SessionStart hook: give CLAUDE.md's "continue" rule its data.
# Prints the last milestone tag, the current branch, and every SDD ledger present, so a fresh
# session can resume at the right task without guessing. Read-only; never fails the session.
set -u
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

last_tag="$(git tag --list 'm[0-9]*' 2>/dev/null | sort -V | tail -1)"
branch="$(git branch --show-current 2>/dev/null)"
head="$(git rev-parse --short HEAD 2>/dev/null)"

echo "SentinelBrief: last milestone tag=${last_tag:-none} branch=${branch:-none} head=${head:-none}"
echo "Plan registry: docs/plans/README.md (next milestone = first row not tagged above)"

shopt -s nullglob
ledgers=(.superpowers/sdd/*/progress.md)
if ((${#ledgers[@]})); then
  for f in "${ledgers[@]}"; do
    done_count="$(grep -c '^Task [0-9]*: complete' "$f" 2>/dev/null || echo 0)"
    echo "Ledger: $f (tasks complete: ${done_count}; last line: $(tail -1 "$f" | cut -c1-120))"
  done
else
  echo "Ledger: none yet — start one with superpowers:subagent-driven-development on the next milestone's spine"
fi
exit 0
