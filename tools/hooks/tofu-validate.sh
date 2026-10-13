#!/usr/bin/env bash
# Validates the terraform root, which needs its providers installed.
set -uo pipefail

if [[ ! -d terraform/.terraform ]]; then
  # -backend=false: the state lives in the main checkout, and validate needs
  # none of it.
  echo "terraform/ is not initialised. Run:"
  echo "    tofu -chdir=terraform init -backend=false"
  exit 1
fi
exec tofu -chdir=terraform validate -no-color
