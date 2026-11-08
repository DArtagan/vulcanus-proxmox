#!/usr/bin/env bash
# Runs the unittest suite beside each changed file: a suite runs when anything
# in its directory changes, its tests reading fixtures and scripts from there.
# A suite may also sit in a tests/ directory beside what it tests, where the
# directory holds only what is deployed, as ARM's scripts/ does.
set -uo pipefail

declare -A suites=()
for file in "$@"; do
  dir=$(dirname "$file")
  for suite in "$dir" "$dir/tests"; do
    compgen -G "$suite/test_*.py" >/dev/null && suites[$suite]=1
  done
done

status=0
for dir in "${!suites[@]}"; do
  if ! out=$(python3 -m unittest discover -s "$dir" -q 2>&1); then
    echo "$dir:"
    echo "$out"
    status=1
  fi
done
exit "$status"
