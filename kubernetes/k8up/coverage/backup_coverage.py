#!/usr/bin/env python3
"""Assert backup coverage per claim and per dump, from the repository itself.

K8up's own signals cannot say this. Its job counters carry no claim label, a
Backup reports Succeeded when restic exits 3 having read nothing (k8up#1032),
and `k8up_schedule_last_job_succeeded` never covers a backup. So this reads the
store -- `restic snapshots` through the rest-server -- and compares it with what
the cluster says should be there.

Scope comes from the cluster API, cluster-wide: every PVC, every pod carrying
`k8up.io/backupcommand`, and every PreBackupPod. Not from git, where six live
claims have no literal declaration, and not from the namespaces that happen to
have a Schedule, which cannot see a namespace created later.

Two outcomes, as in ansible/files/pbs_freshness.py. Failures ping /fail and exit
non-zero; reports print and ride along in the ping's body. An excluded claim, or
a retired application's last snapshot, is a statement rather than a failure --
failing on it would make this the always-on warning docs/README.md warns against.

Restic's count of unreadable files exists only in each backup Job's log, so it
is read from there, and only reports: files deleted mid-scan count too, and a
Job's pods go when K8up prunes its history. See docs/backups.md.
"""

import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

EXCLUDE_ANNOTATION = "k8up.io/backup"
COMMAND_ANNOTATION = "k8up.io/backupcommand"
CONTAINER_ANNOTATION = "k8up.io/backupcommand-container"
EXTENSION_ANNOTATION = "k8up.io/file-extension"

# The full Schedules run nightly from 01:00 UTC and this at :45 past every sixth
# hour, so a limit under 24 h would fail on a healthy estate. 26 h is one night
# plus the slack a long run needs.
VOLUME_MAX_AGE_HOURS = 26
# Dumps run at 01:00 (with `full`), 07:00, 13:00 and 19:00: one six-hour cycle
# plus an hour.
DUMP_MAX_AGE_HOURS = 7
# A claim or producer younger than this has not yet met a nightly run.
NEW_GRACE_HOURS = 26
# Losing more than half of an item between two runs is the signal: a volume
# restic could not read (k8up#1032) or a dump cut short. Below the floor it is
# ordinary churn -- headscale's whole database is 90 KB.
SHRINK_RATIO = 0.5
SHRINK_FLOOR_BYTES = 1 << 20
# Snapshots the repository host takes of its own mounts. Its timer reports them
# to healthchecks itself, and they belong to no claim.
REPOSITORY_HOSTS = frozenset({"restic-repository"})
# The tag the repository host's prune keeps forever. See docs/backups.md.
DECOMMISSIONED = "decommissioned"

# healthchecks keeps the first 100 kB of a ping's body.
PING_BODY_BYTES = 100_000
SERVICE_ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")

_FRACTION = re.compile(r"(\.\d{6})\d+")
_JOB_EVENT = re.compile(
    r"\t(starting backup for folder|starting stdin backup|backup finished)"
    r"\t(\{.*\})\s*$"
)


class Claim(NamedTuple):
    """A PVC, and what its annotations and access modes say about its backup."""

    namespace: str
    name: str
    excluded: bool
    shared: bool
    created: int


class Producer(NamedTuple):
    """Something K8up will stream a dump from, and the path the dump lands at."""

    namespace: str
    path: str
    source: str
    created: int


class Series(NamedTuple):
    """The snapshots of one (host, path), reduced to what the verdicts read.

    Sizes are None for a snapshot restic took without a summary, which it has
    written into every snapshot since 0.17. `previous_*` are None for a series
    of one.
    """

    host: str
    path: str
    newest: int
    files: int | None
    size: int | None
    previous_files: int | None
    previous_size: int | None
    tags: frozenset[str]


def parse_time(text: str) -> int:
    """Read an RFC 3339 time to the second. restic writes nanoseconds."""
    return int(datetime.fromisoformat(_FRACTION.sub(r"\1", text)).timestamp())


def _summary(snapshot: Mapping[str, Any], field: str) -> int | None:
    summary = snapshot.get("summary")
    return None if summary is None else summary.get(field)


def parse_snapshots(text: str | bytes) -> dict[tuple[str, str], Series]:
    """Group `restic snapshots --json` by host and first path.

    K8up gives each item one path, so the first path is the item. The repository
    host's own snapshots carry several, and are keyed by their first.
    """
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for snapshot in json.loads(text):
        key = (snapshot["hostname"], snapshot["paths"][0])
        grouped.setdefault(key, []).append(snapshot)

    held = {}
    for (host, path), snapshots in grouped.items():
        ordered = sorted(snapshots, key=lambda s: parse_time(s["time"]))
        newest = ordered[-1]
        previous = ordered[-2] if len(ordered) > 1 else None
        held[host, path] = Series(
            host=host,
            path=path,
            newest=parse_time(newest["time"]),
            files=_summary(newest, "total_files_processed"),
            size=_summary(newest, "total_bytes_processed"),
            previous_files=None
            if previous is None
            else _summary(previous, "total_files_processed"),
            previous_size=None
            if previous is None
            else _summary(previous, "total_bytes_processed"),
            tags=frozenset(newest.get("tags") or ()),
        )
    return held


def parse_claims(text: str) -> list[Claim]:
    """Read every PVC from the API's list."""
    claims = []
    for item in json.loads(text)["items"]:
        metadata = item["metadata"]
        annotations = metadata.get("annotations") or {}
        claims.append(
            Claim(
                namespace=metadata["namespace"],
                name=metadata["name"],
                excluded=annotations.get(EXCLUDE_ANNOTATION) == "false",
                shared="ReadWriteMany" in item["spec"].get("accessModes", ()),
                created=parse_time(metadata["creationTimestamp"]),
            )
        )
    return claims


def parse_producers(pods_text: str, prebackuppods_text: str) -> list[Producer]:
    """Name every dump K8up will take, and the path it will write it to.

    K8up names a dump /<namespace>-<container><extension>. A PreBackupPod runs
    as a pod carrying the same annotation, so the two are one producer, dated
    from whichever has existed longer.
    """
    found: dict[tuple[str, str], Producer] = {}

    def add(
        namespace: str, container: str, extension: str, source: str, created: int
    ) -> None:
        path = f"/{namespace}-{container}{extension}"
        existing = found.get((namespace, path))
        if existing is None or created < existing.created:
            found[namespace, path] = Producer(namespace, path, source, created)

    for pod in json.loads(pods_text)["items"]:
        metadata = pod["metadata"]
        annotations = metadata.get("annotations") or {}
        if COMMAND_ANNOTATION not in annotations:
            continue
        if pod.get("status", {}).get("phase") in {"Succeeded", "Failed"}:
            continue
        container = annotations.get(
            CONTAINER_ANNOTATION, pod["spec"]["containers"][0]["name"]
        )
        add(
            metadata["namespace"],
            container,
            annotations.get(EXTENSION_ANNOTATION, ""),
            f"pod {metadata['name']}",
            parse_time(metadata["creationTimestamp"]),
        )

    for template in json.loads(prebackuppods_text)["items"]:
        metadata, spec = template["metadata"], template["spec"]
        add(
            metadata["namespace"],
            spec["pod"]["spec"]["containers"][0]["name"],
            spec.get("fileExtension", ""),
            f"PreBackupPod {metadata['name']}",
            parse_time(metadata["creationTimestamp"]),
        )
    return list(found.values())


def parse_job_log(text: str) -> dict[str, int]:
    """Read restic's per-item error count from one K8up backup Job's log.

    An item whose `backup finished` line never came is absent rather than zero:
    a Job that died mid-item has not shown that item to be clean.
    """
    errors = {}
    current = None
    for line in text.splitlines():
        match = _JOB_EVENT.search(line)
        if match is None:
            continue
        event, fields = match.group(1), json.loads(match.group(2))
        if event == "starting backup for folder":
            current = f"/data/{fields['foldername']}"
        elif event == "starting stdin backup":
            current = f"{fields['filename']}{fields.get('extension', '')}"
        elif current is not None:
            errors[current] = int(fields.get("errors", 0))
            current = None
    return errors


def _shrank(held: Series) -> bool:
    return (
        held.size is not None
        and held.previous_size is not None
        and held.previous_size >= SHRINK_FLOOR_BYTES
        and held.size < held.previous_size * SHRINK_RATIO
    )


def _hours(now: int, then: int) -> int:
    return (now - then) // 3600


def _volume_contents(name: str, held: Series) -> tuple[str | None, str | None]:
    """Judge what a current snapshot holds, against the one before it."""
    if held.files == 0:
        if held.previous_files:
            return (
                f"{name}: went empty -- the newest snapshot holds no files, the one "
                f"before held {held.previous_files}. Can the Job still read it?"
            ), None
        return None, f"{name}: in scope and holds nothing"
    if _shrank(held):
        return (
            f"{name}: shrank from {held.previous_size} to {held.size} bytes "
            "between its last two snapshots"
        ), None
    return None, None


def _judge_volume(
    name: str, claim: Claim, held: Series | None, now: int
) -> tuple[str | None, str | None]:
    """Return (problem, report) for one in-scope claim; at most one of each."""
    if held is None:
        if _hours(now, claim.created) < NEW_GRACE_HOURS:
            return None, f"{name}: new, awaiting its first backup"
        return f"{name}: in scope and never backed up", None
    age = _hours(now, held.newest)
    if age > VOLUME_MAX_AGE_HOURS:
        return (
            f"{name}: newest snapshot {age}h old, limit {VOLUME_MAX_AGE_HOURS}h",
            None,
        )
    return _volume_contents(name, held)


def _judge_dump(
    name: str, producer: Producer, held: Series | None, now: int
) -> tuple[str | None, str | None]:
    """Return (problem, report) for one dump. A dump is never rightly empty."""
    if held is None:
        if _hours(now, producer.created) < NEW_GRACE_HOURS:
            return None, f"{name}: new, awaiting its first dump ({producer.source})"
        return f"{name}: never dumped ({producer.source})", None
    age = _hours(now, held.newest)
    if age > DUMP_MAX_AGE_HOURS:
        return f"{name}: newest dump {age}h old, limit {DUMP_MAX_AGE_HOURS}h", None
    if held.size == 0:
        return f"{name}: the newest dump is empty", None
    if _shrank(held):
        return (
            f"{name}: shrank from {held.previous_size} to {held.size} bytes "
            "between its last two dumps"
        ), None
    return None, None


def _orphan_report(held: Series, now: int) -> str:
    age = _hours(now, held.newest)
    if DECOMMISSIONED in held.tags:
        return (
            f"{held.host} {held.path}: decommissioned, newest snapshot {age}h old, "
            "kept by the prune's --keep-tag"
        )
    if held.path.startswith("/data/"):
        return (
            f"{held.host}/{held.path.removeprefix('/data/')}: claim gone, newest "
            f"snapshot {age}h old -- tag it {DECOMMISSIONED} if it is worth keeping"
        )
    return f"{held.host} dump {held.path}: producer gone, newest dump {age}h old"


def classify(
    claims: Iterable[Claim],
    producers: Iterable[Producer],
    held: Mapping[tuple[str, str], Series],
    now: int,
    item_errors: Mapping[tuple[str, str], int],
) -> tuple[list[str], list[str]]:
    """Sort every claim, dump and snapshot series into failures and reports.

    Pure: it takes parsed state and returns strings, so the verdict table can be
    tested without a cluster or a repository.
    """
    problems: list[str] = []
    reports: list[str] = []
    covered = set()

    def record(verdict: tuple[str | None, str | None]) -> None:
        problem, report = verdict
        if problem:
            problems.append(problem)
        if report:
            reports.append(report)

    for claim in sorted(claims, key=lambda c: (c.namespace, c.name)):
        key = (claim.namespace, f"/data/{claim.name}")
        covered.add(key)
        name = f"{claim.namespace}/{claim.name}"
        if claim.excluded:
            reports.append(f'{name}: excluded by k8up.io/backup: "false"')
        elif claim.shared:
            # Every ReadWriteMany claim here is a network share, reached over SMB
            # and already covered by the ZFS layer. One in scope is almost
            # certainly one that joined by K8up's opt-out default -- and the
            # nightly Job will try to read all of it.
            problems.append(
                f"{name}: in scope and ReadWriteMany -- a share that joined the "
                'backup set by default? Annotate it k8up.io/backup: "false".'
            )
        else:
            record(_judge_volume(name, claim, held.get(key), now))

    for producer in sorted(producers, key=lambda p: (p.namespace, p.path)):
        key = (producer.namespace, producer.path)
        covered.add(key)
        record(
            _judge_dump(
                f"{producer.namespace} dump {producer.path}",
                producer,
                held.get(key),
                now,
            )
        )

    for key, series in sorted(held.items()):
        if key in covered or series.host in REPOSITORY_HOSTS:
            continue
        reports.append(_orphan_report(series, now))

    reports.extend(
        f"{namespace} {path}: restic could not read {count} files on its latest run"
        for (namespace, path), count in sorted(item_errors.items())
        if count > 0
    )
    return problems, reports


def _api(path: str) -> str:
    """GET a path from the Kubernetes API as this pod's service account."""
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ["KUBERNETES_SERVICE_PORT"]
    request = urllib.request.Request(
        f"https://{host}:{port}{path}",
        headers={"Authorization": f"Bearer {(SERVICE_ACCOUNT / 'token').read_text()}"},
    )
    context = ssl.create_default_context(cafile=str(SERVICE_ACCOUNT / "ca.crt"))
    # The Request above is built on a literal https:// URL.
    with urllib.request.urlopen(request, context=context, timeout=30) as response:  # noqa: S310
        return response.read().decode()


def collect_snapshots() -> dict[tuple[str, str], Series]:
    """List every snapshot through the rest-server, taking no lock.

    `--no-lock` because append-only accepts a lock but a read needs none, and a
    lock file left by a killed run would be one more thing to clean up.
    """
    result = subprocess.run(
        ["restic", "snapshots", "--json", "--no-lock", "--no-cache"],
        capture_output=True,
        check=False,
        timeout=300,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode().strip()
        message = f"restic snapshots exited {result.returncode}: {stderr}"
        raise RuntimeError(message)
    return parse_snapshots(result.stdout)


def collect_item_errors() -> dict[tuple[str, str], int]:
    """Read each item's error count from the newest finished Job that took it."""
    query = urllib.parse.urlencode({"labelSelector": "k8up.io/type=backup"})
    newest: dict[tuple[str, str], tuple[int, int]] = {}
    for job in json.loads(_api(f"/apis/batch/v1/jobs?{query}"))["items"]:
        namespace = job["metadata"]["namespace"]
        pods_query = urllib.parse.urlencode(
            {"labelSelector": f"batch.kubernetes.io/job-name={job['metadata']['name']}"}
        )
        pods = json.loads(_api(f"/api/v1/namespaces/{namespace}/pods?{pods_query}"))
        for pod in pods["items"]:
            # A running pod's log is still being written.
            if pod["status"].get("phase") not in {"Succeeded", "Failed"}:
                continue
            started = parse_time(
                pod["status"].get("startTime") or pod["metadata"]["creationTimestamp"]
            )
            log = _api(
                f"/api/v1/namespaces/{namespace}/pods/{pod['metadata']['name']}/log"
            )
            for path, count in parse_job_log(log).items():
                key = (namespace, path)
                if key not in newest or started > newest[key][0]:
                    newest[key] = (started, count)
    return {key: count for key, (_, count) in newest.items()}


def ping(url: str, body: str, *, failed: bool) -> None:
    """Report to the healthchecks check, never raising if it cannot be reached.

    A monitor that cannot be reached must not turn a correct assertion into a
    crash; the check goes red on its own period instead.
    """
    if not url:
        print("backup-coverage: no healthchecks URL, not pinging", file=sys.stderr)
        return
    try:
        # A malformed URL -- the Secret's placeholder, say -- raises ValueError
        # here, before any connection is tried, so the Request is built inside
        # the try too.
        request = urllib.request.Request(  # noqa: S310 -- the check's https URL
            url + ("/fail" if failed else ""),
            data=body.encode()[:PING_BODY_BYTES],
            method="POST",
        )
        urllib.request.urlopen(request, timeout=10).close()  # noqa: S310
    except (OSError, ValueError) as error:
        print(f"backup-coverage: could not ping healthchecks: {error}", file=sys.stderr)


def main() -> int:
    """Assert coverage, ping healthchecks, and return the exit code."""
    url = os.environ.get("HC_PING_URL", "")
    try:
        claims = parse_claims(_api("/api/v1/persistentvolumeclaims"))
        producers = parse_producers(
            _api("/api/v1/pods"), _api("/apis/k8up.io/v1/prebackuppods")
        )
        held = collect_snapshots()
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
    ) as error:
        message = f"backup-coverage: {error}"
        print(message, file=sys.stderr)
        ping(url, message, failed=True)
        return 1

    extra = []
    try:
        item_errors = collect_item_errors()
    except (OSError, ValueError, KeyError) as error:
        item_errors = {}
        extra.append(f"could not read restic's per-item error counts: {error}")

    problems, reports = classify(
        claims, producers, held, now=int(time.time()), item_errors=item_errors
    )
    reports.extend(extra)

    in_scope = sum(1 for c in claims if not c.excluded)
    # The error counts are read from Job logs K8up prunes, so say how many were
    # found: none read and all clean would otherwise look the same.
    summary = (
        f"{in_scope} claims in scope, {len(producers)} dumps, "
        f"{len(problems)} failing, {len(reports)} reported, "
        f"error counts read for {len(item_errors)} items"
    )
    print(summary)
    for report in reports:
        print(report)
    if problems:
        print("\n".join(problems), file=sys.stderr)
    ping(url, "\n".join([summary, *problems, *reports]), failed=bool(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
