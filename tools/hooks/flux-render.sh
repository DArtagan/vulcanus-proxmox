#!/usr/bin/env bash
# Renders every path a Flux Kustomization in kubernetes/cluster points at.
# kubernetes/cluster itself is not one of them: it has no kustomization.yaml,
# Flux generating one at reconcile time, so kubectl cannot render it.
set -uo pipefail

mapfile -t paths < <(sed -n 's|^  path: \./||p' kubernetes/cluster/*.yaml)
status=0
for path in "${paths[@]}"; do
  if ! out=$(kubectl kustomize "$path" 2>&1 >/dev/null); then
    echo "$path: $out"
    status=1
  fi
done
exit "$status"
