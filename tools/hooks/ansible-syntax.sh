#!/usr/bin/env bash
# Syntax-checks every playbook. All of them, since a change to a role, a
# template or the inventory reaches whichever playbooks use it.
set -uo pipefail

status=0
for playbook in ansible/*.yaml; do
  grep -q '^- .*hosts:\|^  hosts:' "$playbook" || continue
  if ! out=$(ansible-playbook --syntax-check -i ansible/hosts.yaml \
    "$playbook" 2>&1); then
    echo "$out"
    status=1
  fi
done
exit "$status"
