#!/bin/bash
# Mirrors every repository in the plan mirror_plan.py wrote, and fails when a
# mirror has stopped following GitHub. Soft Serve syncs the mirrors itself; a
# sync that fails is otherwise only a log line. See docs/git.md.
set -euo pipefail

plan=$PLAN_FILE
work=$HOME

# The key is mounted group-readable for this non-root user, and ssh refuses a
# private key anyone but its owner can read.
install -m 600 /run/secrets/mirror/ssh-key "$work/id"

# The host key is taken on first use each run: the job reaches Soft Serve by its
# ClusterIP Service, never across the LAN.
cat >"$work/ssh_config" <<EOF
Host soft-serve
  Port 22
  IdentityFile $work/id
  IdentitiesOnly yes
  UserKnownHostsFile $work/known_hosts
  StrictHostKeyChecking accept-new
  LogLevel ERROR
EOF
export GIT_SSH_COMMAND="ssh -F $work/ssh_config"
soft() { ssh -F "$work/ssh_config" soft-serve "$@"; }

# A repository's heads and tags, which is what a mirror has to match. GitHub
# also advertises refs/pull/*, which says nothing about the mirror's health.
# The `|| true` is for an empty repository, which has none; pipefail still
# carries a failure of ls-remote itself.
refs() { git ls-remote --refs "$1" | { grep -E $'\trefs/(heads|tags)/' || true; } | sort; }

# Soft Serve syncs every 10 minutes; a push younger than this is still in flight.
margin=3600
now=$(date +%s)
failed=0

soft repo list --all | { grep '^github/' || true; } | sort >"$work/existing"
cut -f1 "$plan" >"$work/planned"

while IFS=$'\t' read -r name private pushed url; do
	if ! grep -qxF "$name" "$work/existing"; then
		flags=(--mirror --lfs)
		[ "$private" = 1 ] && flags+=(--private)
		# A failed import never returns: ImportRepository blocks on a channel
		# only a successful clone writes to. The timeout is what ends it, and
		# it runs ssh directly because it cannot run a shell function.
		if timeout 600 ssh -F "$work/ssh_config" soft-serve \
			repo import "${flags[@]}" "$name" "$url" </dev/null; then
			echo "imported $name"
		else
			echo "FAILED to import $name from $url"
			failed=1
		fi
		continue
	fi

	if ! upstream=$(refs "$url") || ! mirror=$(refs "ssh://soft-serve/$name"); then
		echo "FAILED to read the refs of $name"
		failed=1
	# Refs, not the newest commit's date against pushed_at: deleting a branch
	# moves pushed_at without adding a commit, and GitHub deletes merged PR
	# branches itself, so a date test calls an in-sync mirror stale for good.
	elif [ "$upstream" != "$mirror" ]; then
		if [ $((now - pushed)) -gt $margin ]; then
			echo "STALE $name: its refs differ from GitHub's, last pushed $(date -u -d "@$pushed" +%FT%TZ)"
			failed=1
		else
			echo "catching up $name: pushed within the last ${margin}s"
		fi
	fi
done <"$plan"

# Kept, never deleted: the mirror is a backup, and a deletion upstream is when
# it is wanted. A renamed repository appears here under its old name.
comm -23 "$work/existing" "$work/planned" | while read -r name; do
	echo "kept $name: gone from GitHub or out of scope"
done

exit $failed
