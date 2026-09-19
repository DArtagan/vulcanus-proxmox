"""Tests for the PBS freshness assertion.

Run with `python3 -m unittest discover ansible/files`.

The check's whole job is a classification, and the expensive mistakes are all
misclassifications rather than crashes: a frozen group that fails turns the
check into the always-on warning docs/README.md warns against, and a live
in-scope guest that only reports is a backup failure nobody hears about. So the
verdict table is tested directly, as a pure function over parsed inputs, with
every PBS and PVE call kept at the edges where these tests do not reach.

The parsers get tests of their own because all three inputs are formats nothing
else in this repo reads: /etc/pve/jobs.cfg, /etc/pve/.vmlist and the config
blob stored inside each backup.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pbs_freshness  # noqa: E402

NOW = 1_789_000_000
FRESH = NOW - 3600
STALE = NOW - (40 * 3600)

# The three inputs, written the way PVE writes them: tab-indented sections in
# jobs.cfg, and one `key: value` per line in a config blob.
JOBS_CFG = """vzdump: 9af0fe23b91fd64972f0d7b4a414b8c912dbf200:1
	schedule 4:00
	all 1
	enabled 1
	exclude 100,101,106,107
	mode snapshot
	storage pbs
"""

VM_CONFIG_BLOB = """boot: order=scsi0;net0
name: rancheros
scsi0: local-zfs:vm-100-disk-0,size=256G
smbios1: uuid=5e726013-8241-44e0-9657-701d7cdddb25
sockets: 1
"""

CT_CONFIG_BLOB = """arch: amd64
hostname: wireguard
net0: name=eth0,bridge=vmbr0,hwaddr=BC:24:11:22:33:26,ip=192.168.0.103/24,type=veth
rootfs: local-zfs:subvol-103-disk-0,size=8G
"""


def guest(vmid, guest_type="qemu", in_scope=True):
    return pbs_freshness.Guest(vmid=vmid, guest_type=guest_type, in_scope=in_scope)


def group(
    name,
    count=30,
    newest=FRESH,
    oldest_identity="uuid-a",
    newest_identity="uuid-a",
    prior_count=None,
):
    return pbs_freshness.Group(
        name=name,
        count=count,
        newest=newest,
        oldest_identity=oldest_identity,
        newest_identity=newest_identity,
        prior_count=prior_count,
    )


def classify(guests, groups):
    return pbs_freshness.classify(guests, groups, now=NOW)


class ScopeParsing(unittest.TestCase):
    """/etc/pve/jobs.cfg — `all 1` minus `exclude`, read rather than hardcoded."""

    JOB = JOBS_CFG

    def test_excluded_guests_are_out_of_scope(self):
        self.assertEqual(
            pbs_freshness.parse_excluded(self.JOB), {"100", "101", "106", "107"}
        )

    def test_a_job_with_no_exclude_excludes_nothing(self):
        text = "vzdump: abc:1\n\tall 1\n\tstorage pbs\n"
        self.assertEqual(pbs_freshness.parse_excluded(text), set())

    def test_a_disabled_job_is_not_read_as_scope(self):
        # A job set `enabled 0` backs nothing up, so treating its exclude list
        # as the scope would report full coverage for a cluster with none.
        text = "vzdump: abc:1\n\tall 1\n\tenabled 0\n\texclude 107\n"
        with self.assertRaises(pbs_freshness.NoActiveJob):
            pbs_freshness.parse_excluded(text)

    def test_no_vzdump_job_at_all_is_an_error_not_an_empty_scope(self):
        with self.assertRaises(pbs_freshness.NoActiveJob):
            pbs_freshness.parse_excluded("replication: foo\n\tschedule 4:00\n")


class GuestListParsing(unittest.TestCase):
    """/etc/pve/.vmlist gives id and type together, so group names are derived."""

    VMLIST = """{
"version": 109,
"ids": {
"910": { "node": "vulcanus", "type": "qemu", "version": 116 },
"104": { "node": "vulcanus", "type": "lxc", "version": 102 },
"107": { "node": "vulcanus", "type": "qemu", "version": 6 }}

}"""

    def test_ids_carry_their_type(self):
        self.assertEqual(
            pbs_freshness.parse_vmlist(self.VMLIST),
            {"910": "qemu", "104": "lxc", "107": "qemu"},
        )

    def test_group_name_follows_the_type(self):
        self.assertEqual(pbs_freshness.group_name("910", "qemu"), "vm/910")
        self.assertEqual(pbs_freshness.group_name("104", "lxc"), "ct/104")


class IdentityParsing(unittest.TestCase):
    """A guest's identity, read out of the config blob stored in the backup."""

    VM_CONF = VM_CONFIG_BLOB
    CT_CONF = CT_CONFIG_BLOB

    def test_a_vm_is_identified_by_its_smbios_uuid(self):
        self.assertEqual(
            pbs_freshness.parse_identity(self.VM_CONF, "qemu"),
            "5e726013-8241-44e0-9657-701d7cdddb25",
        )

    def test_a_container_is_identified_by_its_interface_address(self):
        # Containers carry no smbios1, so the MAC is the stand-in: regenerated
        # for a new container at the same ID, stable for that container's life.
        self.assertEqual(
            pbs_freshness.parse_identity(self.CT_CONF, "lxc"), "BC:24:11:22:33:26"
        )

    def test_a_config_with_no_identity_reads_as_unknown_rather_than_crashing(self):
        self.assertIsNone(
            pbs_freshness.parse_identity("cores: 1\nmemory: 512\n", "qemu")
        )


class VerdictTable(unittest.TestCase):
    """The five rows of the spec's table, plus the empty group measured on vm/107."""

    def test_live_in_scope_and_current_is_silent(self):
        problems, reports = classify({"900": guest("900")}, {"vm/900": group("vm/900")})
        self.assertEqual(problems, [])
        self.assertEqual(reports, [])

    def test_live_in_scope_and_stale_fails(self):
        problems, reports = classify(
            {"900": guest("900")}, {"vm/900": group("vm/900", newest=STALE)}
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("vm/900", problems[0])
        self.assertEqual(reports, [])

    def test_live_in_scope_with_no_group_fails(self):
        problems, _ = classify({"900": guest("900")}, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("never", problems[0].lower())

    def test_live_in_scope_with_an_empty_group_fails(self):
        # vm/107 is exactly this shape: a group with count 0 and no files. It
        # must not read as coverage, and must not crash the age comparison.
        problems, _ = classify(
            {"900": guest("900")}, {"vm/900": group("vm/900", count=0, newest=None)}
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("never", problems[0].lower())

    def test_a_guest_that_is_gone_reports_and_never_fails(self):
        problems, reports = classify({}, {"vm/200": group("vm/200", newest=STALE)})
        self.assertEqual(problems, [])
        self.assertEqual(len(reports), 1)
        self.assertIn("vm/200", reports[0])

    def test_live_but_excluded_reports_whatever_its_group_looks_like(self):
        for name, grp in (
            ("frozen", group("vm/101", newest=STALE)),
            ("empty", group("vm/101", count=0, newest=None)),
            ("absent", None),
        ):
            with self.subTest(group=name):
                groups = {} if grp is None else {grp.name: grp}
                problems, reports = classify(
                    {"101": guest("101", in_scope=False)}, groups
                )
                self.assertEqual(problems, [])
                self.assertEqual(len(reports), 1)

    def test_a_reused_vmid_fails(self):
        # Unlike every other non-failure row, reuse is bounded: it clears on its
        # own once keep-last has evicted the last old backup. A self-clearing
        # condition can be an alert without becoming an always-on warning.
        problems, reports = classify(
            {"900": guest("900")},
            {
                "vm/900": group(
                    "vm/900", oldest_identity="uuid-a", newest_identity="uuid-b"
                )
            },
        )
        self.assertEqual(reports, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("reused", problems[0].lower())

    def test_reuse_fails_even_while_the_guest_is_healthy(self):
        # The eviction is silent and the group looks fine from the live guest
        # list alone; that is the entire reason this row exists.
        problems, _ = classify(
            {"900": guest("900")},
            {
                "vm/900": group(
                    "vm/900", newest=FRESH, oldest_identity="a", newest_identity="b"
                )
            },
        )
        self.assertTrue(any("reused" in p.lower() for p in problems))

    def test_the_failure_names_how_long_is_left_to_decide(self):
        # The decision-relevant number is how many backups of the earlier
        # machine survive, because the job evicts one per run.
        problems, _ = classify(
            {"900": guest("900")},
            {
                "vm/900": group(
                    "vm/900",
                    count=30,
                    oldest_identity="uuid-old",
                    newest_identity="uuid-new",
                    prior_count=12,
                )
            },
        )
        self.assertIn("12 of 30", problems[0])
        self.assertIn("12 more", problems[0])
        self.assertIn("uuid-old", problems[0])

    def test_an_uncounted_reuse_still_fails(self):
        # Counting costs a blob read per snapshot and can come back empty. The
        # failure must not depend on it.
        problems, _ = classify(
            {"900": guest("900")},
            {
                "vm/900": group(
                    "vm/900", oldest_identity="a", newest_identity="b", prior_count=None
                )
            },
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("reused", problems[0].lower())

    def test_a_single_backup_cannot_show_reuse(self):
        problems, reports = classify(
            {"900": guest("900")},
            {
                "vm/900": group(
                    "vm/900", count=1, oldest_identity="a", newest_identity="b"
                )
            },
        )
        self.assertEqual(problems, [])
        self.assertEqual(reports, [])

    def test_an_unreadable_identity_does_not_fail(self):
        # A blob that fails to parse gives None at both ends; two unknowns are
        # not evidence of two machines.
        problems, reports = classify(
            {"900": guest("900")},
            {"vm/900": group("vm/900", oldest_identity=None, newest_identity=None)},
        )
        self.assertEqual(problems, [])
        self.assertEqual(reports, [])


class TheEstateAsMeasured(unittest.TestCase):
    """The 2026-09-18 baseline, as a regression fixture.

    Six guests in scope and current, five reports, zero failures. Phase 1 moves
    this to four reports and then to one; pinning the starting point is what
    makes those numbers mean something.
    """

    def setUp(self):
        live = {
            "103": ("lxc", True),
            "104": ("lxc", True),
            "105": ("lxc", True),
            "900": ("qemu", True),
            "910": ("qemu", True),
            "911": ("qemu", True),
            "100": ("qemu", False),
            "101": ("qemu", False),
            "106": ("qemu", False),
            "107": ("qemu", False),
        }
        self.guests = {
            vmid: guest(vmid, guest_type=t, in_scope=s) for vmid, (t, s) in live.items()
        }
        self.groups = {
            g.name: g
            for g in [
                group("ct/103"),
                group("ct/104"),
                group("ct/105"),
                group("vm/900"),
                group("vm/910"),
                group("vm/911"),
                group("vm/101", newest=STALE),
                group("vm/106", newest=STALE),
                group("vm/200", newest=STALE),
                group("vm/107", count=0, newest=None),
            ]
        }

    def test_the_baseline_is_five_reports_and_no_failures(self):
        problems, reports = classify(self.guests, self.groups)
        self.assertEqual(problems, [])
        self.assertEqual(len(reports), 5)

    def test_destroying_the_three_guests_leaves_four_reports(self):
        for vmid in ("100", "101", "106"):
            del self.guests[vmid]
        problems, reports = classify(self.guests, self.groups)
        self.assertEqual(problems, [])
        # 100 leaves entirely, having neither scope nor group; 101 and 106
        # cross from live-excluded to guest-gone; 200 and 107 are unchanged.
        self.assertEqual(len(reports), 4)

    def test_removing_the_frozen_groups_leaves_only_the_empty_one(self):
        for vmid in ("100", "101", "106"):
            del self.guests[vmid]
        for name in ("vm/101", "vm/106", "vm/200"):
            del self.groups[name]
        problems, reports = classify(self.guests, self.groups)
        self.assertEqual(problems, [])
        self.assertEqual(len(reports), 1)
        self.assertIn("vm/107", reports[0])


if __name__ == "__main__":
    unittest.main()
