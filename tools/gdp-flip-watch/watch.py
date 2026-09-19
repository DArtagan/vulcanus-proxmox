"""Watch generic-device-plugin for a flip and capture the process when one happens.

    python3 tools/gdp-flip-watch/watch.py --node piraeus-worker-1 --once
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
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import detect  # noqa: E402

NS = "infrastructure"
SELECTOR = "app.kubernetes.io/name=generic-device-plugin"
ALERTMANAGER = "alertmanager-kube-prometheus-kube-prome-alertmanager-0"
PROM = "http://kube-prometheus-kube-prome-prometheus.infrastructure.svc:9090"


def run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Run a command, leaving its return code for the caller to judge."""
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )


def log(msg: str) -> None:
    """Print a line stamped with the UTC time."""
    stamp = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%SZ")
    print(f"{stamp} {msg}", flush=True)


def promq(query: str) -> list[dict[str, Any]]:
    """Run an instant query from inside the cluster, via the Alertmanager pod."""
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


def sample() -> dict[str, tuple[float, float | None]]:
    """Return {pod_ip: (gather_ms, process_start)} for every plugin instance."""
    out = {}
    for r in promq('scrape_duration_seconds{job=~".*generic-device-plugin.*"}'):
        out[r["metric"]["instance"]] = [float(r["value"][1]) * 1000.0, None]
    for r in promq('process_start_time_seconds{job=~".*generic-device-plugin.*"}'):
        inst = r["metric"]["instance"]
        if inst in out:
            out[inst][1] = float(r["value"][1])
    return {k: tuple(v) for k, v in out.items()}


def pod_map() -> dict[str, tuple[str, str]]:
    """{pod_ip: (pod_name, node)} — refreshed each poll, since pods are reaped."""
    res = run(["kubectl", "get", "pods", "-n", NS, "-l", SELECTOR, "-o", "json"])
    res.check_returncode()
    out = {}
    for p in json.loads(res.stdout)["items"]:
        ip = p.get("status", {}).get("podIP")
        if ip:
            out[f"{ip}:8080"] = (p["metadata"]["name"], p["spec"]["nodeName"])
    return out


def restart_count(pod: str) -> int:
    """Read the plugin container's restart count, or -1 if it cannot be read."""
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


def debug_exec(pod: str, name: str, script: str) -> str:
    """Run `script` in an ephemeral container sharing the plugin's namespaces.

    Container names are RFC 1123 labels, so `name` must be lowercase with no
    stray characters: a name built from a `%Y%m%dT%H%M%SZ` stamp keeps the
    trailing `Z`, the API rejects it, and an unchecked return code turns that
    into two empty files and a report of success.
    """
    res = run(
        [
            "kubectl",
            "debug",
            "-n",
            NS,
            pod,
            "--image=busybox:1.36",
            "--target=generic-device-plugin",
            "-c",
            name,
            "--attach=false",
            "--",
            "sh",
            "-c",
            script,
        ],
        timeout=180,
    )
    if res.returncode != 0:
        message = f"kubectl debug {name} failed: {res.stderr.strip()[:300]}"
        raise RuntimeError(message)
    return name


def wait_for_container(pod: str, name: str, timeout: int = 180) -> bool:
    """Block until the ephemeral container has run, rather than guessing a sleep."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = run(["kubectl", "get", "pod", "-n", NS, pod, "-o", "json"], timeout=60)
        if res.returncode == 0:
            for c in json.loads(res.stdout)["status"].get(
                "ephemeralContainerStatuses", []
            ):
                if c["name"] == name and "terminated" in c.get("state", {}):
                    return True
        time.sleep(5)
    return False


def capture(pod: str, node: str, out_dir: Path, gather_ms: float) -> None:
    """Collect thread state, then SIGQUIT and keep the goroutine dump."""
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dt%H%M%S")
    base = f"{node}-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"CAPTURE {pod} on {node} at {gather_ms:.1f}ms -> {out_dir / base}.*")

    # Per-thread utime/stime first: the one unexplained clue is that the CPU is
    # overwhelmingly system time, and SIGQUIT destroys the evidence.
    script = (
        'for t in /proc/1/task/*; do echo "== $t"; cat $t/stat; '
        "echo; echo -n 'wchan: '; cat $t/wchan 2>/dev/null; echo; done; "
        "echo '== status'; cat /proc/1/status"
    )
    proc_c = debug_exec(pod, f"dbg-proc-{stamp[-6:]}", script)
    if not wait_for_container(pod, proc_c):
        log(f"  WARNING: {proc_c} never terminated; reading logs anyway")
    res = run(["kubectl", "logs", "-n", NS, pod, "-c", proc_c], timeout=120)
    if not res.stdout:
        log(f"  WARNING: {proc_c} produced no output: {res.stderr.strip()[:200]}")
    threads = out_dir / f"{base}.threads.txt"
    threads.write_text(res.stdout)
    log(f"  wrote {threads} ({len(res.stdout)} bytes)")

    before = restart_count(pod)
    debug_exec(pod, f"dbg-kill-{stamp[-6:]}", "kill -QUIT 1")

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
    goroutines = out_dir / f"{base}.goroutines.txt"
    goroutines.write_text(res.stdout)
    log(f"  wrote {goroutines} ({len(res.stdout)} bytes)")


class Watcher:
    """Every instance's recent samples, and the excursion each is in, if any."""

    def __init__(self, arguments: argparse.Namespace) -> None:
        """Watch as `arguments` says, keeping the ledger in its --out directory."""
        self.arguments = arguments
        self.ledger = arguments.out / "excursions.jsonl"
        self.history = collections.defaultdict(list)
        self.prev_start = {}
        self.excursion = {}

    def record(self, **fields: object) -> None:
        """Log every excursion, whether or not it is worth capturing.

        Whether flips begin as transients is unanswered, and the only way to
        find out is to keep the ones that recover as well as the ones that do
        not. This costs nothing and needs no destructive action.
        """
        fields["t"] = datetime.datetime.now(datetime.UTC).isoformat()
        with self.ledger.open("a") as fh:
            fh.write(json.dumps(fields) + "\n")

    def observe(self, inst: str, ms: float, start: float | None) -> list[float] | None:
        """Take one sample, returning the excursion's samples once it is a flip."""
        hist = self.history[inst]
        onset = detect.is_flip(hist, ms, self.prev_start.get(inst), start)
        hist.append(ms)
        del hist[:-40]
        self.prev_start[inst] = start

        if onset:
            self.excursion[inst] = [ms]
            log(f"ONSET {inst}: {hist[-2]:.1f}ms -> {ms:.1f}ms")
            self.record(event="onset", instance=inst, ms=ms)
            return None
        if inst not in self.excursion:
            return None

        # In an excursion: either it recovers, or it holds and is a flip.
        if ms <= detect.FLIP_MS:
            samples = self.excursion.pop(inst)
            log(f"  transient on {inst} ended after {len(samples)} sample(s)")
            self.record(event="transient", instance=inst, samples=samples)
            return None
        self.excursion[inst].append(ms)
        if not detect.is_sustained(self.excursion[inst]):
            return None
        return self.excursion.pop(inst)

    def respond(
        self, inst: str, pod: str | None, node: str | None, samples: list[float]
    ) -> bool:
        """Capture a confirmed flip if allowed; True once --once is satisfied."""
        log(f"FLIP CONFIRMED {node or inst}: {samples}")
        self.record(event="flip", instance=inst, node=node, samples=samples)
        if pod is None:
            log(f"  no pod matches {inst}; cannot capture")
            return False
        if self.arguments.node and node not in self.arguments.node:
            log(f"  {node} not in capture list; watching only")
            return False
        if self.arguments.dry_run:
            log("  dry run; not capturing")
            return True
        try:
            capture(pod, node, self.arguments.out, samples[-1])
        except Exception as exc:  # noqa: BLE001 - one failed capture must not end hours of watching
            log(f"  CAPTURE FAILED: {exc}")
            self.record(event="capture_failed", instance=inst, error=str(exc))
            return False
        return True


def main() -> None:
    """Poll every plugin instance until a flip is captured, or forever."""
    ap = argparse.ArgumentParser()
    # captures/ in this checkout, which git ignores; see "Debugging captures"
    # in CLAUDE.md.
    ap.add_argument(
        "--out", type=Path, default=HERE.parent.parent / "captures" / "gdp-flip"
    )
    ap.add_argument(
        "--node",
        action="append",
        help="only capture on this node (repeatable); detect on all",
    )
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="capture one flip, then exit")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    watcher = Watcher(args)
    log(
        f"watching; capture on {args.node or 'any node'}"
        f"{' (dry run)' if args.dry_run else ''}; ledger {watcher.ledger}"
    )

    while True:
        try:
            cur = sample()
        except Exception as exc:  # noqa: BLE001 - a failed query must not end hours of watching
            log(f"query failed, retrying: {exc}")
            time.sleep(args.interval)
            continue
        pods = None
        for inst, (ms, start) in sorted(cur.items()):
            samples = watcher.observe(inst, ms, start)
            if samples is None:
                continue
            if pods is None:
                pods = pod_map()
            pod, node = pods.get(inst, (None, None))
            if watcher.respond(inst, pod, node, samples) and args.once:
                return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
