"""Lists the GitHub repositories Soft Serve mirrors, as a plan for mirror_sync.sh.

The scope is the user's decision, recorded in docs/git.md: every repository
that is not a fork, archived or not, across the user and the three
organisations. Forks are mostly upstream history, and most of the size.

Writes one line per repository, tab-separated and sorted:

    github/<owner>/<name>  <private 0|1>  <pushed_at, epoch seconds>  <clone URL>

The token is read-only and fine-grained, so its one resource owner is the user.
GitHub refuses it for any other owner's endpoints, public ones included, so the
organisations are listed without it: their repositories are all public, and
three unauthenticated calls an hour sit well inside the limit of 60. A private
organisation repository would need a token of its own.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

API = "https://api.github.com"
ORGS = ("DynamicMarkdown", "birthdays-today", "green-nearby")
PER_PAGE = 100


def in_scope(repo: dict) -> bool:
    """Whether a repository is mirrored: any that is not a fork."""
    return not repo["fork"]


def plan_line(repo: dict) -> str:
    """One repository as a plan line."""
    # null for a repository that has never been pushed to.
    pushed = repo["pushed_at"]
    epoch = int(datetime.fromisoformat(pushed).timestamp()) if pushed else 0
    return "\t".join(
        (
            f"github/{repo['full_name']}",
            "1" if repo["private"] else "0",
            str(epoch),
            repo["clone_url"],
        )
    )


def plan(repos: list[dict]) -> list[str]:
    """Return the sorted plan lines of the repositories in scope."""
    return sorted(plan_line(r) for r in repos if in_scope(r))


def get(path: str, token: str | None) -> list[dict]:
    """Every page of a listing endpoint, anonymously when token is None."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    items: list[dict] = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        req = urllib.request.Request(  # noqa: S310 -- API is a fixed https URL
            f"{API}{path}{sep}per_page={PER_PAGE}&page={page}",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            batch = json.load(resp)
        items.extend(batch)
        if len(batch) < PER_PAGE:
            return items
        page += 1


def fetch(token: str) -> list[dict]:
    """Every repository of the user and the organisations."""
    # /user/repos rather than /users/<user>/repos: only the authenticated
    # listing includes private repositories.
    repos = get("/user/repos?affiliation=owner", token)
    for org in ORGS:
        repos.extend(get(f"/orgs/{org}/repos?type=all", None))
    return repos


def main() -> int:
    """Write the plan; fail if it would be empty."""
    token = Path(os.environ["GITHUB_TOKEN_FILE"]).read_text().strip()
    out = Path(os.environ["PLAN_FILE"])

    lines = plan(fetch(token))
    # An empty plan checks no mirror, so mirror_sync.sh would pass while
    # watching nothing. GitHub answering with nothing is a fault, not a fact.
    if not lines:
        print("GitHub listed no repositories in scope", file=sys.stderr)
        return 1

    tmp = out.with_suffix(".tmp")
    tmp.write_text("".join(f"{line}\n" for line in lines))
    tmp.replace(out)
    print(f"{len(lines)} repositories in scope")
    return 0


if __name__ == "__main__":
    sys.exit(main())
