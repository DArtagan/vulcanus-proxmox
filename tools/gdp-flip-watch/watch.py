"""Watch generic-device-plugin for a flip and capture the process when one happens.

    python3 tools/gdp-flip-watch/watch.py --out /tmp/gdp-flip --node piraeus-worker-1
    python3 tools/gdp-flip-watch/watch.py --dry-run          # detect, capture nothing

Why this exists, and what a capture has to explain, are in
todos/generic-device-plugin-hang.md. In short: a running process steps its
`/metrics` gather by a median 495x inside one scrape interval and no runtime
metric moves with it. A dump can only be taken from a process that has already
flipped, load will not induce one, and roughly 11 minutes separate a flip from
the first 10s timeout — so the capture has to be automatic.

**The capture is destructive.** It SIGQUITs the plugin, which is fatal to a Go
process, so the container restarts. On piraeus-worker-1 that briefly withdraws
`devic.es/cdrom`, and an automatic-ripping-machine pod being admitted in that
window would be rejected permanently. Use --dry-run first.

Prometheus is reached by exec-ing into the Alertmanager pod rather than through
a port-forward: a tunnel does not survive the hours this has to run, and
port-forward latency has already corrupted one reading in this investigation.
"""

import argparse
import collections
import datetime
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import detect  # noqa: E402

NS = "infrastructure"
SELECTOR = "app.kubernetes.io/name=generic-device-plugin"
ALERTMANAGER = "alertmanager-kube-prometheus-kube-prome-alertmanager-0"
PROM = "http://kube-prometheus-kube-prome-prometheus.infrastructure.svc:9090"


def run(args, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def log(msg):
    stamp = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%SZ")
    print(f"{stamp} {msg}", flush=True)


def promq(query):
    """Instant query, run from inside the cluster via the Alertmanager pod."""
    url = f"{PROM}/api/v1/query?query={query}"
    res = run(
        [
            "kubectl",
            "exec",
            "-n",
            NS,
            ALERTMANAGER,
            "-c",
            "alertmanager",
            "--",
            "wget",
            "-qO-",
            url,
        ]
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[:200])
    return json.loads(res.stdout)["data"]["result"]


def sample():
    """Return {pod_ip: (gather_ms, process_start)} for every plugin instance."""
    out = {}
    for r in promq('scrape_duration_seconds{job=~".*generic-device-plugin.*"}'):
        out[r["metric"]["instance"]] = [float(r["value"][1]) * 1000.0, None]
    for r in promq('process_start_time_seconds{job=~".*generic-device-plugin.*"}'):
        inst = r["metric"]["instance"]
        if inst in out:
            out[inst][1] = float(r["value"][1])
    return {k: tuple(v) for k, v in out.items()}


def pod_map():
    """{pod_ip: (pod_name, node)} — refreshed each poll, since pods are reaped."""
    res = run(["kubectl", "get", "pods", "-n", NS, "-l", SELECTOR, "-o", "json"])
    res.check_returncode()
    out = {}
    for p in json.loads(res.stdout)["items"]:
        ip = p.get("status", {}).get("podIP")
        if ip:
            out[f"{ip}:8080"] = (p["metadata"]["name"], p["spec"]["nodeName"])
    return out


def restart_count(pod):
    res = run(
        [
            "kubectl",
            "get",
            "pod",
            "-n",
            NS,
            pod,
            "-o",
            "jsonpath={.status.containerStatuses[0].restartCount}",
        ]
    )
    return int(res.stdout.strip() or -1)


def capture(pod, node, out_dir, gather_ms):
    """Collect thread state, then SIGQUIT and keep the goroutine dump."""
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    base = os.path.join(out_dir, f"{node}-{stamp}")
    os.makedirs(out_dir, exist_ok=True)
    log(f"CAPTURE {pod} on {node} at {gather_ms:.1f}ms -> {base}.*")

    # Per-thread utime/stime first: the one unexplained clue is that the CPU is
    # overwhelmingly system time, and SIGQUIT destroys the evidence.
    script = (
        'for t in /proc/1/task/*; do echo "== $t"; cat $t/stat; '
        "echo; echo -n 'wchan: '; cat $t/wchan 2>/dev/null; echo; done; "
        "echo '== status'; cat /proc/1/status"
    )
    proc_c = f"dbg-proc-{stamp[-6:]}"
    run(
        [
            "kubectl",
            "debug",
            "-n",
            NS,
            pod,
            "--image=busybox:1.36",
            "--target=generic-device-plugin",
            "-c",
            proc_c,
            "--attach=false",
            "--",
            "sh",
            "-c",
            script,
        ],
        timeout=180,
    )
    time.sleep(15)
    res = run(["kubectl", "logs", "-n", NS, pod, "-c", proc_c], timeout=120)
    with open(f"{base}.threads.txt", "w") as fh:
        fh.write(res.stdout)
    log(f"  wrote {base}.threads.txt ({len(res.stdout)} bytes)")

    before = restart_count(pod)
    kill_c = f"dbg-kill-{stamp[-6:]}"
    run(
        [
            "kubectl",
            "debug",
            "-n",
            NS,
            pod,
            "--image=busybox:1.36",
            "--target=generic-device-plugin",
            "-c",
            kill_c,
            "--attach=false",
            "--",
            "sh",
            "-c",
            "kill -QUIT 1",
        ],
        timeout=180,
    )

    # The container restarts only once the dump has finished writing, so
    # --previous stays empty until then. Poll restartCount rather than sleeping.
    for _ in range(60):
        time.sleep(10)
        if restart_count(pod) > before:
            break
    else:
        log("  WARNING: restartCount never moved; dump may be incomplete")
    res = run(
        ["kubectl", "logs", "-n", NS, pod, "-c", "generic-device-plugin", "--previous"],
        timeout=120,
    )
    with open(f"{base}.goroutines.txt", "w") as fh:
        fh.write(res.stdout)
    log(f"  wrote {base}.goroutines.txt ({len(res.stdout)} bytes)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/gdp-flip")
    ap.add_argument(
        "--node",
        action="append",
        help="only capture on this node (repeatable); detect on all",
    )
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="capture one flip, then exit")
    args = ap.parse_args()

    hist = collections.defaultdict(list)
    prev_start = {}
    log(
        f"watching; capture on {args.node or 'any node'}"
        f"{' (dry run)' if args.dry_run else ''}"
    )
    while True:
        try:
            cur = sample()
        except Exception as exc:
            log(f"query failed, retrying: {exc}")
            time.sleep(args.interval)
            continue
        pods = None
        for inst, (ms, start) in sorted(cur.items()):
            flipped = detect.is_flip(hist[inst], ms, prev_start.get(inst), start)
            hist[inst].append(ms)
            del hist[inst][:-40]
            prev_start[inst] = start
            if not flipped:
                continue
            if pods is None:
                pods = pod_map()
            pod, node = pods.get(inst, (None, None))
            if pod is None:
                log(f"FLIP on {inst} at {ms:.1f}ms but no pod matches that IP")
                continue
            log(f"FLIP {node} {pod}: {hist[inst][-2]:.1f}ms -> {ms:.1f}ms")
            if args.node and node not in args.node:
                log(f"  {node} not in capture list; watching only")
                continue
            if args.dry_run:
                log("  dry run; not capturing")
            else:
                capture(pod, node, args.out, ms)
            if args.once:
                return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
