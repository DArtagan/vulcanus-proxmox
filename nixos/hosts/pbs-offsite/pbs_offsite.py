"""PBS #2's two readers of the primary: the protection mirror, and freshness.

Both compare the primary's snapshots with this store's, which is why they share
a file. The primary is read over its API with the sync token, the one
credential PBS #2 holds for it, which grants reading and nothing else. This
store is read and written through `proxmox-backup-debug api`, which calls the
local API as root.

Where the primary is, and where its snapshots land here, are read from PBS's
own sync job and remote rather than repeated as arguments, so nothing here can
disagree with the sync that copies them.

`mirror` makes each snapshot's protected flag here match the primary's, for
every snapshot both hold. A pull copies a snapshot but never its flag, so
without this, archiving on the primary would not reach the offsite copy and
PBS #2's prune would evict what the primary keeps. A snapshot protected here
that the primary no longer holds is reported and left alone: there is nothing
left to mirror it from.

`freshness` asserts the offsite copy from the two stores rather than from the
sync's exit code, and pings healthchecks with the verdict.
"""

import hashlib
import http.client
import json
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

SYNC_JOB = "pull-primary"
DEFAULT_PORT = 8007

# vzdump runs at 10:00 or 11:00 UTC and the sync at 12:00. A snapshot this old
# has had at least one sync since it was taken, whatever the season.
SYNC_LIMIT_HOURS = 26

# The weekly verify runs on Sunday at 14:00, so a snapshot synced on Monday
# waits six days and two hours for it; a day's slack covers a long verify.
VERIFY_LIMIT_HOURS = 8 * 24


class Snapshot(NamedTuple):
    """One snapshot, reduced to what the comparisons read."""

    group: str
    time: int
    protected: bool
    verified: str | None


class Endpoints(NamedTuple):
    """Where the primary is, and where its snapshots land here."""

    host: str
    port: int
    auth_id: str
    fingerprint: str
    remote_store: str
    store: str
    ns: str


def parse_snapshots(items: Iterable[Mapping[str, Any]]) -> list[Snapshot]:
    """Read PBS's snapshot listing, as both the API and the debug CLI return it."""
    return [
        Snapshot(
            group=f"{item['backup-type']}/{item['backup-id']}",
            time=item["backup-time"],
            protected=bool(item.get("protected", False)),
            verified=(item.get("verification") or {}).get("state"),
        )
        for item in items
    ]


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _name(snapshot: Snapshot) -> str:
    return f"{snapshot.group}/{_iso(snapshot.time)}"


def plan_mirror(
    primary: Iterable[Snapshot], local: Iterable[Snapshot]
) -> tuple[list[tuple[Snapshot, bool]], list[str]]:
    """Return the flags to set here, and what to report.

    Pure, so the table of cases is testable without either store.
    """
    remote = {(s.group, s.time): s for s in primary}
    changes = []
    reports = []
    for snapshot in sorted(local, key=lambda s: (s.group, s.time)):
        source = remote.get((snapshot.group, snapshot.time))
        if source is None:
            if snapshot.protected:
                reports.append(
                    f"{_name(snapshot)}: protected here, and the primary no longer "
                    "holds it"
                )
            continue
        if source.protected != snapshot.protected:
            changes.append((snapshot, source.protected))
    return changes, reports


def assess(
    primary: Iterable[Snapshot],
    local: Iterable[Snapshot],
    now: int,
    sync_limit_hours: int = SYNC_LIMIT_HOURS,
    verify_limit_hours: int = VERIFY_LIMIT_HOURS,
) -> tuple[list[str], list[str]]:
    """Sort the two stores' state into failures and reports.

    Each group's newest snapshot on the primary that is past the sync limit has
    to be here. Comparing that snapshot, rather than ages, lets a frozen group
    pass with nothing new to copy, while a live group fails once a snapshot
    taken after the sync stopped crosses the limit.

    Verification is this store's own: a pull strips the primary's verify state
    from what it copies. A failed verify fails at once; an unverified snapshot
    fails once the weekly verify should have reached it.
    """
    primary = list(primary)
    local = list(local)
    problems = []
    reports = []

    held = {(s.group, s.time) for s in local}
    sync_cutoff = now - sync_limit_hours * 3600
    by_group: dict[str, list[Snapshot]] = {}
    for snapshot in primary:
        by_group.setdefault(snapshot.group, []).append(snapshot)

    for group, snapshots in sorted(by_group.items()):
        due = [s for s in snapshots if s.time <= sync_cutoff]
        if not due:
            continue
        newest = max(due, key=lambda s: s.time)
        if (group, newest.time) not in held:
            age = (now - newest.time) // 3600
            problems.append(
                f"{_name(newest)}: on the primary {age}h, not copied here "
                f"(limit {sync_limit_hours}h)"
            )

    verify_cutoff = now - verify_limit_hours * 3600
    for snapshot in sorted(local, key=lambda s: (s.group, s.time)):
        if snapshot.verified == "failed":
            problems.append(f"{_name(snapshot)}: verification failed")
        elif snapshot.verified is None and snapshot.time <= verify_cutoff:
            problems.append(
                f"{_name(snapshot)}: unverified after {verify_limit_hours}h"
            )

    for group in sorted({s.group for s in local} - by_group.keys()):
        count = sum(1 for s in local if s.group == group)
        reports.append(
            f"{group}: {count} snapshots here, none on the primary -- kept until "
            "forgotten here too"
        )
    return problems, reports


def _manager_json(*args: str) -> Any:  # noqa: ANN401 -- PBS's JSON, whatever its shape
    result = subprocess.run(
        ["proxmox-backup-manager", *args, "--output-format", "json"],
        capture_output=True,
        check=True,
        text=True,
    )
    return json.loads(result.stdout)


def read_endpoints() -> Endpoints:
    """Read the sync job and its remote, as PBS itself holds them."""
    job = _manager_json("sync-job", "show", SYNC_JOB)
    remote = _manager_json("remote", "show", job["remote"])
    return Endpoints(
        host=remote["host"],
        port=int(remote.get("port", DEFAULT_PORT)),
        auth_id=remote["auth-id"],
        fingerprint=remote["fingerprint"],
        remote_store=job["remote-store"],
        store=job["store"],
        ns=job.get("ns", ""),
    )


def _pinned_connection(endpoints: Endpoints) -> http.client.HTTPSConnection:
    """Connect to the primary, trusting its certificate by fingerprint alone.

    PBS's certificate is self-signed, so a CA cannot vouch for it; the pinned
    fingerprint is what PVE and PBS's own remote both trust, and the check runs
    before any credential is sent.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(
        endpoints.host, endpoints.port, context=context, timeout=60
    )
    connection.connect()
    der = connection.sock.getpeercert(binary_form=True)
    seen = hashlib.sha256(der).hexdigest()
    wanted = endpoints.fingerprint.replace(":", "").lower()
    if seen != wanted:
        connection.close()
        message = f"{endpoints.host}'s certificate is {seen}, not the remote's {wanted}"
        raise ssl.SSLError(message)
    return connection


def primary_snapshots(endpoints: Endpoints, secret: str) -> list[Snapshot]:
    """List the primary's snapshots over its API, as the sync token."""
    connection = _pinned_connection(endpoints)
    store = urllib.parse.quote(endpoints.remote_store, safe="")
    try:
        connection.request(
            "GET",
            f"/api2/json/admin/datastore/{store}/snapshots",
            headers={
                "Authorization": f"PBSAPIToken={endpoints.auth_id}:{secret}",
            },
        )
        response = connection.getresponse()
        body = response.read()
    finally:
        connection.close()
    if response.status != http.HTTPStatus.OK:
        message = f"the primary answered {response.status} {response.reason}"
        raise OSError(message)
    return parse_snapshots(json.loads(body)["data"])


def _debug_api(method: str, path: str, *args: str) -> str:
    result = subprocess.run(
        ["proxmox-backup-debug", "api", method, path, *args],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout


def local_snapshots(endpoints: Endpoints) -> list[Snapshot]:
    """List this store's copies of the primary's snapshots."""
    store = endpoints.store
    out = _debug_api(
        "get",
        f"/admin/datastore/{store}/snapshots",
        "--ns",
        endpoints.ns,
        "--output-format",
        "json",
    )
    return parse_snapshots(json.loads(out))


def set_protected(endpoints: Endpoints, snapshot: Snapshot, protected: bool) -> None:  # noqa: FBT001 -- the flag is the value being set
    """Set one snapshot's protected flag here."""
    backup_type, backup_id = snapshot.group.split("/", 1)
    _debug_api(
        "set",
        f"/admin/datastore/{endpoints.store}/protected",
        "--ns",
        endpoints.ns,
        "--backup-type",
        backup_type,
        "--backup-id",
        backup_id,
        "--backup-time",
        str(snapshot.time),
        "--protected",
        str(protected).lower(),
    )


def mirror(secret: str) -> int:
    """Mirror the primary's protected flags onto this store."""
    endpoints = read_endpoints()
    changes, reports = plan_mirror(
        primary_snapshots(endpoints, secret), local_snapshots(endpoints)
    )
    for report in reports:
        print(report)
    for snapshot, protected in changes:
        set_protected(endpoints, snapshot, protected)
        print(f"{_name(snapshot)}: {'protected' if protected else 'unprotected'}")
    return 0


def ping(url: str, suffix: str = "") -> None:
    """Report to healthchecks, never raising: a lost ping turns the check red."""
    try:
        with urllib.request.urlopen(url + suffix, timeout=10):  # noqa: S310 -- an https:// URL from our own secret
            pass
    except OSError as error:
        print(f"could not ping healthchecks: {error}", file=sys.stderr)


def freshness(secret: str, url: str) -> int:
    """Assert the offsite copy, and ping the check with the verdict."""
    try:
        endpoints = read_endpoints()
        primary = primary_snapshots(endpoints, secret)
        local = local_snapshots(endpoints)
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        print(f"pbs-offsite-freshness: {error}", file=sys.stderr)
        ping(url, "/fail")
        return 1

    problems, reports = assess(primary, local, now=int(datetime.now(UTC).timestamp()))
    for report in reports:
        print(report)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        ping(url, "/fail")
        return 1
    ping(url)
    return 0


def main(argv: list[str]) -> int:
    """Run `mirror <secret-file>` or `freshness <secret-file> <url-file>`."""
    command, secret_file, *rest = argv[1:]
    secret = Path(secret_file).read_text().strip()
    if command == "mirror":
        return mirror(secret)
    if command == "freshness":
        url = Path(rest[0]).read_text().strip()
        if not url.startswith("https://"):
            print(f"{rest[0]} holds no https:// URL", file=sys.stderr)
            return 1
        return freshness(secret, url)
    print(f"unknown command {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
