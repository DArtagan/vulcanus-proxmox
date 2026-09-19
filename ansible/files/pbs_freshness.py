#!/usr/bin/env python3
"""Assert PBS coverage from the datastore, not from the vzdump job's exit code.

A vzdump run carrying 8 of 13 guests exits 0, exactly as syncoid did for the
seven months nobody noticed. Only a per-guest assertion against the store
catches it, so this reads three things and compares them: the job's scope, the
live guest list, and the groups PBS actually holds.

Scope is read from /etc/pve/jobs.cfg at runtime rather than hardcoded, so a
guest created tomorrow is expected without anyone remembering to edit this. A
guest that is merely absent from the check's own list cannot alert as missing.

Two outcomes, not one. Failures ping /fail; reports only print. A retired
guest's group is frozen forever by nothing arriving, so failing on it would
make this an always-on warning -- see docs/README.md -- while a live in-scope
guest going stale is a real backup failure. The reports are what the retention
decision depends on: without them "someone inspects and decides" has nothing to
prompt it.

Managed by ansible/backup-monitoring.yaml. The mini-nas copy of the reporting
helpers is in ~/repositories/mini-nas/modules/backup_monitoring; this check has
no counterpart there, because PBS runs only here.
"""

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

HC_PING = Path("/usr/local/bin/hc-ping")
JOBS_CFG = Path("/etc/pve/jobs.cfg")
VMLIST = Path("/etc/pve/.vmlist")
STORAGE_CFG = Path("/etc/pve/storage.cfg")
STORAGE_PRIV = Path("/etc/pve/priv/storage")

# vzdump runs at 10:00 UTC and this at 12:30, so a limit under 24 h would fail
# every day on a healthy estate. 26 h is one run plus slack.
DEFAULT_MAX_AGE_HOURS = 26

# Reuse shows as the identities at the two ends of a group differing, and a
# single backup has only one end.
BACKUPS_TO_SHOW_REUSE = 2


class Guest(NamedTuple):
    """A live guest, and whether the vzdump job's scope takes it in."""

    vmid: str
    guest_type: str
    in_scope: bool


class Group(NamedTuple):
    """A PBS backup group, reduced to what the verdict table reads.

    `newest` is None for a group holding no backups at all, which is a real
    state rather than a parse failure: vm/107 has existed with count 0 since PBS
    was built. Identities are None when the config blob could not be read.
    """

    name: str
    count: int
    newest: int | None
    oldest_identity: str | None
    newest_identity: str | None


class NoActiveJobError(Exception):
    """No enabled `all 1` vzdump job, so there is no scope to assert against."""


def _sections(text: str, prefix: str) -> list[dict[str, str]]:
    """Parse PVE's flat config format: `<type>: <id>` then indented `key value`.

    Shared by jobs.cfg and storage.cfg, which are the same shape.

    Returns a list rather than generating one. A section's keys arrive on the
    lines *after* its header, so a generator would hand out each dict while it
    was still empty and any caller filtering on a key -- `enabled`, say --
    would read every section as unset.
    """
    sections = []
    section = None
    for line in text.splitlines():
        if not line.startswith((" ", "\t")):
            head, _, ident = line.partition(":")
            section = {"id": ident.strip()} if head.strip() == prefix else None
            if section is not None:
                sections.append(section)
            continue
        if section is None:
            continue
        key, _, value = line.strip().partition(" ")
        section[key] = value.strip()
    return sections


def _active_vzdump_job(text: str) -> dict[str, str]:
    """Return the enabled vzdump job's settings, as a flat dict.

    Raising rather than returning an empty scope is deliberate: a disabled or
    missing job means nothing is being backed up, and reporting full coverage
    for that is the failure this whole check exists to prevent.
    """
    jobs = [job for job in _sections(text, "vzdump") if job.get("enabled", "1") != "0"]
    if not jobs:
        message = f"no enabled vzdump job in {JOBS_CFG}"
        raise NoActiveJobError(message)
    job = jobs[0]
    if job.get("all") != "1":
        message = (
            "the vzdump job does not use `all 1`, so its scope is not all-minus-exclude"
        )
        raise NoActiveJobError(message)
    return job


def parse_excluded(text: str) -> set[str]:
    """Return the VMIDs the vzdump job skips. Scope is every live guest but these."""
    excluded = _active_vzdump_job(text).get("exclude", "")
    return {vmid for vmid in excluded.split(",") if vmid}


def parse_vmlist(text: str) -> dict[str, str]:
    """Read live guests as {vmid: "qemu"|"lxc"}, from PVE's own cluster list.

    One file gives both the id and the type, so PBS group names are derived
    rather than guessed -- `qm list` plus `pct list` would need two calls and
    still leave the mapping implicit.
    """
    return {vmid: entry["type"] for vmid, entry in json.loads(text)["ids"].items()}


def group_name(vmid: str, guest_type: str) -> str:
    """Name the PBS group a guest's backups land in."""
    return ("ct/" if guest_type == "lxc" else "vm/") + vmid


def parse_identity(conf_text: str, guest_type: str) -> str | None:
    """Read the guest's own identity, as carried in its backed-up config.

    A VM's smbios1 UUID is regenerated when a new guest is built at the same
    VMID and stable for that machine's life, which is what lets reuse be
    detected retroactively from the group's contents alone.

    Containers have no smbios1. The closest intrinsic equivalent is the
    interface's MAC, which PVE generates per container and which survives a
    restore of the same container.
    """
    field = "uuid" if guest_type != "lxc" else "hwaddr"
    key = "smbios1" if guest_type != "lxc" else "net0"
    for line in conf_text.splitlines():
        name, _, value = line.partition(":")
        if name.strip() != key:
            continue
        match = re.search(rf"\b{field}=([^,\s]+)", value)
        if match:
            return match.group(1)
    return None


def _excluded_group_state(group: Group | None, now: int) -> str:
    """Describe the group of a guest the job excludes, which nothing refreshes."""
    if group is None:
        return "no group"
    if group.count == 0:
        return "an empty group"
    return f"a group frozen {(now - group.newest) // 3600}h ago"


def _reuse_reports(groups: Mapping[str, Group]) -> list[str]:
    """Report each group whose oldest and newest backups are different machines."""
    reports = []
    for name, group in sorted(groups.items()):
        ends = (group.oldest_identity, group.newest_identity)
        if group.count >= BACKUPS_TO_SHOW_REUSE and all(ends) and ends[0] != ends[1]:
            reports.append(
                f"{name}: VMID reused -- oldest backup is {ends[0]}, "
                f"newest is {ends[1]}; "
                "the earlier machine is being evicted one backup per run"
            )
    return reports


def classify(
    guests: Mapping[str, Guest],
    groups: Mapping[str, Group],
    now: int,
    max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
) -> tuple[list[str], list[str]]:
    """Sort every guest and group into failures and reports.

    Pure: it takes parsed state and returns strings, so the verdict table can
    be tested without a datastore. Everything that talks to PBS or PVE lives
    below this line.
    """
    problems = []
    reports = []
    covered = set()

    for vmid, guest in sorted(guests.items(), key=lambda item: int(item[0])):
        name = group_name(vmid, guest.guest_type)
        covered.add(name)
        group = groups.get(name)
        held = group is not None and group.count > 0

        if not guest.in_scope:
            state = _excluded_group_state(group, now)
            reports.append(f"{name}: live but excluded from the job, with {state}")
            continue

        if not held:
            problems.append(f"{name}: in scope and never backed up")
            continue

        age = (now - group.newest) // 3600
        if age > max_age_hours:
            problems.append(f"{name}: newest backup {age}h old, limit {max_age_hours}h")

    for name, group in sorted(groups.items()):
        if name in covered:
            continue
        age = (
            "never" if group.newest is None else f"{(now - group.newest) // 3600}h ago"
        )
        reports.append(
            f"{name}: guest gone, group frozen at {group.count} backups, newest {age}"
        )

    reports.extend(_reuse_reports(groups))
    return problems, reports


def pbs_environment() -> dict[str, str]:
    """Build credentials for proxmox-backup-client, from PVE's own storage entry.

    Read rather than duplicated into this repo: the datastore, its fingerprint
    and its password already live on the host, and a second copy would be a
    second thing to rotate. The backups are unencrypted (every file reports
    crypt-mode "none"), so the datastore password is the whole credential.
    """
    entries = _sections(STORAGE_CFG.read_text(), "pbs")
    if not entries:
        message = f"no pbs storage entry in {STORAGE_CFG}"
        raise RuntimeError(message)
    storage = entries[0]
    password = (STORAGE_PRIV / f"{storage['id']}.pw").read_text().strip()
    repository = f"{storage['username']}@{storage['server']}:{storage['datastore']}"
    return {
        **os.environ,
        "PBS_REPOSITORY": repository,
        "PBS_PASSWORD": password,
        "PBS_FINGERPRINT": storage["fingerprint"],
    }


def _client(args: list[str], env: Mapping[str, str]) -> bytes:
    """Run proxmox-backup-client and return what it wrote to stdout."""
    result = subprocess.run(
        ["proxmox-backup-client", *args],
        env=env,
        capture_output=True,
        check=True,
    )
    return result.stdout


def _client_json(args: list[str], env: Mapping[str, str]) -> list[dict[str, Any]]:
    """Run a proxmox-backup-client listing and parse its JSON."""
    return json.loads(_client([*args, "--output-format", "json"], env))


def _iso(epoch: int) -> str:
    """Format an epoch the way proxmox-backup-client names a snapshot."""
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot_identity(
    snapshot: str, blob: str, pve_type: str, env: Mapping[str, str]
) -> str | None:
    """Read the identity from one snapshot's config blob, or None if unreadable.

    An unreadable end is not evidence of two machines, so it reads as unknown
    rather than failing the whole check.
    """
    try:
        raw = _client(["restore", snapshot, blob, "-"], env)
        return parse_identity(raw.decode("utf-8", "replace"), pve_type)
    except (subprocess.CalledProcessError, UnicodeError):
        return None


def collect_groups(env: Mapping[str, str]) -> dict[str, Group]:
    """Read every group in the datastore, with the identity at each end.

    Two blob reads per group, ~0.25 s each, and only where there are at least
    two backups to compare -- a single backup cannot show reuse.
    """
    groups = {}
    for entry in _client_json(["list"], env):
        guest_type, vmid = entry["backup-type"], entry["backup-id"]
        name = f"{guest_type}/{vmid}"
        snapshots = _client_json(["snapshots", name], env)
        times = sorted(snapshot["backup-time"] for snapshot in snapshots)
        blob = "pct.conf.blob" if guest_type == "ct" else "qemu-server.conf.blob"
        pve_type = "lxc" if guest_type == "ct" else "qemu"

        identities = [None, None]
        if len(times) >= BACKUPS_TO_SHOW_REUSE:
            identities = [
                _snapshot_identity(f"{name}/{_iso(when)}", blob, pve_type, env)
                for when in (times[0], times[-1])
            ]

        groups[name] = Group(
            name=name,
            count=len(times),
            newest=times[-1] if times else None,
            oldest_identity=identities[0],
            newest_identity=identities[1],
        )
    return groups


def ping(check: str, suffix: str = "") -> None:
    """Report to a healthchecks check, never raising if it cannot be reached.

    `-` in spirit: a monitor that cannot be reached must not turn a correct
    assertion into a crash. The check goes red on its own period instead.
    """
    subprocess.run([HC_PING, check, *([suffix] if suffix else [])], check=False)


def main(argv: list[str]) -> int:
    """Assert coverage, ping the check named in argv, and return the exit code."""
    check, *limit = argv[1:]
    max_age_hours = int(limit[0]) if limit else DEFAULT_MAX_AGE_HOURS

    try:
        excluded = parse_excluded(JOBS_CFG.read_text())
        live = parse_vmlist(VMLIST.read_text())
        env = pbs_environment()
        groups = collect_groups(env)
    except (
        OSError,
        NoActiveJobError,
        RuntimeError,
        subprocess.CalledProcessError,
        ValueError,
    ) as error:
        print(f"pbs-freshness: {error}", file=sys.stderr)
        ping(check, "/fail")
        return 1

    guests = {
        vmid: Guest(vmid=vmid, guest_type=guest_type, in_scope=vmid not in excluded)
        for vmid, guest_type in live.items()
    }
    problems, reports = classify(
        guests,
        groups,
        now=int(datetime.now(UTC).timestamp()),
        max_age_hours=max_age_hours,
    )

    for report in reports:
        print(report)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        ping(check, "/fail")
        return 1
    ping(check)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
