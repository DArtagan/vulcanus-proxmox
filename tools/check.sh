#!/usr/bin/env bash
# Runs the checks that apply to the changed files, printing only failures and a
# one-line summary.
#
#   check            # files changed against HEAD, untracked included
#   check <path>...  # these files
#
# Offline only: nothing here asks the cluster, so a server-side dry run is still
# worth doing before deploying.
set -uo pipefail

root=$(git rev-parse --show-toplevel)
files=()
if [[ $# -gt 0 ]]; then
  for path in "$@"; do files+=("$(realpath -m --relative-to="$root" "$path")"); done
else
  mapfile -t files < <(
    git -C "$root" diff --name-only HEAD
    git -C "$root" ls-files --others --exclude-standard
  )
fi
cd "$root" || exit 1
mapfile -t files < <(printf '%s\n' "${files[@]}" | sort -u | sed '/^$/d')
if [[ ${#files[@]} -eq 0 ]]; then
  echo "check: nothing changed"
  exit 0
fi

passed=()
skipped=()
failed=0

run() {
  local label=$1 out
  shift
  if out=$("$@" 2>&1); then
    passed+=("$label")
  else
    echo "check: $label failed:"
    tail -n 40 <<<"$out"
    failed=1
  fi
}

# Whatever prek runs on commit: formatting, the sops-encrypted guard, and the
# linters. Deleted files are left out.
existing=()
for f in "${files[@]}"; do [[ -e $f ]] && existing+=("$f"); done
[[ ${#existing[@]} -gt 0 ]] && run prek prek run --files "${existing[@]}"

# Each Flux Kustomization's path renders whole, so a change anywhere under it
# renders the path it belongs to: the longest that contains it.
mapfile -t flux_paths < <(
  {
    echo kubernetes/cluster
    sed -n 's|^ *path: \./\(kubernetes/.*\)$|\1|p' kubernetes/cluster/*.yaml
  } | sort -u
)
declare -A render=()
for f in "${files[@]}"; do
  if [[ $f == kubernetes/charts/* ]]; then
    chart=${f#kubernetes/charts/}
    [[ " ${skipped[*]} " == *" chart ${chart%%/*} "* ]] || skipped+=("chart ${chart%%/*}")
    continue
  fi
  best=
  for p in "${flux_paths[@]}"; do
    [[ $f == "$p"/* && ${#p} -gt ${#best} ]] && best=$p
  done
  [[ -n $best ]] && render[$best]=1
done
for p in "${!render[@]}"; do
  run "render $p" kubectl kustomize "$p" -o /dev/null
done

# A Python suite runs when anything in its directory changes.
declare -A suites=()
for f in "${files[@]}"; do
  d=$(dirname "$f")
  compgen -G "$d/test_*.py" >/dev/null && suites[$d]=1
done
for d in "${!suites[@]}"; do
  run "tests $d" python3 -m unittest discover -s "$d" -q
done

for f in "${files[@]}"; do
  if [[ $f == terraform/* ]]; then
    if [[ -d terraform/.terraform ]]; then
      run "tofu validate" tofu -chdir=terraform validate -no-color
    else
      skipped+=("tofu validate: run tofu init in terraform/")
    fi
    break
  fi
done

for f in "${files[@]}"; do
  if [[ $f == nixos/* ]]; then
    # Untracked files are invisible to a flake until `git add -N`.
    mapfile -t hosts < <(nix eval --json ./nixos#packages.x86_64-linux \
      --apply builtins.attrNames 2>/dev/null | tr -d '[]"' | tr , '\n')
    for h in "${hosts[@]}"; do
      run "nixos $h" nix eval --raw "./nixos#packages.x86_64-linux.$h.drvPath"
    done
    break
  fi
done

for f in "${files[@]}"; do
  if [[ $f == ansible/*.yaml && -e $f ]] && grep -q '^- .*hosts:\|^  hosts:' "$f"; then
    run "ansible ${f#ansible/}" ansible-playbook --syntax-check \
      -i ansible/hosts.yaml "$f"
  fi
done

join() {
  local IFS=$'\n'
  sed -z 's/\n$//; s/\n/, /g' <<<"$*"
}
summary=$(join "${passed[@]:-nothing applicable}")
[[ ${#skipped[@]} -gt 0 ]] && summary+="; not checked: $(join "${skipped[@]}")"
if [[ $failed -eq 0 ]]; then
  echo "check: ok -- $summary"
else
  exit 1
fi
