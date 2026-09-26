"""Tests for ensure-security-policy.py, which reconciles the Security threats policy.

Run with `python3 -m unittest discover tools/gateway-security-policy`.

The script lives in `kubernetes/apps/cloudflare-gateway/config-map.yaml`,
because that ConfigMap is what the CronJob mounts. These tests extract it from
there so there is one copy.

The household allowlist exempts its domains from this policy as well as from
the filter lists, so the property that matters is that an allowlisted domain
never appears in the block rule's reach, and that editing the allowlist reaches
Cloudflare on the next run rather than being mistaken for no drift.
"""

import os
import tempfile
import types
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CONFIG_MAP = REPO / "kubernetes" / "apps" / "cloudflare-gateway" / "config-map.yaml"


def load_script() -> types.ModuleType:
    data = yaml.safe_load(CONFIG_MAP.read_text())["data"]
    module = types.ModuleType("ensure_security_policy")
    source = data["ensure-security-policy.py"]
    exec(compile(source, "ensure-security-policy.py", "exec"), module.__dict__)  # noqa: S102
    return module


policy = load_script()
CATEGORIES_ONLY = (
    "any(dns.security_category[*] in {"
    "80 83 117 131 134 151 153 175 176 178 187 188 191"
    "})"
)


class FakeCloudflare:
    def __init__(self, rules: list[dict]) -> None:
        self.rules = rules
        self.writes: list[tuple[str, dict]] = []

    def __call__(
        self, method: str, _url: str, _token: str, body: dict | None = None
    ) -> dict:
        if method == "GET":
            return {"success": True, "result": self.rules}
        self.writes.append((method, body))
        return {"success": True, "result": body}


class AllowedDomainsTest(unittest.TestCase):
    def test_skips_comments_and_blank_lines(self) -> None:
        text = "# Household exceptions.\n\narchive.ph\n  # comment\narchive.is\n"
        self.assertEqual(policy.allowed_domains(text), ["archive.is", "archive.ph"])

    def test_normalises_case_whitespace_and_duplicates(self) -> None:
        self.assertEqual(
            policy.allowed_domains("  Archive.PH \narchive.ph\n"), ["archive.ph"]
        )

    def test_refuses_a_line_that_is_not_a_domain(self) -> None:
        # The domains are spliced into a filter expression; a quote or brace
        # would change its meaning rather than merely fail to match.
        for line in ['archive.ph" or true', "@@||archive.ph^", "archive ph"]:
            with self.subTest(line=line), self.assertRaises(ValueError):
                policy.allowed_domains(line)


class TrafficTest(unittest.TestCase):
    def test_no_exceptions_blocks_the_categories_alone(self) -> None:
        self.assertEqual(policy.traffic([]), CATEGORIES_ONLY)

    def test_exceptions_are_carved_out_of_the_block(self) -> None:
        self.assertEqual(
            policy.traffic(["archive.is", "archive.ph"]),
            CATEGORIES_ONLY
            + ' and not(any(dns.domains[*] in {"archive.is" "archive.ph"}))',
        )


class ReconcileTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CLOUDFLARE_API_TOKEN"] = "unused"  # noqa: S105
        os.environ["CLOUDFLARE_ACCOUNT_ID"] = "account"
        handle, self.allowlist = tempfile.mkstemp()
        os.close(handle)
        self.addCleanup(os.unlink, self.allowlist)

    def write_allowlist(self, text: str) -> None:
        Path(self.allowlist).write_text(text)

    def existing(self, traffic: str) -> dict:
        return {
            "id": "rule",
            "name": policy.NAME,
            "action": "block",
            "enabled": True,
            "traffic": traffic,
        }

    def test_creates_the_policy_with_the_allowlist_carved_out(self) -> None:
        self.write_allowlist("archive.ph\n")
        cloudflare = FakeCloudflare([])
        policy.main(cloudflare, self.allowlist)
        [(method, body)] = cloudflare.writes
        self.assertEqual(method, "POST")
        self.assertIn('"archive.ph"', body["traffic"])

    def test_an_allowlist_edit_is_drift(self) -> None:
        self.write_allowlist("archive.ph\n")
        cloudflare = FakeCloudflare([self.existing(CATEGORIES_ONLY)])
        policy.main(cloudflare, self.allowlist)
        [(method, body)] = cloudflare.writes
        self.assertEqual(method, "PUT")
        self.assertEqual(body["traffic"], policy.traffic(["archive.ph"]))

    def test_an_unchanged_allowlist_writes_nothing(self) -> None:
        self.write_allowlist("archive.ph\n")
        cloudflare = FakeCloudflare([self.existing(policy.traffic(["archive.ph"]))])
        policy.main(cloudflare, self.allowlist)
        self.assertEqual(cloudflare.writes, [])


if __name__ == "__main__":
    unittest.main()
