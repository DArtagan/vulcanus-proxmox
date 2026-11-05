#!/bin/sh
# A SOPS file as git diffs it: values and age stanzas masked, so a diff shows
# keys, recipients and plaintext fields changing, not ciphertext churn.
exec sed -E \
  -e '/-----BEGIN AGE ENCRYPTED FILE-----/,/-----END AGE ENCRYPTED FILE-----/d' \
  -e 's/ENC\[[^]]*\]/ENC[…]/g' \
  "$1"
