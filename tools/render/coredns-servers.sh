#!/usr/bin/env bash
# Renders kubernetes/infrastructure/coredns-servers.yaml.template into the
# SOPS-encrypted Secret that Flux feeds to the CoreDNS HelmRelease.
#
# The template is the source; the encrypted file is a build artefact. Editing
# the artefact by hand is how the two silently diverge, and the file decides
# whether a household several states away has working DNS.
#
#   tools/render/coredns-servers.sh <doh-subdomain>
#
# <doh-subdomain> is the bare label from the Gateway location's DoH hostname:
# for https://65y9p2vm1u.cloudflare-gateway.com/dns-query it is 65y9p2vm1u.
set -euo pipefail

TEMPLATE="kubernetes/infrastructure/coredns-servers.yaml.template"
OUT="kubernetes/infrastructure/coredns-servers.sops.yaml"

[[ $# -eq 1 ]] || { sed -n '2,12p' "$0" | sed 's/^# \?//'; exit 64; }
SUB=$1
[[ $SUB =~ ^[a-z0-9]+$ ]] || { echo "Subdomain looks wrong: '$SUB'. Expected the bare label only, no dots."; exit 64; }
[[ -f $TEMPLATE ]] || { echo "Run me from the repo root (can't see $TEMPLATE)"; exit 1; }

# Strip the template's own header comments: they describe the template, and
# repeating them inside an encrypted blob helps nobody.
body=$(grep -v '^#' "$TEMPLATE" | sed 's/DOH-SUBDOMAIN/'"$SUB"'/g')

# An `x && { ...; }` here would be a trap: under `set -e` the success case --
# grep finding nothing -- returns non-zero and kills the script.
if grep -q 'DOH-SUBDOMAIN' <<<"$body"; then
  echo "Substitution missed an occurrence"; exit 1
fi
if ! python3 -c 'import sys,yaml; yaml.safe_load(sys.stdin)' <<<"$body"; then
  echo "Rendered servers block is not valid YAML; refusing to write."; exit 1
fi

tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
{
  echo "apiVersion: v1"
  echo "kind: Secret"
  echo "metadata:"
  echo "  name: coredns-servers"
  echo "stringData:"
  echo "  servers.yaml: |"
  echo "    ${body//$'\n'/$'\n'    }"
} > "$tmp"

# --filename-override is required, not cosmetic: sops picks its creation rule
# by matching the *input* path against .sops.yaml, and the temp file does not
# end in .sops.yaml, so without this it exits "no matching creation rules found".
# Written via a second temp so a failure cannot leave a truncated $OUT behind.
out_tmp=$(mktemp); trap 'rm -f "$tmp" "$out_tmp"' EXIT
sops --encrypt --input-type yaml --output-type yaml \
  --filename-override "$OUT" "$tmp" > "$out_tmp"
mv "$out_tmp" "$OUT"
echo "Wrote $OUT"
echo
echo "This decides DNS for the vulcanus LAN, which is a household several states"
echo "away. Merging it to main deploys it. dns-canary reports whether filtering"
echo "survived; watch it rather than assuming."
