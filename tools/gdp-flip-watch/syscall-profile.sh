#!/usr/bin/env bash
# Profile the system-call cost of a generic-device-plugin gather.
#
# The capture of 2026-09-22 showed a flipped process with every goroutine parked
# and no metrics machinery in flight, but stime:utime at 6.59:1 against a
# healthy 0.42:1. So the cost is in the kernel, not in Go, and the question is
# which syscall. See todos/generic-device-plugin-hang.md.
#
#   tools/gdp-flip-watch/syscall-profile.sh <pod> [fetches]
#
# Always profile a healthy pod too. A syscall count means nothing without a
# control: futex and epoll_pwait dominate any Go process at rest, and only the
# difference against a healthy run says which one the flip inflates.
#
# Reach for `go_gc_duration_seconds` off the target's own /metrics before this.
# It is what actually identified the flip as stop-the-world inflation, costs one
# wget, and perturbs nothing — where strace attaches ptrace to a live PID 1.
#
# `strace -c` without `-w`, deliberately: -w reports wall-clock time per call,
# under which blocking waits swamp everything and the output is unreadable.
# System time is the quantity the capture implicates.
#
# Destructive-ish: strace attaches to PID 1 of a live container. Prefer
# piraeus-worker-0, which advertises no devic.es/cdrom, so a mishap costs
# nothing. Never leave this attached — it detaches on timeout.
set -euo pipefail

POD="${1:?usage: syscall-profile.sh <pod> [fetches]}"
FETCHES="${2:-5}"
NS=infrastructure
NAME="prof-$(date -u +%H%M%S)"

# Every `$` below is expanded by the shell inside the ephemeral container, not
# here, so the single quotes are the point and SC2016 does not apply. $FETCHES
# is passed as a positional argument rather than spliced into the string.
# shellcheck disable=SC2016
kubectl debug -n "$NS" "$POD" --image=nicolaka/netshoot:latest \
  --target=generic-device-plugin -c "$NAME" --attach=false -- \
  bash -c '
    timeout 60 strace -f -p 1 -c -o /tmp/sc.txt &
    sleep 5
    for i in $(seq "$1"); do
      S=$(date +%s%N)
      curl -s -o /dev/null http://127.0.0.1:8080/metrics
      E=$(date +%s%N)
      echo "fetch $i: $(( (E-S)/1000000 )) ms"
    done
    sleep 2
    pkill -INT strace || true
    sleep 3
    echo "=== strace -c (system time per syscall) ==="
    cat /tmp/sc.txt
  ' _ "$FETCHES" >/dev/null 2>&1

echo "profiling $POD as $NAME; waiting..."
for _ in $(seq 40); do
  sleep 5
  if kubectl get pod -n "$NS" "$POD" -o json \
     | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(any(c['name']=='$NAME' and 'terminated' in c.get('state',{})
          for c in d['status'].get('ephemeralContainerStatuses',[]) or []))" \
     | grep -q True; then break; fi
done
kubectl logs -n "$NS" "$POD" -c "$NAME"
