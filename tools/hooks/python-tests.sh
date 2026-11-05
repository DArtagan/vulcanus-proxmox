#!/usr/bin/env bash
# Runs the unittest suite beside each changed file: a suite runs when anything
# in its directory changes, its tests reading fixtures and scripts from there.
set -uo pipefail

declare -A suites=()
for file in "$@"; do
  dir=$(dirname "$file")
  compgen -G "$dir/test_*.py" >/dev/null && suites[$dir]=1
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
