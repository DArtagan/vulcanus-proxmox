#!/usr/bin/env bash
# Evaluates every host the nixos flake builds: the LXC templates, and the
# containers another host runs. A flake sees only files git tracks, so a new
# file needs `git add -N` before this can see it.
set -uo pipefail

eval=(nix eval --option warn-dirty false)
names() {
  "${eval[@]}" --json "./nixos#$1" \
    --apply 'p: builtins.concatStringsSep " " (builtins.attrNames p)' | tr -d '"'
}

status=0
for host in $(names packages.x86_64-linux); do
  "${eval[@]}" --raw "./nixos#packages.x86_64-linux.$host.drvPath" >/dev/null ||
    status=1
done
for host in $(names nixosConfigurations); do
  "${eval[@]}" --raw \
    "./nixos#nixosConfigurations.$host.config.system.build.toplevel.drvPath" \
    >/dev/null || status=1
done
exit "$status"
