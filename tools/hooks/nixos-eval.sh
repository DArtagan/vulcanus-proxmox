#!/usr/bin/env bash
# Evaluates every host the nixos flake builds. A flake sees only files git
# tracks, so a new file needs `git add -N` before this can see it.
set -uo pipefail

eval=(nix eval --option warn-dirty false)
hosts=$("${eval[@]}" --json ./nixos#packages.x86_64-linux \
  --apply 'p: builtins.concatStringsSep " " (builtins.attrNames p)' | tr -d '"')

status=0
for host in $hosts; do
  "${eval[@]}" --raw "./nixos#packages.x86_64-linux.$host.drvPath" >/dev/null ||
    status=1
done
exit "$status"
