"""Tests for the mirror plan.

Run with `python3 -m unittest discover kubernetes/apps/soft-serve`.

The expensive mistakes are in scope, not in parsing: a fork let in costs
gigabytes of upstream history (nixpkgs alone is 3 GB), and a repository left
out is one GitHub can lose with no copy here. Against GitHub on 2026-09-28 the
plan held 70 repositories: 67 of DArtagan's, 7 of them archived, and one per
organisation.
"""

import json
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mirror_plan  # noqa: E402


def repo(  # noqa: PLR0913 -- one keyword per field a test varies
    *,
    name: str = "r",
    owner: str = "DArtagan",
    fork: bool = False,
    archived: bool = False,
    private: bool = False,
    pushed_at: str | None = "2026-09-26T12:00:00Z",
) -> dict:
    return {
        "full_name": f"{owner}/{name}",
        "fork": fork,
        "archived": archived,
        "private": private,
        "pushed_at": pushed_at,
        "clone_url": f"https://github.com/{owner}/{name}.git",
    }


class Scope(unittest.TestCase):
    def test_own_repository_is_in(self) -> None:
        self.assertTrue(mirror_plan.in_scope(repo()))

    def test_fork_is_out(self) -> None:
        self.assertFalse(mirror_plan.in_scope(repo(fork=True)))

    def test_archived_fork_is_out(self) -> None:
        self.assertFalse(mirror_plan.in_scope(repo(fork=True, archived=True)))

    def test_archived_repository_is_in(self) -> None:
        self.assertTrue(mirror_plan.in_scope(repo(archived=True)))


class Lines(unittest.TestCase):
    def test_fields(self) -> None:
        line = mirror_plan.plan_line(repo(name="x", owner="green-nearby", private=True))
        self.assertEqual(
            line.split("\t"),
            [
                "github/green-nearby/x",
                "1",
                "1790424000",
                "https://github.com/green-nearby/x.git",
            ],
        )

    def test_never_pushed_reads_as_epoch_zero(self) -> None:
        self.assertEqual(
            mirror_plan.plan_line(repo(pushed_at=None)).split("\t")[2], "0"
        )

    def test_plan_filters_and_sorts(self) -> None:
        lines = mirror_plan.plan(
            [repo(name="b"), repo(name="nixpkgs", fork=True), repo(name="a")]
        )
        self.assertEqual(
            [line.split("\t")[0] for line in lines],
            ["github/DArtagan/a", "github/DArtagan/b"],
        )


class Fetch(unittest.TestCase):
    def test_follows_pages_until_a_short_one(self) -> None:
        pages = {
            1: [repo(name=str(i)) for i in range(mirror_plan.PER_PAGE)],
            2: [repo(name="last")],
        }
        seen = []

        def urlopen(req: urllib.request.Request, **_: object) -> mock.MagicMock:
            page = int(req.full_url.rsplit("page=", 1)[1])
            seen.append(page)
            resp = mock.MagicMock()
            resp.__enter__.return_value = resp
            resp.read.return_value = json.dumps(pages[page]).encode()
            return resp

        with mock.patch("urllib.request.urlopen", urlopen):
            got = mirror_plan.get("/user/repos?affiliation=owner", "t")
        self.assertEqual(seen, [1, 2])
        self.assertEqual(len(got), mirror_plan.PER_PAGE + 1)

    def test_sends_the_token_only_to_the_users_own_listing(self) -> None:
        # A fine-grained token is refused by every other owner's endpoints,
        # public ones included: GitHub answered the organisations with 403.
        calls = []
        with mock.patch.object(
            mirror_plan, "get", lambda path, token: calls.append((path, token)) or []
        ):
            mirror_plan.fetch("t")
        self.assertEqual(
            calls,
            [
                ("/user/repos?affiliation=owner", "t"),
                ("/orgs/DynamicMarkdown/repos?type=all", None),
                ("/orgs/birthdays-today/repos?type=all", None),
                ("/orgs/green-nearby/repos?type=all", None),
            ],
        )

    def test_anonymous_requests_carry_no_authorization(self) -> None:
        sent = []

        def urlopen(req: urllib.request.Request, **_: object) -> mock.MagicMock:
            sent.append(req.has_header("Authorization"))
            resp = mock.MagicMock()
            resp.__enter__.return_value = resp
            resp.read.return_value = b"[]"
            return resp

        with mock.patch("urllib.request.urlopen", urlopen):
            mirror_plan.get("/orgs/x/repos", None)
            mirror_plan.get("/user/repos", "t")
        self.assertEqual(sent, [False, True])


class Main(unittest.TestCase):
    def test_empty_plan_fails_and_writes_nothing(self) -> None:
        out = HERE / "plan-that-must-not-exist.tsv"
        env = {"GITHUB_TOKEN_FILE": __file__, "PLAN_FILE": str(out)}
        with (
            mock.patch.object(mirror_plan, "fetch", return_value=[repo(fork=True)]),
            mock.patch.dict("os.environ", env),
        ):
            self.assertEqual(mirror_plan.main(), 1)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
