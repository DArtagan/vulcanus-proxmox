#!/bin/bash
# Send one Pushover notification, and fail if it does not arrive.
#
# Deliberately the opposite of hc-ping, which carries `-` at its call sites so a
# monitoring failure never fails the job it watches. That rule holds because a
# healthchecks ping that does not arrive turns its own check red on its own
# period -- losing one announces itself. A Pushover push leaves no trace
# anywhere if it is lost, so this exits non-zero and lets the caller decide;
# pbs-freshness turns that into a check failure, which is the only way a lost
# alert becomes visible.
#
# Managed by ansible/backup-monitoring.yaml. No mini-nas counterpart: its
# reporting goes through healthchecks alone.
set -euo pipefail

title="$1"
message="$2"

token_file=/etc/pushover/token
user_file=/etc/pushover/user-key

for file in "$token_file" "$user_file"; do
	if [ ! -r "$file" ]; then
		echo "pushover-notify: no credential at $file" >&2
		exit 1
	fi
done

# The credentials go in via --config rather than on the command line, because
# argv is readable by any user through ps. The title and message are not
# secret, and passing them as arguments avoids having to escape them for
# curl's config format.
curl -fsS -m 10 --retry 3 -o /dev/null \
	--form-string "title=$title" \
	--form-string "message=$message" \
	--config - <<EOF
url = "https://api.pushover.net/1/messages.json"
form-string = "token=$(cat "$token_file")"
form-string = "user=$(cat "$user_file")"
EOF
