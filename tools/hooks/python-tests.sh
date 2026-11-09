#!/usr/bin/env bash
# Runs the test suite beside each changed file: a suite runs when anything in
# its directory changes, its tests reading fixtures and scripts from there. A
# suite may also sit in a tests/ directory beside what it tests, where the
# directory holds only what is deployed, as ARM's scripts/ does. The suites are
# unittest's; pytest runs them, all at once across the CPUs.
set -uo pipefail

declare -A suites=()
for file in "$@"; do
  dir=$(dirname "$file")
  for suite in "$dir" "$dir/tests"; do
    compgen -G "$suite/test_*.py" >/dev/null && suites[$suite]=1
  done
done

[ "${#suites[@]}" -eq 0 ] && exit 0

# In the project's environment, whatever the shell has active: hooks run as
# devenv tasks, where the venv isn't, and --locked fails on a stale lock.
exec uv run --locked pytest -q -n auto -p no:cacheprovider "${!suites[@]}"
