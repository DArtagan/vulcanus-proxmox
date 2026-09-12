#!/usr/bin/env bash
# Renders adblock.mobileconfig.template into the SOPS-encrypted Secret the
# profile server mounts and serves to family devices.
#
#   tools/cloudflare-gateway/render-profile-secret.sh <doh-subdomain>
#
# <doh-subdomain> is the bare label from the Gateway location's DoH hostname:
# for https://65y9p2vm1u.cloudflare-gateway.com/dns-query it is 65y9p2vm1u.
# Use the location the *phones* should report under — `mobile` — not the one
# CoreDNS uses, or every roaming device files its queries against that site and
# the per-location attribution stops meaning anything.
#
# The profile lives in its own Secret rather than beside the API credentials for
# two reasons. Rendering needs only sops --encrypt, which requires the public
# keys alone, so anyone with the repo can regenerate it; sharing a file with the
# credentials would mean decrypting them to rewrite one key. And the CronJob
# consumes the credentials with envFrom, which turns every key into an
# environment variable -- "adblock.mobileconfig" is not a valid name, so kubelet
# skips it and logs InvalidEnvironmentVariableNames on every run.
set -euo pipefail

TEMPLATE="kubernetes/apps/cloudflare-gateway/adblock.mobileconfig.template"
OUT="kubernetes/apps/cloudflare-gateway/profile.sops.yaml"
SECRET_NAME="cloudflare-gateway-mobileconfig"

[[ $# -eq 1 ]] || { sed -n '2,12p' "$0" | sed 's/^# \?//'; exit 64; }
SUB=$1
[[ $SUB =~ ^[a-z0-9]+$ ]] || { echo "Subdomain looks wrong: '$SUB'. Expected the bare label only, no dots."; exit 64; }
[[ -f $TEMPLATE ]] || { echo "Run me from the repo root (can't see $TEMPLATE)"; exit 1; }

body=$(python3 - "$TEMPLATE" "$SUB" <<'PYEOF'
import re, sys, plistlib

text = open(sys.argv[1]).read()
sub = sys.argv[2]

# Drop the comment block that explains how to render this file. It describes the
# template, and repeating it inside the artefact helps nobody. The per-setting
# comments further down are kept: they explain choices to anyone inspecting an
# installed profile.
text, n = re.subn(r"<!--.*?To render:.*?-->\n", "", text, count=1, flags=re.S)
if n != 1:
    sys.exit("Could not find the template's header comment to strip")

text = text.replace("DOH-SUBDOMAIN", sub)
if "DOH-SUBDOMAIN" in text:
    sys.exit("Substitution missed an occurrence")

# Parse before writing. A malformed plist installs as nothing on a phone, and
# the failure would first be seen by a relative.
p = plistlib.loads(text.encode())
url = p["PayloadContent"][0]["DNSSettings"]["ServerURL"]
if sub not in url:
    sys.exit(f"Rendered ServerURL does not carry the subdomain: {url}")
print(text, end="")
PYEOF
)

tmp=$(mktemp); out_tmp=$(mktemp); trap 'rm -f "$tmp" "$out_tmp"' EXIT
{
  echo "apiVersion: v1"
  echo "kind: Secret"
  echo "metadata:"
  echo "  name: $SECRET_NAME"
  echo "stringData:"
  echo "  adblock.mobileconfig: |"
  echo "    ${body//$'\n'/$'\n'    }"
} > "$tmp"

# --filename-override so sops matches the *.sops.yaml creation rule in
# .sops.yaml against the output name rather than the temp path.
sops --encrypt --input-type yaml --output-type yaml \
  --filename-override "$OUT" "$tmp" > "$out_tmp"
mv "$out_tmp" "$OUT"
echo "Wrote $OUT (Secret/$SECRET_NAME)"
echo
echo "Still to do by hand, because it needs the private key:"
echo "  sops kubernetes/apps/cloudflare-gateway/secret.sops.yaml"
echo "  ...and delete the now-duplicated adblock.mobileconfig key."
